"""iCatch DVR live-stream protocol (``/cgi-bin/net_video.cgi``).

Validated against an iWatch DVR (iCatch SoC, web server ``mini_httpd/1.19``).

Request (HTTP Basic auth, on the DVR web port)::

    GET /cgi-bin/net_video.cgi?hq=<0|1>&iframe=<mask>&pframe=<mask>
        &audio=0&complete=0&beg=-1&end=-1&ivs=0

* ``hq=0`` low-definition sub-stream, ``hq=1`` main stream.
* ``iframe`` / ``pframe`` are channel bitmasks (bit 0 = camera 1).

Response: ``multipart/x-mixed-replace;boundary=--myboundary``. The device writes
the boundary literally as ``--myboundary`` and parts carry no Content-Length.
Each part body is one frame message::

    0x000  u32  magic 0x00001234
    0x004  u32  (varies)
    0x00c  u32  wall-clock UNIX time of the frame
    0x010  u32  local time offset in seconds (e.g. 7200)
    0x018  u32  length of everything after the 0x120-byte top header
    0x01c  u32  number of sub-chunks
    0x120       first sub-chunk

Sub-chunk (0x2c-byte header, then payload)::

    0x00  u32  type  0=H.264 I, 1=H.264 P, 2=audio, 11=H.265 I, 12=H.265 P
    0x04  u32  channel (0-based)
    0x08  u32  width
    0x0c  u32  height
    0x10  u32  frame rate configured on the DVR
    0x14  u32  microsecond counter (wraps)
    0x24  u32  payload size
    0x28  u32  step to the next sub-chunk, counted from the end of this header
    0x2c       payload: Annex-B NAL units (start codes included)

Only the standard library is used so the module runs in a bare Python image.
"""

from __future__ import annotations

import base64
import http.client
import socket
import struct
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urlencode

MAGIC = 0x00001234
TOP_HEADER = 0x120
SUB_HEADER = 0x2C
_PART_START = b"\r\n\r\n" + struct.pack("<I", MAGIC)

TYPE_H264_I, TYPE_H264_P, TYPE_AUDIO, TYPE_H265_I, TYPE_H265_P = 0, 1, 2, 11, 12
VIDEO_TYPES = {
    TYPE_H264_I: ("h264", True),
    TYPE_H264_P: ("h264", False),
    TYPE_H265_I: ("hevc", True),
    TYPE_H265_P: ("hevc", False),
}

MAX_FRAME_MESSAGE = 16 * 1024 * 1024
MAX_CHANNELS = 16


class DVRError(Exception):
    """The DVR refused or broke the stream."""


class AuthError(DVRError):
    """The DVR rejected the credentials (HTTP 401)."""


@dataclass(frozen=True)
class Frame:
    type: int
    channel: int  # 0-based, as sent by the DVR
    width: int
    height: int
    fps: int
    timestamp_us: int
    unix_time: int
    payload: bytes

    @property
    def codec(self) -> str | None:
        """'h264', 'hevc' or None for audio/unknown sub-chunks."""
        info = VIDEO_TYPES.get(self.type)
        return info[0] if info else None

    @property
    def is_video(self) -> bool:
        return self.type in VIDEO_TYPES

    @property
    def is_keyframe(self) -> bool:
        info = VIDEO_TYPES.get(self.type)
        return bool(info and info[1])


def channel_mask(channels: list[int] | tuple[int, ...] | set[int]) -> int:
    """Bitmask for 1-based channel numbers."""
    mask = 0
    for ch in channels:
        if not 1 <= ch <= MAX_CHANNELS:
            raise ValueError(f"channel {ch} out of range 1..{MAX_CHANNELS}")
        mask |= 1 << (ch - 1)
    return mask


def stream_path(mask: int, hq: bool) -> str:
    query = urlencode(
        {
            "hq": 1 if hq else 0,
            "iframe": mask,
            "pframe": mask,
            "audio": 0,
            "complete": 0,
            "beg": -1,
            "end": -1,
            "ivs": 0,
        }
    )
    return f"/cgi-bin/net_video.cgi?{query}"


