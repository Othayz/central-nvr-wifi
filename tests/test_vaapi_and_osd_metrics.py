#!/usr/bin/env python3
"""
Testes da Issue #31: decodificação VA-API real via PyAV HWAccel (Host Delegation)
e eliminação de métricas/estados fabricados no OSD de cada Dispositivo Cadastrado.
"""
import errno
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

import central_nvr.core.stream_worker as sw
from central_nvr.core.camera import CameraDevice, ConnectionState, StreamStats
from central_nvr.core.config import ConfigManager
from central_nvr.core.stream_worker import StreamOutcome, StreamWorker, VAAPI_DEVICE_PATH, VideoCodec

try:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QMessageBox
    from central_nvr.core.threads import drain_retired_threads
    from central_nvr.ui.camera_view import CameraViewWidget, ViewOptions
    from central_nvr.ui.main_window import MainWindow
    HAS_PYSIDE6 = True
except ImportError:
    QCoreApplication = None
    QEvent = None
    QApplication = None
    QMessageBox = None
    drain_retired_threads = None
    CameraViewWidget = None
    ViewOptions = None
    MainWindow = None
    HAS_PYSIDE6 = False


def _camera(manufacturer: str = "Genérico", rtsp_url: str = "") -> CameraDevice:
    return CameraDevice(
        id="cam-31",
        name="Fachada",
        ip="192.0.2.31",
        rtsp_path="/onvif1",
        rtsp_url=rtsp_url,
        manufacturer=manufacturer,
    )


def _fourcc(code: str) -> int:
    return (
        ord(code[0])
        | (ord(code[1]) << 8)
        | (ord(code[2]) << 16)
        | (ord(code[3]) << 24)
    )


class FakeFrame:
    def to_ndarray(self, format="rgb24"):
        return np.zeros((72, 128, 3), dtype=np.uint8)


class FakePacket:
    def __init__(self, size=1500, fail=False):
        self.size = size
        self.is_keyframe = True
        self._fail = fail

    def decode(self):
        if self._fail:
            raise ValueError("pacote corrompido")
        return [FakeFrame()]


class FakeStream:
    type = "video"
    thread_type = "SLICE"

    def __init__(self, codec_name="hevc", is_hwaccel=False):
        self.codec_context = types.SimpleNamespace(name=codec_name, is_hwaccel=is_hwaccel)


class FakeContainer:
    def __init__(self, packets, codec_name="hevc", is_hwaccel=False):
        self.streams = [FakeStream(codec_name=codec_name, is_hwaccel=is_hwaccel)]
        self._packets = packets
        self.closed = False

    def demux(self, stream):
        yield from self._packets

    def close(self):
        self.closed = True


