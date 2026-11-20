#!/usr/bin/env python3
"""
Testes da Issue #31: decodificação VA-API real via PyAV HWAccel (Host Delegation)
e eliminação de métricas/estados fabricados no OSD de cada Dispositivo Cadastrado.
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

import central_nvr.core.stream_worker as sw
from central_nvr.core.camera import CameraDevice, ConnectionState, StreamStats
from central_nvr.core.stream_worker import StreamOutcome, StreamWorker, VAAPI_DEVICE_PATH, VideoCodec
from central_nvr.core.threads import drain_retired_threads
from central_nvr.ui.camera_view import CameraViewWidget, ViewOptions


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
            self.assertTrue(cfg.allow_software_fallback)

    def test_vaapi_and_auto_fallback_to_drm_device_via_host_delegation(self):
        for mode in ("vaapi", "auto"):
            fake_av, _ = self._fake_av(["cuda", "drm"])
            worker = StreamWorker(camera=_camera(), hw_accel=mode)
            with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av), \
                    patch.object(sw, "is_vaapi_supported", return_value=True):
                cfg = worker._build_pyav_hwaccel()
            self.assertIsNotNone(cfg, mode)
            self.assertEqual(cfg.device_type, "drm")
            self.assertEqual(cfg.device, VAAPI_DEVICE_PATH)
            self.assertTrue(cfg.allow_software_fallback)

    def test_vaapi_returns_none_when_dri_device_unsupported(self):
        fake_av, _ = self._fake_av(["vaapi", "drm"])
        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av), \
                patch.object(sw, "is_vaapi_supported", return_value=False):
            self.assertIsNone(worker._build_pyav_hwaccel())

    def test_cuda_mode_returns_cuda_hwaccel_when_available(self):
        fake_av, _ = self._fake_av(["cuda", "drm"])
        worker = StreamWorker(camera=_camera(), hw_accel="cuda")
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av), \
                patch.object(sw, "is_vaapi_supported", return_value=False):
            cfg = worker._build_pyav_hwaccel()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.device_type, "cuda")
        self.assertIsNone(cfg.device)
        self.assertTrue(cfg.allow_software_fallback)

    def test_unsupported_or_missing_hwaccel_api_returns_none(self):
        worker = StreamWorker(camera=_camera(), hw_accel="cuda")
        fake_av, _ = self._fake_av(["drm"])
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", fake_av):
            self.assertIsNone(worker._build_pyav_hwaccel())

        with patch.object(sw, "HAS_AV", False):
            self.assertIsNone(worker._build_pyav_hwaccel())

        broken_av = types.SimpleNamespace(codec=types.SimpleNamespace(hwaccel=None))
        with patch.object(sw, "HAS_AV", True), patch.object(sw, "av", broken_av):
            self.assertIsNone(worker._build_pyav_hwaccel())


class PyavStreamHwaccelAndDecodeTest(unittest.TestCase):
    def test_passes_hwaccel_to_av_open_and_reflects_codec_context_is_hwaccel(self):
        sentinel_hw = object()
        open_calls = []

        def fake_open(url, mode="r", options=None, timeout=5.0, hwaccel=None):
            open_calls.append(hwaccel)
            return FakeContainer([FakePacket()], codec_name="hevc", is_hwaccel=True)

        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker._is_running = True
        with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(sw.av, "open", side_effect=fake_open):
            outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.ENDED)
        self.assertEqual(open_calls, [sentinel_hw])
        self.assertTrue(worker.stats.hw_accel_active)
        self.assertEqual(worker.stats.codec, VideoCodec.H265_HEVC.value)

    def test_hwaccel_active_is_false_when_decoder_falls_back_to_software(self):
        sentinel_hw = object()
        worker = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker._is_running = True
        with patch.object(worker, "_build_pyav_hwaccel", return_value=sentinel_hw), \
                patch.object(
                    sw.av,
                    "open",
                    return_value=FakeContainer([FakePacket()], codec_name="h264", is_hwaccel=False),
                ):
            outcome = worker._run_pyav_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")

        self.assertEqual(outcome, StreamOutcome.ENDED)
        self.assertFalse(worker.stats.hw_accel_active)

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

        fake_cv2_on, _ = self._make_fake_cv2(with_hw_props=True, hw_get_val=4, fourcc_val=_fourcc("HEVC"))
        worker_on = StreamWorker(camera=_camera(), hw_accel="vaapi")
        worker_on._is_running = True
        with patch.object(sw, "cv2", fake_cv2_on), patch.object(sw, "is_vaapi_supported", return_value=True):
            worker_on._run_opencv_stream("rtsp://192.0.2.31:554/onvif1", transport="tcp")
        self.assertTrue(worker_on.stats.hw_accel_active)
        self.assertEqual(worker_on.stats.codec, VideoCodec.H265_HEVC.value)

    def test_opencv_detects_codec_from_explicit_url_hint(self):
        fake_cv2, _ = self._make_fake_cv2(with_hw_props=True, hw_get_val=0, fourcc_val=0)
        worker = StreamWorker(camera=_camera(), hw_accel="cpu")
        worker._is_running = True
        with patch.object(sw, "cv2", fake_cv2):
            worker._run_opencv_stream("rtsp://192.0.2.31:554/h264Preview_01_main", transport="tcp")
        self.assertEqual(worker.stats.codec, VideoCodec.H264.value)

    def test_demo_stream_does_not_fabricate_hw_accel_or_bitrate(self):
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
        self.assertEqual(worker._bytes_count, 0)


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

        view._on_state_changed(ConnectionState.DISCONNECTED, "Desconectado")
        self.assertEqual(view.lbl_hw_accel.text(), "CPU")

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