def parse_message(body: bytes | memoryview) -> list[Frame]:
    """Parse one frame message (a multipart part body). Raises ValueError."""
    if len(body) < TOP_HEADER:
        raise ValueError("frame message shorter than the top header")
    magic, = struct.unpack_from("<I", body, 0)
    if magic != MAGIC:
        raise ValueError(f"bad magic 0x{magic:08x}")
    unix_time, = struct.unpack_from("<I", body, 0x0C)
    count, = struct.unpack_from("<I", body, 0x1C)
    frames: list[Frame] = []
    off = TOP_HEADER
    for _ in range(count):
        if off + SUB_HEADER > len(body):
            raise ValueError("sub-chunk header past end of message")
        typ, ch, width, height, fps, ts_us = struct.unpack_from("<6I", body, off)
        size, step = struct.unpack_from("<2I", body, off + 0x24)
        start = off + SUB_HEADER
        if start + size > len(body):
            raise ValueError("sub-chunk payload past end of message")
        frames.append(
            Frame(typ, ch, width, height, fps, ts_us, unix_time, bytes(body[start:start + size]))
        )
        off = start + max(step, size)
    return frames


class StreamParser:
    """Incremental parser: feed raw HTTP body bytes, get frames back.

    It does not rely on the multipart boundary string: a part starts right
    after a blank line that is followed by the magic, and its length comes
    from the frame header. After a corrupt part it resynchronises on the next
    part start.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.resyncs = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buf += data
        out: list[Frame] = []
        while True:
            start = self._buf.find(_PART_START)
            if start < 0:
                # keep a tail long enough to hold a split marker
                if len(self._buf) > len(_PART_START):
                    del self._buf[: len(self._buf) - len(_PART_START) + 1]
                return out
            body_at = start + 4
            if len(self._buf) < body_at + TOP_HEADER:
                del self._buf[:start]
                return out
            length, = struct.unpack_from("<I", self._buf, body_at + 0x18)
            end = body_at + TOP_HEADER + length
            if length > MAX_FRAME_MESSAGE:
                self.resyncs += 1
                del self._buf[: body_at]
                continue
            if len(self._buf) < end:
                del self._buf[:start]
                return out
            try:
                # bytes() copy: a live memoryview would forbid resizing the buffer
                out.extend(parse_message(bytes(self._buf[body_at:end])))
                del self._buf[:end]
            except ValueError:
                self.resyncs += 1
                del self._buf[: body_at]


class DVRStream:
    """Live stream from the DVR as an iterator of frames.

    >>> with DVRStream("192.168.1.108", 1027, "admin", "pw", [1], hq=False) as s:
    ...     for frame in s: ...
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        channels: list[int],
        hq: bool,
        timeout: float = 10.0,
    ) -> None:
        self.host, self.port = host, port
        self._auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.path = stream_path(channel_mask(channels), hq)
        self.timeout = timeout
        self._conn: http.client.HTTPConnection | None = None
        self._resp: http.client.HTTPResponse | None = None
        self._sock: socket.socket | None = None
        self._closed = False
        self.parser = StreamParser()

    def open(self) -> "DVRStream":
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request(
                "GET",
                self.path,
                headers={"Authorization": f"Basic {self._auth}", "User-Agent": "icatch-hass"},
            )
            # keep the socket: on an HTTP/1.0 answer the response takes it over,
            # and close() must be able to wake up a blocked read
            self._sock = conn.sock
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as err:
            conn.close()
            raise DVRError(f"cannot reach DVR at {self.host}:{self.port}: {err}") from err
        if resp.status == 401:
            conn.close()
            raise AuthError("DVR rejected the username/password (HTTP 401)")
        ctype = resp.getheader("Content-Type", "")
        if resp.status != 200 or "multipart" not in ctype.lower():
            conn.close()
            raise DVRError(f"unexpected DVR answer: HTTP {resp.status} {ctype!r}")
        self._conn, self._resp = conn, resp
        return self

    def interrupt(self) -> None:
        """Make a blocked __iter__ end promptly. Safe to call from a signal handler.

        Only the socket is touched: closing the response object while read1()
        runs on it raises "reentrant call inside BufferedReader".
        """
        self._closed = True
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)  # the pending recv returns EOF
            except OSError:
                pass

    def close(self) -> None:
        """Release the connection (not from a signal handler: use interrupt())."""
        self.interrupt()
        if self._resp is not None:
            self._resp.close()
        if self._conn is not None:
            self._conn.close()

    def __enter__(self) -> "DVRStream":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    def __iter__(self) -> Iterator[Frame]:
        if self._resp is None and not self._closed:
            self.open()
        resp = self._resp
        while True:
            if self._closed or resp is None:
                raise DVRError("stream closed")
            try:
                chunk = resp.read1(65536)
            except (OSError, ValueError, http.client.HTTPException) as err:
                if self._closed:
                    raise DVRError("stream closed") from err
                raise DVRError(f"stream interrupted: {err}") from err
            if not chunk:
                raise DVRError("stream closed" if self._closed else "DVR closed the stream")
            yield from self.parser.feed(chunk)
