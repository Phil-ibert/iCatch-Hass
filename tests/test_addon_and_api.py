"""Tests for the add-on go2rtc config generator and the integration's API client.

The integration needs Home Assistant and aiohttp, which are not required to run
these tests: a tiny aiohttp stand-in is injected so the pure logic of api.py
(stream list parsing, error mapping, add-on host discovery) can be exercised.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "icatch_dvr", "app"))

import gen_go2rtc  # noqa: E402


CREDS = {"username": "homeassistant", "password": "s3cret"}


class GenGo2rtcTest(unittest.TestCase):
    def test_streams_per_camera(self):
        cfg = gen_go2rtc.build({"cameras": [3, 1, 2, 1], "hd_video": "h264_1080p", "log_level": "warning"}, CREDS)
        self.assertEqual(list(cfg["streams"]), ["cam1_sd", "cam1_hd", "cam2_sd", "cam2_hd", "cam3_sd", "cam3_hd"])
        sd, hd = cfg["streams"]["cam1_sd"], cfg["streams"]["cam1_hd"]
        self.assertTrue(sd.startswith("exec:/opt/icatch/icatch_stream.py --channel 1 --quality sd"))
        self.assertTrue(sd.endswith("--output {output}#killsignal=15#killtimeout=5"))
        self.assertIn("--quality hd --video h264_1080p", hd)
        self.assertEqual(cfg["exec"], {"allow_paths": ["/opt/icatch/icatch_stream.py"]})
        self.assertEqual(cfg["echo"], {"allow_paths": ["/bin/false"]})
        self.assertEqual(cfg["webrtc"]["listen"], "")
        self.assertEqual(cfg["log"]["level"], "warn")
        self.assertNotIn("s3cret", json.dumps(cfg["streams"]))

    def test_api_and_rtsp_always_authenticated(self):
        cfg = gen_go2rtc.build({"cameras": [1]}, CREDS)
        self.assertEqual(cfg["api"], {"listen": ":1986", "username": "homeassistant", "password": "s3cret"})
        self.assertEqual(cfg["rtsp"], {"listen": ":8586", "username": "homeassistant", "password": "s3cret"})

    def test_unknown_hd_mode_falls_back_to_copy(self):
        cfg = gen_go2rtc.build({"cameras": [1], "hd_video": "h264_vaapi"}, CREDS)
        self.assertIn("--video copy", cfg["streams"]["cam1_hd"])

    def test_credentials_generated_once_and_private(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "credentials.json")
            first = gen_go2rtc.credentials({}, path)
            self.assertEqual(first["username"], "homeassistant")
            self.assertGreaterEqual(len(first["password"]), 24)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(gen_go2rtc.credentials({}, path), first)
            self.assertEqual(gen_go2rtc.credentials({"api_password": "mine"}, path),
                             {"username": "admin", "password": "mine"})

    def test_discovery_message(self):
        self.assertEqual(gen_go2rtc.discovery(CREDS, host="abcd1234-icatch-dvr"), {
            "host": "abcd1234-icatch-dvr", "port": 1986, "rtsp_port": 8586,
            "username": "homeassistant", "password": "s3cret"})

    def test_main_writes_private_files(self):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            opts = os.path.join(tmp, "options.json")
            with open(opts, "w", encoding="utf-8") as fh:
                json.dump({"cameras": [1, 2], "hd_video": "copy"}, fh)
            out = [os.path.join(tmp, n) for n in ("credentials.json", "go2rtc.json", "discovery.json")]
            res = subprocess.run([sys.executable, os.path.join(ROOT, "icatch_dvr", "app", "gen_go2rtc.py"),
                                  opts, *out], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            for path in out:
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600, path)
            with open(out[1], encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue(text.startswith("{"))  # go2rtc treats it as inline config
            with open(out[2], encoding="utf-8") as fh:
                disc = json.load(fh)
            with open(out[0], encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["password"], disc["password"])

    def test_stream_bin_is_executable_script(self):
        path = os.path.join(ROOT, "icatch_dvr", "app", "icatch_stream.py")
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.readline().strip(), "#!/usr/bin/env python3")

    def test_file_output_refused_outside_tests(self):
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "ICATCH_ALLOW_FILE_OUTPUT"}
        env.update(ICATCH_HOST="127.0.0.1")
        res = subprocess.run([sys.executable, os.path.join(ROOT, "icatch_dvr", "app", "icatch_stream.py"),
                              "--channel", "1", "--quality", "sd", "--output", "/tmp/x.ts"],
                             env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(res.returncode, 2)
        self.assertIn("rtsp://", res.stderr)

    def test_no_camera_is_an_error(self):
        with self.assertRaises(SystemExit):
            gen_go2rtc.build({"cameras": []}, CREDS)

    def test_addon_defaults_match_generator(self):
        """config.yaml defaults must produce a valid go2rtc config."""
        with open(os.path.join(ROOT, "icatch_dvr", "config.yaml"), encoding="utf-8") as fh:
            text = fh.read()
        options = text.split("\noptions:\n")[1].split("\nschema:\n")[0]
        schema = text.split("\nschema:\n")[1]
        cams = [int(line.strip()[2:]) for line in options.splitlines() if line.strip().startswith("- ")]
        self.assertEqual(cams, [1, 2, 3, 4, 5])
        self.assertIn("hd_video: copy", options)
        modes = schema.split("hd_video: list(")[1].split(")")[0].split("|")
        self.assertEqual(tuple(modes), gen_go2rtc.HD_MODES)
        self.assertIn("discovery:\n  - icatch_dvr", text)
        gen_go2rtc.build({"cameras": cams, "hd_video": "copy"}, CREDS)


# --- minimal aiohttp stand-in -------------------------------------------------

def _install_fake_aiohttp():
    mod = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientResponseError(ClientError):
        pass

    class BasicAuth(tuple):
        def __new__(cls, login, password=""):
            return super().__new__(cls, (login, password))

    class ClientTimeout:
        def __init__(self, total=None):
            self.total = total

    mod.ClientError, mod.ClientResponseError = ClientError, ClientResponseError
    mod.BasicAuth, mod.ClientTimeout, mod.ClientSession = BasicAuth, ClientTimeout, object
    sys.modules["aiohttp"] = mod
    return mod


aiohttp = _install_fake_aiohttp()


class FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(self.status)

    async def read(self):
        return self._body


class FakeSession:
    """Routes 'http://host:port/path' to canned answers; unknown hosts fail."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, auth=None, timeout=None):
        self.calls.append((url, params, auth))
        host = url.split("//")[1].split(":")[0]
        if host not in self.routes:
            raise aiohttp.ClientError(f"cannot connect to {host}")
        status, body = self.routes[host]
        return FakeResponse(status, body)


