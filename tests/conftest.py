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


def pytest_sessionfinish(session, exitstatus):
    """
    Encerra o que os testes deixaram para trás antes de o interpretador destruir os objetos:
    janelas abertas, deleteLater pendentes e threads aposentadas ainda em execução. Sem isto, a
    destruição na saída do processo pode ocorrer fora de ordem e abortar ("free(): invalid size").
    """
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

    from central_nvr.core.threads import drain_retired_threads

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    drain_retired_threads(10000)
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        gc.collect()
