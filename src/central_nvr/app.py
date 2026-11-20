"""
Ponto de Entrada da Aplicação e Ciclo de Vida Qt.
"""
import argparse
import gc
import logging
import os
import sys

from central_nvr import __version__
from central_nvr.core.config import ConfigManager


def parse_arguments() -> argparse.Namespace:
    """Configura e processa argumentos de linha de comando."""
    parser = argparse.ArgumentParser(
        description="Central NVR WiFi - Monitoramento, Descoberta ONVIF e Streaming RTSP para Linux",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Ativa mensagens detalhadas de depuração no console",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="Desativa a verificação automática de atualizações na inicialização",
    )
    parser.add_argument(
        "--hw-accel",
        choices=["vaapi", "cpu", "cuda", "auto"],
        default=None,
        help="Força método de aceleração por hardware para decodificação de vídeo",
    )
    parser.add_argument(
        "--transport",
        choices=["tcp", "udp"],
        default=None,
        help="Força protocolo de transporte RTSP",
    )
    return parser.parse_args()


def setup_logging(debug: bool = False):
    """Configura o sistema de logs da aplicação."""
    log_level = logging.DEBUG if debug else logging.INFO
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=log_level, format=log_format, datefmt="%H:%M:%S")


# Tempo máximo de espera, no encerramento, por threads de rede ainda bloqueadas
SHUTDOWN_THREAD_TIMEOUT_MS = 8000


def install_main_thread_gc(parent, interval_ms: int = 2000):
    """
    Executa a coleta de lixo cíclica do Python somente na thread principal do Qt (Issue #55).

    Desabilita o GC automático para evitar que threads de worker (StreamWorker, atualizador e
    descoberta) disparem coleta cíclica e executem destrutores C++ de widgets Qt fora da thread
    principal, agendando coletas periódicas via QTimer na thread da interface.

    Retorna o par ``(timer, cleanup)``: ``cleanup()`` para o timer, restaura ``gc.enable()`` e
    executa uma coleta final na thread principal.
    """
    from PySide6.QtCore import QTimer

    gc.collect()
    gc.disable()

    timer = QTimer(parent)
    timer.setInterval(interval_ms)

    def _collect_on_main_thread():
        gc.collect()

    timer.timeout.connect(_collect_on_main_thread)

    about_to_quit = getattr(parent, "aboutToQuit", None)
    if about_to_quit is not None:
        about_to_quit.connect(timer.stop)

    cleaned_up = False

    def cleanup():
        nonlocal cleaned_up
        if cleaned_up:
            return
        cleaned_up = True
        try:
            timer.stop()
            timer.timeout.disconnect(_collect_on_main_thread)
        except (RuntimeError, TypeError):
            pass
        if about_to_quit is not None:
            try:
                about_to_quit.disconnect(timer.stop)
            except (RuntimeError, TypeError):
                pass
        try:
            timer.setParent(None)
        except RuntimeError:
            pass
        gc.enable()
        gc.collect()

    timer.start()
    return timer, cleanup


def _exit_code_after_background_threads(exit_code: int, *thread_owners) -> int:
    """
    Devolve exit_code depois de esperar as threads de segundo plano, para que nenhuma thread Qt
    seja destruída em execução (o que abortaria com SIGABRT). Se alguma continuar presa na rede
    além do limite, NÃO retorna: termina o processo com os._exit, sem a destruição dos objetos Qt.
    """
    from central_nvr.core.threads import drain_retired_threads, retire_running_threads_of

    for owner in thread_owners:
        if owner is not None:
            retire_running_threads_of(owner)

    if not drain_retired_threads(SHUTDOWN_THREAD_TIMEOUT_MS):
        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code)
    return exit_code


def main() -> int:
    """Função principal de inicialização da Central NVR WiFi."""
    args = parse_arguments()
    setup_logging(debug=args.debug)

    logger = logging.getLogger("central_nvr")
    logger.info(f"Iniciando Central NVR WiFi v{__version__} no Linux...")

    try:
        from PySide6.QtWidgets import QApplication
        from central_nvr.ui.main_window import MainWindow
    except ImportError:
        print("\n" + "=" * 70)
        print(" [Central NVR WiFi] Dependência PySide6 (Qt6) não encontrada!")
        print("=" * 70)
        print(" Para executar a interface gráfica, instale as dependências com:")
        print("   pip install -r requirements.txt")
        print(" ou no sistema nativo:")
        print("   Ubuntu/Debian:  sudo apt install python3-pyside6")
        print("   Fedora/RHEL:    sudo dnf install python3-pyside6")
        print("=" * 70 + "\n")
        return 1

    # Inicializar aplicação Qt
    app = QApplication(sys.argv)
    app.setApplicationName("Central NVR WiFi")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("CentralNVR")
    app.setOrganizationDomain("centralnvr.local")

    # Mantém a coleta cíclica na thread principal enquanto threads de worker puderem existir (Issue #55)
    _, cleanup_gc = install_main_thread_gc(app)
    try:
        # Definir ícone global do aplicativo
        from PySide6.QtGui import QIcon
        from pathlib import Path

        icon_paths = [
            Path(__file__).parent / "ui" / "assets" / "central-nvr.png",
            Path("/usr/share/icons/hicolor/512x512/apps/central-nvr.png"),
            Path("/usr/share/icons/hicolor/scalable/apps/central-nvr.svg"),
        ]
        for p in icon_paths:
            if p.exists():
                app.setWindowIcon(QIcon(str(p)))
                break

        # Carregar configurações persistentes
        config_mgr = ConfigManager()
        if args.hw_accel:
            config_mgr.set("hw_accel", args.hw_accel)
        if args.transport:
            config_mgr.set("rtsp_transport", args.transport)

        # 1. A janela principal abre imediatamente: a abertura não depende do GitHub (Issue #37)
        window = MainWindow(config_mgr=config_mgr)
        window.show()

        # 2. Verificação de atualização em segundo plano; se houver versão nova, o botão da barra de
        #    status avisa, sem diálogo modal sobre o vídeo
        if config_mgr.get("check_updates_on_startup", True) and not args.no_update_check:
            window.start_background_update_check()

        return _exit_code_after_background_threads(app.exec(), window)
    finally:
        cleanup_gc()


if __name__ == "__main__":
    sys.exit(main())
