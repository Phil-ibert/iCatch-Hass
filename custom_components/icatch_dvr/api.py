"""Client for the go2rtc server run by the iCatch DVR add-on."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from urllib.parse import quote

import aiohttp

from .const import ADDON_SLUG, DEFAULT_API_PORT, REPOSITORY_URL

_STREAM_RE = re.compile(r"^cam(\d+)_(sd|hd)$")


class CannotConnect(Exception):
    """go2rtc did not answer."""


class InvalidAuth(Exception):
    """go2rtc refused the API credentials."""


class Go2rtcApi:
    """Minimal go2rtc HTTP API client (stream list and snapshots)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        rtsp_port: int,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._session = session
        self.host = host
        self.port = port
        self.rtsp_port = rtsp_port
        self._username = username
        self._password = password or ""
        self._auth = aiohttp.BasicAuth(username, self._password) if username else None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def rtsp_url(self, stream: str) -> str:
        """RTSP URL of a stream, with the add-on's credentials when it has some."""
        userinfo = ""
        if self._username:
            userinfo = f"{quote(self._username, safe='')}:{quote(self._password, safe='')}@"
        return f"rtsp://{userinfo}{self.host}:{self.rtsp_port}/{stream}"

    async def _get(self, path: str, timeout: float, **params) -> bytes:
        try:
            async with self._session.get(
                f"{self.base_url}{path}",
                params=params or None,
                auth=self._auth,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 401:
                    raise InvalidAuth
                resp.raise_for_status()
                return await resp.read()
        except InvalidAuth:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CannotConnect(str(err)) from err

    async def cameras(self) -> list[int]:
        """Camera numbers the add-on exposes (it creates camN_sd and camN_hd)."""
        raw = await self._get("/api/streams", timeout=10)
        try:
            streams = json.loads(raw or b"{}")
        except ValueError as err:
            raise CannotConnect("not a go2rtc server") from err
        found = set()
        for name in streams:
            if (m := _STREAM_RE.match(name)) and m.group(2) == "sd":
                found.add(int(m.group(1)))
        return sorted(found)

    async def snapshot(self, stream: str) -> bytes:
        """JPEG of the current picture of a stream (go2rtc starts it if needed)."""
        return await self._get("/api/frame.jpeg", timeout=20, src=stream)


def addon_host_candidates() -> list[str]:
    """Internal host names the add-on may have, most likely first.

    The Supervisor names a third-party add-on '<repo hash>-<slug>', where the
    hash is the first 8 hex digits of sha1(lower-case repository URL).
    """
    slug = ADDON_SLUG.replace("_", "-")
    hosts = []
    for url in (REPOSITORY_URL, f"{REPOSITORY_URL}.git", f"{REPOSITORY_URL}/"):
        digest = hashlib.sha1(url.lower().encode()).hexdigest()[:8]
        hosts.append(f"{digest}-{slug}")
    hosts.append(f"local-{slug}")  # add-on copied into /addons
    return hosts


async def find_addon(session: aiohttp.ClientSession) -> str | None:
    """Return the first candidate host where the add-on's go2rtc answers."""

    async def probe(host: str) -> str | None:
        try:
            await Go2rtcApi(session, host, DEFAULT_API_PORT, 0)._get("/api/streams", timeout=3)
        except InvalidAuth:
            return host  # it is there, just protected
        except CannotConnect:
            return None
        return host

    for host in await asyncio.gather(*(probe(h) for h in addon_host_candidates())):
        if host:
            return host
    return None
