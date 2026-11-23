#!/usr/bin/env python3
"""
Testes do instalador (Issue #38): fsync antes do os.replace, somente AppImage, destino somente
leitura com fallback para ~/.local/bin e escape do caminho no Exec da entrada .desktop.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from central_nvr.core import updater
from central_nvr.core.updater import compute_file_sha256, desktop_exec_path, install_downloaded_package


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.new = self.root / "Central_NVR_WiFi-9.9.9-x86_64.AppImage"
        self.new.write_bytes(b"nova versao")
        self.sha = compute_file_sha256(str(self.new))
        self.patchers = [patch.object(Path, "home", return_value=self.home)]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        self.tmp.cleanup()

    def test_staged_copy_is_fsynced_before_replace(self):
        events = []
        real_fsync, real_replace = os.fsync, os.replace
        with patch.object(updater.os, "fsync", side_effect=lambda fd: (events.append("fsync"), real_fsync(fd))), \
                patch.object(updater.os, "replace", side_effect=lambda a, b: (events.append("replace"), real_replace(a, b))), \
                patch.dict(os.environ, {"APPIMAGE": ""}):
            ok, msg = install_downloaded_package(str(self.new), expected_sha256=self.sha)
        self.assertTrue(ok, msg)
        self.assertIn("replace", events)
        self.assertIn("fsync", events[:events.index("replace")])
        self.assertEqual(events[-1], "fsync", "o diretório é sincronizado depois do rename")

    def test_only_appimage_is_accepted(self):
        tarball = self.root / "central-nvr-9.9.9.tar.gz"
        tarball.write_bytes(b"x")
        ok, msg = install_downloaded_package(str(tarball), expected_sha256=compute_file_sha256(str(tarball)))
        self.assertFalse(ok)
        self.assertIn("AppImage", msg)

    def test_removed_tarball_helpers(self):
        for name in ("_extract_tar_members_to_local", "install_package_to_user_local", "detect_system_package_format"):
            self.assertFalse(hasattr(updater, name), name)

    def _running_appimage(self):
        opt = self.root / "opt"
        opt.mkdir(exist_ok=True)
        current = opt / "central-nvr.AppImage"
        current.write_bytes(b"versao antiga")
        return current

    def _appimage_env(self, appimage_path: Path):
        """Prepara ambiente de execução a partir do AppDir correspondente ao AppImage."""
        appdir = self.root / "mount_central_nvr"
        appdir.mkdir(exist_ok=True)
        bundled_module = appdir / "usr" / "src" / "central_nvr" / "core" / "updater.py"
        return (
            patch.dict(os.environ, {"APPIMAGE": str(appimage_path), "APPDIR": str(appdir)}),
            patch.object(updater, "__file__", str(bundled_module)),
        )

    def test_read_only_destination_falls_back_to_user_bin(self):
        current = self._running_appimage()
        real_access = os.access
        env_patch, mod_patch = self._appimage_env(current)
        with env_patch, mod_patch, \
                patch.object(updater.os, "access",
                             side_effect=lambda p, m: False if Path(p) == current.parent else real_access(p, m)):
            ok, msg = install_downloaded_package(str(self.new), expected_sha256=self.sha)
        self.assertTrue(ok, msg)
        fallback = self.home / ".local" / "bin" / "central-nvr.AppImage"
        self.assertEqual(fallback.read_bytes(), b"nova versao")
        self.assertEqual(current.read_bytes(), b"versao antiga")
        self.assertIn("somente leitura", msg)
        desktop = (self.home / ".local" / "share" / "applications" / "central-nvr.desktop").read_text()
        self.assertIn(str(fallback), desktop)

    def test_permission_error_during_replace_falls_back(self):
        current = self._running_appimage()
        real = updater._replace_appimage_atomically

        def replace(source, target, sha):
            if target == current.resolve():
                raise PermissionError("Read-only file system")
            return real(source, target, sha)

        env_patch, mod_patch = self._appimage_env(current)
        with env_patch, mod_patch, \
                patch.object(updater, "_replace_appimage_atomically", side_effect=replace):
            ok, msg = install_downloaded_package(str(self.new), expected_sha256=self.sha)
        self.assertTrue(ok, msg)
        self.assertTrue((self.home / ".local" / "bin" / "central-nvr.AppImage").exists())

    def test_success_message_explains_rollback(self):
        current = self._running_appimage()
        env_patch, mod_patch = self._appimage_env(current)
        with env_patch, mod_patch:
            ok, msg = install_downloaded_package(str(self.new), expected_sha256=self.sha)
        self.assertTrue(ok, msg)
        self.assertIn(".bak", msg)
        self.assertEqual(current.with_name(current.name + ".bak").read_bytes(), b"versao antiga")

    def test_inherited_appimage_without_appdir_installs_to_user_bin(self):
        """Issue #72: APPIMAGE herdado sem APPDIR não sobrescreve o AppImage de outro programa."""
        foreign = self.root / "Outro.AppImage"
        foreign.write_bytes(b"outro programa")
        env = dict(os.environ)
        env["APPIMAGE"] = str(foreign)
        env.pop("APPDIR", None)
        with patch.dict(os.environ, env, clear=True), \
                self.assertLogs(updater.logger, level="WARNING") as logs:
            ok, msg = install_downloaded_package(str(self.new), expected_sha256=self.sha)
        self.assertTrue(ok, msg)
        self.assertEqual(foreign.read_bytes(), b"outro programa")
        self.assertFalse(foreign.with_name(foreign.name + ".bak").exists())
        installed = self.home / ".local" / "bin" / "central-nvr.AppImage"
        self.assertEqual(installed.read_bytes(), b"nova versao")
        self.assertTrue(any("APPDIR" in line for line in logs.output), logs.output)

    def test_inherited_appimage_with_package_outside_appdir_installs_to_user_bin(self):
        """Issue #72: APPIMAGE + APPDIR de outro programa (pacote fora do AppDir) instala em ~/.local/bin."""
        foreign = self.root / "Cursor.AppImage"
        foreign.write_bytes(b"ide terceiro")
        foreign_appdir = self.root / "mount_cursor"
        foreign_appdir.mkdir()
        source_module = self.root / "src" / "central_nvr" / "core" / "updater.py"
        with patch.dict(os.environ, {"APPIMAGE": str(foreign), "APPDIR": str(foreign_appdir)}), \
                patch.object(updater, "__file__", str(source_module)), \
                self.assertLogs(updater.logger, level="WARNING") as logs:
            ok, msg = install_downloaded_package(str(self.new), expected_sha256=self.sha)
        self.assertTrue(ok, msg)
        self.assertEqual(foreign.read_bytes(), b"ide terceiro")
        self.assertFalse(foreign.with_name(foreign.name + ".bak").exists())
        installed = self.home / ".local" / "bin" / "central-nvr.AppImage"
        self.assertEqual(installed.read_bytes(), b"nova versao")
        self.assertTrue(any("AppDir" in line for line in logs.output), logs.output)

    def test_inherited_appimage_with_python_in_foreign_appdir_but_package_outside_installs_to_user_bin(self):
        """Issue #72: outro AppImage que embute Python no PATH (sys.executable no APPDIR, pacote fora) não é sobrescrito."""
        foreign = self.root / "OutroIDE.AppImage"
        foreign.write_bytes(b"ide com python embutido")
        foreign_appdir = self.root / "mount_outro_ide"
        foreign_appdir.mkdir()
        source_module = self.root / "src" / "central_nvr" / "core" / "updater.py"
        with patch.dict(os.environ, {"APPIMAGE": str(foreign), "APPDIR": str(foreign_appdir)}), \
                patch.object(sys, "executable", str(foreign_appdir / "usr" / "bin" / "python3")), \
                patch.object(updater, "__file__", str(source_module)), \
                self.assertLogs(updater.logger, level="WARNING") as logs:
            ok, msg = install_downloaded_package(str(self.new), expected_sha256=self.sha)
        self.assertTrue(ok, msg)
        self.assertEqual(foreign.read_bytes(), b"ide com python embutido")
        self.assertFalse(foreign.with_name(foreign.name + ".bak").exists())
        installed = self.home / ".local" / "bin" / "central-nvr.AppImage"
        self.assertEqual(installed.read_bytes(), b"nova versao")
        self.assertTrue(any("AppDir" in line for line in logs.output), logs.output)

    def test_appimage_with_package_inside_appdir_replaces_atomically(self):
        """Issue #72: APPIMAGE + APPDIR com pacote dentro do AppDir mantém o Atomic Replacement."""
        current = self._running_appimage()
        env_patch, mod_patch = self._appimage_env(current)
        with env_patch, mod_patch:
            ok, msg = install_downloaded_package(str(self.new), expected_sha256=self.sha)
        self.assertTrue(ok, msg)
        self.assertEqual(current.read_bytes(), b"nova versao")
        self.assertEqual(current.with_name(current.name + ".bak").read_bytes(), b"versao antiga")
        self.assertFalse((self.home / ".local" / "bin" / "central-nvr.AppImage").exists())

    def test_running_appimage_path_edge_cases(self):
        """Valida casos de borda de _running_appimage_path (prefixo parecido, __file__ no AppDir, APPDIR relativo/raiz)."""
        current = self._running_appimage()
        appdir = self.root / "appdir"
        appdir.mkdir()
        sibling_dir = self.root / "appdir_outro"
        sibling_dir.mkdir()
        bundled_module = appdir / "usr" / "src" / "central_nvr" / "core" / "updater.py"

        # Prefixo de nome semelhante não engana is_relative_to
        with patch.dict(os.environ, {"APPIMAGE": str(current), "APPDIR": str(appdir)}), \
                patch.object(updater, "__file__", str(sibling_dir / "usr" / "src" / "central_nvr" / "core" / "updater.py")):
            self.assertIsNone(updater._running_appimage_path())

        # APPDIR relativo ou raiz é rejeitado
        for bad_appdir in (".", "/"):
            with patch.dict(os.environ, {"APPIMAGE": str(current), "APPDIR": bad_appdir}), \
                    patch.object(updater, "__file__", str(bundled_module)):
                self.assertIsNone(updater._running_appimage_path())

        # Pacote central_nvr (__file__) dentro de APPDIR valida mesmo usando python3 do host (fallback do AppRun)
        with patch.dict(os.environ, {"APPIMAGE": str(current), "APPDIR": str(appdir)}), \
                patch.object(sys, "executable", str(sibling_dir / "python3")), \
                patch.object(updater, "__file__", str(bundled_module)):
            self.assertEqual(updater._running_appimage_path(), current.resolve())

        # Apenas sys.executable dentro de APPDIR (com __file__ fora) não basta
        with patch.dict(os.environ, {"APPIMAGE": str(current), "APPDIR": str(appdir)}), \
                patch.object(sys, "executable", str(appdir / "usr" / "bin" / "python3")), \
                patch.object(updater, "__file__", str(sibling_dir / "src" / "central_nvr" / "core" / "updater.py")):
            self.assertIsNone(updater._running_appimage_path())


class DesktopExecTest(unittest.TestCase):
    def test_plain_path_is_quoted(self):
        self.assertEqual(desktop_exec_path(Path("/home/u/apps/central.AppImage")), '"/home/u/apps/central.AppImage"')

    def test_reserved_characters_are_escaped(self):
        backslash = "\\"
        path = Path(f'/home/u/My Apps/a"b$c`d{backslash}e%f.AppImage')
        # Regra de aspas (\" \$ \` \\) seguida do escape de string do arquivo (\ → \\) e % → %%
        expected = '"/home/u/My Apps/a\\\\"b\\\\$c\\\\`d\\\\\\\\e%%f.AppImage"'
        self.assertEqual(desktop_exec_path(path), expected)

    def test_control_characters_are_rejected(self):
        with self.assertRaises(ValueError):
            desktop_exec_path(Path("/tmp/a\nb.AppImage"))


if __name__ == "__main__":
    unittest.main()
