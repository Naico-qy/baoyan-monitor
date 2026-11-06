"""HTTP 抓取层：仅用标准库，处理国内高校站点的常见坑。

- 自动解压 gzip/deflate
- 自动识别 UTF-8 / GBK(GB18030) 编码
- 失败重试 + 指数退避；5xx/429 重试，4xx 直接放弃
- 同域限速，避免给院系站点带来压力
- 证书链不完整的 .edu.cn 站点自动降级为不校验（只读公开页面）
"""

from __future__ import annotations

import gzip
import io
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 BaoyanMonitor/1.0"
)

DEFAULT_TIMEOUT = 25
MIN_HOST_INTERVAL = 0.7
MAX_RETRIES = 3

_last_hit: dict[str, float] = {}
_insecure_ctx = ssl.create_default_context()
_insecure_ctx.check_hostname = False
_insecure_ctx.verify_mode = ssl.CERT_NONE


class FetchResult:
    __slots__ = ("url", "final_url", "status", "text", "encoding", "error", "elapsed")

    def __init__(self, url, final_url=None, status=None, text=None, encoding=None, error=None, elapsed=0.0):
        self.url = url
        self.final_url = final_url or url
        self.status = status
        self.text = text
        self.encoding = encoding
        self.error = error
        self.elapsed = elapsed

    @property
    def ok(self) -> bool:
        return self.error is None and self.text is not None and (self.status or 0) < 400


def _polite_wait(url: str) -> None:
    host = urllib.parse.urlsplit(url).netloc
    last = _last_hit.get(host)
    now = time.monotonic()
    if last is not None:
        wait = MIN_HOST_INTERVAL - (now - last)
        if wait > 0:
            time.sleep(wait)
    _last_hit[host] = time.monotonic()


def _decode(raw: bytes, content_type: str) -> tuple[str, str]:
    candidates: list[str] = []
    m = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type or "", re.I)
    if m:
        candidates.append(m.group(1))
    head = raw[:4096].decode("latin-1", "ignore")
    m = re.search(r"charset\s*=\s*[\"']?([\w-]+)", head, re.I)
    if m:
        candidates.append(m.group(1))
    candidates.extend(["utf-8", "gb18030"])

    seen = set()
    for enc in candidates:
        enc = enc.lower().strip()
        if enc in seen:
            continue
        seen.add(enc)
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace"), "utf-8/replace"


def _decompress(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").lower()
    if encoding == "gzip":
        try:
            return gzip.decompress(raw)
        except OSError:
            return raw
    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                return raw
    if encoding in ("br", "zstd"):
        # 标准库无解压支持；请求时未声明支持，正常不会走到这里
        return raw
    return raw


def _request_once(url: str, timeout: int, *, insecure: bool = False) -> FetchResult:
    parts = urllib.parse.urlsplit(url)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            # 同源 Referer：部分院系站有简单防盗链，缺 Referer 会直接 403
            "Referer": f"{parts.scheme}://{parts.netloc}/",
            "Connection": "close",
        },
    )
    kwargs = {"timeout": timeout}
    if insecure:
        kwargs["context"] = _insecure_ctx
    with urllib.request.urlopen(req, **kwargs) as resp:
        raw = _decompress(resp.read(), resp.headers.get("Content-Encoding", ""))
        text, enc = _decode(raw, resp.headers.get("Content-Type", ""))
        return FetchResult(url, resp.geturl(), resp.status, text, enc, None)


def _is_cert_error(exc: BaseException) -> bool:
    """证书链问题的判定：urllib 会把 SSLCertVerificationError 包进 URLError，需要看文本。"""
    if isinstance(exc, ssl.SSLError):
        return True
    text = f"{type(exc).__name__}: {exc}" + " ".join(str(a) for a in getattr(exc, "args", ()))
    return "CERTIFICATE_VERIFY_FAILED" in text.upper() or "certificate verify failed" in text.lower()


def fetch(url: str, *, timeout: int = DEFAULT_TIMEOUT, retries: int = MAX_RETRIES) -> FetchResult:
    """抓取单个 URL，返回解码后的文本。不抛异常，错误记录在 FetchResult.error。"""
    started = time.monotonic()
    last_error = None

    for attempt in range(retries):
        _polite_wait(url)
        try:
            res = _request_once(url, timeout)
            res.elapsed = time.monotonic() - started
            return res
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code < 500 and exc.code != 429:
                break
        except Exception as exc:  # noqa: BLE001
            # .edu.cn 站点常见中间证书缺失：降级为不校验证书重试一次（仅读取公开页面）
            if _is_cert_error(exc):
                last_error = "SSLCertVerificationError"
                try:
                    res = _request_once(url, timeout, insecure=True)
                    res.elapsed = time.monotonic() - started
                    return res
                except urllib.error.HTTPError as exc2:
                    last_error = f"HTTP {exc2.code}"
                    if exc2.code < 500 and exc2.code != 429:
                        break
                except Exception as exc2:  # noqa: BLE001
                    last_error = f"{type(exc2).__name__}: {exc2}"
            else:
                last_error = f"{type(exc).__name__}: {exc}"

        if attempt < retries - 1:
            time.sleep(1.5 * (attempt + 1))

    return FetchResult(url, error=last_error or "unknown error", elapsed=time.monotonic() - started)


def fetch_stream(url: str, timeout: int = DEFAULT_TIMEOUT):
    """流式抓取，用于回退到备用解码方式（少用）。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return io.BytesIO(urllib.request.urlopen(req, timeout=timeout).read())