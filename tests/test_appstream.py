#!/usr/bin/env python3
"""
Testes automatizados para validação dos metadados AppStream (Ticket #3).
Garante conformidade com o padrão Freedesktop e ausência de erros no appstreamcli.
"""
import os
import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APPDATA_PATH = PROJECT_ROOT / "packaging" / "io.github.othayz.central_nvr.appdata.xml"


class AppStreamMetadataTest(unittest.TestCase):
    def test_appdata_file_exists(self):
        self.assertTrue(APPDATA_PATH.exists(), "packaging/io.github.othayz.central_nvr.appdata.xml deve existir")

    def test_appdata_xml_structure(self):
        content = APPDATA_PATH.read_text(encoding="utf-8")
        # Substitui placeholders temporariamente para validar o XML
        content = content.replace("@VERSION@", "1.2.0").replace("@DATE@", "2026-11-06")
        
        root = ET.fromstring(content)
        self.assertEqual(root.tag, "component")
        self.assertEqual(root.attrib.get("type"), "desktop-application")

        # Tags essenciais
        id_tag = root.find("id")
        self.assertIsNotNone(id_tag)
        self.assertEqual(id_tag.text.strip(), "io.github.othayz.central_nvr")

        name_tag = root.find("name")
        self.assertIsNotNone(name_tag)
        self.assertEqual(name_tag.text.strip(), "Central NVR WiFi")

        summary_tag = root.find("summary")
        self.assertIsNotNone(summary_tag)
        self.assertTrue(len(summary_tag.text.strip()) > 10)

        desc_tag = root.find("description")
        self.assertIsNotNone(desc_tag)
        self.assertGreater(len(desc_tag.findall("p")), 0)

        launchable_tag = root.find("launchable")
        self.assertIsNotNone(launchable_tag)
        self.assertEqual(launchable_tag.attrib.get("type"), "desktop-id")
        self.assertEqual(launchable_tag.text.strip(), "io.github.othayz.central_nvr.desktop")

    def test_appstreamcli_validate(self):
        appstreamcli = shutil.which("appstreamcli")
        if not appstreamcli:
            self.skipTest("appstreamcli não instalado neste ambiente")

        # Gera arquivo temporário com os placeholders preenchidos
        content = APPDATA_PATH.read_text(encoding="utf-8")
        content = content.replace("@VERSION@", "1.2.0").replace("@DATE@", "2026-11-06")

        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".xml", encoding="utf-8", delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            res = subprocess.run(
                [appstreamcli, "validate", "--no-net", temp_path],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                res.returncode,
                0,
                f"appstreamcli validate falhou (code {res.returncode}):\n{res.stdout}\n{res.stderr}",
            )
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()
