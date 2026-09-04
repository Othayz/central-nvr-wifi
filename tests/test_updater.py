#!/usr/bin/env python3
"""
Testes unitários para o módulo de atualizações via GitHub (central_nvr.core.updater).
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

app = QApplication.instance()
if app is None:
    app = QApplication([])

from central_nvr.core.updater import (
    fetch_latest_release,
    AssetDownloadWorker,
    ReleaseAsset,
    ReleaseInfo,
    UpdateCheckWorker,
    _parse_github_release_dict,
    detect_system_package_format,
    download_asset,
    find_best_asset_for_system,
    is_version_newer,
    parse_version,
)
from central_nvr.ui.update_dialog import _format_markdown_to_html as ui_format_md


class TestUpdaterCore(unittest.TestCase):
    """Testes de lógica de versão e comunicação com GitHub."""

    def test_parse_version(self):
        self.assertEqual(parse_version("1.0.0")[:3], (1, 0, 0))
        self.assertEqual(parse_version("v1.2.3")[:3], (1, 2, 3))
        self.assertEqual(parse_version("2.1")[:2], (2, 1))
        self.assertEqual(parse_version("v3.0.0-rc1")[:3], (3, 0, 0))
        self.assertEqual(parse_version("")[:3], (0, 0, 0))

    def test_is_version_newer(self):
        self.assertTrue(is_version_newer("1.0.0", "1.0.1"))
        self.assertTrue(is_version_newer("1.0.0", "1.1.0"))
        self.assertTrue(is_version_newer("1.0.0", "2.0.0"))
        self.assertTrue(is_version_newer("v1.0.0", "v1.0.1"))
        self.assertTrue(is_version_newer("1.2.0", "1.10.0"))

        # Casos que não devem ser considerados mais novos
        self.assertFalse(is_version_newer("1.0.0", "1.0.0"))
        self.assertFalse(is_version_newer("1.1.0", "1.0.9"))
        self.assertFalse(is_version_newer("2.0.0", "1.9.9"))
        self.assertFalse(is_version_newer("v1.0.0", "v1.0.0"))

    def test_release_asset_formatting(self):
        asset_small = ReleaseAsset(name="pkg.deb", size=500, download_url="http://example.com/pkg.deb")
        self.assertEqual(asset_small.formatted_size, "500 B")

        asset_kb = ReleaseAsset(name="pkg.deb", size=1024 * 50, download_url="http://example.com/pkg.deb")
        self.assertEqual(asset_kb.formatted_size, "50.0 KB")

        asset_mb = ReleaseAsset(name="pkg.deb", size=1024 * 1024 * 4, download_url="http://example.com/pkg.deb")
        self.assertEqual(asset_mb.formatted_size, "4.0 MB")

    def test_find_best_asset_for_system(self):
        assets = [
            ReleaseAsset(name="central-nvr-1.1.0.tar.gz", size=1000, download_url="http://a.com/tar"),
            ReleaseAsset(name="central-nvr_1.1.0_all.deb", size=2000, download_url="http://a.com/deb"),
            ReleaseAsset(name="central-nvr-1.1.0.noarch.rpm", size=2500, download_url="http://a.com/rpm"),
        ]

        with patch("central_nvr.core.updater.detect_system_package_format", return_value="deb"):
            best = find_best_asset_for_system(assets)
            self.assertIsNotNone(best)
            self.assertTrue(best.name.endswith(".deb"))

        with patch("central_nvr.core.updater.detect_system_package_format", return_value="rpm"):
            best = find_best_asset_for_system(assets)
            self.assertIsNotNone(best)
            self.assertTrue(best.name.endswith(".rpm"))

    def test_parse_github_release_dict(self):
        mock_payload = {
            "tag_name": "v9.9.9",
            "name": "Central NVR WiFi v9.9.9",
            "body": "## O que há de novo\n- Suporte a H.265\n- Otimização de latência",
            "html_url": "https://github.com/Othayz/central-nvr-wifi/releases/tag/v9.9.9",
            "published_at": "2026-08-30T18:00:00Z",
            "prerelease": False,
            "assets": [
                {
                    "name": "central-nvr_9.9.9_all.deb",
                    "size": 4500000,
                    "browser_download_url": "https://github.com/Othayz/central-nvr-wifi/releases/download/v9.9.9/central-nvr_9.9.9_all.deb",
                    "content_type": "application/vnd.debian.binary-package",
                }
            ],
        }

        info = _parse_github_release_dict(mock_payload)
        self.assertEqual(info.version, "9.9.9")
        self.assertEqual(info.tag_name, "v9.9.9")
        self.assertTrue(info.is_newer)
        self.assertEqual(len(info.assets), 1)
        self.assertEqual(info.assets[0].name, "central-nvr_9.9.9_all.deb")
        self.assertIn("30/08/2026", info.formatted_date)

    def test_markdown_formatting(self):
        md = "## Novidades\n- Item 1\n- Item 2\n> Nota importante"
        html_out = ui_format_md(md)
        self.assertIn("<h3", html_out)
        self.assertIn("Novidades", html_out)
        self.assertIn("<li", html_out)
        self.assertIn("Item 1", html_out)
        self.assertIn("<blockquote", html_out)

    def test_download_asset_mocked(self):
        mock_response = MagicMock()
        mock_response.headers = {"content-length": "11"}
        mock_response.iter_content.return_value = [b"Central ", b"NVR"]
        mock_response.raise_for_status = MagicMock()

        progress_calls = []
        def on_progress(dl, tot, spd):
            progress_calls.append((dl, tot))

        with tempfile.TemporaryDirectory() as tmpdir:
            dest = os.path.join(tmpdir, "test_pkg.deb")
            with patch("requests.get", return_value=mock_response):
                success = download_asset("http://example.com/test.deb", dest, progress_callback=on_progress)
                self.assertTrue(success)
                self.assertTrue(os.path.exists(dest))
                with open(dest, "rb") as f:
                    self.assertEqual(f.read(), b"Central NVR")
                self.assertTrue(len(progress_calls) > 0)


class TestUpdaterWorkers(unittest.TestCase):
    """Testes de emissão de sinais dos workers assíncronos Qt."""

    def test_update_check_worker_signal_found(self):
        worker = UpdateCheckWorker(repo="Othayz/central-nvr-wifi")
        mock_info = ReleaseInfo(
            tag_name="v9.9.9",
            version="9.9.9",
            title="v9.9.9",
            body="Novidades",
            html_url="http://test.url",
            published_at="2026-08-30T18:00:00Z",
            is_newer=True,
        )

        results = []
        worker.update_available.connect(lambda rel: results.append(rel))

        with patch("central_nvr.core.updater.fetch_latest_release", return_value=mock_info):
            worker.run()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].version, "9.9.9")


    def test_fetch_latest_release_private_repo_raises_clear_error(self):
        """Garante que repositório privado retornando 404 lance erro explicativo."""
        mock_404 = MagicMock()
        mock_404.status_code = 404
        mock_404.text = "Not Found"

        with patch("requests.get", return_value=mock_404):
            with self.assertRaises(RuntimeError) as ctx:
                fetch_latest_release(repo="Othayz/central-nvr-wifi")
            self.assertIn("PRIVADO", str(ctx.exception))

    def test_fetch_latest_release_rate_limit_raises_clear_error(self):
        """Garante que rate-limit do GitHub emita mensagem orientadora."""
        mock_403 = MagicMock()
        mock_403.status_code = 403
        mock_403.text = "API rate limit exceeded"

        with patch("requests.get", return_value=mock_403):
            with self.assertRaises(RuntimeError) as ctx:
                fetch_latest_release(repo="Othayz/central-nvr-wifi")
            self.assertIn("Limite de requisições", str(ctx.exception))

    def test_fetch_latest_release_empty_releases_returns_up_to_date(self):
        """Quando o repositório é acessível mas não tem releases, reconhece como atualizado."""
        mock_404 = MagicMock()
        mock_404.status_code = 404

        mock_empty_list = MagicMock()
        mock_empty_list.status_code = 200
        mock_empty_list.json.return_value = []

        def side_effect(url, **kwargs):
            if "releases/latest" in url:
                return mock_404
            if "releases" in url:
                return mock_empty_list
            return mock_empty_list

        with patch("requests.get", side_effect=side_effect):
            info = fetch_latest_release(repo="Othayz/central-nvr-wifi")
            self.assertIsNotNone(info)
            self.assertFalse(info.is_newer)
            self.assertEqual(info.title, "Repositório Sincronizado")

    def test_fetch_latest_release_passes_token_header(self):
        """Garante que token seja enviado no header Authorization."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tag_name": "v1.0.0",
            "name": "v1.0.0",
            "body": "",
            "html_url": "",
            "published_at": "",
            "assets": []
        }

        with patch("requests.get", return_value=mock_resp) as mock_get:
            fetch_latest_release(repo="Othayz/central-nvr-wifi", token="ghp_test_token_12345")
            self.assertTrue(mock_get.called)
            headers = mock_get.call_args[1].get("headers", {})
            self.assertEqual(headers.get("Authorization"), "Bearer ghp_test_token_12345")

    def test_update_check_worker_signal_no_update(self):
        worker = UpdateCheckWorker(repo="Othayz/central-nvr-wifi")
        mock_info = ReleaseInfo(
            tag_name="v1.0.0",
            version="1.0.0",
            title="v1.0.0",
            body="Atual",
            html_url="http://test.url",
            published_at="2026-08-30T18:00:00Z",
            is_newer=False,
        )

        no_update_results = []
        worker.no_update_available.connect(lambda rel: no_update_results.append(rel))

        with patch("central_nvr.core.updater.fetch_latest_release", return_value=mock_info):
            worker.run()

        self.assertEqual(len(no_update_results), 1)


    def test_install_downloaded_package_safe_execution(self):
        from central_nvr.core.updater import install_downloaded_package
        with patch("os.path.exists", side_effect=lambda p: False if "ostree" in str(p) else True):
            with patch("shutil.which") as mock_which:
                mock_which.side_effect = lambda cmd: cmd in ("pkexec", "apt")
                with patch("subprocess.Popen") as mock_popen:
                    success, msg = install_downloaded_package("/tmp/central-nvr_1.0.0.deb")
                    self.assertTrue(success)
                    self.assertTrue(mock_popen.called)
                    call_args = mock_popen.call_args[0][0]
                    self.assertEqual(call_args[0], "pkexec")
                    self.assertEqual(call_args[1], "apt")
                    self.assertEqual(call_args[2], "install")
                    self.assertNotIn("bash", call_args)

    def test_install_downloaded_package_user_local(self):
        from central_nvr.core.updater import install_downloaded_package
        import tempfile
        from pathlib import Path
        deb_file = "dist/central-nvr_1.0.0_all.deb"
        if os.path.exists(deb_file):
            with tempfile.TemporaryDirectory() as tmpdir:
                with patch("pathlib.Path.home", return_value=Path(tmpdir)):
                    success, msg = install_downloaded_package(deb_file)
                    self.assertTrue(success)
                    self.assertTrue((Path(tmpdir) / ".local" / "bin" / "central-nvr").exists())

    def test_parse_release_version_title_tag_divergence(self):
        from central_nvr.core.updater import _parse_github_release_dict
        mock_data = {
            "tag_name": "Central_NVR_WiFi_v1.0.0",
            "name": "v1.1",
            "body": "Notas de lançamento v1.1",
            "html_url": "https://github.com/Othayz/central-nvr-wifi/releases/tag/Central_NVR_WiFi_v1.0.0",
            "published_at": "2026-08-30T21:48:05Z",
            "assets": [],
        }
        rel = _parse_github_release_dict(mock_data)
        self.assertEqual(rel.version, "1.1")
        self.assertTrue(rel.is_newer)

if __name__ == "__main__":
    unittest.main()