def _load_api():
    pkg = types.ModuleType("icatch_dvr_pkg")
    pkg.__path__ = [os.path.join(ROOT, "custom_components", "icatch_dvr")]
    sys.modules["icatch_dvr_pkg"] = pkg
    for name in ("const", "api"):
        spec = importlib.util.spec_from_file_location(
            f"icatch_dvr_pkg.{name}", os.path.join(pkg.__path__[0], f"{name}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["icatch_dvr_pkg.api"]


api = _load_api()
STREAMS = json.dumps({
    "cam1_sd": {"producers": [], "consumers": []}, "cam1_hd": {},
    "cam5_sd": {}, "cam5_hd": {}, "cam12_sd": {}, "other": {}, "cam3_hd": {},
}).encode()


class ApiTest(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_cameras_parsed_from_streams(self):
        session = FakeSession({"h": (200, STREAMS)})
        client = api.Go2rtcApi(session, "h", 1986, 8586)
        self.assertEqual(self.run_async(client.cameras()), [1, 5, 12])
        self.assertEqual(client.rtsp_url("cam5_hd"), "rtsp://h:8586/cam5_hd")

    def test_rtsp_url_carries_quoted_credentials(self):
        client = api.Go2rtcApi(FakeSession({}), "h", 1986, 8586, "homeassistant", "a/b:c@d")
        self.assertEqual(client.rtsp_url("cam1_sd"), "rtsp://homeassistant:a%2Fb%3Ac%40d@h:8586/cam1_sd")

    def test_errors(self):
        client = api.Go2rtcApi(FakeSession({"h": (401, b"")}), "h", 1986, 8586, "u", "p")
        with self.assertRaises(api.InvalidAuth):
            self.run_async(client.cameras())
        client = api.Go2rtcApi(FakeSession({}), "h", 1986, 8586)
        with self.assertRaises(api.CannotConnect):
            self.run_async(client.cameras())
        client = api.Go2rtcApi(FakeSession({"h": (200, b"<html>")}), "h", 1986, 8586)
        with self.assertRaises(api.CannotConnect):
            self.run_async(client.cameras())

    def test_snapshot_uses_src_param_and_auth(self):
        session = FakeSession({"h": (200, b"\xff\xd8jpeg")})
        client = api.Go2rtcApi(session, "h", 1986, 8586, "u", "p")
        self.assertEqual(self.run_async(client.snapshot("cam2_sd")), b"\xff\xd8jpeg")
        url, params, auth = session.calls[-1]
        self.assertEqual(url, "http://h:1986/api/frame.jpeg")
        self.assertEqual(params, {"src": "cam2_sd"})
        self.assertEqual(tuple(auth), ("u", "p"))

    def test_addon_host_candidates(self):
        digest = hashlib.sha1(b"https://github.com/phil-ibert/icatch-hass").hexdigest()[:8]
        hosts = api.addon_host_candidates()
        self.assertEqual(hosts[0], f"{digest}-icatch-dvr")
        self.assertIn("local-icatch-dvr", hosts)
        self.assertNotIn("127.0.0.1", hosts)

    def test_find_addon_prefers_repository_host(self):
        hosts = api.addon_host_candidates()
        session = FakeSession({hosts[1]: (200, STREAMS), "local-icatch-dvr": (200, STREAMS)})
        self.assertEqual(self.run_async(api.find_addon(session)), hosts[1])
        self.assertIsNone(self.run_async(api.find_addon(FakeSession({}))))
        protected = FakeSession({"local-icatch-dvr": (401, b"")})
        self.assertEqual(self.run_async(api.find_addon(protected)), "local-icatch-dvr")


if __name__ == "__main__":
    unittest.main()
