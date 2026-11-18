"""
Fila serial de comandos de rede (Issue #23).

Cada comando PTZ disparado em uma thread própria não tem ordem garantida: em um clique rápido o
Stop podia chegar à câmera antes do ContinuousMove, e o motor girava até o fim de curso.
SerialCommandQueue executa os comandos de uma câmera um de cada vez, na ordem de envio, em uma
única thread de fundo (sem bloquear a interface).
"""
import logging
import queue
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_SHUTDOWN = object()


class SerialCommandQueue:
    """Executa callables em série (FIFO) em uma thread daemon dedicada."""

    def __init__(self, name: str = "ptz"):
        self._queue: "queue.Queue[object]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=f"cmd-queue-{name}", daemon=True)
        self._thread.start()

    def submit(self, command: Callable[[], object]) -> None:
        """Agenda o comando; ele roda depois de todos os enviados antes dele."""
        self._queue.put(command)

    def shutdown(self, timeout: Optional[float] = None) -> None:
        """Encerra a thread depois dos comandos já enfileirados."""
        self._queue.put(_SHUTDOWN)
        if timeout is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        while True:
            command = self._queue.get()
            if command is _SHUTDOWN:
                return
            try:
                command()
            except Exception as e:
                logger.error(f"Erro ao executar comando enfileirado: {e}")
