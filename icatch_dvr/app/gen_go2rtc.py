#!/usr/bin/env python3
"""Build the go2rtc configuration and the discovery message from the add-on options.

Usage: gen_go2rtc.py /data/options.json /data/credentials.json /tmp/go2rtc.json /tmp/discovery.json

Two go2rtc streams per camera, started on demand:
  camN_sd  low-definition sub-stream (H.264), for previews and snapshots
  camN_hd  main stream (H.265 on recent DVRs), passed through or transcoded

Security:
* the go2rtc API and RTSP server always require a username/password: either
  the ``api_password`` option (user ``admin``) or credentials generated once
  and kept in /data. Home Assistant receives them through Supervisor discovery.
  go2rtc exempts loopback, which the exec sources use to publish.
* run.sh passes the config inline (``go2rtc -config '{...}'``): go2rtc then has
  no config file, so its API cannot rewrite it, and ``allow_paths`` limits
  exec/echo sources to the stream bridge.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sys

API_PORT = 1986
RTSP_PORT = 8586
STREAM_BIN = "/opt/icatch/icatch_stream.py"
# go2rtc exec parameters: SIGTERM first, SIGKILL after 5 s. The default start
# timeout (30 s) leaves plenty of room for the DVR's one-per-second key frame.
EXEC_PARAMS = "#killsignal=15#killtimeout=5"
HD_MODES = ("copy", "h264_1080p", "h264")


def credentials(options: dict, path: str) -> dict:
    """User-chosen API password, or credentials generated once and persisted."""
    if options.get("api_password"):
        return {"username": "admin", "password": options["api_password"]}
    try:
        with open(path, encoding="utf-8") as fh:
            creds = json.load(fh)
        if creds.get("username") and creds.get("password"):
            return creds
    except (OSError, ValueError):
        pass
    creds = {"username": "homeassistant", "password": secrets.token_urlsafe(24)}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(creds, fh)
    return creds


def camera_numbers(options: dict) -> list[int]:
    """Cameras 1..camera_count (an explicit 'cameras' list is still accepted)."""
    if options.get("cameras"):
        return sorted({int(c) for c in options["cameras"]})
    count = int(options.get("camera_count") or 0)
    return list(range(1, min(count, 16) + 1))


def build(options: dict, creds: dict) -> dict:
    cameras = camera_numbers(options)
    if not cameras:
        raise SystemExit("Aucune caméra configurée (option 'camera_count').")
    hd_video = options.get("hd_video", "copy")
    if hd_video not in HD_MODES:
        hd_video = "copy"
    streams = {}
    for ch in cameras:
        base = f"exec:{STREAM_BIN} --channel {ch}"
        streams[f"cam{ch}_sd"] = f"{base} --quality sd --output {{output}}{EXEC_PARAMS}"
        streams[f"cam{ch}_hd"] = f"{base} --quality hd --video {hd_video} --output {{output}}{EXEC_PARAMS}"

    auth = {"username": creds["username"], "password": creds["password"]}
    level = {"debug": "debug", "info": "info", "warning": "warn", "error": "error"}
    return {
        "api": {"listen": f":{API_PORT}", **auth},
        "rtsp": {"listen": f":{RTSP_PORT}", **auth},
        # WebRTC is served by Home Assistant's own go2rtc, not by this one.
        "webrtc": {"listen": "", "ice_servers": []},
        "exec": {"allow_paths": [STREAM_BIN]},
        "echo": {"allow_paths": ["/bin/false"]},
        "log": {"level": level.get(options.get("log_level", "info"), "info")},
        "streams": streams,
    }


def discovery(creds: dict, host: str | None = None) -> dict:
    """Connection details sent to Home Assistant (service 'icatch_dvr')."""
    return {
        "host": host or socket.gethostname(),
        "port": API_PORT,
        "rtsp_port": RTSP_PORT,
        "username": creds["username"],
        "password": creds["password"],
    }


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(__doc__, file=sys.stderr)
        return 2
    options_path, creds_path, config_path, discovery_path = argv[1:]
    with open(options_path, encoding="utf-8") as fh:
        options = json.load(fh)
    creds = credentials(options, creds_path)
    config = build(options, creds)
    for path, data in ((config_path, config), (discovery_path, discovery(creds))):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"))
    names = ", ".join(config["streams"])
    print(f"go2rtc : {len(config['streams'])} flux configurés ({names})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
