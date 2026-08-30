"""
Módulo de Interface Gráfica da Central NVR WiFi.
"""
from central_nvr.ui.camera_grid import CameraGridWidget
from central_nvr.ui.camera_view import CameraViewWidget
from central_nvr.ui.discovery_dialog import DiscoveryDialog
from central_nvr.ui.main_window import MainWindow
from central_nvr.ui.ptz_controller import PTZControllerWidget
from central_nvr.ui.settings_dialog import SettingsDialog
from central_nvr.ui.styles import DARK_THEME_QSS, LIGHT_THEME_QSS, get_theme_qss
from central_nvr.ui.timeline_bar import TimelineBarWidget

__all__ = [
    "MainWindow",
    "CameraGridWidget",
    "CameraViewWidget",
    "PTZControllerWidget",
    "DiscoveryDialog",
    "SettingsDialog",
    "TimelineBarWidget",
    "DARK_THEME_QSS",
    "LIGHT_THEME_QSS",
    "get_theme_qss",
]
