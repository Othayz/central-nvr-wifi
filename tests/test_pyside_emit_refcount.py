#!/usr/bin/env python3
"""
Regressão: o PySide6 6.12.0 decrementa a contagem de referências de True a cada emit() feito em
Python < 3.12 (onde True não é imortal); depois de alguns milhares de sinais (um por frame de
vídeo) o processo aborta com "free(): invalid size". O pyproject restringe PySide6 < 6.12 nessas
versões; este teste falha se a versão instalada voltar a ter o problema.
"""
import re
import sys
import unittest
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent


class _Emitter(QObject):
    fired = Signal()


class PySideEmitRefcountTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @unittest.skipIf(sys.version_info >= (3, 12), "True é imortal a partir do Python 3.12")
    def test_emit_does_not_steal_reference_to_true(self):
        emitter = _Emitter()
        before = sys.getrefcount(True)
        for _ in range(20):
            emitter.fired.emit()
        self.assertEqual(sys.getrefcount(True), before,
                         "PySide6 instalado corrompe a contagem de referências de True em emit()")

    def test_dependency_constraint_for_old_python(self):
        for name in ("pyproject.toml", "requirements.txt"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertTrue(re.search(r"PySide6>=6\.5\.0,<6\.12;\s*python_version\s*<\s*['\"]3\.12['\"]", text), name)


if __name__ == "__main__":
    unittest.main()