class BuildPyavHwaccelTest(unittest.TestCase):
    def _fake_av(self, devices, hwaccel_cls=None):
        created = []

        def default_hwaccel(device_type, device=None, allow_software_fallback=True):
            obj = types.SimpleNamespace(
                device_type=device_type,
                device=device,
                allow_software_fallback=allow_software_fallback,
            )
            created.append(obj)
            return obj

        hwaccel_mod = types.SimpleNamespace(
            hwdevices_available=lambda: list(devices),
            HWAccel=hwaccel_cls or default_hwaccel,
        )
        fake_av = types.SimpleNamespace(codec=types.SimpleNamespace(hwaccel=hwaccel_mod))
        return fake_av, created

    def test_cpu_mode_returns_none(self):
        fake_av, _ = self._fake_av(["vaapi", "drm", "cuda"])
        worker = StreamWorker(camera=_camera(), hw_accel="cpu")
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av), \
                patch.object(sw, "is_vaapi_supported", return_value=True):
            self.assertIsNone(worker._build_pyav_hwaccel())

    def test_vaapi_and_auto_prefer_vaapi_device_when_available(self):
        for mode in ("vaapi", "auto"):
            fake_av, _ = self._fake_av(["drm", "vaapi"])
            worker = StreamWorker(camera=_camera(), hw_accel=mode)
            with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av), \
                    patch.object(sw, "is_vaapi_supported", return_value=True):
                cfg = worker._build_pyav_hwaccel()
            self.assertIsNotNone(cfg, mode)
            self.assertEqual(cfg.device_type, "vaapi")
            self.assertEqual(cfg.device, VAAPI_DEVICE_PATH)
            self.assertFalse(cfg.allow_software_fallback)
            self.assertEqual(worker._pyav_hwaccel_backend, "VA-API")

    def test_vaapi_does_not_fallback_to_drm_and_auto_falls_back_to_cuda_only_when_supported(self):
        # Sem "vaapi" em hwdevices_available(), modo vaapi não deve usar "drm" (não ativa libva x86_64)
        fake_av_drm_only, _ = self._fake_av(["drm"])
        worker_vaapi = StreamWorker(camera=_camera(), hw_accel="vaapi")
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av_drm_only), \
                patch.object(sw, "is_vaapi_supported", return_value=True):
            self.assertIsNone(worker_vaapi._build_pyav_hwaccel())

        # Modo auto NÃO tenta CUDA em host sem dispositivo NVIDIA (evita av.open duplicado)
        fake_av_cuda, _ = self._fake_av(["drm", "cuda"])
        worker_auto_no_nvidia = StreamWorker(camera=_camera(), hw_accel="auto")
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av_cuda), \
                patch.object(sw, "is_vaapi_supported", return_value=True), \
                patch.object(sw, "is_cuda_supported", return_value=False):
            self.assertIsNone(worker_auto_no_nvidia._build_pyav_hwaccel())

        # Modo auto cai para "cuda" quando VA-API não está disponível no PyAV e há GPU NVIDIA
        worker_auto = StreamWorker(camera=_camera(), hw_accel="auto")
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av_cuda), \
                patch.object(sw, "is_vaapi_supported", return_value=True), \
                patch.object(sw, "is_cuda_supported", return_value=True):
            cfg = worker_auto._build_pyav_hwaccel()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.device_type, "cuda")
        self.assertIsNone(cfg.device)
        self.assertFalse(cfg.allow_software_fallback)
        self.assertEqual(worker_auto._pyav_hwaccel_backend, "CUDA")

    def test_real_pyav_hwdevices_available_integration(self):
        if not sw.HAS_AV:
            self.skipTest("PyAV não disponível neste ambiente")
        hwaccel_mod = getattr(getattr(sw.av, "codec", None), "hwaccel", None)
        if hwaccel_mod is None:
            self.skipTest("av.codec.hwaccel não disponível nesta versão do PyAV")
        available = set(hwaccel_mod.hwdevices_available())

        # Sem assumir atributo .device_type (cdef privado no Cython do PyAV), valida instância real e rótulo
        worker = StreamWorker(camera=_camera(), hw_accel="auto")
        with patch.object(sw, "is_cuda_supported", return_value="cuda" in available):
            cfg = worker._build_pyav_hwaccel()
        if cfg is not None:
            self.assertIsInstance(cfg, hwaccel_mod.HWAccel)
            self.assertFalse(cfg.allow_software_fallback)
            label = worker._hwaccel_label(cfg, worker._pyav_hwaccel_backend)
            self.assertIn(label, {"VA-API", "CUDA"})
            self.assertNotEqual(label, "DRM")

    def test_vaapi_returns_none_when_dri_device_unsupported(self):
        fake_av, _ = self._fake_av(["vaapi", "drm"])
        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av), \
                patch.object(sw, "is_vaapi_supported", return_value=False):
            self.assertIsNone(worker._build_pyav_hwaccel())

    def test_cuda_mode_returns_cuda_hwaccel_only_when_available_and_supported(self):
        fake_av, _ = self._fake_av(["cuda", "drm"])
        worker = StreamWorker(camera=_camera(), hw_accel="cuda")
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av), \
                patch.object(sw, "is_vaapi_supported", return_value=False), \
                patch.object(sw, "is_cuda_supported", return_value=False):
            self.assertIsNone(worker._build_pyav_hwaccel())

        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av), \
                patch.object(sw, "is_vaapi_supported", return_value=False), \
                patch.object(sw, "is_cuda_supported", return_value=True):
            cfg = worker._build_pyav_hwaccel()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.device_type, "cuda")
        self.assertIsNone(cfg.device)
        self.assertFalse(cfg.allow_software_fallback)
        self.assertEqual(worker._pyav_hwaccel_backend, "CUDA")

    def test_unsupported_or_missing_hwaccel_api_returns_none(self):
        worker = StreamWorker(camera=_camera(), hw_accel="cuda")
        fake_av, _ = self._fake_av(["drm"])
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av), \
                patch.object(sw, "is_cuda_supported", return_value=True):
            self.assertIsNone(worker._build_pyav_hwaccel())

        with patch.object(sw, "HAS_AV", False):
            self.assertIsNone(worker._build_pyav_hwaccel())

        broken_av = types.SimpleNamespace(codec=types.SimpleNamespace(hwaccel=None))
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", broken_av):
            self.assertIsNone(worker._build_pyav_hwaccel())


