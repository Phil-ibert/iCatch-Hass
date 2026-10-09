#!/usr/bin/env python3
"""List the cameras an iCatch DVR is streaming (diagnostic, run at add-on start).

Reads the low-definition stream of every channel for a few seconds and reports
codec, resolution, frame rate and whether the camera looks connected (a
"VIDEO LOSS" channel only sends one still key frame per second).

Connection settings: ICATCH_HOST, ICATCH_PORT, ICATCH_USERNAME, ICATCH_PASSWORD.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from icatch_protocol import MAX_CHANNELS, AuthError, DVRError, DVRStream  # noqa: E402


def probe(host: str, port: int, user: str, password: str, seconds: float, hq: bool) -> dict:
    stats: dict[int, dict] = defaultdict(lambda: {"frames": 0, "keyframes": 0, "bytes": 0})
    channels = list(range(1, MAX_CHANNELS + 1))
    deadline = time.monotonic() + seconds
    with DVRStream(host, port, user, password, channels, hq=hq) as stream:
        for frame in stream:
            if frame.is_video:
                s = stats[frame.channel + 1]
                s["frames"] += 1
                s["keyframes"] += frame.is_keyframe
                s["bytes"] += len(frame.payload)
                s.update(codec=frame.codec, width=frame.width, height=frame.height, fps=frame.fps)
            if time.monotonic() > deadline:
                break
    result = {}
    for ch in sorted(stats):
        s = stats[ch]
        s["connected"] = s["frames"] > s["keyframes"]
        s["kbps"] = round(s["bytes"] * 8 / 1000 / seconds)
        result[ch] = s
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--quality", choices=("sd", "hd"), default="sd")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        result = probe(
            os.environ.get("ICATCH_HOST", ""),
            int(os.environ.get("ICATCH_PORT", "80")),
            os.environ.get("ICATCH_USERNAME", ""),
            os.environ.get("ICATCH_PASSWORD", ""),
            args.seconds,
            args.quality == "hd",
        )
    except AuthError as err:
        print(f"ERREUR : {err}", file=sys.stderr)
        return 3
    except DVRError as err:
        print(f"ERREUR : {err}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result))
        return 0
    if not result:
        print("Aucune vidéo reçue du DVR.")
        return 1
    for ch, s in result.items():
        state = "branchée" if s["connected"] else "pas de signal (VIDEO LOSS ?)"
        print(f"  caméra {ch}: {s['codec']} {s['width']}x{s['height']} @{s['fps']} i/s, "
              f"{s['kbps']} kbit/s - {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
