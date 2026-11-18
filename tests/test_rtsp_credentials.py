#!/usr/bin/env python3
"""
Testes de credenciais em URLs RTSP (Issue #18): senhas com caracteres especiais, IPv6,
sub-stream configurado e mascaramento no log.
"""
import os
import sys
import unittest
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from central_nvr.core.camera import CameraDevice
from central_nvr.core.stream_worker import sanitize_rtsp_url

SPECIAL_PASSWORDS = ["pa/ss", "p@ss", "pa:ss", "pa#ss", "pa?ss", "pa%ss", "a/b@c:d#e?f%g"]


def _cam(password, **kw):
    params = dict(id="c1", name="Cam", ip="192.168.1.10", rtsp_port=554, rtsp_path="/onvif1",
                  username="admin", password=password, manufacturer="Yoosee")
    params.update(kw)
    return CameraDevice(**params)


class SpecialPasswordTest(unittest.TestCase):
    def _assert_url_ok(self, url, password, host="192.168.1.10"):
        parsed = urllib.parse.urlparse(url)
        self.assertEqual(parsed.hostname, host, url)
        self.assertEqual(parsed.port, 554, url)
        self.assertEqual(parsed.username, "admin", url)
        self.assertEqual(urllib.parse.unquote(parsed.password), password, url)

    def test_field_built_urls_keep_host(self):
        for pwd in SPECIAL_PASSWORDS:
            with self.subTest(pwd=pwd):
                cam = _cam(pwd)
                self._assert_url_ok(cam.get_full_rtsp_url(), pwd)
                self._assert_url_ok(cam.get_substream_url(), pwd)
                for url in cam.get_candidate_rtsp_urls(prefer_substream=True):
                    self._assert_url_ok(url, pwd)

    def test_explicit_urls_keep_host(self):
        for pwd in SPECIAL_PASSWORDS:
            with self.subTest(pwd=pwd):
                cam = _cam(pwd, rtsp_url="rtsp://192.168.1.10:554/main",
                           substream_url="rtsp://192.168.1.10:554/sub")
                self._assert_url_ok(cam.get_full_rtsp_url(), pwd)
                self._assert_url_ok(cam.get_substream_url(), pwd)

    def test_substream_url_receives_credentials(self):
        cam = _cam("s3cret", substream_url="rtsp://192.168.1.10:554/sub")
        self.assertEqual(cam.get_substream_url(), "rtsp://admin:s3cret@192.168.1.10:554/sub")

    def test_ipv6_keeps_brackets(self):
        cam = _cam("s3cret", rtsp_url="rtsp://[fe80::1]:554/live")
        url = cam.get_full_rtsp_url()
        self.assertEqual(url, "rtsp://admin:s3cret@[fe80::1]:554/live")
        self.assertEqual(urllib.parse.urlparse(url).hostname, "fe80::1")

        field_cam = _cam("s3cret", ip="fe80::2")
        self.assertEqual(urllib.parse.urlparse(field_cam.get_full_rtsp_url()).hostname, "fe80::2")

    def test_username_with_special_chars(self):
        cam = _cam("x", username="op@site")
        parsed = urllib.parse.urlparse(cam.get_full_rtsp_url())
        self.assertEqual(urllib.parse.unquote(parsed.username), "op@site")
        self.assertEqual(parsed.hostname, "192.168.1.10")


class SanitizeTest(unittest.TestCase):
    def test_masks_password_in_generated_urls(self):
        for pwd in SPECIAL_PASSWORDS:
            with self.subTest(pwd=pwd):
                url = _cam(pwd).get_full_rtsp_url()
                sanitized = sanitize_rtsp_url(url)
                self.assertNotIn(urllib.parse.quote(pwd, safe=""), sanitized)
                self.assertIn("admin:****@192.168.1.10:554/onvif1", sanitized)

    def test_masks_raw_unencoded_password(self):
        for pwd in SPECIAL_PASSWORDS:
            with self.subTest(pwd=pwd):
                raw = f"rtsp://admin:{pwd}@192.168.1.10:554/onvif1"
                sanitized = sanitize_rtsp_url(raw)
                self.assertNotIn(pwd, sanitized)
                self.assertTrue(sanitized.startswith("rtsp://admin:****@"))

    def test_url_without_credentials_unchanged(self):
        for url in ("rtsp://192.168.1.50:554/live", "rtsp://admin@192.168.1.50/live", ""):
            self.assertEqual(sanitize_rtsp_url(url), url)


if __name__ == "__main__":
    unittest.main()
