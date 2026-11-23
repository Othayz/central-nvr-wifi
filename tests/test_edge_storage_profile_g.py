#!/usr/bin/env python3
"""
Testes de ONVIF Profile G (Issue #29):
- Descoberta do serviço Recording Search (`tse`) em GetCapabilities com validação Anti-SSRF;
- Fluxo completo GetRecordingSummary (com fallback `trc`), FindRecordings -> SearchToken ->
  GetRecordingSearchResults -> EndSearch (garantido no `finally`) -> GetReplayUri;
- Consulta assíncrona ao Cartão SD em PlaybackWidget via EdgeStorageWorker sem travar a UI.
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from central_nvr.core.camera import CameraDevice
from central_nvr.core.config import ConfigManager
from central_nvr.core.onvif_client import (
    OnvifClient,
    OnvifFaultError,
    OnvifRedirectError,
    OnvifTimeoutError,
)

try:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from central_nvr.core.threads import drain_retired_threads
    from central_nvr.ui import playback_view
    from central_nvr.ui.main_window import MainWindow
    from central_nvr.ui.playback_view import EdgeStorageWorker, PlaybackWidget
    HAS_PYSIDE6 = True
except ImportError:
    QCoreApplication = None
    QEvent = None
    QApplication = None
    drain_retired_threads = None
    playback_view = None
    MainWindow = None
    EdgeStorageWorker = None
    PlaybackWidget = None
    HAS_PYSIDE6 = False

ENV = '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" {ns}><s:Body>{body}</s:Body></s:Envelope>'
NS = (
    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
    'xmlns:trc="http://www.onvif.org/ver10/recording/wsdl" '
    'xmlns:tse="http://www.onvif.org/ver10/search/wsdl" '
    'xmlns:trp="http://www.onvif.org/ver10/replay/wsdl" '
    'xmlns:tt="http://www.onvif.org/ver10/schema"'
)

CAPS_WITH_SEARCH = ENV.format(
    ns=NS,
    body="""
<tds:GetCapabilitiesResponse><tds:Capabilities>
  <tt:Media><tt:XAddr>http://10.0.0.5/onvif/Media</tt:XAddr></tt:Media>
  <tt:Extension>
    <tt:Search><tt:XAddr>http://10.0.0.5:8080/onvif/Search</tt:XAddr></tt:Search>
    <tt:Replay><tt:XAddr>http://10.0.0.5:8080/onvif/Replay</tt:XAddr></tt:Replay>
  </tt:Extension>
</tds:Capabilities></tds:GetCapabilitiesResponse>""",
)

CAPS_WITH_RELATIVE_SEARCH = ENV.format(
    ns=NS,
    body="""
<tds:GetCapabilitiesResponse><tds:Capabilities>
  <tt:Extension>
    <tt:Search><tt:XAddr>/onvif/custom_search</tt:XAddr></tt:Search>
  </tt:Extension>
</tds:Capabilities></tds:GetCapabilitiesResponse>""",
)

CAPS_WITH_EXTERNAL_SEARCH = ENV.format(
    ns=NS,
    body="""
<tds:GetCapabilitiesResponse><tds:Capabilities>
  <tt:Extension>
    <tt:Search><tt:XAddr>http://evil.example.com/onvif/Search</tt:XAddr></tt:Search>
  </tt:Extension>
</tds:Capabilities></tds:GetCapabilitiesResponse>""",
)

SUMMARY_RESPONSE = ENV.format(
    ns=NS,
    body="""
<tse:GetRecordingSummaryResponse>
  <tse:Summary>
    <tt:DataFrom>2026-10-01T00:00:00Z</tt:DataFrom>
    <tt:DataUntil>2026-10-20T23:59:59Z</tt:DataUntil>
    <tt:NumberRecordings>3</tt:NumberRecordings>
  </tse:Summary>
</tse:GetRecordingSummaryResponse>""",
)

FIND_RECORDINGS_TOKEN_RESPONSE = ENV.format(
    ns=NS,
    body="""
<tse:FindRecordingsResponse>
  <tse:SearchToken>search&amp;tok&lt;1&gt;</tse:SearchToken>
</tse:FindRecordingsResponse>""",
)

SEARCH_RESULTS_RESPONSE = ENV.format(
    ns=NS,
    body="""
<tse:GetRecordingSearchResultsResponse>
  <tse:ResultList>
    <tt:SearchState>Completed</tt:SearchState>
    <tt:RecordingInformation>
      <tt:RecordingToken>rec_001</tt:RecordingToken>
    </tt:RecordingInformation>
    <tt:RecordingInformation>
      <tt:RecordingToken>rec_002</tt:RecordingToken>
    </tt:RecordingInformation>
    <tt:RecordingInformation>
      <tt:RecordingToken>rec_001</tt:RecordingToken>
    </tt:RecordingInformation>
  </tse:ResultList>
</tse:GetRecordingSearchResultsResponse>""",
)

END_SEARCH_RESPONSE = ENV.format(ns=NS, body="<tse:EndSearchResponse/>")

FIND_RECORDINGS_LEGACY_RESPONSE = ENV.format(
    ns=NS,
    body="""
