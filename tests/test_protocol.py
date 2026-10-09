"""Tests for the iCatch protocol parser and the stream bridge.

Run from the repository root:  python3 -m unittest discover -s tests -v

Set ICATCH_CAPTURE_SD / ICATCH_CAPTURE_HD to raw captures of a real DVR
(``curl -o``) to also run the tests against real data; they are skipped otherwise.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, "..", "icatch_dvr", "app")
sys.path.insert(0, APP)
sys.path.insert(0, HERE)

from fake_dvr import FakeDVR, build_message, load_capture, multipart_part, synthetic_frames  # noqa: E402
from icatch_protocol import (  # noqa: E402
    AuthError,
    DVRStream,
    Frame,
    StreamParser,
    channel_mask,
    parse_message,
    stream_path,
)

H264_I = b"\x00\x00\x00\x01\x67\x64\x00\x1e" + b"\xaa" * 37  # odd length -> padding
H264_P = b"\x00\x00\x00\x01\x41\xe0" + b"\xbb" * 10
AUDIO = b"\x55" * 64


def frame(typ, ch, payload, ts=0):
    return Frame(typ, ch, 640, 368, 12, ts, 1791561596, payload)


class ProtocolTest(unittest.TestCase):
    def test_mask_and_path(self):
        self.assertEqual(channel_mask([1]), 1)
        self.assertEqual(channel_mask([1, 3, 5]), 0b10101)
        with self.assertRaises(ValueError):
            channel_mask([0])
        path = stream_path(1, hq=True)
        self.assertTrue(path.startswith("/cgi-bin/net_video.cgi?hq=1&iframe=1&pframe=1"))
        self.assertIn("complete=0&beg=-1&end=-1&ivs=0", path)

    def test_parse_message_multiple_subchunks(self):
        msg = build_message([frame(0, 4, H264_I, 10), frame(2, 4, AUDIO, 11), frame(1, 1, H264_P, 12)])
        frames = parse_message(msg)
        self.assertEqual([(f.type, f.channel) for f in frames], [(0, 4), (2, 4), (1, 1)])
        self.assertEqual(frames[0].payload, H264_I)  # padding stripped
        self.assertEqual(frames[2].payload, H264_P)
        self.assertTrue(frames[0].is_keyframe and frames[0].codec == "h264")
        self.assertFalse(frames[1].is_video)
        self.assertEqual(frames[0].unix_time, 1791561596)

    def test_hevc_types(self):
        f = parse_message(build_message([frame(11, 0, b"\x00\x00\x00\x01\x40\x01")]))[0]
        self.assertEqual((f.codec, f.is_keyframe), ("hevc", True))
        f = parse_message(build_message([frame(12, 0, b"\x00\x00\x00\x01\x02\x01")]))[0]
        self.assertEqual((f.codec, f.is_keyframe), ("hevc", False))

    def test_bad_messages(self):
        with self.assertRaises(ValueError):
            parse_message(b"\0" * 400)
        good = build_message([frame(0, 0, H264_I)])
        with self.assertRaises(ValueError):
            parse_message(good[:-5])  # truncated payload

    def test_stream_parser_any_chunking(self):
        msgs = [build_message([frame(i % 2, i % 3, H264_I if i % 2 == 0 else H264_P, i)])
                for i in range(50)]
        data = b"".join(multipart_part(m) for m in msgs)
        rng = random.Random(1)
        for _ in range(20):
            parser = StreamParser()
            out, pos = [], 0
            while pos < len(data):
                n = rng.randint(1, 700)
                out += parser.feed(data[pos:pos + n])
                pos += n
            self.assertEqual([f.timestamp_us for f in out], list(range(50)))
            self.assertEqual(parser.resyncs, 0)

    def test_stream_parser_resyncs_after_garbage(self):
        good = multipart_part(build_message([frame(0, 0, H264_I, 1)]))
        corrupt = bytearray(multipart_part(build_message([frame(0, 0, H264_I, 2)])))
        body = corrupt.index(b"\r\n\r\n") + 4
        size_field = body + 0x120 + 0x24
        corrupt[size_field:size_field + 4] = (10**6).to_bytes(4, "little")  # payload past end
        parser = StreamParser()
        out = parser.feed(b"junk" + good + bytes(corrupt) + good)
        self.assertEqual([f.timestamp_us for f in out], [1, 1])
        self.assertGreaterEqual(parser.resyncs, 1)


@unittest.skipUnless(os.environ.get("ICATCH_CAPTURE_SD"), "no real capture provided")
class RealCaptureTest(unittest.TestCase):
    def test_sd_capture(self):
        frames = load_capture(os.environ["ICATCH_CAPTURE_SD"])
        channels = {f.channel for f in frames}
        self.assertTrue(frames)
        self.assertTrue(all(f.codec == "h264" for f in frames))
        self.assertGreaterEqual(len(channels), 1)

    @unittest.skipUnless(os.environ.get("ICATCH_CAPTURE_HD"), "no HD capture")
    def test_hd_capture_decodes(self):
        frames = load_capture(os.environ["ICATCH_CAPTURE_HD"])
        codec = frames[0].codec
        with tempfile.NamedTemporaryFile(suffix=".bin") as tmp:
            for f in frames:
                tmp.write(f.payload)
            tmp.flush()
            res = subprocess.run(["ffmpeg", "-v", "error", "-f", codec, "-i", tmp.name, "-f", "null", "-"],
                                 capture_output=True, text=True)
        self.assertEqual(res.stderr.strip(), "")


class BridgeTest(unittest.TestCase):
    """Fake DVR -> DVRStream / icatch_stream.py -> ffmpeg."""

    @classmethod
    def setUpClass(cls):
        sd_cap, hd_cap = os.environ.get("ICATCH_CAPTURE_SD"), os.environ.get("ICATCH_CAPTURE_HD")
        sd = load_capture(sd_cap) if sd_cap else synthetic_frames("h264", 640, 368, 12, [0, 1, 2])
        hd = load_capture(hd_cap) if hd_cap else synthetic_frames("hevc", 1280, 960, 12, [0])
        cls.dvr = FakeDVR(sd, hd, "admin", "secret")
        cls.server = cls.dvr.serve()
        cls.port = cls.server.server_address[1]
        cls.env = dict(os.environ, ICATCH_HOST="127.0.0.1", ICATCH_PORT=str(cls.port),
                       ICATCH_USERNAME="admin", ICATCH_PASSWORD="secret", ICATCH_LOG_LEVEL="info",
                       ICATCH_ALLOW_FILE_OUTPUT="1")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_wrong_password(self):
        with self.assertRaises(AuthError):
            DVRStream("127.0.0.1", self.port, "admin", "nope", [1], hq=False).open()

    def test_channel_filter(self):
        with DVRStream("127.0.0.1", self.port, "admin", "secret", [2], hq=False) as s:
            got = []
            for f in s:
                got.append(f)
                if len(got) >= 10:
                    break
        self.assertEqual({f.channel for f in got}, {1})

    def _bridge_to_file(self, quality, video="copy", seconds=6):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.ts")
            proc = subprocess.Popen(
                [sys.executable, os.path.join(APP, "icatch_stream.py"), "--channel", "1",
                 "--quality", quality, "--video", video, "--output", out],
                env=self.env, stderr=subprocess.PIPE, text=True)
            time.sleep(seconds)
            proc.terminate()
            _, err = proc.communicate(timeout=10)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height",
                 "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1", out],
                capture_output=True, text=True)
            return proc.returncode, err, probe.stdout

    def test_bridge_sd_copy(self):
        code, err, info = self._bridge_to_file("sd")
        self.assertEqual(code, 0, err)
        self.assertIn("codec_name=h264", info)
        frames = int(info.split("nb_read_frames=")[1].split()[0])
        self.assertGreater(frames, 20, info)

    def test_bridge_hd_copy(self):
        code, err, info = self._bridge_to_file("hd")
        self.assertEqual(code, 0, err)
        self.assertIn("codec_name=hevc", info)

    def test_bridge_hd_transcode(self):
        code, err, info = self._bridge_to_file("hd", video="h264")
        self.assertEqual(code, 0, err)
        self.assertIn("codec_name=h264", info)

    def test_bridge_hd_transcode_1080p(self):
        code, err, info = self._bridge_to_file("hd", video="h264_1080p")
        self.assertEqual(code, 0, err)
        self.assertIn("codec_name=h264", info)
        height = int(info.split("height=")[1].split()[0])
        self.assertLessEqual(height, 1080, info)

    def _sigterm_exit_time(self, channel, quality, wait):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.Popen(
                [sys.executable, os.path.join(APP, "icatch_stream.py"), "--channel", str(channel),
                 "--quality", quality, "--output", os.path.join(tmp, "o.ts")],
                env=self.env, stderr=subprocess.PIPE, text=True)
            time.sleep(wait)
            start = time.monotonic()
            proc.terminate()
            _, err = proc.communicate(timeout=15)
        return proc.returncode, time.monotonic() - start, err

    def test_sigterm_while_dvr_is_silent(self):
        """A channel without video: the bridge blocks in recv and must still stop at once."""
        code, elapsed, err = self._sigterm_exit_time(9, "sd", 1.5)
        self.assertEqual(code, 0, err)
        self.assertLess(elapsed, 2.0, err)
        self.assertNotIn("Traceback", err)

    def test_sigterm_mid_stream(self):
        for _ in range(3):
            code, elapsed, err = self._sigterm_exit_time(1, "hd", 2.3)
            self.assertEqual(code, 0, err)
            self.assertLess(elapsed, 3.0, err)
            self.assertNotIn("Traceback", err)

    def test_no_orphan_processes_when_killed(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.Popen(
                [sys.executable, os.path.join(APP, "icatch_stream.py"), "--channel", "1",
                 "--quality", "sd", "--output", os.path.join(tmp, "o.ts")],
                env=self.env, stderr=subprocess.DEVNULL)
            time.sleep(4)
            children = subprocess.run(["pgrep", "-P", str(proc.pid)],
                                      capture_output=True, text=True).stdout.split()
            self.assertTrue(children, "ffmpeg should be running")
            proc.kill()  # what go2rtc does after killtimeout
            proc.wait()
            time.sleep(1)
            for pid in children:
                # gone, or a zombie waiting for PID 1 to reap it (s6 does in the add-on)
                self.assertIn(_proc_state(pid), ("gone", "Z"), "ffmpeg survived its parent")


def _proc_state(pid: str) -> str:
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("State:"):
                    return line.split()[1]
    except FileNotFoundError:
        pass
    return "gone"


if __name__ == "__main__":
    unittest.main()
