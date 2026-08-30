"""
Módulo central (Core) da Central NVR WiFi.
"""
from central_nvr.core.camera import CameraDevice, ConnectionState, StreamStats
from central_nvr.core.config import ConfigManager, get_config_dir, get_data_dir
from central_nvr.core.onvif_client import OnvifClient
from central_nvr.core.stream_worker import StreamWorker

__all__ = [
    "CameraDevice",
    "ConnectionState",
    "StreamStats",
    "ConfigManager",
    "get_config_dir",
    "get_data_dir",
    "OnvifClient",
    "StreamWorker",
]
