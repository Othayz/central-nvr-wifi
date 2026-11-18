"""
Worker de Streaming RTSP de Baixa Latência com suporte a VA-API / Aceleração de Hardware,
Transporte Adaptativo (Auto-Fallback UDP/TCP), Métricas de QoS (Jitter/Perda) e Detecção de Movimento Integrada.
"""
import datetime
from enum import Enum
import logging
import math
import os
import random
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# Tentar importar numpy
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None

# Tentar importar PySide6
try:
    from PySide6.QtCore import QObject, Qt, QThread, Signal
    from PySide6.QtGui import QImage
    HAS_PYSIDE = True
except ImportError:
    HAS_PYSIDE = False

    class QObject:
        def __init__(self, parent=None):
            pass

    class QThread(threading.Thread):
        def __init__(self, parent=None):
            super().__init__()
            self._parent = parent

        def wait(self, ms=0):
            self.join(timeout=ms / 1000.0 if ms > 0 else None)

    class Signal:
        def __init__(self, *args):
            self._callbacks = []

        def connect(self, cb):
            self._callbacks.append(cb)

        def emit(self, *args):
            for cb in self._callbacks:
                try:
                    cb(*args)
                except Exception:
                    pass

    class QImage:
        def __init__(self, *args):
            pass

        def isNull(self):
            return True

        def copy(self):
            return self

from central_nvr.core.camera import DEFAULT_RTSP_TRANSPORT, CameraDevice, ConnectionState, StreamStats

logger = logging.getLogger(__name__)

def sanitize_rtsp_url(url: str) -> str:
    """
    Ofusca a senha em URLs RTSP para gravação segura em logs.

    Usa o último `@` do trecho após o esquema como fim das credenciais: assim a senha é mascarada
    mesmo se contiver `/`, `:` ou `@` sem codificação. Na dúvida, mascara a mais (nunca a menos).
    """
    if not url:
        return ""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    at = rest.rfind("@")
    if at == -1:
        return url
    userinfo, hostpart = rest[:at], rest[at + 1:]
    if ":" not in userinfo:
        return url  # Só usuário, sem senha
    user = userinfo.split(":", 1)[0]
    return f"{scheme}://{user}:****@{hostpart}"

# Tentar importar bibliotecas de decodificação de vídeo
HAS_AV = False
HAS_CV2 = False

try:
    import av
    HAS_AV = True
except ImportError:
    pass

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    pass

_opencv_lock = threading.Lock()

VAAPI_DEVICE_PATH = "/dev/dri/renderD128"


def is_vaapi_supported() -> bool:
    """Verifica se o dispositivo de aceleração gráfica por hardware VA-API (/dev/dri/renderD128) está acessível."""
    try:
        return os.path.exists(VAAPI_DEVICE_PATH) and os.access(VAAPI_DEVICE_PATH, os.R_OK)
    except Exception:
        return False


class StreamOutcome(Enum):
    """Resultado de uma tentativa de stream (Issue #27)."""
    NOT_CONNECTED = "not_connected"   # não abriu ou não entregou nenhum frame
    AUTH_FAILED = "auth_failed"       # credenciais recusadas (RTSP 401)
    ENDED = "ended"                   # transmitiu e terminou (câmera fechou, queda no meio)
    STOPPED = "stopped"               # parada pedida pela aplicação
    RESTART = "restart"               # troca HD/SD pedida: reconectar já, sem espera (Issue #26)
    PAUSED = "paused"                 # câmera fora da tela com backend que não lê sem decodificar


def _pyav_socket_timeout_option() -> Dict[str, str]:
    """
    Timeout de E/S do demuxer RTSP: `stimeout` foi removido no FFmpeg 5 (libavformat 59) e
    substituído por `timeout`; nas versões antigas, `timeout` significaria modo listen.
    """
    try:
        lavf_major = av.library_versions.get("libavformat", (0,))[0]
    except Exception:
        lavf_major = 0
    return {"timeout": "5000000"} if lavf_major >= 59 else {"stimeout": "5000000"}


class TransportMode(str, Enum):
    AUTO_UDP = "Auto (UDP)"
    AUTO_TCP_FALLBACK = "Auto (TCP Fallback)"
    UDP = "UDP"
    TCP = "TCP"
    OPENCV_UDP = "OpenCV (UDP)"
    OPENCV_TCP = "OpenCV (TCP)"
    DEMO = "Demo"


