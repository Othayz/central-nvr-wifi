#!/usr/bin/env python3
"""
Testes do launcher do AppImage (packaging/AppRun) contra caminho de busca não confiável (#16).
Executa o AppRun real sobre um AppDir mínimo, a partir de um diretório com conteúdo hostil.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGING = PROJECT_ROOT / "packaging"

# Aplicação mínima: importa um módulo da biblioteca padrão e relata o ambiente em que rodou
FAKE_APP_MAIN = """\
import argparse, json, os, sys
print(json.dumps({
    "argparse": argparse.__file__,
    "sys_path": sys.path,
    "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH"),
    "PYTHONPATH": os.environ.get("PYTHONPATH"),
    "APPIMAGE": os.environ.get("APPIMAGE"),
    "APPDIR": os.environ.get("APPDIR"),
}))
"""


@unittest.skipUnless(sys.version_info >= (3, 11), "o launcher usa a opção -P do Python 3.11+")
class AppRunSearchPathTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()

        self.appdir = root / "Central_NVR_WiFi.AppDir"
        (self.appdir / "usr" / "bin").mkdir(parents=True)
        (self.appdir / "usr" / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
        pkg = self.appdir / "usr" / "src" / "central_nvr"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "__main__.py").write_text(FAKE_APP_MAIN)

        shutil.copy(PACKAGING / "AppRun", self.appdir / "AppRun")
        os.chmod(self.appdir / "AppRun", 0o755)

        # Interpretador "embutido": delega ao Python dos testes (o AppDir de teste não tem stdlib própria)
        python3 = self.appdir / "usr" / "bin" / "python3"
        python3.write_text(f'#!/bin/sh\nunset PYTHONHOME\nexec "{sys.executable}" "$@"\n')
        os.chmod(python3, 0o755)

        # Diretório de onde o usuário lança o AppImage, com um módulo plantado
        self.downloads = root / "Downloads"
        self.downloads.mkdir()
        (self.downloads / "argparse.py").write_text('raise SystemExit("modulo plantado foi importado")\n')

    def _launch(self, **host_env):
        env = {k: v for k, v in os.environ.items()
               if k not in ("LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME", "PYTHONSAFEPATH", "APPIMAGE", "APPDIR")}
        env.update(host_env)
        proc = subprocess.run(
            [str(self.appdir / "AppRun")],
            cwd=self.downloads, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_module_planted_in_current_directory_is_not_imported(self):
        report = self._launch()
        self.assertNotIn("", report["sys_path"])
        self.assertNotIn(str(self.downloads), report["sys_path"])
        self.assertFalse(report["argparse"].startswith(str(self.downloads)))

    def test_library_path_has_no_empty_entries(self):
        """Entrada vazia em LD_LIBRARY_PATH faz o loader procurar bibliotecas no diretório atual."""
        for host_value, host_entries in ((None, []), (":/opt/host/lib:", ["/opt/host/lib"])):
            with self.subTest(host_LD_LIBRARY_PATH=host_value):
                host_env = {} if host_value is None else {"LD_LIBRARY_PATH": host_value}
                entries = self._launch(**host_env)["LD_LIBRARY_PATH"].split(":")
                self.assertNotIn("", entries)
                self.assertEqual(entries[0], f"{self.appdir}/usr/lib")
                self.assertEqual(entries[-len(host_entries):] if host_entries else [], host_entries)

    def test_host_pythonpath_is_not_inherited(self):
        """O interpretador embutido não carrega módulos apontados pelo PYTHONPATH do host."""
        report = self._launch(PYTHONPATH="/opt/host/python-modules")
        self.assertNotIn("/opt/host/python-modules", report["sys_path"])
        self.assertNotIn("", report["PYTHONPATH"].split(":"))

    def test_bundled_launcher_does_not_import_from_current_directory(self):
        """O launcher usr/bin/central-nvr (usado pelo AppRun quando presente) tem a mesma proteção."""
        launcher = self.appdir / "usr" / "bin" / "central-nvr"
        shutil.copy(PACKAGING / "central-nvr-launcher.sh", launcher)
        os.chmod(launcher, 0o755)

        report = self._launch(PYTHONPATH="/opt/host/python-modules")

        self.assertNotIn("", report["sys_path"])
        self.assertNotIn(str(self.downloads), report["sys_path"])
        self.assertNotIn("/opt/host/python-modules", report["sys_path"])
        self.assertFalse(report["argparse"].startswith(str(self.downloads)))

    def test_host_python_fallback_does_not_import_from_current_directory(self):
        """Sem interpretador embutido, o AppRun usa o python3 do host com a mesma proteção."""
        (self.appdir / "usr" / "bin" / "python3").unlink()
        host_bin = self.appdir.parent / "host-bin"
        host_bin.mkdir()
        os.symlink(sys.executable, host_bin / "python3")

        report = self._launch(PATH=f"{host_bin}:{os.environ.get('PATH', '')}", PYTHONPATH="/opt/host/python-modules")

        self.assertNotIn("", report["sys_path"])
        self.assertNotIn(str(self.downloads), report["sys_path"])
        self.assertIn(f"{self.appdir}/usr/src", report["sys_path"])
        self.assertFalse(report["argparse"].startswith(str(self.downloads)))

    def test_inherited_appimage_is_unset_when_apprun_is_invoked_directly(self):
        """Issue #72: ao executar AppRun direto com APPDIR ausente ou diferente, APPIMAGE herdado é descartado."""
        foreign_appdir = self.appdir.parent / "mount_outro_appimage"
        foreign_appdir.mkdir()
        for host_env in (
            {"APPIMAGE": "/algum/Outro.AppImage", "APPDIR": str(foreign_appdir)},
            {"APPIMAGE": "/algum/Outro.AppImage", "APPDIR": "/caminho/inexistente/outro.AppDir"},
            {"APPIMAGE": "/algum/Outro.AppImage", "APPDIR": ""},
            {"APPIMAGE": "/algum/Outro.AppImage"},
        ):
            with self.subTest(host_env=host_env):
                report = self._launch(**host_env)
                self.assertIsNone(report["APPIMAGE"])
                self.assertEqual(report["APPDIR"], str(self.appdir))

    def test_appimage_is_preserved_when_appdir_matches_apprun(self):
        """Issue #72: quando o runtime do AppImage define APPDIR igual ao AppDir, APPIMAGE é preservado."""
        symlink_appdir = self.appdir.parent / "link_appdir"
        os.symlink(self.appdir, symlink_appdir)
        for appdir_val in (str(self.appdir), str(symlink_appdir)):
            with self.subTest(APPDIR=appdir_val):
                report = self._launch(
                    APPIMAGE="/algum/Central_NVR_WiFi.AppImage",
                    APPDIR=appdir_val,
                )
                self.assertEqual(report["APPIMAGE"], "/algum/Central_NVR_WiFi.AppImage")
                self.assertEqual(report["APPDIR"], str(self.appdir))


if __name__ == "__main__":
    unittest.main()
