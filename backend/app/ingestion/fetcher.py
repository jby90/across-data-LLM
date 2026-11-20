"""礼貌抓取:遵守 robots.txt、限速、体积上限、防 SSRF、手动处理重定向。"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

Resolver = Callable[[str], Awaitable[list[str]]]


class BlockedError(Exception):
    """被策略拦截(robots、内网地址、协议不允许),不是网络故障,不应盲目重试。"""


class FetchError(Exception):
    """网络或 HTTP 错误。"""


@dataclass(frozen=True)
class FetchResult:
    url: str  # 最终 URL(跟随重定向之后)
    status: int
    content: bytes | None  # 304 时为 None
    mime: str
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


_NAT64_NET = ipaddress.ip_network("64:ff9b::/96")


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None and _ip_blocked(ip.ipv4_mapped):
            return True
        if ip.sixtofour is not None and _ip_blocked(ip.sixtofour):
            return True
        if ip.teredo is not None and (_ip_blocked(ip.teredo[0]) or _ip_blocked(ip.teredo[1])):
            return True
        if ip in _NAT64_NET:
            return True
    return (
        not ip.is_global
        or ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_blocked_host(host: str) -> bool:
    """字面量判断:localhost、内网/回环/链路本地/CGNAT IP、变形数字 IP、常见内部域名后缀。"""
    h = host.lower().rstrip(".").strip("[]")
    blocked_suffixes = (".localhost", ".local", ".internal", ".lan", ".intranet")
    if not h or h == "localhost" or h.endswith(blocked_suffixes):
        return True
    try:
        return _ip_blocked(ipaddress.ip_address(h))
    except ValueError:
        pass
    # 拦截十进制/八进制/十六进制变形 IP 字面量（如 http://2130706433/ 或 http://0177.0.0.1/）
    segs = h.split(".")
    if all(s and (s.isdigit() or s.startswith("0x")) for s in segs):
        return True
    try:
        packed = socket.inet_aton(h)
        return _ip_blocked(ipaddress.IPv4Address(packed))
    except OSError:
        return False


async def default_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def validate_public_http_url(url: str) -> str:
    """校验配置里的 URL:只允许 http/https 且主机不是内网字面量。返回规范化后的 URL。"""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("只允许 http/https 地址")
    if is_blocked_host(parts.hostname):
        raise ValueError("不允许内网或本机地址")
    return parts.geturl()


class PoliteFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        user_agent: str,
        delay_seconds: float = 2.0,
        max_bytes: int = 100 * 1024 * 1024,
        max_redirects: int = 5,
        resolver: Resolver = default_resolver,
        allow_resolved_cidrs: list[str] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        fallback_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client
        self._fallback_client = fallback_client
        self._ua = user_agent
        self._ua_token = user_agent.split("/")[0].strip() or "*"
        self._delay = delay_seconds
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._resolver = resolver
        # 仅放行「域名解析结果」落在这些网段的情况(如开发机代理软件的 fake-IP 198.18.0.0/15)。
        # 字面量内网地址、localhost 永远不放行;生产环境应保持为空。
        self._allowed_nets = [
            ipaddress.ip_network(c, strict=False) for c in (allow_resolved_cidrs or [])
        ]
        self._sleep = sleep
        self._clock = clock
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}  # None = 一律不允许
        self._robots_note: dict[str, str] = {}  # 每个源站 robots.txt 的获取情况,供预检报告

    async def _check_target(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise BlockedError(f"协议或地址不被允许: {url}")
        host = parts.hostname
        if is_blocked_host(host):
            raise BlockedError(f"内网或本机地址被拦截: {host}")
        try:
            addresses = await self._resolver(host)
        except OSError as e:
            raise FetchError(f"域名解析失败: {host}: {e}") from e
        for addr in addresses:
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if _ip_blocked(ip) and not any(ip in net for net in self._allowed_nets):
                raise BlockedError(f"域名解析到内网地址被拦截: {host} -> {addr}")
        return host

    async def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self._delay - (self._clock() - last)
            if wait > 0:
                await self._sleep(wait)
        self._last_hit[host] = self._clock()

    async def _stream_bytes(self, resp: httpx.Response, limit: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > limit:
                raise FetchError(f"响应超过大小上限: {resp.url}")
            chunks.append(chunk)
        return b"".join(chunks)

    async def _robots_for(self, url: str) -> RobotFileParser | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        robots_url = f"{origin}/robots.txt"
        parser: RobotFileParser | None = None
        robots_limit = min(self._max_bytes, 2 * 1024 * 1024)
        try:
            current_robots_url = robots_url
            status_code = 0
            ctype = ""
            robots_bytes = b""
            for _ in range(min(self._max_redirects, 5) + 1):
                r_host = await self._check_target(current_robots_url)
                await self._throttle(r_host)
                redirect_loc: str | None = None
                try:
                    async with self._client.stream(
                        "GET",
                        current_robots_url,
                        headers={"User-Agent": self._ua},
                        follow_redirects=False,
                        timeout=15,
                    ) as resp:
                        status_code = resp.status_code
                        ctype = resp.headers.get("content-type", "").lower()
                        if status_code in (301, 302, 303, 307, 308):
                            redirect_loc = resp.headers.get("location")
                        elif status_code == 200:
                            robots_bytes = await self._stream_bytes(resp, robots_limit)
                except httpx.HTTPError:
                    if self._fallback_client is None:
                        raise
                    async with self._fallback_client.stream(
                        "GET",
                        current_robots_url,
                        headers={"User-Agent": self._ua},
                        follow_redirects=False,
                        timeout=15,
                    ) as resp:
                        status_code = resp.status_code
                        ctype = resp.headers.get("content-type", "").lower()
                        if status_code in (301, 302, 303, 307, 308):
                            redirect_loc = resp.headers.get("location")
                        elif status_code == 200:
                            robots_bytes = await self._stream_bytes(resp, robots_limit)
                if status_code in (301, 302, 303, 307, 308):
                    if not redirect_loc:
                        break
                    current_robots_url = urljoin(current_robots_url, redirect_loc)
                    continue
                break
            final_path = urlsplit(current_robots_url).path.lower()
            robots_text = (
                robots_bytes.decode("utf-8", errors="replace") if status_code == 200 else ""
            )
            sniff_head = robots_text[:512].lstrip("\ufeff \t\r\n").lower()
            looks_html = (
                "text/html" in ctype
                or final_path.endswith("/error")
                or sniff_head.startswith(("<!doctype html", "<html"))
                or (sniff_head.startswith("<!--") and "<html" in sniff_head)
            )
            if status_code == 200 and not looks_html:
                parser = RobotFileParser()
                parser.parse(robots_text.splitlines())
                self._robots_note[origin] = "HTTP 200,已读取规则"
            elif status_code == 200 and looks_html:
                parser = None
                self._robots_note[origin] = "返回 HTML/错误页，无法确认 robots 规则，不抓取"
            elif status_code in (404, 410):
                parser = RobotFileParser()  # 没有 robots.txt:视为允许
                parser.parse([])
                self._robots_note[origin] = f"HTTP {status_code},无 robots.txt,视为允许"
            else:
                parser = None  # Authentication/rate limits/5xx never imply permission.
                self._robots_note[origin] = f"HTTP {status_code},无法确认规则,保守起见不抓取"
        except (httpx.HTTPError, BlockedError, FetchError) as e:
            parser = None
            self._robots_note[origin] = f"网络错误({type(e).__name__}),无法确认规则,不抓取"
        self._robots[origin] = parser
        return parser

    async def _robots_allowed(self, url: str) -> bool:
        parser = await self._robots_for(url)
        return parser is not None and parser.can_fetch(self._ua_token, url)

    async def preflight(self, url: str) -> dict[str, str]:
        """只读预检:不下载文件,只检查地址是否被策略拦截,以及 robots.txt 是否允许抓取。"""
        result = {"url": url, "target": "ok", "robots": "", "robots_detail": ""}
        try:
            await self._check_target(url)
        except BlockedError as e:
            result["target"] = f"拦截:{e}"
            return result
        except FetchError as e:
            result["target"] = f"无法访问:{e}"
            return result
        allowed = await self._robots_allowed(url)
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        result["robots"] = "允许" if allowed else "不允许/无法确认"
        result["robots_detail"] = self._robots_note.get(origin, "")
        return result

    async def get(
        self, url: str, *, etag: str | None = None, last_modified: str | None = None
    ) -> FetchResult:
        current = url
        for _ in range(self._max_redirects + 1):
            host = await self._check_target(current)
            if not await self._robots_allowed(current):
                raise BlockedError(f"robots.txt 不允许抓取或无法确认: {current}")
            await self._throttle(host)

            headers = {"User-Agent": self._ua, "Accept": "*/*"}
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified
            last_err: httpx.HTTPError | None = None
            clients = [self._client]
            if self._fallback_client is not None:
                clients.append(self._fallback_client)
            redirect_to: str | None = None
            for cli in clients:
                try:
                    async with cli.stream(
                        "GET", current, headers=headers, follow_redirects=False
                    ) as resp:
                        if resp.status_code in (301, 302, 303, 307, 308):
                            location = resp.headers.get("location")
                            if not location:
                                raise FetchError(f"重定向缺少 Location: {current}")
                            redirect_to = urljoin(current, location)
                            last_err = None
                            break
                        if resp.status_code == 304:
                            return FetchResult(
                                current, 304, None, "", etag, last_modified, not_modified=True
                            )
                        if resp.status_code >= 400:
                            raise FetchError(f"HTTP {resp.status_code}: {current}")
                        declared = resp.headers.get("content-length")
                        if declared and declared.isdigit() and int(declared) > self._max_bytes:
                            raise FetchError(f"文件过大({declared} 字节): {current}")
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in resp.aiter_bytes():
                            total += len(chunk)
                            if total > self._max_bytes:
                                raise FetchError(f"文件超过大小上限: {current}")
                            chunks.append(chunk)
                        mime = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                        return FetchResult(
                            current,
                            resp.status_code,
                            b"".join(chunks),
                            mime,
                            resp.headers.get("etag"),
                            resp.headers.get("last-modified"),
                        )
                except httpx.HTTPError as e:
                    last_err = e
                    continue
            if last_err is not None:
                raise FetchError(f"网络错误: {current}: {last_err}") from last_err
            if redirect_to is not None:
                current = redirect_to
                continue
        raise FetchError(f"重定向次数过多: {url}")

    async def post_form(self, url: str, form: dict[str, str]) -> FetchResult:
        """礼貌 POST 表单(用于官方目录的公开检索接口,如行业标准信息服务平台)。规则同 get。"""
        host = await self._check_target(url)
        if not await self._robots_allowed(url):
            raise BlockedError(f"robots.txt 禁止抓取: {url}")
        await self._throttle(host)
        headers = {"User-Agent": self._ua, "X-Requested-With": "XMLHttpRequest"}
        try:
            async with self._client.stream(
                "POST", url, data=form, headers=headers, follow_redirects=False
            ) as resp:
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code}: {url}")
                body = await self._stream_bytes(resp, self._max_bytes)
                mime = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                return FetchResult(url, resp.status_code, body, mime, None, None)
        except httpx.HTTPError as e:
            raise FetchError(f"网络错误: {url}: {e}") from e

    async def post_json(
        self,
        url: str,
        payload: dict,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> FetchResult:
        """礼貌 POST JSON(用于官方目录公开检索接口,如新加坡电子宪报)。

        遵守与 get 相同的内网拦截与同源限速规则。"""
        host = await self._check_target(url)
        if not await self._robots_allowed(url):
            raise BlockedError(f"robots.txt 禁止抓取: {url}")
        await self._throttle(host)
        headers = {
            "User-Agent": self._ua,
            "Accept": "application/json",
            "Content-Type": "application/json",
            **(extra_headers or {}),
        }
        try:
            async with self._client.stream(
                "POST", url, json=payload, headers=headers, follow_redirects=False
            ) as resp:
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code}: {url}")
                body = await self._stream_bytes(resp, self._max_bytes)
                mime = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                return FetchResult(url, resp.status_code, body, mime, None, None)
        except httpx.HTTPError as e:
            raise FetchError(f"网络错误: {url}: {e}") from e
