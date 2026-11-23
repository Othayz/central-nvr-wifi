#!/usr/bin/env python3
"""
Testes de comparação de versões e escolha da release (Issue #35).
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from central_nvr.core import updater
from central_nvr.core.updater import (
    _parse_github_release_dict,
    _pick_highest_release,
    fetch_latest_release,
    is_prerelease,
    is_version_newer,
    parse_release_version,
    version_key,
)


def _resp(status, payload=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = ""
    return r


class VersionCompareTest(unittest.TestCase):
    def test_prerelease_is_not_newer_than_final(self):
        self.assertFalse(is_version_newer("1.2.1", "1.2.1-rc2"))
        self.assertTrue(is_version_newer("1.2.1-rc2", "1.2.1"))

    def test_beta_is_older_than_final(self):
        self.assertLess(version_key("1.3.0-beta"), version_key("1.3.0"))
        self.assertTrue(is_version_newer("1.3.0-beta", "1.3.0"))

    def test_prerelease_ordering(self):
        ordered = ["1.3.0-alpha", "1.3.0-alpha.1", "1.3.0-beta", "1.3.0-beta.2", "1.3.0-rc2", "1.3.0-rc10", "1.3.0"]
        self.assertEqual(sorted(reversed(ordered), key=version_key), ordered)

    def test_prerelease_of_next_version_is_newer(self):
        self.assertTrue(is_version_newer("1.2.2", "1.3.0-rc1"))

    def test_pep440_style_suffix(self):
        self.assertTrue(is_prerelease("1.3.0rc1"))
        self.assertFalse(is_version_newer("1.3.0", "1.3.0rc1"))

    def test_build_metadata_ignored(self):
        self.assertFalse(is_version_newer("1.2.2", "1.2.2+build5"))

    def test_existing_numeric_comparisons(self):
        self.assertTrue(is_version_newer("1.2.0", "1.10.0"))
        self.assertFalse(is_version_newer("2.0", "2.0.0"))


class ReleaseVersionFromTagTest(unittest.TestCase):
    def test_title_does_not_influence_version(self):
        for title in ("Suporte ao Ubuntu 24.04 (v1.2.2)", "Release 2026.11 hotfix"):
            info = _parse_github_release_dict({"tag_name": "v1.2.2", "name": title, "assets": []})
            self.assertEqual(info.version, "1.2.2")

    def test_prefixed_tag(self):
        self.assertEqual(parse_release_version("Central_NVR_WiFi_v1.0.0"), "1.0.0")

    def test_prerelease_tag_kept(self):
        self.assertEqual(parse_release_version("v1.3.0-beta"), "1.3.0-beta")


class FetchSelectionTest(unittest.TestCase):
    def setUp(self):
        updater._ETAG_CACHE.clear()

    def tearDown(self):
        updater._ETAG_CACHE.clear()

    def test_tag_fallback_sorts_by_version_and_ignores_prereleases(self):
        tags = [{"name": "v1.2.10"}, {"name": "v1.3.0-rc1"}, {"name": "v1.2.9"}, {"name": "nightly"}]

        def get(url, headers=None, timeout=None):
            if url.endswith("/releases/latest") or url.endswith("/releases"):
                return _resp(404)
            return _resp(200, tags)

        with patch("requests.get", side_effect=get), patch("central_nvr.core.updater.__version__", "1.2.2"):
            info = fetch_latest_release(repo="o/r", token="t")
        self.assertEqual(info.tag_name, "v1.2.10")
        self.assertEqual(info.version, "1.2.10")
        self.assertTrue(info.is_newer)

    def test_include_prereleases_lists_releases(self):
        releases = [
            {"tag_name": "v1.2.2", "prerelease": False, "assets": []},
            {"tag_name": "v1.3.0-rc1", "prerelease": True, "assets": []},
            {"tag_name": "v1.4.0", "draft": True, "assets": []},
        ]
        calls = []

        def get(url, headers=None, timeout=None):
            calls.append(url)
            if url.endswith("/releases"):
                return _resp(200, releases)
            return _resp(200, releases[0])

        with patch("requests.get", side_effect=get), patch("central_nvr.core.updater.__version__", "1.2.2"):
            info = fetch_latest_release(repo="o/r", include_prereleases=True, token="t")
        self.assertEqual(info.version, "1.3.0-rc1")
        self.assertTrue(info.is_newer)
        self.assertTrue(calls[0].endswith("/releases"))

    def test_without_prereleases_uses_latest(self):
        with patch("requests.get", return_value=_resp(200, {"tag_name": "v1.2.2", "assets": []})) as get:
            fetch_latest_release(repo="o/r", token="t")
        self.assertTrue(get.call_args_list[0][0][0].endswith("/releases/latest"))

    def test_latest_prerelease_tag_without_api_flag_falls_back_to_stable(self):
        """Issue #73: RC publicada em /releases/latest sem prerelease=true não é oferecida a usuários estáveis."""
        rc = {"tag_name": "v1.4.0-rc1", "prerelease": False, "assets": []}
        stable = {"tag_name": "v1.3.2", "prerelease": False, "assets": []}

        def get(url, headers=None, timeout=None):
            if url.endswith("/releases/latest"):
                return _resp(200, rc)
            if url.endswith("/releases"):
                return _resp(200, [rc, stable])
            return _resp(404)

        with patch("requests.get", side_effect=get), patch("central_nvr.core.updater.__version__", "1.3.1"):
            info = fetch_latest_release(repo="o/r", include_prereleases=False, token="t")
        self.assertEqual(info.tag_name, "v1.3.2")
        self.assertEqual(info.version, "1.3.2")
        self.assertTrue(info.is_newer)
        self.assertFalse(info.prerelease)

    def test_latest_prerelease_tag_without_api_flag_returns_no_update_when_no_stable_exists(self):
        """Issue #73: se só existir a RC sem flag, retorna sem atualização em vez de oferecer a RC."""
        rc = {"tag_name": "v1.4.0-rc1", "prerelease": False, "assets": []}

        def get(url, headers=None, timeout=None):
            if url.endswith("/releases/latest"):
                return _resp(200, rc)
            if url.endswith("/releases"):
                return _resp(200, [rc])
            return _resp(404)

        with patch("requests.get", side_effect=get), patch("central_nvr.core.updater.__version__", "1.3.1"):
            info = fetch_latest_release(repo="o/r", include_prereleases=False, token="t")
        self.assertIsNotNone(info)
        self.assertFalse(info.is_newer)
        self.assertEqual(info.version, "1.3.1")

    def test_latest_prerelease_tag_without_api_flag_accepted_when_include_prereleases_true(self):
        """Issue #73: com include_prereleases=True, a RC sem flag continua sendo aceita."""
        rc = {"tag_name": "v1.4.0-rc1", "prerelease": False, "assets": []}
        stable = {"tag_name": "v1.3.2", "prerelease": False, "assets": []}

        def get(url, headers=None, timeout=None):
            if url.endswith("/releases"):
                return _resp(200, [rc, stable])
            return _resp(200, rc)

        with patch("requests.get", side_effect=get), patch("central_nvr.core.updater.__version__", "1.3.1"):
            info = fetch_latest_release(repo="o/r", include_prereleases=True, token="t")
        self.assertEqual(info.tag_name, "v1.4.0-rc1")
        self.assertEqual(info.version, "1.4.0-rc1")
        self.assertTrue(info.is_newer)
        self.assertTrue(info.prerelease)

    def test_cached_304_prerelease_without_api_flag_is_ignored_when_excluded(self):
        """Issue #73: resposta 304 em cache contendo RC sem flag também é filtrada."""
        rc = {"tag_name": "v1.4.0-rc1", "prerelease": False, "assets": []}
        stable = {"tag_name": "v1.3.2", "prerelease": False, "assets": []}
        latest_url = "https://api.github.com/repos/o/r/releases/latest"
        updater._ETAG_CACHE[latest_url] = ('"etag-rc1"', rc)

        def get(url, headers=None, timeout=None):
            if url.endswith("/releases/latest"):
                return _resp(304)
            if url.endswith("/releases"):
                return _resp(200, [rc, stable])
            return _resp(404)

        with patch("requests.get", side_effect=get), patch("central_nvr.core.updater.__version__", "1.3.1"):
            info = fetch_latest_release(repo="o/r", include_prereleases=False, token="t")
        self.assertEqual(info.tag_name, "v1.3.2")

    def test_pick_highest_release_ignores_prerelease_suffix_without_api_flag(self):
        releases = [
            {"tag_name": "v1.4.0-rc1", "prerelease": False, "assets": []},
            {"tag_name": "v1.3.2", "prerelease": False, "assets": []},
        ]
        self.assertEqual(_pick_highest_release(releases, include_prereleases=False)["tag_name"], "v1.3.2")
        self.assertEqual(_pick_highest_release(releases, include_prereleases=True)["tag_name"], "v1.4.0-rc1")


if __name__ == "__main__":
    unittest.main()