<tse:FindRecordingsResponse>
  <tt:RecordingToken>legacy_a</tt:RecordingToken>
  <tt:RecordingToken>legacy_b</tt:RecordingToken>
  <tt:RecordingToken>legacy_a</tt:RecordingToken>
</tse:FindRecordingsResponse>""",
)

FAULT_ACTION_NOT_SUPPORTED = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:ter="http://www.onvif.org/ver10/error">
<s:Body><s:Fault><s:Code><s:Value>s:Receiver</s:Value><s:Subcode><s:Value>ter:ActionNotSupported</s:Value></s:Subcode></s:Code>
<s:Reason><s:Text xml:lang="en">Action Not Implemented</s:Text></s:Reason></s:Fault></s:Body></s:Envelope>"""


def _replay_uri_response(token: str) -> str:
    return ENV.format(
        ns=NS,
        body=f"<trp:GetReplayUriResponse><trp:Uri>rtsp://10.0.0.5:554/replay/{token}</trp:Uri></trp:GetReplayUriResponse>",
    )


def _http_response(status: int, text: str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


class ProfileGCapabilitiesTest(unittest.TestCase):
    def setUp(self):
        self.client = OnvifClient(ip="10.0.0.5", port=80)

    def test_default_search_service_url(self):
        self.assertEqual(
            self.client.search_service_url,
            "http://10.0.0.5:80/onvif/search_service",
        )

    def test_get_capabilities_discovers_search_xaddr(self):
        self.client._send_soap_request = lambda url, body, action="": CAPS_WITH_SEARCH
        caps = self.client.get_capabilities()
        self.assertEqual(caps["search"], "http://10.0.0.5:8080/onvif/Search")
        self.assertEqual(self.client.search_service_url, "http://10.0.0.5:8080/onvif/Search")
        self.assertEqual(self.client.replay_service_url, "http://10.0.0.5:8080/onvif/Replay")
        self.assertTrue(self.client.has_edge_storage)

    def test_get_capabilities_accepts_relative_search_xaddr(self):
        self.client._send_soap_request = lambda url, body, action="": CAPS_WITH_RELATIVE_SEARCH
        caps = self.client.get_capabilities()
        self.assertEqual(caps["search"], "http://10.0.0.5:80/onvif/custom_search")
        self.assertEqual(self.client.search_service_url, "http://10.0.0.5:80/onvif/custom_search")
        self.assertTrue(self.client.has_edge_storage)

    def test_get_capabilities_rejects_external_search_xaddr_ssrf(self):
        self.client._send_soap_request = lambda url, body, action="": CAPS_WITH_EXTERNAL_SEARCH
        caps = self.client.get_capabilities()
        self.assertNotIn("search", caps)
        self.assertEqual(
            self.client.search_service_url,
            "http://10.0.0.5:80/onvif/search_service",
        )
        self.assertFalse(self.client.has_edge_storage)


class ProfileGSearchFlowTest(unittest.TestCase):
    def setUp(self):
        self.client = OnvifClient(ip="10.0.0.5", port=80, username="admin", password="secret")
        self.client._session = MagicMock()
        self.calls = []

    def _mock_post(self, handler):
        def post(url, data, headers, timeout, allow_redirects=True):
            self.assertFalse(allow_redirects)
            xml = data.decode("utf-8")
            self.calls.append((url, xml))
            return handler(url, xml)

        self.client._session.post.side_effect = post

    def test_full_summary_and_find_recordings_flow(self):
        def handler(url, xml):
            if "GetCapabilities" in xml:
                return _http_response(200, CAPS_WITH_SEARCH)
            if "tse:GetRecordingSummary" in xml:
                return _http_response(200, SUMMARY_RESPONSE)
            if "tse:FindRecordings" in xml:
                return _http_response(200, FIND_RECORDINGS_TOKEN_RESPONSE)
            if "tse:GetRecordingSearchResults" in xml:
                return _http_response(200, SEARCH_RESULTS_RESPONSE)
            if "tse:EndSearch" in xml:
                return _http_response(200, END_SEARCH_RESPONSE)
            if "trp:GetReplayUri" in xml:
                token = "rec_001" if "rec_001" in xml else "rec_002"
                return _http_response(200, _replay_uri_response(token))
            raise AssertionError(f"Requisição inesperada: {url} {xml}")

        self._mock_post(handler)

        self.client.get_capabilities()
        summary = self.client.get_recording_summary()
        recs = self.client.find_recordings(max_matches=10)

        self.assertEqual(
            summary,
            {
                "num_recordings": 3,
                "has_storage": True,
                "earliest_time": "2026-10-01T00:00:00Z",
                "latest_time": "2026-10-20T23:59:59Z",
            },
        )
        self.assertEqual(
            recs,
            [
                {"token": "rec_001", "replay_uri": "rtsp://10.0.0.5:554/replay/rec_001"},
                {"token": "rec_002", "replay_uri": "rtsp://10.0.0.5:554/replay/rec_002"},
            ],
        )

        # Verifica namespace tse no envelope e ordem exata das chamadas
        for _, xml in self.calls:
            self.assertIn('xmlns:tse="http://www.onvif.org/ver10/search/wsdl"', xml)

        ops = []
        for url, xml in self.calls:
            for tag in (
                "tds:GetCapabilities",
                "tse:GetRecordingSummary",
                "tse:FindRecordings",
                "tse:GetRecordingSearchResults",
                "tse:EndSearch",
                "trp:GetReplayUri",
            ):
                if f"<{tag}" in xml:
                    ops.append((url, tag))
                    break

        search_url = "http://10.0.0.5:8080/onvif/Search"
        replay_url = "http://10.0.0.5:8080/onvif/Replay"
        self.assertEqual(
            ops,
            [
                ("http://10.0.0.5:80/onvif/device_service", "tds:GetCapabilities"),
                (search_url, "tse:GetRecordingSummary"),
                (search_url, "tse:FindRecordings"),
                (search_url, "tse:GetRecordingSearchResults"),
                (search_url, "tse:EndSearch"),
                (replay_url, "trp:GetReplayUri"),
                (replay_url, "trp:GetReplayUri"),
            ],
        )

        # Verifica payload de FindRecordings, GetRecordingSearchResults e EndSearch com escape XML
        find_xml = self.calls[2][1]
        self.assertIn("<tse:Scope/>", find_xml)
        self.assertNotIn("IncludedSources", find_xml)
        self.assertIn("<tse:MaxMatches>10</tse:MaxMatches>", find_xml)
        self.assertIn("<tse:KeepAliveTime>PT10S</tse:KeepAliveTime>", find_xml)

        results_xml = self.calls[3][1]
        self.assertIn("<tse:SearchToken>search&amp;tok&lt;1&gt;</tse:SearchToken>", results_xml)
        self.assertIn("<tse:MinResults>1</tse:MinResults>", results_xml)
        self.assertIn("<tse:MaxResults>10</tse:MaxResults>", results_xml)
        self.assertIn("<tse:WaitTime>PT2S</tse:WaitTime>", results_xml)

        end_xml = self.calls[4][1]
        self.assertIn("<tse:SearchToken>search&amp;tok&lt;1&gt;</tse:SearchToken>", end_xml)

    def test_get_recording_summary_falls_back_to_trc_on_fault(self):
        def handler(url, xml):
            if "tse:GetRecordingSummary" in xml:
                return _http_response(500, FAULT_ACTION_NOT_SUPPORTED)
            if "trc:GetRecordingSummary" in xml:
                return _http_response(200, SUMMARY_RESPONSE)
            raise AssertionError(f"Requisição inesperada: {url} {xml}")

        self._mock_post(handler)

        summary = self.client.get_recording_summary()
        self.assertEqual(summary["num_recordings"], 3)
        self.assertTrue(summary["has_storage"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][0], "http://10.0.0.5:80/onvif/search_service")
        self.assertIn("<tse:GetRecordingSummary/>", self.calls[0][1])
        self.assertEqual(self.calls[1][0], "http://10.0.0.5:80/onvif/recording_service")
        self.assertIn("<trc:GetRecordingSummary/>", self.calls[1][1])

    def test_end_search_always_called_when_get_results_fails(self):
        def handler(url, xml):
            if "tse:FindRecordings" in xml:
                return _http_response(200, FIND_RECORDINGS_TOKEN_RESPONSE)
            if "tse:GetRecordingSearchResults" in xml:
                return _http_response(500, FAULT_ACTION_NOT_SUPPORTED)
            if "tse:EndSearch" in xml:
                return _http_response(200, END_SEARCH_RESPONSE)
            raise AssertionError(f"Requisição inesperada: {url} {xml}")

        self._mock_post(handler)

        with self.assertRaises(OnvifFaultError):
            self.client.find_recordings(max_matches=5)

        self.assertEqual(len(self.calls), 3)
        self.assertIn("<tse:FindRecordings>", self.calls[0][1])
        self.assertIn("<tse:GetRecordingSearchResults>", self.calls[1][1])
        self.assertIn("<tse:EndSearch>", self.calls[2][1])
        self.assertIn("<tse:SearchToken>search&amp;tok&lt;1&gt;</tse:SearchToken>", self.calls[2][1])

    def test_end_search_error_is_ignored_without_masking_results(self):
        def handler(url, xml):
            if "tse:FindRecordings" in xml:
                return _http_response(200, FIND_RECORDINGS_TOKEN_RESPONSE)
            if "tse:GetRecordingSearchResults" in xml:
                return _http_response(200, SEARCH_RESULTS_RESPONSE)
            if "tse:EndSearch" in xml:
                return _http_response(500, FAULT_ACTION_NOT_SUPPORTED)
            if "trp:GetReplayUri" in xml:
                token = "rec_001" if "rec_001" in xml else "rec_002"
                return _http_response(200, _replay_uri_response(token))
            raise AssertionError(f"Requisição inesperada: {url} {xml}")

        self._mock_post(handler)

        recs = self.client.find_recordings(max_matches=10)
        self.assertEqual([r["token"] for r in recs], ["rec_001", "rec_002"])

    def test_find_recordings_legacy_fallback_without_search_token(self):
        def handler(url, xml):
            if "tse:FindRecordings" in xml:
                return _http_response(200, FIND_RECORDINGS_LEGACY_RESPONSE)
            if "trp:GetReplayUri" in xml:
                token = "legacy_a" if "legacy_a" in xml else "legacy_b"
                return _http_response(200, _replay_uri_response(token))
            raise AssertionError(f"Requisição inesperada: {url} {xml}")

        self._mock_post(handler)

        recs = self.client.find_recordings(max_matches=10)
        self.assertEqual(
            recs,
            [
                {"token": "legacy_a", "replay_uri": "rtsp://10.0.0.5:554/replay/legacy_a"},
                {"token": "legacy_b", "replay_uri": "rtsp://10.0.0.5:554/replay/legacy_b"},
            ],
        )
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn("GetRecordingSearchResults", "".join(xml for _, xml in self.calls))
        self.assertNotIn("EndSearch", "".join(xml for _, xml in self.calls))

    def test_get_recording_summary_falls_back_to_trc_on_http_404(self):
        def handler(url, xml):
            if "tse:GetRecordingSummary" in xml:
                return _http_response(404, "<html>Not Found</html>")
            if "trc:GetRecordingSummary" in xml:
                return _http_response(200, SUMMARY_RESPONSE)
            raise AssertionError(f"Requisição inesperada: {url} {xml}")

        self._mock_post(handler)

        summary = self.client.get_recording_summary()
        self.assertEqual(summary["num_recordings"], 3)
        self.assertTrue(summary["has_storage"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][0], "http://10.0.0.5:80/onvif/search_service")
        self.assertEqual(self.calls[1][0], "http://10.0.0.5:80/onvif/recording_service")
        self.assertIn("<trc:GetRecordingSummary/>", self.calls[1][1])

    def test_find_recordings_falls_back_to_recording_service_on_http_404(self):
        def handler(url, xml):
            if "tse:FindRecordings" in xml:
                return _http_response(404, "Not Found")
            if "trc:FindRecordings" in xml:
                return _http_response(200, FIND_RECORDINGS_LEGACY_RESPONSE)
            if "trp:GetReplayUri" in xml:
                token = "legacy_a" if "legacy_a" in xml else "legacy_b"
                return _http_response(200, _replay_uri_response(token))
            raise AssertionError(f"Requisição inesperada: {url} {xml}")

        self._mock_post(handler)

        recs = self.client.find_recordings(max_matches=10)
        self.assertEqual([r["token"] for r in recs], ["legacy_a", "legacy_b"])
        self.assertEqual(self.calls[0][0], "http://10.0.0.5:80/onvif/search_service")
        self.assertEqual(self.calls[1][0], "http://10.0.0.5:80/onvif/recording_service")
        self.assertIn("<trc:Scope/>", self.calls[1][1])
        self.assertNotIn("IncludedSources", self.calls[1][1])

    def test_find_recordings_loops_while_search_state_is_searching(self):
        search_batches = [
            ENV.format(
                ns=NS,
                body="""
<tse:GetRecordingSearchResultsResponse>
  <tse:ResultList>
    <tt:SearchState>Searching</tt:SearchState>
  </tse:ResultList>
</tse:GetRecordingSearchResultsResponse>""",
            ),
            ENV.format(
                ns=NS,
                body="""
<tse:GetRecordingSearchResultsResponse>
  <tse:ResultList>
    <tt:SearchState>Searching</tt:SearchState>
    <tt:RecordingInformation><tt:RecordingToken>rec_001</tt:RecordingToken></tt:RecordingInformation>
  </tse:ResultList>
</tse:GetRecordingSearchResultsResponse>""",
            ),
            ENV.format(
                ns=NS,
                body="""
<tse:GetRecordingSearchResultsResponse>
  <tse:ResultList>
    <tt:SearchState>Completed</tt:SearchState>
    <tt:RecordingInformation><tt:RecordingToken>rec_002</tt:RecordingToken></tt:RecordingInformation>
    <tt:RecordingInformation><tt:RecordingToken>rec_003</tt:RecordingToken></tt:RecordingInformation>
  </tse:ResultList>
</tse:GetRecordingSearchResultsResponse>""",
            ),
        ]
        batch_idx = 0
        results_requests = []

        def handler(url, xml):
            nonlocal batch_idx
            if "tse:FindRecordings" in xml:
                return _http_response(200, FIND_RECORDINGS_TOKEN_RESPONSE)
            if "tse:GetRecordingSearchResults" in xml:
                results_requests.append(xml)
                resp_xml = search_batches[batch_idx]
                batch_idx += 1
                return _http_response(200, resp_xml)
            if "tse:EndSearch" in xml:
                return _http_response(200, END_SEARCH_RESPONSE)
            if "trp:GetReplayUri" in xml:
                for tok in ("rec_001", "rec_002", "rec_003"):
                    if tok in xml:
                        return _http_response(200, _replay_uri_response(tok))
            raise AssertionError(f"Requisição inesperada: {url} {xml}")

        self._mock_post(handler)

        recs = self.client.find_recordings(max_matches=5)
        self.assertEqual([r["token"] for r in recs], ["rec_001", "rec_002", "rec_003"])
        self.assertEqual(len(results_requests), 3)
        self.assertIn("<tse:MaxResults>5</tse:MaxResults>", results_requests[0])
        self.assertIn("<tse:MaxResults>5</tse:MaxResults>", results_requests[1])
        self.assertIn("<tse:MaxResults>4</tse:MaxResults>", results_requests[2])
        end_calls = [xml for _, xml in self.calls if "<tse:EndSearch>" in xml]
        self.assertEqual(len(end_calls), 1)

    def test_find_recordings_aborts_when_should_abort_triggers(self):
        abort_flag = {"stop": False}

        def handler(url, xml):
            if "tse:FindRecordings" in xml:
                return _http_response(200, FIND_RECORDINGS_TOKEN_RESPONSE)
            if "tse:GetRecordingSearchResults" in xml:
                abort_flag["stop"] = True
                return _http_response(
                    200,
                    ENV.format(
                        ns=NS,
                        body="""
<tse:GetRecordingSearchResultsResponse>
  <tse:ResultList>
    <tt:SearchState>Searching</tt:SearchState>
    <tt:RecordingInformation><tt:RecordingToken>rec_001</tt:RecordingToken></tt:RecordingInformation>
  </tse:ResultList>
</tse:GetRecordingSearchResultsResponse>""",
                    ),
                )
            raise AssertionError(f"Não deveria continuar após abortar: {url} {xml}")

        self._mock_post(handler)

        recs = self.client.find_recordings(max_matches=10, should_abort=lambda: abort_flag["stop"])
        self.assertEqual(recs, [])
        self.assertEqual(len(self.calls), 2)

    def test_get_recording_summary_does_not_fallback_on_redirect(self):
        for status in (301, 302, 307, 308):
            with self.subTest(status=status):
                self.calls.clear()

                def handler(url, xml, status_code=status):
                    if url == "http://10.0.0.5:80/onvif/search_service":
                        return _http_response(status_code, "")
                    raise AssertionError(f"Não deveria fazer fallback em 3xx: {url}")

                self._mock_post(handler)
                with self.assertRaises(OnvifRedirectError):
                    self.client.get_recording_summary()
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.calls[0][0], "http://10.0.0.5:80/onvif/search_service")

    def test_find_recordings_does_not_fallback_on_redirect(self):
        for status in (301, 302, 307, 308):
            with self.subTest(status=status):
                self.calls.clear()

                def handler(url, xml, status_code=status):
                    if url == "http://10.0.0.5:80/onvif/search_service":
                        return _http_response(status_code, "")
                    raise AssertionError(f"Não deveria fazer fallback em 3xx: {url}")

                self._mock_post(handler)
                with self.assertRaises(OnvifRedirectError):
                    self.client.find_recordings(max_matches=10)
                self.assertEqual(len(self.calls), 1)
                self.assertEqual(self.calls[0][0], "http://10.0.0.5:80/onvif/search_service")


