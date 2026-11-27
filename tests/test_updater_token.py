#!/usr/bin/env python3
"""
Testes do token do GitHub no download de assets (Issue #19): URL de API do asset com token,
token restrito aos hosts do GitHub (comparando hostname) e a mesma regra na busca do checksum.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import central_nvr.core.config as cfg_mod
import central_nvr.core.updater as updater_mod
from central_nvr.core.updater import (
    ReleaseAsset,
    ReleaseInfo,
    _etag_cache_key,
    _fetch_github_json_cached,
    _parse_github_release_dict,
    download_asset,
    fetch_latest_release,
    get_expected_sha256_for_asset,
    github_auth_headers,
    preferred_download_url,
    resolve_github_token,
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


class ResolveGithubTokenPrecedenceTest(unittest.TestCase):
    """Precedência de resolve_github_token: explícito -> GITHUB_TOKEN -> settings.json -> keyring."""

    def test_settings_json_token_takes_precedence_over_readable_keyring(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp)
            (cfg_dir / "settings.json").write_text(
                json.dumps({"github_token": "ghp_novo_arquivo"}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False), \
                    patch.object(cfg_mod, "get_config_dir", return_value=cfg_dir), \
                    patch.object(cfg_mod, "is_keyring_available", return_value=True), \
                    patch.object(cfg_mod, "get_keyring_pat", return_value="ghp_antigo_keyring"):
                self.assertEqual(resolve_github_token(), "ghp_novo_arquivo")

    def test_env_token_takes_precedence_over_settings_json_and_keyring(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp)
            (cfg_dir / "settings.json").write_text(
                json.dumps({"github_token": "ghp_novo_arquivo"}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_env"}, clear=False), \
                    patch.object(cfg_mod, "get_config_dir", return_value=cfg_dir), \
                    patch.object(cfg_mod, "is_keyring_available", return_value=True), \
                    patch.object(cfg_mod, "get_keyring_pat", return_value="ghp_antigo_keyring"):
                self.assertEqual(resolve_github_token(), "ghp_env")

    def test_falls_back_to_keyring_when_settings_json_has_no_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp)
            (cfg_dir / "settings.json").write_text(json.dumps({"github_token": ""}), encoding="utf-8")
            with patch.dict(os.environ, {"GITHUB_TOKEN": ""}, clear=False), \
                    patch.object(cfg_mod, "get_config_dir", return_value=cfg_dir), \
                    patch.object(cfg_mod, "is_keyring_available", return_value=True), \
                    patch.object(cfg_mod, "get_keyring_pat", return_value="ghp_do_keyring"):
                self.assertEqual(resolve_github_token(), "ghp_do_keyring")


class EtagCacheIsolationTest(unittest.TestCase):
    """Regressão da Issue #101: isolamento do _ETAG_CACHE por estado de autenticação e cópias defensivas."""

    LATEST_URL = "https://api.github.com/repos/o/r/releases/latest"

    def setUp(self):
        updater_mod._ETAG_CACHE.clear()

    def tearDown(self):
        updater_mod._ETAG_CACHE.clear()

    @staticmethod
    def _json_resp(status, payload=None, etag=None):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = payload
        resp.headers = {"ETag": etag} if etag is not None else {}
        resp.text = ""
        return resp

    def test_authenticated_cache_is_not_reused_by_anonymous_call_or_vice_versa(self):
        """Critério 1: resposta autenticada não vaza ETag/payload para chamada anônima (nem o inverso)."""
        private_payload = {"tag_name": "v2.0.0", "name": "Privada", "assets": []}
        public_payload = {"tag_name": "v1.5.0", "name": "Pública", "assets": []}
        seen = []

        def fake_get(url, headers=None, timeout=None):
            hdrs = dict(headers or {})
            seen.append(hdrs)
            if hdrs.get("Authorization") == "Bearer pat_alpha":
                return self._json_resp(200, private_payload, etag='"etag-priv"')
            return self._json_resp(200, public_payload, etag='"etag-pub"')

        with patch("requests.get", side_effect=fake_get), \
                patch.object(updater_mod, "resolve_github_token", side_effect=lambda t=None: t):
            auth_info = fetch_latest_release(repo="o/r", token="pat_alpha")
            anon_info = fetch_latest_release(repo="o/r", token=None)

        self.assertEqual(auth_info.tag_name, "v2.0.0")
        self.assertEqual(anon_info.tag_name, "v1.5.0")
        self.assertNotIn("If-None-Match", seen[0])
        self.assertNotIn("If-None-Match", seen[1])
        self.assertNotIn("Authorization", seen[1])

        # O inverso: cache populado como anônimo não envia ETag em chamada autenticada com outro token
        seen.clear()
        with patch("requests.get", side_effect=fake_get), \
                patch.object(updater_mod, "resolve_github_token", side_effect=lambda t=None: t):
            _fetch_github_json_cached(self.LATEST_URL, {"Authorization": "Bearer pat_beta"}, 5, "o/r")
        self.assertNotIn("If-None-Match", seen[0])

    def test_changing_token_does_not_reuse_previous_token_cache(self):
        """Critério 2: trocar de token A para token B não envia If-None-Match de A nem reaproveita seu payload."""
        seen = []

        def fake_get(url, headers=None, timeout=None):
            hdrs = dict(headers or {})
            seen.append(hdrs)
            auth = hdrs.get("Authorization")
            if auth == "Bearer pat_token_a":
                return self._json_resp(200, {"tag_name": "v2.1.0", "assets": []}, etag='"etag-a"')
            if auth == "Bearer pat_token_b":
                return self._json_resp(200, {"tag_name": "v2.2.0", "assets": []}, etag='"etag-b"')
            return self._json_resp(401)

        with patch("requests.get", side_effect=fake_get):
            res_a = _fetch_github_json_cached(self.LATEST_URL, {"Authorization": "Bearer pat_token_a"}, 5, "o/r")
            res_b = _fetch_github_json_cached(self.LATEST_URL, {"Authorization": "Bearer pat_token_b"}, 5, "o/r")

        self.assertEqual(res_a["tag_name"], "v2.1.0")
        self.assertEqual(res_b["tag_name"], "v2.2.0")
        self.assertNotIn("If-None-Match", seen[0])
        self.assertNotIn("If-None-Match", seen[1])
        self.assertEqual(len(updater_mod._ETAG_CACHE), 2)

    def test_same_url_and_same_token_reuses_cached_payload_on_304(self):
        """Critério 3: mesma URL e mesmo token (inclusive header em caixa diferente) reutilizam cache no 304."""
        seen = []
        responses = [
            self._json_resp(200, {"tag_name": "v2.3.0", "assets": [{"name": "a.AppImage"}]}, etag='"etag-same"'),
            self._json_resp(304),
            self._json_resp(304),
        ]

        def fake_get(url, headers=None, timeout=None):
            seen.append(dict(headers or {}))
            return responses.pop(0)

        with patch("requests.get", side_effect=fake_get):
            first = _fetch_github_json_cached(self.LATEST_URL, {"Authorization": "Bearer pat_same"}, 5, "o/r")
            second = _fetch_github_json_cached(self.LATEST_URL, {"Authorization": "Bearer pat_same"}, 5, "o/r")
            third = _fetch_github_json_cached(self.LATEST_URL, {"authorization": "Bearer pat_same"}, 5, "o/r")

        self.assertEqual(first, {"tag_name": "v2.3.0", "assets": [{"name": "a.AppImage"}]})
        self.assertEqual(second, first)
        self.assertEqual(third, first)
        self.assertNotIn("If-None-Match", seen[0])
        self.assertEqual(seen[1].get("If-None-Match"), '"etag-same"')
        self.assertEqual(seen[2].get("If-None-Match"), '"etag-same"')

    def test_mutating_returned_payload_does_not_corrupt_subsequent_304(self):
        """Critério 4: mutar dict ou lista retornado no 200 ou no 304 não altera respostas 304 seguintes."""
        source_payload = [{"tag_name": "v2.4.0", "assets": [{"name": "orig.AppImage"}]}]
        responses = [
            self._json_resp(200, source_payload, etag='"etag-mut"'),
            self._json_resp(304),
            self._json_resp(304),
        ]

        with patch("requests.get", side_effect=lambda *a, **kw: responses.pop(0)):
            first = _fetch_github_json_cached(self.LATEST_URL, {}, 5, "o/r")
            first[0]["tag_name"] = "v9.9.9-mutado"
            first[0]["assets"].append({"name": "injetado.AppImage"})
            first.append({"tag_name": "extra"})
            source_payload[0]["tag_name"] = "v0.0.0-externo"

            second = _fetch_github_json_cached(self.LATEST_URL, {}, 5, "o/r")
            self.assertEqual(second, [{"tag_name": "v2.4.0", "assets": [{"name": "orig.AppImage"}]}])

            second[0]["assets"][0]["name"] = "alterado-304.AppImage"
            third = _fetch_github_json_cached(self.LATEST_URL, {}, 5, "o/r")
            self.assertEqual(third, [{"tag_name": "v2.4.0", "assets": [{"name": "orig.AppImage"}]}])

    def test_cache_key_never_stores_raw_token(self):
        """Critério 5: chave de cache armazena apenas hash truncado (ou '' para anônimo), nunca o token em claro."""
        secret = "secret_marker_987654321"
        anon_key = _etag_cache_key(self.LATEST_URL, {})
        auth_key = _etag_cache_key(self.LATEST_URL, {"Authorization": f"Bearer {secret}"})

        self.assertEqual(anon_key, (self.LATEST_URL, ""))
        self.assertEqual(auth_key[0], self.LATEST_URL)
        self.assertEqual(len(auth_key[1]), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in auth_key[1]))
        self.assertNotIn(secret, auth_key[1])

        with patch("requests.get", return_value=self._json_resp(200, {"tag_name": "v2.5.0"}, etag='"etag-sec"')):
            _fetch_github_json_cached(self.LATEST_URL, {"Authorization": f"Bearer {secret}"}, 5, "o/r")

        for cached_key in updater_mod._ETAG_CACHE:
            self.assertNotIn(secret, str(cached_key))


if __name__ == "__main__":
    unittest.main()
