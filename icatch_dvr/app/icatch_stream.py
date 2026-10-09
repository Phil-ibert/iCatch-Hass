#!/usr/bin/env python3
"""Bridge one iCatch DVR camera to ffmpeg (and from there to go2rtc).

go2rtc starts this program on demand through an ``exec:`` source and stops it
when the last viewer leaves::

    exec:python3 /opt/icatch/icatch_stream.py --channel 1 --quality sd --output {output}

DVR connection settings come from the environment so the password never shows
up on a command line or in go2rtc's logs:
ICATCH_HOST, ICATCH_PORT, ICATCH_USERNAME, ICATCH_PASSWORD.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from icatch_protocol import AuthError, DVRError, DVRStream  # noqa: E402

LOG = logging.getLogger("icatch_stream")
FIRST_KEYFRAME_TIMEOUT = 15.0
STALL_TIMEOUT = 10.0
_PR_SET_PDEATHSIG = 1


def _die_with_parent(sig: int = signal.SIGTERM) -> None:
    """Ask the kernel to signal us when our parent exits (Linux only)."""
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(_PR_SET_PDEATHSIG, sig)
    except (OSError, AttributeError):
        try:  # musl (Alpine)
            ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_PDEATHSIG, sig)
        except (OSError, AttributeError):
            pass


def ffmpeg_command(codec: str, output: str, video: str, fps: int) -> list[str]:
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-fflags", "+genpts+nobuffer", "-flags", "low_delay",
        # small probe: first output ~1 s after the key frame (256k -> ~2 s on SD)
        "-probesize", "32k", "-analyzeduration", "0",
        "-use_wallclock_as_timestamps", "1",
    ]
    cmd += ["-f", codec, "-i", "pipe:0", "-an"]
    gop = str(max(fps, 1) * 2)
    if video == "copy":
        cmd += ["-c:v", "copy"]
    elif video in ("h264", "h264_1080p"):
        if video == "h264_1080p":
            cmd += ["-vf", "scale=-2:'min(1080,ih)'"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                "-pix_fmt", "yuv420p", "-g", gop, "-bf", "0", "-crf", "23"]
    else:
        raise ValueError(f"unknown video mode {video!r}")
    if output.startswith("rtsp://"):
        cmd += ["-f", "rtsp", "-rtsp_transport", "tcp", output]
    else:
        cmd += ["-y", output]
    return cmd


def run(args: argparse.Namespace) -> int:
    host = os.environ.get("ICATCH_HOST", "")
    port = int(os.environ.get("ICATCH_PORT", "80"))
    user = os.environ.get("ICATCH_USERNAME", "")
    password = os.environ.get("ICATCH_PASSWORD", "")
    if not host:
        LOG.error("ICATCH_HOST is not set")
        return 2
    if not args.output.startswith("rtsp://") and os.environ.get("ICATCH_ALLOW_FILE_OUTPUT") != "1":
        LOG.error("--output must be an rtsp:// URL (file output is for tests only)")
        return 2
    hq = args.quality == "hd"
    label = f"camera {args.channel} {args.quality.upper()}"
    wanted = args.channel - 1

    stream = DVRStream(host, port, user, password, [args.channel], hq=hq)
    ffmpeg: subprocess.Popen | None = None
    stop = False

    def _stop(signum, _frame):
        nonlocal stop
        stop = True
        stream.interrupt()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        stream.open()
        LOG.info("%s: connected to %s:%s", label, host, port)
        started = time.monotonic()
        last_frame = started
        for frame in stream:
            now = time.monotonic()
            if stop:
                break
            if frame.channel != wanted or not frame.is_video:
                if ffmpeg is None and now - started > FIRST_KEYFRAME_TIMEOUT:
                    LOG.error("%s: no video from the DVR (camera unplugged or wrong channel?)", label)
                    return 1
                if ffmpeg is not None and now - last_frame > STALL_TIMEOUT:
                    LOG.error("%s: video stalled", label)
                    return 1
                continue
            if ffmpeg is None:
                if not frame.is_keyframe:
                    if now - started > FIRST_KEYFRAME_TIMEOUT:
                        LOG.error("%s: no keyframe received", label)
                        return 1
                    continue
                video = args.video if hq else "copy"
                cmd = ffmpeg_command(frame.codec or "h264", args.output, video, frame.fps)
                LOG.info("%s: %s %dx%d @%d fps -> ffmpeg (%s)",
                         label, frame.codec, frame.width, frame.height, frame.fps, video)
                ffmpeg = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, preexec_fn=lambda: _die_with_parent(signal.SIGKILL)
                )
            last_frame = now
            try:
                assert ffmpeg.stdin is not None
                ffmpeg.stdin.write(frame.payload)
                ffmpeg.stdin.flush()
            except (BrokenPipeError, ValueError):
                code = ffmpeg.wait()
                LOG.info("%s: ffmpeg exited (code %s)", label, code)
                return 0 if code == 0 else 1
    except AuthError as err:
        LOG.error("%s: %s", label, err)
        return 3
    except DVRError as err:
        if stop:
            return 0
        LOG.error("%s: %s", label, err)
        return 1
    finally:
        stream.close()
        if ffmpeg is not None:
            try:
                if ffmpeg.stdin:
                    ffmpeg.stdin.close()
            except OSError:
                pass
            try:
                ffmpeg.wait(timeout=3)
            except subprocess.TimeoutExpired:
                ffmpeg.kill()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--channel", type=int, required=True, help="camera number, 1-based")
    parser.add_argument("--quality", choices=("sd", "hd"), required=True)
    parser.add_argument("--output", required=True, help="rtsp:// URL ({output} from go2rtc) or a file")
    parser.add_argument("--video", choices=("copy", "h264_1080p", "h264"), default="copy",
                        help="HD only: pass the stream through or transcode to H.264 "
                             "(h264_1080p scales down to 1080 lines)")
    parser.add_argument("--log-level", default=os.environ.get("ICATCH_LOG_LEVEL", "info"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _die_with_parent(signal.SIGTERM)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