def _camera(cam_id: str = "cam_10_0_0_5", name: str = "Câmera Garagem", ip: str = "10.0.0.5") -> CameraDevice:
    return CameraDevice(
        id=cam_id,
        name=name,
        ip=ip,
        port=80,
        username="admin",
        password="secret",
    )


@unittest.skipUnless(HAS_PYSIDE6, "PySide6 não disponível neste ambiente")
class PlaybackEdgeStorageAsyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_patch = patch.object(
            playback_view, "get_data_dir", return_value=Path(self._tmp.name)
        )
        self.data_patch.start()
        self.msg_patch = patch.object(playback_view, "QMessageBox")
        self.msgbox = self.msg_patch.start()
        self.widget = PlaybackWidget()
        self.widget.set_camera(_camera())

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()
        self.assertTrue(drain_retired_threads(5000))
        self.app.processEvents()
        self.msg_patch.stop()
        self.data_patch.stop()
        self._tmp.cleanup()

    def _wait_until(self, predicate, timeout_s: float = 3.0):
        deadline = time.monotonic() + timeout_s
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertTrue(predicate(), "Condição assíncrona não foi satisfeita no prazo")

    def test_check_edge_storage_runs_off_ui_thread_and_ignores_concurrent_click(self):
        started = threading.Event()
        release = threading.Event()
        call_threads = []
        created_clients = []

        class FakeOnvifClient:
            def __init__(self, ip, port=80, username="", password="", timeout=3.0):
                self.args = (ip, port, username, password, timeout)
                created_clients.append(self)

            def get_capabilities(self):
                call_threads.append(("get_capabilities", QApplication.instance().thread().isCurrentThread()))
                started.set()
                release.wait(timeout=3.0)
                return {"search": "http://10.0.0.5/onvif/search_service"}

            def get_recording_summary(self):
                call_threads.append(("get_recording_summary", QApplication.instance().thread().isCurrentThread()))
                return {
                    "num_recordings": 2,
                    "has_storage": True,
                    "earliest_time": "2026-10-01T00:00:00Z",
                    "latest_time": "2026-10-20T00:00:00Z",
                }

            def find_recordings(self, max_matches=20):
                call_threads.append(("find_recordings", QApplication.instance().thread().isCurrentThread()))
                return [
                    {"token": "rec_001", "replay_uri": "rtsp://10.0.0.5:554/replay/rec_001"},
                ]

        msg_threads = []
        self.msgbox.information.side_effect = lambda *args, **kwargs: msg_threads.append(
            QApplication.instance().thread().isCurrentThread()
        )

        with patch.object(playback_view, "OnvifClient", FakeOnvifClient):
            self.widget._check_edge_storage()
            self.assertIsInstance(self.widget._edge_worker, EdgeStorageWorker)
            self.assertFalse(self.widget.btn_sync_sd.isEnabled())
            self.assertEqual(self.widget.btn_sync_sd.text(), "Consultando SD...")

            self.assertTrue(started.wait(timeout=2.0))
            # Novo clique enquanto a consulta está ativa deve ser ignorado
            self.widget._check_edge_storage()
            self.assertEqual(len(created_clients), 1)

            release.set()
            self._wait_until(lambda: self.msgbox.information.called)

        self.assertEqual(len(created_clients), 1)
        self.assertEqual(
            call_threads,
            [
                ("get_capabilities", False),
                ("get_recording_summary", False),
                ("find_recordings", False),
            ],
        )
        self.assertEqual(msg_threads, [True])
        self.assertTrue(self.widget.btn_sync_sd.isEnabled())
        self.assertEqual(self.widget.btn_sync_sd.text(), "💾 Cartão SD (Profile G)")
        self.assertIsNone(self.widget._edge_worker)
        body_text = self.msgbox.information.call_args[0][2]
        self.assertIn("Câmera Garagem", body_text)
        self.assertIn("rec_001", body_text)
        self.assertIn("rtsp://10.0.0.5:554/replay/rec_001", body_text)
        self.assertTrue(drain_retired_threads(5000))

    def test_check_edge_storage_error_restores_button_and_warns_on_main_thread(self):
        class FailingOnvifClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_capabilities(self):
                raise OnvifTimeoutError("Sem resposta em 3.0s")

        warn_threads = []
        self.msgbox.warning.side_effect = lambda *args, **kwargs: warn_threads.append(
            QApplication.instance().thread().isCurrentThread()
        )

        with patch.object(playback_view, "OnvifClient", FailingOnvifClient):
            self.widget._check_edge_storage()
            self._wait_until(lambda: self.msgbox.warning.called)

        self.assertEqual(warn_threads, [True])
        self.assertTrue(self.widget.btn_sync_sd.isEnabled())
        self.assertEqual(self.widget.btn_sync_sd.text(), "💾 Cartão SD (Profile G)")
        self.assertIsNone(self.widget._edge_worker)
        warn_text = self.msgbox.warning.call_args[0][2]
        self.assertIn("Câmera Garagem", warn_text)
        self.assertIn("Sem resposta em 3.0s", warn_text)
        self.assertTrue(drain_retired_threads(5000))

    def test_check_edge_storage_summary_only_and_empty_results(self):
        class SummaryOnlyClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_capabilities(self):
                return {}

            def get_recording_summary(self):
                return {
                    "num_recordings": 5,
                    "has_storage": True,
                    "earliest_time": "2026-10-01T00:00:00Z",
                    "latest_time": "2026-10-02T00:00:00Z",
                }

            def find_recordings(self, max_matches=20):
                return []

        with patch.object(playback_view, "OnvifClient", SummaryOnlyClient):
            self.widget._check_edge_storage()
            self._wait_until(lambda: self.msgbox.information.called)

        summary_text = self.msgbox.information.call_args[0][2]
        self.assertIn("Câmera Garagem", summary_text)
        self.assertIn("5 gravações", summary_text)
        self.assertTrue(self.widget.btn_sync_sd.isEnabled())
        self.assertTrue(drain_retired_threads(5000))

        self.msgbox.information.reset_mock()

        class EmptyClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_capabilities(self):
                return {}

            def get_recording_summary(self):
                return {"num_recordings": 0, "has_storage": False, "earliest_time": "", "latest_time": ""}

            def find_recordings(self, max_matches=20):
                return []

        with patch.object(playback_view, "OnvifClient", EmptyClient):
            self.widget._check_edge_storage()
            self._wait_until(lambda: self.msgbox.information.called)

        self.assertIn("Nenhuma gravação", self.msgbox.information.call_args[0][2])
        self.assertTrue(drain_retired_threads(5000))

    def test_check_edge_storage_preserves_summary_when_find_recordings_times_out(self):
        class SummaryOkFindTimeoutClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_capabilities(self):
                return {"search": "http://10.0.0.5/onvif/search_service"}

            def get_recording_summary(self):
                return {
                    "num_recordings": 5,
                    "has_storage": True,
                    "earliest_time": "2026-10-01T00:00:00Z",
                    "latest_time": "2026-10-02T00:00:00Z",
                }

            def find_recordings(self, max_matches=20, should_abort=None):
                raise OnvifTimeoutError("Tempo esgotado na busca")

        with patch.object(playback_view, "OnvifClient", SummaryOkFindTimeoutClient):
            self.widget._check_edge_storage()
            self._wait_until(lambda: self.msgbox.information.called)

        self.msgbox.warning.assert_not_called()
        body = self.msgbox.information.call_args[0][2]
        self.assertIn("Câmera Garagem", body)
        self.assertIn("5 gravações", body)
        self.assertIn("2026-10-01T00:00:00Z", body)
        self.assertIn("2026-10-02T00:00:00Z", body)
        self.assertIn("listagem detalhada", body)
        self.assertIn("Tempo esgotado na busca", body)
        self.assertTrue(self.widget.btn_sync_sd.isEnabled())
        self.assertIsNone(self.widget._edge_worker)
        self.assertTrue(drain_retired_threads(5000))

    def test_check_edge_storage_summary_failure_shows_general_error(self):
        class SummaryFailingClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_capabilities(self):
                return {}

            def get_recording_summary(self):
                raise OnvifTimeoutError("Resumo indisponível")

            def find_recordings(self, max_matches=20, should_abort=None):
                raise AssertionError("find_recordings não deveria ser chamado após falha no resumo")

        with patch.object(playback_view, "OnvifClient", SummaryFailingClient):
            self.widget._check_edge_storage()
            self._wait_until(lambda: self.msgbox.warning.called)

        self.msgbox.information.assert_not_called()
        warn_text = self.msgbox.warning.call_args[0][2]
        self.assertIn("Falha na consulta", warn_text)
        self.assertIn("Câmera Garagem", warn_text)
        self.assertIn("Resumo indisponível", warn_text)
        self.assertTrue(self.widget.btn_sync_sd.isEnabled())
        self.assertIsNone(self.widget._edge_worker)
        self.assertTrue(drain_retired_threads(5000))

    def test_check_edge_storage_zero_summary_and_find_timeout_shows_warning_not_empty(self):
        class ZeroSummaryFindTimeoutClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_capabilities(self):
                return {"search": "http://10.0.0.5/onvif/search_service"}

            def get_recording_summary(self):
                return {
                    "num_recordings": 0,
                    "has_storage": False,
                    "earliest_time": "",
                    "latest_time": "",
                }

            def find_recordings(self, max_matches=20, should_abort=None):
                raise OnvifTimeoutError("Tempo esgotado na busca")

        with patch.object(playback_view, "OnvifClient", ZeroSummaryFindTimeoutClient):
            self.widget._check_edge_storage()
            self._wait_until(lambda: self.msgbox.warning.called)

        self.msgbox.information.assert_not_called()
        warn_text = self.msgbox.warning.call_args[0][2]
        self.assertIn("Falha na consulta", warn_text)
        self.assertIn("Câmera Garagem", warn_text)
        self.assertIn("Tempo esgotado na busca", warn_text)
        self.assertNotIn("Nenhuma gravação", warn_text)
        self.assertTrue(self.widget.btn_sync_sd.isEnabled())
        self.assertIsNone(self.widget._edge_worker)
        self.assertTrue(drain_retired_threads(5000))

    def test_check_edge_storage_redirect_error_is_not_hidden_after_summary(self):
        class RedirectOnFindClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_capabilities(self):
                return {"search": "http://10.0.0.5/onvif/search_service"}

            def get_recording_summary(self):
                return {
                    "num_recordings": 5,
                    "has_storage": True,
                    "earliest_time": "2026-10-01T00:00:00Z",
                    "latest_time": "2026-10-02T00:00:00Z",
                }

            def find_recordings(self, max_matches=20, should_abort=None):
                raise OnvifRedirectError("Redirecionamento HTTP 302 recusado")

        with patch.object(playback_view, "OnvifClient", RedirectOnFindClient):
            self.widget._check_edge_storage()
            self._wait_until(lambda: self.msgbox.warning.called)

        self.msgbox.information.assert_not_called()
        warn_text = self.msgbox.warning.call_args[0][2]
        self.assertIn("Falha na consulta", warn_text)
        self.assertIn("Câmera Garagem", warn_text)
        self.assertIn("Redirecionamento HTTP 302 recusado", warn_text)
        self.assertTrue(self.widget.btn_sync_sd.isEnabled())
        self.assertIsNone(self.widget._edge_worker)
        self.assertTrue(drain_retired_threads(5000))

    def test_closing_widget_while_worker_running_retires_thread_cleanly(self):
        started = threading.Event()
        release = threading.Event()

        class SlowOnvifClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_capabilities(self):
                started.set()
                release.wait(timeout=3.0)
                return {}

            def get_recording_summary(self):
                return {"num_recordings": 0, "has_storage": False, "earliest_time": "", "latest_time": ""}

            def find_recordings(self, max_matches=20):
                return []

        with patch.object(playback_view, "OnvifClient", SlowOnvifClient):
            self.widget._check_edge_storage()
            self.assertTrue(started.wait(timeout=2.0))
            self.widget.close()
            self.assertIsNone(self.widget._edge_worker)
            release.set()
            self.assertTrue(drain_retired_threads(5000))
            self.app.processEvents()

        self.msgbox.information.assert_not_called()
        self.msgbox.warning.assert_not_called()

    def test_set_camera_cancels_active_query_restores_button_and_suppresses_stale_dialog(self):
        cam_a = _camera("cam_a", "Câmera A", "10.0.0.5")
        cam_b = _camera("cam_b", "Câmera B", "10.0.0.6")
        started_a = threading.Event()
        release_a = threading.Event()
        calls = []

        class SwitchingOnvifClient:
            def __init__(self, ip, port=80, username="", password="", timeout=3.0):
                self.ip = ip

            def get_capabilities(self):
                calls.append((self.ip, "get_capabilities"))
                if self.ip == cam_a.ip:
                    started_a.set()
                    release_a.wait(timeout=3.0)
                return {}

            def get_recording_summary(self):
                calls.append((self.ip, "get_recording_summary"))
                return {"num_recordings": 1, "has_storage": True, "earliest_time": "", "latest_time": ""}

            def find_recordings(self, max_matches=20, should_abort=None):
                calls.append((self.ip, "find_recordings"))
                return [{"token": f"rec_{self.ip}", "replay_uri": f"rtsp://{self.ip}:554/r1"}]

        self.widget.set_camera(cam_a)
        with patch.object(playback_view, "OnvifClient", SwitchingOnvifClient):
            self.widget._check_edge_storage()
            self.assertTrue(started_a.wait(timeout=2.0))
            self.assertFalse(self.widget.btn_sync_sd.isEnabled())

            # Trocar para Câmera B cancela a consulta de A e restaura o botão imediatamente
            self.widget.set_camera(cam_b)
            self.assertIsNone(self.widget._edge_worker)
            self.assertTrue(self.widget.btn_sync_sd.isEnabled())
            self.assertEqual(self.widget.btn_sync_sd.text(), "💾 Cartão SD (Profile G)")

            # Libera Câmera A: worker interrompido não avança nem exibe QMessageBox
            release_a.set()
            self.assertTrue(drain_retired_threads(5000))
            self.app.processEvents()
            self.msgbox.information.assert_not_called()
            self.msgbox.warning.assert_not_called()
            self.assertEqual(calls, [(cam_a.ip, "get_capabilities")])

            # Agora consultar Câmera B funciona normalmente
            self.widget._check_edge_storage()
            self._wait_until(lambda: self.msgbox.information.called)
            body = self.msgbox.information.call_args[0][2]
            self.assertIn("Câmera B", body)
            self.assertIn(f"rec_{cam_b.ip}", body)

            # Remover câmera (set_camera(None)) durante consulta ativa também cancela e restaura o botão
            self.msgbox.information.reset_mock()
            started_a.clear()
            release_a.clear()
            self.widget.set_camera(cam_a)
            self.widget._check_edge_storage()
            self.assertTrue(started_a.wait(timeout=2.0))
            self.widget.set_camera(None)
            self.assertIsNone(self.widget._edge_worker)
            self.assertTrue(self.widget.btn_sync_sd.isEnabled())
            release_a.set()
            self.assertTrue(drain_retired_threads(5000))
            self.app.processEvents()
            self.msgbox.information.assert_not_called()
            self.msgbox.warning.assert_not_called()

    def test_closing_main_window_shuts_down_recordings_panel_and_aborts_worker_between_steps(self):
        started = threading.Event()
        release = threading.Event()
        called_steps = []

        class SlowMultiStepClient:
            def __init__(self, *args, **kwargs):
                pass

            def get_capabilities(self):
                called_steps.append("get_capabilities")
                started.set()
                release.wait(timeout=3.0)
                return {}

            def get_recording_summary(self):
                called_steps.append("get_recording_summary")
                return {"num_recordings": 1, "has_storage": True, "earliest_time": "", "latest_time": ""}

            def find_recordings(self, max_matches=20, should_abort=None):
                called_steps.append("find_recordings")
                return []

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config_dir = base / "config"
            data_dir = base / "data"
            config_dir.mkdir(parents=True)
            data_dir.mkdir(parents=True)

            with patch("central_nvr.core.config.get_config_dir", return_value=config_dir), \
                    patch("central_nvr.core.config.get_data_dir", return_value=data_dir), \
                    patch("central_nvr.core.config.is_keyring_available", return_value=False), \
                    patch.object(playback_view, "get_data_dir", return_value=data_dir), \
                    patch.object(playback_view, "OnvifClient", SlowMultiStepClient):
                cfg = ConfigManager()
                cfg.set("periodic_update_check", False)

                window = MainWindow(config_mgr=cfg)
                try:
                    self.app.processEvents()
                    window.recordings_panel.set_camera(_camera())
                    window.recordings_panel._check_edge_storage()
                    self.assertTrue(started.wait(timeout=2.0))

                    # Fechar a MainWindow deve cancelar o worker filho em recordings_panel
                    window.close()
                    self.assertIsNone(window.recordings_panel._edge_worker)
                    release.set()
                    self.assertTrue(drain_retired_threads(5000))
                    self.app.processEvents()
                finally:
                    release.set()
                    window.deleteLater()
                    QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    self.app.processEvents()

        self.assertEqual(called_steps, ["get_capabilities"])
        self.msgbox.information.assert_not_called()
        self.msgbox.warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