@unittest.skipUnless(HAS_NUMPY and sw.HAS_AV, "PyAV/NumPy não disponíveis neste ambiente")
class PyavStreamHwaccelAndDecodeTest(unittest.TestCase):
    def test_passes_hwaccel_to_av_open_skips_auto_threading_and_reflects_backend(self):
        sentinel_hw = types.SimpleNamespace(device_type="vaapi")
        open_calls = []
        created_containers = []

        def fake_open(url, mode="r", options=None, timeout=5.0, hwaccel=None):
            open_calls.append(hwaccel)
            c = FakeContainer([FakePacket()], codec_name="hevc", is_hwaccel=True)
            created_containers.append(c)
            return c

        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker._is_running = True
        with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(sw.av, "open", side_effect=fake_open):
            outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.ENDED)
        self.assertEqual(open_calls, [sentinel_hw])
        self.assertTrue(worker.stats.hw_accel_active)
        self.assertEqual(worker.stats.hw_accel_backend, "VA-API")
        self.assertEqual(worker.stats.codec, VideoCodec.H265_HEVC.value)
        # Com HWAccel ativo, thread_type="AUTO" não deve ser forçado
        self.assertNotEqual(created_containers[0].streams[0].thread_type, "AUTO")

    def test_software_mode_enables_auto_thread_type(self):
        c = FakeContainer([FakePacket()], codec_name="h264", is_hwaccel=False)
        worker = StreamWorker(camera=_camera(), hw_accel="cpu")
        worker._is_running = True
        with patch.object(sw.av, "open", return_value=c):
            outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")
        self.assertEqual(outcome, StreamOutcome.ENDED)
        self.assertEqual(c.streams[0].thread_type, "AUTO")

    def test_real_pyav_hw_errors_fallback_while_network_and_auth_errors_do_not_retry(self):
        sentinel_hw = types.SimpleNamespace(device_type="vaapi")

        # av.error.UnknownError, ExternalError e av.error.OSError (sem URL de rede) devem cair para software
        hw_errors = []
        unknown_cls = getattr(getattr(sw.av, "error", None), "UnknownError", None)
        if isinstance(unknown_cls, type):
            hw_errors.append(unknown_cls(1313558101, "Unknown error occurred"))
        external_cls = getattr(getattr(sw.av, "error", None), "ExternalError", None)
        if isinstance(external_cls, type):
            hw_errors.append(external_cls(542398533, "Generic error in an external library"))
        av_os_cls = getattr(getattr(sw.av, "error", None), "OSError", None)
        if isinstance(av_os_cls, type):
            hw_errors.append(av_os_cls(errno.EIO, "Input/output error"))

        for exc in hw_errors:
            calls = []

            def open_with_hw_error(url, mode="r", options=None, timeout=5.0, **kwargs):
                calls.append(kwargs.get("hwaccel"))
                if "hwaccel" in kwargs:
                    raise exc
                return FakeContainer([FakePacket()], codec_name="h264", is_hwaccel=False)

            worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
            worker._is_running = True
            with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                    patch.object(sw.av, "open", side_effect=open_with_hw_error):
                outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")
            self.assertEqual(outcome, StreamOutcome.ENDED, repr(exc))
            self.assertEqual(calls, [sentinel_hw, None], repr(exc))

        # Erros de rede, HTTP, UnknownError/OSError de URL RTSP e ValueError genérico NÃO devem disparar segundo av.open
        non_hw_errors = [
            ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"),
            TimeoutError("timed out"),
        ]
        if isinstance(unknown_cls, type):
            non_hw_errors.append(unknown_cls(1313558101, "Unknown error occurred", "rtsp://192.0.2.31/onvif1"))
        if isinstance(av_os_cls, type):
            non_hw_errors.append(av_os_cls(errno.EIO, "Input/output error", "rtsp://192.0.2.31/onvif1"))
        av_val_cls = getattr(getattr(sw.av, "error", None), "ValueError", None)
        if isinstance(av_val_cls, type):
            non_hw_errors.append(av_val_cls(errno.EINVAL, "Invalid argument"))
        http_404_cls = getattr(getattr(sw.av, "error", None), "HTTPNotFoundError", None)
        if isinstance(http_404_cls, type):
            non_hw_errors.append(http_404_cls(404, "Not Found"))

        for exc in non_hw_errors:
            calls = []

            def open_with_net_error(url, mode="r", options=None, timeout=5.0, **kwargs):
                calls.append(kwargs.get("hwaccel"))
                raise exc

            worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
            worker._is_running = True
            with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                    patch.object(sw.av, "open", side_effect=open_with_net_error):
                outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")
            self.assertEqual(outcome, StreamOutcome.NOT_CONNECTED, repr(exc))
            self.assertEqual(calls, [sentinel_hw], repr(exc))

        # HTTPUnauthorizedError retorna AUTH_FAILED na primeira tentativa sem retry
        auth_calls = []

        def open_with_auth(url, mode="r", options=None, timeout=5.0, **kwargs):
            auth_calls.append(kwargs.get("hwaccel"))
            raise sw.av.HTTPUnauthorizedError(401, "Unauthorized")

        worker_auth = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker_auth._is_running = True
        with patch.object(worker_auth, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(sw.av, "open", side_effect=open_with_auth):
            outcome = worker_auth._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")
        self.assertEqual(outcome, StreamOutcome.AUTH_FAILED)
        self.assertEqual(auth_calls, [sentinel_hw])

    def test_persistent_decode_failure_on_hw_falls_back_to_software_and_limits_sw_errors(self):
        sentinel_hw = types.SimpleNamespace(device_type="vaapi")
        open_calls = []

        def open_failing_hw_decode(url, mode="r", options=None, timeout=5.0, **kwargs):
            hw = kwargs.get("hwaccel")
            open_calls.append(hw)
            if hw is not None:
                return FakeContainer([FakePacket(fail=True) for _ in range(10)], codec_name="hevc", is_hwaccel=True)
            return FakeContainer([FakePacket(fail=False)], codec_name="hevc", is_hwaccel=False)

        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker._is_running = True
        with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(sw.av, "open", side_effect=open_failing_hw_decode):
            outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.ENDED)
        self.assertEqual(open_calls, [sentinel_hw, None])
        self.assertFalse(worker.stats.hw_accel_active)
        self.assertEqual(worker.stats.dropped_frames, StreamWorker.MAX_HW_STARTUP_DECODE_ERRORS)

        # Em software, excesso de erros consecutivos encerra o laço em vez de ficar preso
        worker_sw = StreamWorker(camera=_camera(), hw_accel="cpu")
        worker_sw._is_running = True
        many_bad = FakeContainer([FakePacket(fail=True) for _ in range(50)], codec_name="h264")
        with patch.object(sw.av, "open", return_value=many_bad):
            outcome_sw = worker_sw._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")
        self.assertEqual(outcome_sw, StreamOutcome.NOT_CONNECTED)
        self.assertEqual(worker_sw.stats.dropped_frames, StreamWorker.MAX_CONSECUTIVE_DECODE_ERRORS)

    def test_stats_updated_emits_copy_not_shared_worker_stats_instance_and_reset_clears_backend(self):
        worker = StreamWorker(camera=_camera(), hw_accel="cpu")
        worker._is_running = True
        worker._last_stats_calc = 0.0
        emitted = []
        worker.stats_updated.connect(emitted.append)

        with patch.object(sw.av, "open", return_value=FakeContainer([FakePacket()], codec_name="h264")):
            worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")

        self.assertGreaterEqual(len(emitted), 1)
        for item in emitted:
            self.assertIsNot(item, worker.stats)

        # _reset_stats também deve limpar hw_accel_backend e emitir cópia isolada
        worker.stats.hw_accel_active = True
        worker.stats.hw_accel_backend = "CUDA"
        emitted.clear()
        worker._reset_stats()
        self.assertEqual(len(emitted), 1)
        self.assertIsNot(emitted[0], worker.stats)
        self.assertFalse(emitted[0].hw_accel_active)
        self.assertEqual(emitted[0].hw_accel_backend, "")

    def test_real_hwaccel_object_reports_cuda_backend_in_auto_mode_without_device_type_attr(self):
        hwaccel_mod = getattr(getattr(sw.av, "codec", None), "hwaccel", None)
        if hwaccel_mod is None or "cuda" not in hwaccel_mod.hwdevices_available():
            self.skipTest("PyAV HWAccel CUDA não disponível neste ambiente")

        worker = StreamWorker(camera=_camera(), hw_accel="auto")
        worker._is_running = True
        with patch.object(sw, "is_vaapi_supported", return_value=False), \
                patch.object(sw, "is_cuda_supported", return_value=True), \
                patch.object(
                    sw.av,
                    "open",
                    return_value=FakeContainer([FakePacket()], codec_name="hevc", is_hwaccel=True),
                ):
            outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.ENDED)
        self.assertTrue(worker.stats.hw_accel_active)
        self.assertEqual(worker.stats.hw_accel_backend, "CUDA")

    def test_hwaccel_active_is_false_when_decoder_falls_back_to_software_and_enables_auto_threads(self):
        sentinel_hw = object()
        container = FakeContainer([FakePacket()], codec_name="h264", is_hwaccel=False)
        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker._is_running = True
        with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(sw.av, "open", return_value=container):
            outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.ENDED)
        self.assertFalse(worker.stats.hw_accel_active)
        self.assertEqual(container.streams[0].thread_type, "AUTO")

    def test_type_error_fallback_retries_without_hwaccel_kwarg(self):
        sentinel_hw = object()
        calls = []

        def legacy_mock_open(url, mode="r", options=None, timeout=5.0):
            calls.append((url, mode, timeout))
            return FakeContainer([FakePacket()], codec_name="h264", is_hwaccel=True)

        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker._is_running = True
        with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(sw.av, "open", side_effect=legacy_mock_open):
            outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.ENDED)
        self.assertEqual(len(calls), 1)
        self.assertFalse(worker.stats.hw_accel_active)

    def test_hardware_init_error_falls_back_to_software_open(self):
        sentinel_hw = object()
        calls = []

        def failing_hw_open(url, mode="r", options=None, timeout=5.0, **kwargs):
            calls.append(kwargs.get("hwaccel"))
            if "hwaccel" in kwargs:
                raise RuntimeError("VAAPI device initialization failed")
            return FakeContainer([FakePacket()], codec_name="h264", is_hwaccel=False)

        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker._is_running = True
        with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(sw.av, "open", side_effect=failing_hw_open):
            outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.ENDED)
        self.assertEqual(calls, [sentinel_hw, None])
        self.assertFalse(worker.stats.hw_accel_active)

    def test_corrupt_packet_increments_dropped_frames_and_continues_stream(self):
        packets = [FakePacket(fail=True), FakePacket(fail=False), FakePacket(fail=True), FakePacket(fail=False)]
        worker = StreamWorker(camera=_camera(), hw_accel="cpu")
        worker._is_running = True
        emitted_frames = []

        def record_frame(rgb, w, h, delta_ms):
            emitted_frames.append((w, h))

        with patch.object(sw.av, "open", return_value=FakeContainer(packets, codec_name="h264")), \
                patch.object(worker, "_process_and_emit_frame", side_effect=record_frame):
            outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.ENDED)
        self.assertEqual(worker.stats.dropped_frames, 2)
        self.assertEqual(len(emitted_frames), 2)