class VideoCodec(str, Enum):
    H264 = "H.264"
    H265_HEVC = "H.265 / HEVC"
    MJPEG = "MJPEG"
    UNKNOWN = "Desconhecido"


class StreamWorker(QThread):
    """
    Thread de alta performance para consumo e processamento de fluxos RTSP de câmeras IP.
    Implementa:
    - Modo de Transporte Adaptativo com Fallback automático (UDP -> TCP).
    - Cálculo de métricas de QoS em tempo real (FPS, Bitrate, Latência, Jitter).
    - Detecção de movimento (Edge-AI leve) diretamente nos frames.
    - Suporte a Dual-Stream (MainStream para alta resolução / SubStream para economia de banda Wi-Fi).
    """

    # "Último frame vence" (Issue #25): o worker sobrescreve um slot e avisa uma única vez até a
    # interface consumir com take_latest_frame(); frames não lidos são descartados, sem fila.
    frame_available = Signal()
    stats_updated = Signal(object)  # StreamStats
    state_changed = Signal(object, str)  # (ConnectionState, message)
    snapshot_saved = Signal(str)  # filepath
    motion_detected = Signal(str, bool)  # (camera_id, is_motion)
    # (sub-stream em uso, sub-stream pedido): emitido quando a transmissão começa (Issue #26)
    stream_variant = Signal(bool, bool)

    def __init__(
        self,
        camera: CameraDevice,
        hw_accel: str = "vaapi",
        rtsp_transport: str = DEFAULT_RTSP_TRANSPORT,
        buffer_size_ms: int = 150,
        prefer_substream: bool = False,
        reconnect_interval_sec: float = 3.0,
        auto_reconnect: bool = True,
        motion_detection: bool = True,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.camera = camera
        self.hw_accel = hw_accel
        self.rtsp_transport = rtsp_transport or getattr(camera, "rtsp_transport", DEFAULT_RTSP_TRANSPORT)
        # Lido a cada frame: pode ser alterado com o stream aberto (Issue #34)
        self.motion_detection = bool(motion_detection)
        self.buffer_size_ms = buffer_size_ms
        self.prefer_substream = prefer_substream
        self.reconnect_interval_sec = max(0.5, float(reconnect_interval_sec or 3.0))
        self.auto_reconnect = bool(auto_reconnect)
        
        self._is_running = False
        self._stop_requested = False
        self._connected_once = False
        self._switch_requested = False
        self._current_url = ""
        self._is_paused = False
        self._await_keyframe = False
        self._paused_announced = False
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[QImage] = None
        self._frame_pending = False
        self._target_size: Optional[Tuple[int, int]] = None
        self._request_snapshot: Optional[str] = None
        self._is_recording = False
        self._recording_writer = None
        self._recording_path = ""

        # Estatísticas e Métricas de QoS
        self.stats = StreamStats()
        self._frame_count = 0
        self._bytes_count = 0
        self._last_stats_calc = time.time()
        self._last_frame_ts = time.time()

        # Detecção de Movimento (Motion Tracking)
        self._prev_sample_matrix: Optional[np.ndarray] = None
        self._motion_active = False
        self._motion_cooldown = 0
        self._motion_eval_counter = 0

    def request_stop(self):
        """Pede o encerramento sem bloquear; a thread termina ao sair da operação de rede em curso."""
        self._stop_requested = True
        self._is_running = False

    def stop(self):
        """Pede o encerramento e aguarda até 1 s. Na interface, prefira request_stop() + retire_thread()."""
        self.request_stop()
        self.quit()
        self.wait(1000)

    def pause(self):
        """
        Câmera fora da tela (Issue #25): no PyAV a conexão continua aberta e os pacotes são lidos e
        descartados sem decodificar; no OpenCV (que não lê sem decodificar) a conexão é encerrada.
        """
        self._is_paused = True

    def resume(self):
        """Volta a decodificar a partir do próximo keyframe (evita imagem corrompida)."""
        self._await_keyframe = True
        self._is_paused = False

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def set_target_size(self, width: int, height: int):
        """Tamanho em que o frame será exibido; 0 = resolução original (ex.: tela cheia aberta)."""
        self._target_size = (int(width), int(height)) if width > 0 and height > 0 else None

    def take_latest_frame(self) -> Optional[QImage]:
        """Chamado pela interface: devolve o frame mais recente e libera o próximo aviso."""
        with self._frame_lock:
            frame = self._latest_frame
            self._latest_frame = None
            self._frame_pending = False
        return frame

    def _publish_frame(self, q_img: QImage):
        with self._frame_lock:
            self._latest_frame = q_img
            notify = not self._frame_pending
            self._frame_pending = True
        if notify:
            self.frame_available.emit()

    def _update_pause_state(self):
        """Anuncia transições de pausa a partir da thread do worker."""
        if self._is_paused and not self._paused_announced:
            self._paused_announced = True
            self.state_changed.emit(ConnectionState.PAUSED, "Pausada (fora da tela)")
        elif not self._is_paused and self._paused_announced:
            self._paused_announced = False
            self.state_changed.emit(ConnectionState.STREAMING, "Ao Vivo")

    def set_prefer_substream(self, prefer: bool):
        """
        Alterna a preferência de stream (MainStream vs SubStream) e reconecta com a nova preferência:
        o stream atual é encerrado e a lista de URLs candidatas é recalculada (Issue #26).
        """
        if self.prefer_substream != prefer:
            self.prefer_substream = prefer
            # O caminho memorizado era o da preferência anterior
            self.camera.resolved_stream = None
            self._switch_requested = True

    def _keep_reading(self) -> bool:
        return self._is_running and not self._switch_requested

    def _announce_streaming(self, message: str):
        """Estado AO VIVO + qual stream de fato conectou (o rótulo HD/SD segue este sinal)."""
        self.state_changed.emit(ConnectionState.STREAMING, message)
        is_sub = bool(self._current_url) and self._current_url == self.camera.get_substream_url()
        self.stream_variant.emit(is_sub, self.prefer_substream)

    def take_snapshot(self, output_path: str):
        """Solicita a gravação do próximo frame como imagem no disco."""
        self._request_snapshot = output_path

    # ------------------------------------------------------------------
    # Laço de conexão e reconexão (Issue #27)
    # ------------------------------------------------------------------

    MAX_BACKOFF_SEC = 60.0
    # Um stream que durou pelo menos isto zera o backoff
    STABLE_STREAM_SEC = 30.0

    def _backoff_delay(self, failures: int) -> float:
        """Intervalo configurado × 2^(falhas-1), limitado a 60 s, com jitter de ±20%."""
        base = self.reconnect_interval_sec * (2 ** max(0, failures - 1))
        base = min(base, max(self.MAX_BACKOFF_SEC, self.reconnect_interval_sec))
        return base * random.uniform(0.8, 1.2)

    def _wait(self, seconds: float):
        """Espera responsiva: interrompida assim que a parada é pedida."""
        deadline = time.monotonic() + seconds
        while self._is_running and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

    def _transports(self) -> List[str]:
        user_transport = (self.rtsp_transport or DEFAULT_RTSP_TRANSPORT).lower()
        return ["tcp", "udp"] if user_transport == "tcp" else ["udp", "tcp"]

    def _backends(self) -> List[Tuple[str, Callable[..., StreamOutcome]]]:
        backends = []
        if HAS_AV:
            backends.append(("PyAV", self._run_pyav_stream))
        if HAS_CV2:
            backends.append(("OpenCV", self._run_opencv_stream))
        return backends

    def run(self):
        # Uma parada pedida antes de a thread começar a executar não pode ser desfeita aqui
        self._is_running = not self._stop_requested
        self.state_changed.emit(ConnectionState.CONNECTING, "Iniciando conexão RTSP...")

        if self.camera.rtsp_url.startswith("demo://"):
            self.stats.transport_mode = TransportMode.DEMO.value
            self._run_test_pattern_stream()
            self.state_changed.emit(ConnectionState.DISCONNECTED, "Desconectado")
            return

        failures = 0
        while self._is_running:
            started = time.monotonic()
            outcome = self._connect_once()
            if not self._is_running or outcome == StreamOutcome.STOPPED:
                break

            if outcome == StreamOutcome.PAUSED:
                self._update_pause_state()
                while self._is_running and self._is_paused:
                    time.sleep(0.1)
                continue

            if outcome == StreamOutcome.RESTART or self._switch_requested:
                self._switch_requested = False
                self.state_changed.emit(ConnectionState.CONNECTING, "Trocando de stream (HD/SD)...")
                continue

            if outcome == StreamOutcome.AUTH_FAILED:
                # Repetir só aumentaria o risco de bloqueio por tentativas na câmera
                self.camera.last_error = "Credenciais inválidas"
                self.state_changed.emit(ConnectionState.ERROR, "Credenciais inválidas — verifique usuário e senha")
                break

            if outcome == StreamOutcome.ENDED and time.monotonic() - started >= self.STABLE_STREAM_SEC:
                failures = 0
            failures += 1
            self._reset_stats()

            if not self.auto_reconnect:
                self.state_changed.emit(ConnectionState.ERROR, "Conexão perdida (reconexão automática desativada)")
                break

            delay = self._backoff_delay(failures)
            if outcome == StreamOutcome.ENDED:
                self.state_changed.emit(ConnectionState.RECONNECTING, f"Conexão encerrada — reconectando em {delay:.0f}s")
            else:
                logger.debug(f"Falha ao conectar RTSP para {self.camera.name}. Nova tentativa em {delay:.1f}s")
                self.state_changed.emit(ConnectionState.ERROR, f"Câmera Offline / Sem Conexão — nova tentativa em {delay:.0f}s")
            self._wait(delay)

        self.state_changed.emit(ConnectionState.DISCONNECTED, "Desconectado")

    def _reset_stats(self):
        self.stats.fps = 0.0
        self.stats.bitrate_kbps = 0.0
        self.stats.latency_ms = 0.0
        self.stats.jitter_ms = 0.0
        self.stats_updated.emit(self.stats)

    def _connect_once(self) -> StreamOutcome:
        """
        Uma rodada de conexão. Se algo já funcionou nesta sessão, tenta só aquilo; senão, busca
        em todos os caminhos candidatos × transportes × backends, parando em erro de autenticação.
        """
        candidate_urls = self.camera.get_candidate_rtsp_urls(prefer_substream=self.prefer_substream)
        backends = dict(self._backends())

        resolved = self.camera.resolved_stream
        if resolved and resolved[0] in backends and resolved[1] in candidate_urls:
            backend_name, url, transport = resolved
            return self._try_stream(backend_name, backends[backend_name], url, transport)

        for backend_name, runner in backends.items():
            for url in candidate_urls:
                for transport in self._transports():
                    if not self._is_running:
                        return StreamOutcome.STOPPED
                    outcome = self._try_stream(backend_name, runner, url, transport)
                    if outcome != StreamOutcome.NOT_CONNECTED:
                        return outcome
        return StreamOutcome.NOT_CONNECTED

    def _try_stream(self, backend_name: str, runner: Callable[..., StreamOutcome], url: str,
                    transport: str) -> StreamOutcome:
        self.stats.transport_mode = self._transport_label(backend_name, transport)
        logger.info(
            f"Conectando RTSP {backend_name} ({self.camera.name}) via {self.stats.transport_mode}: "
            f"{sanitize_rtsp_url(url)}"
        )
        self._current_url = url
        outcome = runner(url, transport=transport)
        if outcome == StreamOutcome.ENDED or (outcome == StreamOutcome.STOPPED and self._connected_once):
            self.camera.resolved_stream = (backend_name, url, transport)
        return outcome

    def _transport_label(self, backend_name: str, transport: str) -> str:
        if backend_name == "OpenCV":
            return TransportMode.OPENCV_UDP.value if transport == "udp" else TransportMode.OPENCV_TCP.value
        if (self.rtsp_transport or DEFAULT_RTSP_TRANSPORT).lower() == "auto":
            return TransportMode.AUTO_UDP.value if transport == "udp" else TransportMode.AUTO_TCP_FALLBACK.value
        return TransportMode.UDP.value if transport == "udp" else TransportMode.TCP.value

    def _run_pyav_stream(self, stream_url: str, transport: str = "udp") -> StreamOutcome:
        """Consome o fluxo utilizando PyAV (libav / FFmpeg) com suporte nativo a H.265 (HEVC) e UDP."""
        container_options = {
            "rtsp_transport": transport,
            "reorder_queue_size": "100",
            "buffer_size": "2048000",
            "fflags": "nobuffer+genpts+discardcorrupt",
            "flags": "low_delay",
            "max_delay": str(self.buffer_size_ms * 1000),
            **_pyav_socket_timeout_option(),
        }

        container = None
        frames_seen = 0
        try:
            try:
                container = av.open(stream_url, mode="r", options=container_options, timeout=5.0)
            except av.HTTPUnauthorizedError:
                return StreamOutcome.AUTH_FAILED
            video_stream = next((s for s in container.streams if s.type == "video"), None)
            if not video_stream:
                return StreamOutcome.NOT_CONNECTED

            video_stream.thread_type = "AUTO"
            raw_codec = video_stream.codec_context.name.upper()
            if "HEVC" in raw_codec or "265" in raw_codec:
                self.stats.codec = VideoCodec.H265_HEVC.value
            elif "264" in raw_codec or "AVC" in raw_codec:
                self.stats.codec = VideoCodec.H264.value
            elif "MJPEG" in raw_codec or "JPEG" in raw_codec:
                self.stats.codec = VideoCodec.MJPEG.value
            else:
                self.stats.codec = raw_codec or VideoCodec.UNKNOWN.value

            vaapi_ok = (self.hw_accel == "vaapi" and is_vaapi_supported())
            self.stats.hw_accel_active = vaapi_ok
            self._announce_streaming(f"Ao Vivo ({self.stats.codec} {self.stats.transport_mode})")

            self._last_frame_ts = time.time()

            for packet in container.demux(video_stream):
                if not self._keep_reading():
                    break

                self._update_pause_state()
                if self._is_paused:
                    # Continua lendo para manter a sessão RTSP viva, mas sem decodificar
                    self._await_keyframe = True
                    continue
                if self._await_keyframe:
                    if not getattr(packet, "is_keyframe", True):
                        continue
                    self._await_keyframe = False

                self._bytes_count += packet.size or 0

                for frame in packet.decode():
                    if not self._keep_reading():
                        break

                    now = time.time()
                    delta_ms = max(0.0, (now - self._last_frame_ts) * 1000.0)
                    self._last_frame_ts = now

                    # Converter frame para RGB
                    img_rgb = frame.to_ndarray(format="rgb24")
                    h, w, ch = img_rgb.shape
                    self.stats.width = w
                    self.stats.height = h

                    self._process_and_emit_frame(img_rgb, w, h, delta_ms)
                    frames_seen += 1
                    self._connected_once = True

            return self._end_outcome(frames_seen)
        except Exception as e:
            logger.debug(f"PyAV stream error for {self.camera.name} via {transport}: {e}")
            return self._end_outcome(frames_seen)
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass

    def _run_opencv_stream(self, stream_url: str, transport: str = "udp") -> StreamOutcome:
        """Consome o fluxo utilizando OpenCV VideoCapture como fallback resiliente."""
        if self._is_paused:
            return StreamOutcome.PAUSED  # Não abre conexão para uma câmera fora da tela
        cap = None
        frames_seen = 0
        try:
            with _opencv_lock:
                # buffer_size de 2MB (2048000 bytes) para evitar descarte de pacotes UDP em rajadas de I-Frames
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                    f"rtsp_transport;{transport}|"
                    f"reorder_queue_size;100|"
                    f"buffer_size;2048000|"
                    f"stimeout;5000000|"
                    f"fflags;+nobuffer+genpts"
                )
                cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            if not cap.isOpened():
                return StreamOutcome.NOT_CONNECTED

            if hasattr(cv2, "CAP_PROP_HW_ACCELERATION") and hasattr(cv2, "VIDEO_ACCELERATION_VAAPI"):
                if self.hw_accel == "vaapi" and is_vaapi_supported():
                    cap.set(cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_VAAPI)
                    self.stats.hw_accel_active = (cap.get(cv2.CAP_PROP_HW_ACCELERATION) == cv2.VIDEO_ACCELERATION_VAAPI)
                else:
                    self.stats.hw_accel_active = False
            else:
                self.stats.hw_accel_active = (self.hw_accel == "vaapi" and is_vaapi_supported())

            mfg = (self.camera.manufacturer or "").lower()
            url_lower = stream_url.lower()
            if "hevc" in url_lower or "265" in url_lower or "yoosee" in mfg:
                self.stats.codec = VideoCodec.H265_HEVC.value
            else:
                self.stats.codec = VideoCodec.H264.value

            self._announce_streaming(f"Ao Vivo (OpenCV {self.stats.transport_mode})")
            self._last_frame_ts = time.time()

            while self._keep_reading():
                if self._is_paused:
                    return StreamOutcome.PAUSED  # OpenCV não lê sem decodificar: encerra a conexão
                ret, frame = cap.read()
                if not ret:
                    break

                now = time.time()
                delta_ms = max(0.0, (now - self._last_frame_ts) * 1000.0)
                self._last_frame_ts = now

                # Converter BGR para RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, _ = frame_rgb.shape
                self.stats.width = w
                self.stats.height = h

                # Estimativa de fluxo comprimido realista para H.264/H.265 (~1.5-2.0 Mbps)
                self._bytes_count += max(2000, (w * h * 3) // 200)

                self._process_and_emit_frame(frame_rgb, w, h, delta_ms)
                frames_seen += 1
                self._connected_once = True

            return self._end_outcome(frames_seen)
        except Exception as e:
            logger.debug(f"OpenCV stream error for {self.camera.name} via {transport}: {e}")
            return self._end_outcome(frames_seen)
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    def _end_outcome(self, frames_seen: int) -> StreamOutcome:
        """Fim do laço de leitura: parada pedida, queda depois de transmitir, ou nunca transmitiu."""
        if not self._is_running:
            return StreamOutcome.STOPPED
        if self._switch_requested:
            return StreamOutcome.RESTART
        return StreamOutcome.ENDED if frames_seen > 0 else StreamOutcome.NOT_CONNECTED

    def _run_test_pattern_stream(self):
        """
        Gera um fluxo de vídeo de teste com movimentação e overlay dinâmico.
        Garante que a UI funcione perfeitamente com feedback visual mesmo sem câmera física ligada.
        """
        self.state_changed.emit(ConnectionState.STREAMING, "Sinal de Demonstração (Ativo)")
        self.stats.codec = "H.264 (Simulado)"
        self.stats.transport_mode = "TCP (Local)"
        self.stats.hw_accel_active = True
        w, h = 640, 360
        self.stats.width = w
        self.stats.height = h

        target_fps = 25.0
        frame_interval = 1.0 / target_fps
        t = 0.0

        while self._is_running:
            start_loop = time.time()
            t += 0.05

            # Gerar fundo moderno com gradiente e formas em movimento
            grid = np.zeros((h, w, 3), dtype=np.uint8) if HAS_NUMPY else None
            if grid is not None:
                r_val = int(18 + 8 * math.sin(t * 0.5))
                g_val = int(24 + 10 * math.cos(t * 0.3))
                b_val = int(36 + 12 * math.sin(t * 0.4))
                grid[:, :] = [r_val, g_val, b_val]

                # Desenhar grid sutil de linhas de segurança
                grid[::40, :, :] = [r_val + 15, g_val + 15, b_val + 20]
                grid[:, ::40, :] = [r_val + 15, g_val + 15, b_val + 20]

                # Desenhar elemento em movimento (simulação de objeto / motion tracking)
                obj_x = int((w / 2) + (w / 3) * math.sin(t))
                obj_y = int((h / 2) + (h / 4) * math.cos(t * 1.5))
                
                x1, x2 = max(0, obj_x - 30), min(w, obj_x + 30)
                y1, y2 = max(0, obj_y - 20), min(h, obj_y + 20)
                grid[y1:y2, x1:x2] = [40, 160, 220]  # Caixa azul ciano de detecção

            self._bytes_count += 3500  # ~700 kbps estimado
            latency = 18.0 + 4.0 * math.sin(t)

            if grid is not None:
                self._process_and_emit_frame(grid, w, h, latency)

            elapsed = time.time() - start_loop
            sleep_time = max(0.001, frame_interval - elapsed)
            time.sleep(sleep_time)

    def _process_and_emit_frame(self, rgb_array: Any, width: int, height: int, latency_ms: float):
        """Converte o array RGB para QImage, calcula QoS (Jitter) e avalia detecção de movimento."""
        # Salvar snapshot se solicitado
        if self._request_snapshot:
            try:
                from PIL import Image
                img = Image.fromarray(rgb_array)
                img.save(self._request_snapshot)
                self.snapshot_saved.emit(self._request_snapshot)
            except Exception as e:
                logger.error(f"Erro salvando snapshot: {e}")
            finally:
                self._request_snapshot = None

        # Garantir array contíguo em memória para QImage estável
        if HAS_NUMPY and isinstance(rgb_array, np.ndarray) and not rgb_array.flags["C_CONTIGUOUS"]:
            rgb_array = np.ascontiguousarray(rgb_array)

        # Detecção de Movimento Leve (Edge-AI / Motion Tracking)
        self._evaluate_motion(rgb_array, width, height)

        # Criar QImage para a UI Qt, já reduzida ao tamanho de exibição (Issue #25): o
        # redimensionamento sai da thread da interface e o frame entregue ocupa menos memória
        bytes_per_line = 3 * width
        q_img = QImage(
            rgb_array.data,
            width,
            height,
            bytes_per_line,
            QImage.Format.Format_RGB888
        )
        target = self._target_size
        if target and (width > target[0] or height > target[1]):
            q_img = q_img.scaled(
                target[0], target[1],
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            q_img = q_img.copy()  # Desvincula do buffer do numpy

        self._publish_frame(q_img)

        # Atualizar estatísticas de desempenho e QoS
        self._frame_count += 1
        now = time.time()
        time_diff = now - self._last_stats_calc

        # Calcular jitter de rede (variação do intervalo esperado entre frames)
        fps_target = max(1.0, self.stats.fps or 25.0)
        expected_interval_ms = 1000.0 / fps_target
        jitter_instant = abs(latency_ms - expected_interval_ms)
        self.stats.jitter_ms = round(0.85 * self.stats.jitter_ms + 0.15 * jitter_instant, 1)

        if time_diff >= 0.5:
            fps = self._frame_count / time_diff
            bitrate_kbps = (self._bytes_count * 8) / (time_diff * 1000)
            
            self.stats.fps = round(fps, 1)
            self.stats.bitrate_kbps = round(bitrate_kbps, 0)
            self.stats.latency_ms = round(latency_ms, 1)
            self.stats.frames_received += self._frame_count
            self.stats.motion_detected = self._motion_active

            self.stats_updated.emit(self.stats)

            self._frame_count = 0
            self._bytes_count = 0
            self._last_stats_calc = now

    def _evaluate_motion(self, rgb_array: Any, width: int, height: int):
        """Avalia variação entre frames em sub-amostragem (64x36) para detecção de movimento eficiente."""
        if not HAS_NUMPY or rgb_array is None:
            return
        if not self.motion_detection:
            if self._motion_active:
                self._motion_active = False
                self._motion_cooldown = 0
                self.motion_detected.emit(self.camera.id, False)
            self._prev_sample_matrix = None
            return

        self._motion_eval_counter += 1
        if self._motion_eval_counter % 3 != 0:
            return

        try:
            # Sub-amostragem rápida por slicing
            step_y = max(1, height // 36)
            step_x = max(1, width // 64)
            sample_gray = rgb_array[::step_y, ::step_x, 0]  # Canal R como aproximação de luminância

            if self._prev_sample_matrix is not None and self._prev_sample_matrix.shape == sample_gray.shape:
                diff = np.abs(sample_gray.astype(np.int16) - self._prev_sample_matrix.astype(np.int16))
                avg_diff = float(np.mean(diff))

                # Limiar de sensibilidade de movimento (~8.0 em escala de 0-255)
                is_motion = avg_diff > 8.0

                if is_motion:
                    self._motion_cooldown = 15  # Manter ativo por ~15 ciclos
                    if not self._motion_active:
                        self._motion_active = True
                        self.motion_detected.emit(self.camera.id, True)
                else:
                    if self._motion_cooldown > 0:
                        self._motion_cooldown -= 1
                    elif self._motion_active:
                        self._motion_active = False
                        self.motion_detected.emit(self.camera.id, False)

            self._prev_sample_matrix = sample_gray
        except Exception:
            pass
