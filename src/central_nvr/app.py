"""
Ponto de Entrada da Aplicação e Ciclo de Vida Qt.
"""
import argparse
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


def main() -> int:
    """Função principal de inicialização da Central NVR WiFi."""
    args = parse_arguments()
    setup_logging(debug=args.debug)

    logger = logging.getLogger("central_nvr")
    logger.info(f"Iniciando Central NVR WiFi v{__version__} no Linux...")

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        from central_nvr.ui.main_window import MainWindow
        from central_nvr.ui.update_dialog import StartupUpdateDialog
    except ImportError as e:
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

    # Configuração de High-DPI para monitores modernos
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"

    # Inicializar aplicação Qt
    app = QApplication(sys.argv)
    app.setApplicationName("Central NVR WiFi")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("CentralNVR")
    app.setOrganizationDomain("centralnvr.local")

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

    # 1. Verificação de Atualização na Inicialização (Splash / Verificador)
    should_check_updates = config_mgr.get("check_updates_on_startup", True) and not args.no_update_check
    if should_check_updates:
        repo = config_mgr.get("github_repo", "Othayz/central-nvr-wifi")
        token = config_mgr.get("github_token", "").strip() or None
        splash = StartupUpdateDialog(repo=repo, token=token)
        splash.exec()
        if not splash.should_open_main_window:
            logger.info("Encerrando inicialização (instalador acionado).")
            return 0

    # 2. Criar e exibir janela principal
    window = MainWindow(config_mgr=config_mgr)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