@unittest.skipUnless(HAS_NUMPY, "NumPy não disponível neste ambiente")
class OpenCVAndDemoTruthfulnessTest(unittest.TestCase):
    def _make_fake_cv2(self, *, with_hw_props=True, hw_get_val=0, fourcc_val=0):
        frame_bgr = np.zeros((72, 128, 3), dtype=np.uint8)
        cap = MagicMock()
        cap.isOpened.return_value = True
        cap.read.side_effect = [(True, frame_bgr), (False, None)]

        def cap_get(prop):
            if with_hw_props and prop == 50:
                return hw_get_val
            if prop == 6:
                return fourcc_val
            return 0

        cap.get.side_effect = cap_get

        fake_cv2 = types.SimpleNamespace(
            CAP_FFMPEG=1900,
            CAP_PROP_BUFFERSIZE=38,
            CAP_PROP_FOURCC=6,
            COLOR_BGR2RGB=4,
            VideoCapture=MagicMock(return_value=cap),
            cvtColor=lambda frame, code: frame,
        )
        if with_hw_props:
            fake_cv2.CAP_PROP_HW_ACCELERATION = 50
            fake_cv2.VIDEO_ACCELERATION_VAAPI = 4
        return fake_cv2, cap

    def test_opencv_does_not_fabricate_hw_accel_bitrate_or_yoosee_codec(self):
        fake_cv2, _ = self._make_fake_cv2(with_hw_props=False, fourcc_val=0)
        worker = StreamWorker(camera=_camera(manufacturer="Yoosee"), hw_accel="vaapi")
        worker._is_running = True
        worker._last_stats_calc = 0.0  # força recálculo no primeiro frame

        with patch.object(sw, "cv2", fake_cv2), patch.object(sw, "is_vaapi_supported", return_value=True):
            outcome = worker._run_opencv_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.ENDED)
        self.assertFalse(worker.stats.hw_accel_active)
        self.assertEqual(worker.stats.bitrate_kbps, 0.0)
        self.assertEqual(worker._bytes_count, 0)
        self.assertEqual(worker.stats.codec, VideoCodec.UNKNOWN.value)

    def test_opencv_marks_hw_accel_active_only_when_confirmed_by_cap_get(self):
        fake_cv2_off, _ = self._make_fake_cv2(with_hw_props=True, hw_get_val=0, fourcc_val=_fourcc("H264"))
        worker_off = StreamWorker(camera=_camera(manufacturer="Yoosee"), hw_accel="vaapi")
        worker_off._is_running = True
        with patch.object(sw, "cv2", fake_cv2_off), patch.object(sw, "is_vaapi_supported", return_value=True):
            worker_off._run_opencv_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")
        self.assertFalse(worker_off.stats.hw_accel_active)
        self.assertEqual(worker_off.stats.codec, VideoCodec.H264.value)
        # CAP_PROP_HW_ACCELERATION deve ser passado no construtor de VideoCapture
        ctor_args = fake_cv2_off.VideoCapture.call_args[0]
        self.assertGreaterEqual(len(ctor_args), 3)
        self.assertIn(fake_cv2_off.CAP_PROP_HW_ACCELERATION, ctor_args[2])
        self.assertIn(fake_cv2_off.VIDEO_ACCELERATION_VAAPI, ctor_args[2])

        fake_cv2_on, _ = self._make_fake_cv2(with_hw_props=True, hw_get_val=4, fourcc_val=_fourcc("HEVC"))
        worker_on = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker_on._is_running = True
        with patch.object(sw, "cv2", fake_cv2_on), patch.object(sw, "is_vaapi_supported", return_value=True):
            worker_on._run_opencv_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")
        self.assertTrue(worker_on.stats.hw_accel_active)
        self.assertEqual(worker_on.stats.hw_accel_backend, "VA-API")
        self.assertEqual(worker_on.stats.codec, VideoCodec.H265_HEVC.value)

    def test_opencv_detects_codec_from_explicit_url_hint(self):
        fake_cv2, _ = self._make_fake_cv2(with_hw_props=True, hw_get_val=0, fourcc_val=0)
        worker = StreamWorker(camera=_camera(), hw_accel="cpu")
        worker._is_running = True
        with patch.object(sw, "cv2", fake_cv2):
            worker._run_opencv_stream("rtsp://192.0.2.31:554/h264Preview_01_main", transport="tcp")
        self.assertEqual(worker.stats.codec, VideoCodec.H264.value)

    def test_opencv_ignores_264_and_265_in_credentials_host_or_port(self):
        fake_cv2, _ = self._make_fake_cv2(with_hw_props=True, hw_get_val=0, fourcc_val=0)
        worker = StreamWorker(camera=_camera(), hw_accel="cpu")
        worker._is_running = True
        with patch.object(sw, "cv2", fake_cv2):
            worker._run_opencv_stream("rtsp://admin:pass264@192.0.2.31:8265/live/ch0", transport="tcp")
        self.assertEqual(worker.stats.codec, VideoCodec.UNKNOWN.value)

    def test_demo_stream_does_not_fabricate_hw_accel_bitrate_or_jitter(self):
        worker = StreamWorker(camera=_camera(rtsp_url="demo://"), hw_accel="vaapi")
        worker._is_running = True
        worker._last_stats_calc = 0.0

        def stop_after_first_frame(rgb, w, h, latency):
            StreamWorker._process_and_emit_frame(worker, rgb, w, h, latency)
            worker._is_running = False

        with patch.object(worker, "_process_and_emit_frame", side_effect=stop_after_first_frame):
            worker._run_test_pattern_stream()

        self.assertFalse(worker.stats.hw_accel_active)
        self.assertEqual(worker.stats.bitrate_kbps, 0.0)
        self.assertEqual(worker.stats.latency_ms, 0.0)
        self.assertEqual(worker.stats.jitter_ms, 0.0)
        self.assertEqual(worker._bytes_count, 0)


