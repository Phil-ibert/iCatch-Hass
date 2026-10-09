#!/usr/bin/env python3
"""Fake iCatch DVR for tests: serves /cgi-bin/net_video.cgi like the real one.

Frames come either from captures of a real DVR (``--sd FILE --hd FILE``, raw
``curl -o`` output of net_video.cgi) or from a synthetic test pattern encoded
with ffmpeg. They are replayed in a loop at the pace given by the DVR's own
microsecond counter, filtered by the requested channel mask, behind HTTP
Basic auth, as ``multipart/x-mixed-replace;boundary=--myboundary``.
"""

from __future__ import annotations

import argparse
import base64
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "icatch_dvr", "app"))

from icatch_protocol import MAGIC, SUB_HEADER, TOP_HEADER, Frame, StreamParser  # noqa: E402


def build_message(frames: list[Frame]) -> bytes:
    """Serialise frames into one DVR frame message (inverse of parse_message)."""
    subs = b""
    for f in frames:
        pad = (-len(f.payload)) % 4
        hdr = struct.pack("<6I", f.type, f.channel, f.width, f.height, f.fps, f.timestamp_us)
        hdr += b"\0" * (0x24 - len(hdr)) + struct.pack("<2I", len(f.payload), len(f.payload) + pad)
        assert len(hdr) == SUB_HEADER
        subs += hdr + f.payload + b"\0" * pad
    unix_time = frames[0].unix_time if frames else int(time.time())
    top = struct.pack("<4I", MAGIC, 0, 0, unix_time) + struct.pack("<I", 7200)
    top += b"\0" * 4 + struct.pack("<2I", len(subs), len(frames))
    top += b"\0" * (TOP_HEADER - len(top))
    return top + subs


def multipart_part(message: bytes) -> bytes:
    return b"--myboundary\r\nContent-Type: application/octet-stream\r\n\r\n" + message + b"\r\n"


def load_capture(path: str) -> list[Frame]:
    parser = StreamParser()
    frames: list[Frame] = []
    with open(path, "rb") as fh:
        while chunk := fh.read(65536):
            frames.extend(parser.feed(chunk))
    return [f for f in frames if f.is_video]


def _nal_info(data: bytes, n: int, codec: str) -> tuple[bool, bool, bool]:
    """(is_vcl, is_keyframe, starts_new_picture) for the NAL whose header is at n."""
    if codec == "hevc":
        t = (data[n] >> 1) & 0x3F
        vcl = t < 32
        return vcl, t in (19, 20, 21), vcl and n + 2 < len(data) and bool(data[n + 2] & 0x80)
    t = data[n] & 0x1F
    vcl = t in (1, 5)
    return vcl, t == 5, vcl and n + 1 < len(data) and bool(data[n + 1] & 0x80)


def split_pictures(data: bytes, codec: str) -> list[tuple[bool, bytes]]:
    """Split an Annex-B elementary stream into (is_keyframe, access unit) pairs."""
    nals = []  # (start incl. start code, header offset)
    i = 0
    while (j := data.find(b"\x00\x00\x01", i)) != -1:
        nals.append((j - 1 if j and data[j - 1] == 0 else j, j + 3))
        i = j + 3
    pictures: list[tuple[bool, bytes]] = []
    pic_start, prefix_start, have_vcl, key = 0, None, False, False
    for start, hdr in nals:
        vcl, is_key, new_pic = _nal_info(data, hdr, codec)
        if vcl:
            if new_pic and have_vcl:
                cut = prefix_start if prefix_start is not None else start
                pictures.append((key, data[pic_start:cut]))
                pic_start, key = cut, False
            have_vcl, prefix_start = True, None
            key |= is_key
        elif have_vcl and prefix_start is None:
            prefix_start = start
    if have_vcl:
        pictures.append((key, data[pic_start:]))
    return pictures


