"""
Modelo de dados e representação da Câmera / Canal de Vídeo com suporte a Dual-Stream, QoS e Edge Storage.
"""
from dataclasses import dataclass, field
from enum import Enum
import time
import urllib.parse
from typing import Optional, Dict, Any, List


class ConnectionState(Enum):
    DISCONNECTED = "Desconectado"
    CONNECTING = "Conectando..."
    STREAMING = "Ao Vivo"
    RECONNECTING = "Reconectando..."
    ERROR = "Erro de Conexão"
    PAUSED = "Pausado"


@dataclass
class StreamStats:
    fps: float = 0.0
    bitrate_kbps: float = 0.0
    latency_ms: float = 0.0
    jitter_ms: float = 0.0
    width: int = 0
    height: int = 0
    codec: str = "H.264"
    hw_accel_active: bool = False
    frames_received: int = 0
    dropped_frames: int = 0
    transport_mode: str = "TCP"
    motion_detected: bool = False
    last_frame_time: float = 0.0


@dataclass
class CameraDevice:
    id: str
    name: str
    ip: str
    port: int = 80
    rtsp_port: int = 554
    rtsp_path: str = "/live/ch0"
    rtsp_url: str = ""
    substream_path: str = ""
    substream_url: str = ""
    username: str = "admin"
    password: str = ""
    manufacturer: str = "Genérico"
    model: str = "IP Camera"
    onvif_endpoint: str = ""
    event_service_url: str = ""
    has_ptz: bool = False
    has_events: bool = False
    has_edge_storage: bool = False
    edge_recordings_count: int = 0
    rtsp_transport: str = "auto"  # "auto", "tcp", "udp"
    enabled: bool = True
    grid_slot: int = 0
    
    # Perfis e estado em tempo de execução
    profiles: List[Dict[str, Any]] = field(default_factory=list)
    active_profile_token: str = ""
    substream_profile_token: str = ""
    state: ConnectionState = ConnectionState.DISCONNECTED
    stats: StreamStats = field(default_factory=StreamStats)
    last_error: str = ""

    def get_full_rtsp_url(self) -> str:
        """Gera a URL RTSP completa com credenciais embutidas caso necessário."""
        if self.rtsp_url:
            # Se a URL já possui esquema RTSP, injetar credenciais se houver
            parsed = urllib.parse.urlparse(self.rtsp_url)
            if self.username and not parsed.username:
                auth = urllib.parse.quote(self.username)
                if self.password:
                    auth += f":{urllib.parse.quote(self.password)}"
                netloc = f"{auth}@{parsed.hostname}"
                if parsed.port:
                    netloc += f":{parsed.port}"
                return urllib.parse.urlunparse((
                    parsed.scheme,
                    netloc,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment
                ))
            return self.rtsp_url

        # Construir a partir dos campos individuais
        username = self.username if self.username else ("admin" if self.password else "")
        auth = ""
        if username:
            auth = urllib.parse.quote(username)
            if self.password:
                auth += f":{urllib.parse.quote(self.password)}"
            auth += "@"

        path = self.rtsp_path if self.rtsp_path.startswith("/") else f"/{self.rtsp_path}"
        return f"rtsp://{auth}{self.ip}:{self.rtsp_port}{path}"

    def get_substream_url(self) -> str:
        """Retorna a URL RTSP do Sub-Stream (baixa resolução para economia de banda Wi-Fi)."""
        if self.substream_url:
            return self.substream_url

        username = self.username if self.username else ("admin" if self.password else "")
        auth = ""
        if username:
            auth = urllib.parse.quote(username)
            if self.password:
                auth += f":{urllib.parse.quote(self.password)}"
            auth += "@"

        if self.substream_path:
            path = self.substream_path if self.substream_path.startswith("/") else f"/{self.substream_path}"
            return f"rtsp://{auth}{self.ip}:{self.rtsp_port}{path}"

        # Heurística para sub-stream conforme fabricante
        mfg = self.manufacturer.lower()
        if "yoosee" in mfg or "/onvif1" in self.rtsp_path:
            return f"rtsp://{auth}{self.ip}:{self.rtsp_port}/onvif2"
        elif "intelbras" in mfg or "dahua" in mfg:
            return f"rtsp://{auth}{self.ip}:{self.rtsp_port}/cam/realmonitor?channel=1&subtype=1"
        elif "hikvision" in mfg:
            return f"rtsp://{auth}{self.ip}:{self.rtsp_port}/Streaming/Channels/102"
        elif "/11" in self.rtsp_path:
            return f"rtsp://{auth}{self.ip}:{self.rtsp_port}/12"

        return f"rtsp://{auth}{self.ip}:{self.rtsp_port}/live/ch1"

    def get_candidate_rtsp_urls(self, prefer_substream: bool = False) -> List[str]:
        """Retorna uma lista priorizada de URLs candidatas RTSP para câmeras populares."""
        username = self.username if self.username else ("admin" if self.password else "")
        auth = ""
        if username:
            auth = urllib.parse.quote(username)
            if self.password:
                auth += f":{urllib.parse.quote(self.password)}"
            auth += "@"

        urls = []
        if prefer_substream:
            sub_url = self.get_substream_url()
            if sub_url:
                urls.append(sub_url)

        # Se o usuário configurou rtsp_url explícita
        if self.rtsp_url:
            urls.append(self.get_full_rtsp_url())

        # Se o usuário configurou um path específico que não é o placeholder padrão
        if self.rtsp_path and self.rtsp_path not in ("/live/ch0", ""):
            path = self.rtsp_path if self.rtsp_path.startswith("/") else f"/{self.rtsp_path}"
            url_custom = f"rtsp://{auth}{self.ip}:{self.rtsp_port}{path}"
            if url_custom not in urls:
                urls.append(url_custom)

        # Caminhos padrão ordenados por probabilidade de sucesso em câmeras Wi-Fi
        common_paths = [
            "/onvif1",                                     # Yoosee / ONVIF principal
            "/onvif2",                                     # Yoosee / ONVIF sub-stream
            "/live/ch0",                                   # V380 / Genérico
            "/11",                                         # Yoosee / Xiongmai principal
            "/12",                                         # Yoosee / Xiongmai sub-stream
            "/cam/realmonitor?channel=1&subtype=0",        # Intelbras / Dahua Principal
            "/cam/realmonitor?channel=1&subtype=1",        # Intelbras / Dahua Extra
            "/Streaming/Channels/101",                     # Hikvision Principal
            "/Streaming/Channels/102",                     # Hikvision Extra
            "/h264Preview_01_main",                        # Reolink
            "/1",                                          # Genérico 1
            "/live/ch1",                                   # Genérico canal 2
        ]

        for p in common_paths:
            candidate = f"rtsp://{auth}{self.ip}:{self.rtsp_port}{p}"
            if candidate not in urls:
                urls.append(candidate)

        return urls

    def to_dict(self) -> Dict[str, Any]:
        """Converte para dicionário serializável em JSON."""
        return {
            "id": self.id,
            "name": self.name,
            "ip": self.ip,
            "port": self.port,
            "rtsp_port": self.rtsp_port,
            "rtsp_path": self.rtsp_path,
            "rtsp_url": self.rtsp_url,
            "substream_path": self.substream_path,
            "substream_url": self.substream_url,
            "username": self.username,
            "password": self.password,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "onvif_endpoint": self.onvif_endpoint,
            "event_service_url": self.event_service_url,
            "has_ptz": self.has_ptz,
            "has_events": self.has_events,
            "has_edge_storage": self.has_edge_storage,
            "rtsp_transport": self.rtsp_transport,
            "enabled": self.enabled,
            "grid_slot": self.grid_slot,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CameraDevice":
        """Instancia a partir de dicionário carregado do JSON."""
        return cls(
            id=data.get("id", str(time.time())),
            name=data.get("name", "Nova Câmera"),
            ip=data.get("ip", "192.168.1.100"),
            port=data.get("port", 80),
            rtsp_port=data.get("rtsp_port", 554),
            rtsp_path=data.get("rtsp_path", "/live/ch0"),
            rtsp_url=data.get("rtsp_url", ""),
            substream_path=data.get("substream_path", ""),
            substream_url=data.get("substream_url", ""),
            username=data.get("username", "admin"),
            password=data.get("password", ""),
            manufacturer=data.get("manufacturer", "Genérico"),
            model=data.get("model", "Câmera ONVIF"),
            onvif_endpoint=data.get("onvif_endpoint", ""),
            event_service_url=data.get("event_service_url", ""),
            has_ptz=data.get("has_ptz", False),
            has_events=data.get("has_events", False),
            has_edge_storage=data.get("has_edge_storage", False),
            rtsp_transport=data.get("rtsp_transport", "auto"),
            enabled=data.get("enabled", True),
            grid_slot=data.get("grid_slot", 0),
        )
