"""
Configuração comum dos testes.

Vários testes criam widgets Qt; sem uma QApplication o processo aborta ("Must construct a
QApplication before a QWidget"). Criá-la aqui deixa cada arquivo executável sozinho, sem depender
da ordem em que os módulos são coletados.
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])