def synthetic_frames(codec: str, width: int, height: int, fps: int, channels: list[int],
                     seconds: int = 4) -> list[Frame]:
    """Encode a test pattern and split it into DVR-like frames (one per picture)."""
    enc = "libx265" if codec == "hevc" else "libx264"
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "v.bin")
        cmd = ["ffmpeg", "-v", "error", "-f", "lavfi",
               "-i", f"testsrc2=size={width}x{height}:rate={fps}", "-t", str(seconds),
               "-pix_fmt", "yuv420p", "-c:v", enc, "-g", str(fps), "-bf", "0"]
        if codec == "hevc":
            cmd += ["-x265-params", "log-level=error:repeat-headers=1"]
        cmd += ["-f", "hevc" if codec == "hevc" else "h264", out]
        subprocess.run(cmd, check=True)
        with open(out, "rb") as fh:
            data = fh.read()
    base_type = 11 if codec == "hevc" else 0
    frames, ts = [], 1_000_000
    for key, payload in split_pictures(data, codec):
        for ch in channels:
            frames.append(Frame(base_type + (0 if key else 1), ch, width, height, fps, ts,
                                int(time.time()), payload))
        ts += 1_000_000 // fps
    return frames


class FakeDVR:
    def __init__(self, sd: list[Frame], hd: list[Frame], username="admin", password="secret",
                 realtime=True):
        self.sd, self.hd = sd, hd
        self.auth = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.realtime = realtime
        self.requests: list[str] = []
        self.active = 0
        self._lock = threading.Lock()

    def handler(self):
        dvr = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"
            server_version = "mini_httpd/1.19 19dec2003"

            def log_message(self, *_):
                pass

            def do_GET(self):
                dvr.requests.append(self.path)
                if self.headers.get("Authorization") != f"Basic {dvr.auth}":
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="DVR"')
                    self.end_headers()
                    self.wfile.write(b"401 Unauthorized\n")
                    return
                url = urlparse(self.path)
                if url.path != "/cgi-bin/net_video.cgi":
                    self.send_response(404)
                    self.end_headers()
                    return
                q = {k: v[0] for k, v in parse_qs(url.query).items()}
                mask = int(q.get("iframe", "0"))
                source = dvr.hd if q.get("hq") == "1" else dvr.sd
                frames = [f for f in source if mask >> f.channel & 1]
                self.send_response(200)
                self.send_header("Content-type", "multipart/x-mixed-replace;boundary=--myboundary")
                self.end_headers()
                with dvr._lock:
                    dvr.active += 1
                try:
                    self._replay(frames)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with dvr._lock:
                        dvr.active -= 1

            def _replay(self, frames):
                if not frames:
                    while True:  # a real DVR just stays silent
                        time.sleep(1)
                        self.wfile.write(b"")
                        self.wfile.flush()
                first_key = next((i for i, f in enumerate(frames) if f.is_keyframe), 0)
                frames = frames[first_key:]
                prev_ts = None
                while True:
                    for f in frames:
                        if dvr.realtime and prev_ts is not None:
                            delta = ((f.timestamp_us - prev_ts) & 0xFFFFFFFF) / 1e6
                            time.sleep(min(max(delta, 0.0), 0.5))
                        prev_ts = f.timestamp_us
                        self.wfile.write(multipart_part(build_message([f])))
                        self.wfile.flush()

        return Handler

    def serve(self, host="127.0.0.1", port=0) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer((host, port), self.handler())
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sd", help="capture of hq=0 (all channels)")
    ap.add_argument("--hd", help="capture of hq=1")
    ap.add_argument("--port", type=int, default=1027)
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", default="secret")
    args = ap.parse_args()
    sd = load_capture(args.sd) if args.sd else synthetic_frames("h264", 640, 368, 12, list(range(5)))
    hd = load_capture(args.hd) if args.hd else synthetic_frames("hevc", 1280, 960, 12, [0])
    dvr = FakeDVR(sd, hd, args.username, args.password)
    dvr.serve("127.0.0.1", args.port)
    print(f"fake DVR on 127.0.0.1:{args.port} ({len(sd)} SD / {len(hd)} HD frames)", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
