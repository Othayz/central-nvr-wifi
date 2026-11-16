"""
Ciclo de vida de threads Qt de segundo plano.

Destruir um QThread que ainda está executando aborta o processo
("QThread: Destroyed while thread is still running"). As threads de rede (RTSP, HTTP) podem
ficar bloqueadas por vários segundos, então quem as descarta não pode esperar por elas
(congelaria a interface) nem destruí-las.

retire_thread() resolve os dois lados: desvincula a thread do seu dono e a mantém viva até que
termine sozinha. drain_retired_threads() é usado no encerramento do processo, o único ponto em
que é aceitável esperar.
"""
import logging
import time
from functools import partial
from typing import Set

from PySide6.QtCore import QObject, QThread

logger = logging.getLogger(__name__)

# Referências fortes: sem elas o coletor do Python destruiria a thread ainda em execução
_retired_threads: Set[QThread] = set()


def retire_thread(thread: QThread) -> None:
    """
    Descarta uma thread sem bloquear e sem destruí-la enquanto executa.

    Quem chama já deve ter pedido a parada (flag de cancelamento) e desconectado os sinais que
    não quer mais receber. A thread é liberada quando seu run() retornar.
    """
    if thread.parent() is not None:
        thread.setParent(None)  # o dono pode ser destruído sem levar a thread junto

    _retired_threads.add(thread)
    thread.finished.connect(partial(_release_thread, thread))
    if not thread.isRunning():
        _release_thread(thread)


def _release_thread(thread: QThread) -> None:
    if thread in _retired_threads:
        _retired_threads.discard(thread)
        thread.deleteLater()


def retire_running_threads_of(owner: QObject) -> None:
    """Aposenta toda thread ainda em execução que seja descendente de owner."""
    for thread in owner.findChildren(QThread):
        if thread.isRunning():
            retire_thread(thread)


def retired_thread_count() -> int:
    """Quantidade de threads aposentadas que ainda não terminaram."""
    return sum(1 for thread in _retired_threads if thread.isRunning())


def drain_retired_threads(timeout_ms: int) -> bool:
    """
    Espera, por no máximo timeout_ms no total, as threads aposentadas terminarem.
    Retorna True se nenhuma continua em execução.
    """
    deadline = time.monotonic() + timeout_ms / 1000.0
    for thread in list(_retired_threads):
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        if thread.wait(remaining_ms):
            _release_thread(thread)

    still_running = retired_thread_count()
    if still_running:
        logger.warning(f"{still_running} thread(s) de segundo plano não terminaram em {timeout_ms} ms.")
    return still_running == 0