@unittest.skipUnless(HAS_PYSIDE6, "PySide6 não disponível neste ambiente")
class CameraViewOsdMetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.patchers = [
            patch.object(sw.av, "open", side_effect=OSError("offline")),
            patch.object(sw, "HAS_CV2", False),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        drain_retired_threads(5000)
        for p in self.patchers:
            p.stop()

    def _make_view(self, hw_accel="vaapi", options=None):
        view = CameraViewWidget(_camera(), hw_accel=hw_accel, options=options or ViewOptions())
        self.addCleanup(
            lambda: (
                view.dispose(),
                drain_retired_threads(5000),
                view.deleteLater(),
                QCoreApplication.sendPostedEvents(view, QEvent.Type.DeferredDelete),
            )
        )
        return view

    def test_hw_accel_badge_starts_as_cpu_and_tracks_real_decoder_state(self):
        view = self._make_view(hw_accel="vaapi")
        self.assertEqual(view.lbl_hw_accel.text(), "CPU")

        view._on_state_changed(ConnectionState.STREAMING, "Ao Vivo")
        self.assertEqual(view.lbl_hw_accel.text(), "CPU")

        view._on_stats_updated(StreamStats(fps=25.0, bitrate_kbps=1200.0, codec="H.265 / HEVC", hw_accel_active=True))
        self.assertEqual(view.lbl_hw_accel.text(), "VA-API")

        view._on_stats_updated(StreamStats(fps=25.0, bitrate_kbps=1200.0, codec="H.265 / HEVC", hw_accel_active=False))
        self.assertEqual(view.lbl_hw_accel.text(), "CPU")

        view._on_stats_updated(StreamStats(fps=25.0, bitrate_kbps=1200.0, codec="H.265 / HEVC", hw_accel_active=True))
        self.assertEqual(view.lbl_hw_accel.text(), "VA-API")

        # Watchdog de perda de sinal (SEM SINAL) deve voltar o selo para CPU
        view._last_frame_monotonic = 0.0
        view._check_stall(now=view.STALL_TIMEOUT_SEC + 1.0)
        self.assertEqual(view.lbl_hw_accel.text(), "CPU")

        view._on_state_changed(ConnectionState.DISCONNECTED, "Desconectado")
        self.assertEqual(view.lbl_hw_accel.text(), "CPU")

    def test_hw_accel_badge_shows_cuda_and_does_not_mutate_shared_stats(self):
        view = self._make_view(hw_accel="cuda")
        view._on_state_changed(ConnectionState.STREAMING, "Ao Vivo")

        shared_stats = StreamStats(
            fps=25.0,
            bitrate_kbps=1500.0,
            codec="H.265 / HEVC",
            hw_accel_active=True,
            hw_accel_backend="CUDA",
        )
        view._on_stats_updated(shared_stats)
        self.assertEqual(view.lbl_hw_accel.text(), "CUDA")

        # Mudança de estado altera o selo para CPU sem mutar shared_stats.hw_accel_active
        view._on_state_changed(ConnectionState.CONNECTING, "Conectando")
        self.assertEqual(view.lbl_hw_accel.text(), "CPU")
        self.assertTrue(shared_stats.hw_accel_active)

    def test_main_window_status_bar_reflects_real_hw_accel_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            cfg_dir = base / "config"
            data_dir = base / "data"
            cfg_dir.mkdir(parents=True)
            data_dir.mkdir(parents=True)

            with patch("central_nvr.core.config.get_config_dir", return_value=cfg_dir), \
                    patch("central_nvr.core.config.get_data_dir", return_value=data_dir), \
                    patch("central_nvr.core.config.is_keyring_available", return_value=False), \
                    patch("central_nvr.ui.playback_view.get_data_dir", return_value=data_dir):
                cfg = ConfigManager()
                cfg.set("periodic_update_check", False)
                cfg.set("hw_accel", "vaapi")
                cfg.add_or_update_device(_camera().to_dict())

                window = MainWindow(config_mgr=cfg)
                try:
                    self.app.processEvents()
                    self.assertIn("Configurado", window.lbl_sb_hw.text())
                    self.assertNotIn("(Ativo)", window.lbl_sb_hw.text())

                    view = window.camera_grid.camera_views["cam-31"]
                    view._on_state_changed(ConnectionState.STREAMING, "Ao Vivo")
                    self.assertIn("Fallback CPU", window.lbl_sb_hw.text())

                    view._on_stats_updated(
                        StreamStats(fps=25.0, hw_accel_active=True, hw_accel_backend="VA-API")
                    )
                    self.assertEqual(window.lbl_sb_hw.text(), "Aceleração: VA-API (Ativo)")

                    # Remover a última câmera ativa deve tirar "(Ativo)" da barra de status
                    with patch(
                        "central_nvr.ui.main_window.QMessageBox.question",
                        return_value=QMessageBox.StandardButton.Yes,
                    ):
                        window._remove_camera_by_id("cam-31")
                    self.assertEqual(window.lbl_sb_hw.text(), "Aceleração: VAAPI (Configurado)")

                    cfg.set("hw_accel", "cpu")
                    window._update_hw_status_label()
                    self.assertEqual(window.lbl_sb_hw.text(), "Decodificação: CPU (Software)")
                finally:
                    window.close()
                    self.app.processEvents()
                    self.assertTrue(drain_retired_threads(5000))
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    self.app.processEvents()

    def test_render_metrics_omits_kbps_when_bitrate_not_measured(self):
        view = self._make_view(
            options=ViewOptions(show_osd_fps=True, show_osd_bitrate=True, show_osd_timestamp=False)
        )
        view._on_stats_updated(StreamStats(fps=20.0, bitrate_kbps=0.0, codec="H.264"))
        self.assertNotIn("kbps", view.lbl_metrics.text())
        self.assertIn("20.0 FPS", view.lbl_metrics.text())

        view._on_stats_updated(StreamStats(fps=20.0, bitrate_kbps=850.0, codec="H.264"))
        self.assertIn("850 kbps", view.lbl_metrics.text())


if __name__ == "__main__":
    unittest.main()
