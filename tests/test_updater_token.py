#!/usr/bin/env python3
"""
Testes do token do GitHub no download de assets (Issue #19): URL de API do asset com token,
token restrito aos hosts do GitHub (comparando hostname) e a mesma regra na busca do checksum.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from central_nvr.core.updater import (
    ReleaseAsset,
    ReleaseInfo,
    _parse_github_release_dict,
    download_asset,
    get_expected_sha256_for_asset,
    github_auth_headers,
    preferred_download_url,
)

TOKEN = "ghp_test_token"
API_URL = "https://api.github.com/repos/Othayz/central-nvr-wifi/releases/assets/123"
BROWSER_URL = "https://github.com/Othayz/central-nvr-wifi/releases/download/v1.3.0/Central_NVR_WiFi-1.3.0-x86_64.AppImage"
SHA = "a" * 64


def _ok_stream(content=b"conteudo"):
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-length": str(len(content))}
    resp.iter_content.return_value = [content]
    resp.raise_for_status.return_value = None
    return resp


def _asset(name="Central_NVR_WiFi-1.3.0-x86_64.AppImage"):
    return ReleaseAsset(name=name, size=8, download_url=BROWSER_URL, api_url=API_URL)


class AuthHeadersTest(unittest.TestCase):
    def test_api_asset_url_gets_token_and_octet_stream(self):
        headers = github_auth_headers(API_URL, TOKEN)
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(headers["Accept"], "application/octet-stream")

    def test_other_host_with_github_in_path_gets_no_token(self):
        for url in ("https://exemplo.com/api.github.com/x",
                    "https://api.github.com.evil.example/x",
                    "https://evil.example/?h=github.com"):
            self.assertEqual(github_auth_headers(url, TOKEN), {}, url)

    def test_no_token_no_headers(self):
        self.assertEqual(github_auth_headers(API_URL, None), {})


class ParseAndPreferTest(unittest.TestCase):
    def test_release_parse_keeps_asset_api_url(self):
        info = _parse_github_release_dict({
            "tag_name": "v1.3.0", "name": "v1.3.0",
            "assets": [{"name": "a.AppImage", "size": 1, "browser_download_url": BROWSER_URL, "url": API_URL}],
        })
        self.assertEqual(info.assets[0].api_url, API_URL)

    def test_preferred_url_uses_api_with_token(self):
        self.assertEqual(preferred_download_url(_asset(), TOKEN), API_URL)
        self.assertEqual(preferred_download_url(_asset(), None), BROWSER_URL)

    def test_preferred_url_ignores_api_url_on_other_host(self):
        asset = ReleaseAsset(name="a", size=1, download_url=BROWSER_URL, api_url="https://evil.example/assets/1")
        self.assertEqual(preferred_download_url(asset, TOKEN), BROWSER_URL)


class DownloadTest(unittest.TestCase):
    def test_download_with_token_uses_api_url_and_authorization(self):
        with tempfile.TemporaryDirectory() as tmp, patch("requests.get", return_value=_ok_stream()) as get:
            url = preferred_download_url(_asset(), TOKEN)
            self.assertTrue(download_asset(url, os.path.join(tmp, "app.AppImage"), token=TOKEN))
            called_url = get.call_args[0][0]
            headers = get.call_args[1]["headers"]
            self.assertEqual(called_url, API_URL)
            self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
            self.assertEqual(headers["Accept"], "application/octet-stream")

    def test_download_from_other_host_does_not_send_token(self):
        with tempfile.TemporaryDirectory() as tmp, patch("requests.get", return_value=_ok_stream()) as get:
            download_asset("https://exemplo.com/api.github.com/x", os.path.join(tmp, "f"), token=TOKEN)
            self.assertNotIn("Authorization", get.call_args[1]["headers"])


class ChecksumTokenTest(unittest.TestCase):
    def _release(self, sha_asset):
        return ReleaseInfo(tag_name="v1.3.0", version="1.3.0", title="", body="", html_url="",
                           published_at="", assets=[_asset(), sha_asset])

    def test_checksum_uses_api_url_with_token(self):
        sha_asset = ReleaseAsset(name="Central_NVR_WiFi-1.3.0-x86_64.AppImage.sha256", size=80,
                                 download_url=BROWSER_URL + ".sha256",
                                 api_url="https://api.github.com/repos/Othayz/central-nvr-wifi/releases/assets/124")
        resp = MagicMock(status_code=200, text=f"{SHA}  Central_NVR_WiFi-1.3.0-x86_64.AppImage")
        with patch("requests.get", return_value=resp) as get:
            h = get_expected_sha256_for_asset(self._release(sha_asset), _asset().name, token=TOKEN)
        self.assertEqual(h, SHA)
        self.assertEqual(get.call_args[0][0], sha_asset.api_url)
        self.assertEqual(get.call_args[1]["headers"]["Authorization"], f"Bearer {TOKEN}")

    def test_checksum_on_other_host_gets_no_token(self):
        sha_asset = ReleaseAsset(name="sha256sums.txt", size=80, download_url="https://mirror.example/sha256sums.txt")
        resp = MagicMock(status_code=200, text=f"{SHA}  Central_NVR_WiFi-1.3.0-x86_64.AppImage")
        with patch("requests.get", return_value=resp) as get:
            get_expected_sha256_for_asset(self._release(sha_asset), _asset().name, token=TOKEN)
        self.assertNotIn("Authorization", get.call_args[1]["headers"])


if __name__ == "__main__":
    unittest.main()
