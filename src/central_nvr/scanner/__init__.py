"""
Módulo de escaneamento e descoberta de rede para a Central NVR WiFi.
"""
from central_nvr.scanner.discovery import NetworkScanner
from central_nvr.scanner.parser import parse_ws_discovery_response

__all__ = ["NetworkScanner", "parse_ws_discovery_response"]
