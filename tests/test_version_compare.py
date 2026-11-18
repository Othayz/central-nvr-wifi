#!/usr/bin/env python3
"""
Testes de comparação de versões e escolha da release (Issue #35).
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from central_nvr.core.updater import (
    _parse_github_release_dict,
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


if __name__ == "__main__":
    unittest.main()
