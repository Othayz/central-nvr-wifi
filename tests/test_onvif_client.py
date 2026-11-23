#!/usr/bin/env python3
"""
Testes do cliente ONVIF (Issue #28): parse com defusedxml por nome local, has_ptz só com
evidência, erros tipados, entidades XML decodificadas e validação do host das URIs.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import requests
from urllib3.util import parse_url as urllib3_parse_url

from central_nvr.core.onvif_client import (
    OnvifAuthError,
    OnvifClient,
    OnvifConnectionError,
    OnvifFaultError,
    OnvifRedirectError,
    OnvifResponseError,
    OnvifTimeoutError,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ENV = '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" {ns}><s:Body>{body}</s:Body></s:Envelope>'
NS = ('xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
      'xmlns:tt="http://www.onvif.org/ver10/schema"')

CAPS_MEDIA_ONLY = ENV.format(ns=NS, body="""
<tds:GetCapabilitiesResponse><tds:Capabilities>
  <tt:Device><tt:XAddr>http://10.0.0.5/onvif/device_service</tt:XAddr></tt:Device>
  <tt:Media><tt:XAddr>http://10.0.0.5/onvif/Media</tt:XAddr></tt:Media>
</tds:Capabilities></tds:GetCapabilitiesResponse>""")

CAPS_WITH_PTZ = ENV.format(ns=NS, body="""
<tds:GetCapabilitiesResponse><tds:Capabilities>
  <tt:Media><tt:XAddr>http://10.0.0.5/onvif/Media</tt:XAddr></tt:Media>
  <tt:PTZ><tt:XAddr>http://10.0.0.5/onvif/PTZ</tt:XAddr></tt:PTZ>
</tds:Capabilities></tds:GetCapabilitiesResponse>""")

CAPS_PTZ_EXTERNAL = ENV.format(ns=NS, body="""
<tds:GetCapabilitiesResponse><tds:Capabilities>
  <tt:Media><tt:XAddr>http://10.0.0.5/onvif/Media</tt:XAddr></tt:Media>
  <tt:PTZ><tt:XAddr>http://evil.example.com/onvif/PTZ</tt:XAddr></tt:PTZ>
</tds:Capabilities></tds:GetCapabilitiesResponse>""")

PROFILES_NO_PTZ = ENV.format(ns=NS, body="""
<trt:GetProfilesResponse>
  <trt:Profiles token="main" fixed="true"><tt:Name>MainStream</tt:Name>
    <tt:VideoEncoderConfiguration><tt:Encoding>H265</tt:Encoding>
      <tt:Resolution><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:Resolution>
    </tt:VideoEncoderConfiguration>
    <tt:Extension><tt:Comment>sem PTZ</tt:Comment></tt:Extension>
  </trt:Profiles>
  <trt:Profiles token="sub"><tt:Name>SubStream</tt:Name>
    <tt:VideoEncoderConfiguration><tt:Encoding>H264</tt:Encoding>
      <tt:Resolution><tt:Width>640</tt:Width><tt:Height>360</tt:Height></tt:Resolution>
    </tt:VideoEncoderConfiguration>
  </trt:Profiles>
</trt:GetProfilesResponse>""")

PROFILES_WITH_PTZ_CONFIG = ENV.format(ns=NS, body="""
<trt:GetProfilesResponse>
  <trt:Profiles token="main"><tt:Name>Main</tt:Name><tt:PTZConfiguration token="ptz0"/></trt:Profiles>
</trt:GetProfilesResponse>""")

STREAM_URI_DAHUA = ENV.format(ns=NS, body="""
<trt:GetStreamUriResponse><trt:MediaUri>
  <tt:Uri>rtsp://10.0.0.5:554/cam/realmonitor?channel=1&amp;subtype=0</tt:Uri>
</trt:MediaUri></trt:GetStreamUriResponse>""")

STREAM_URI_EXTERNAL = ENV.format(ns=NS, body="""
<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://evil.example.com/x</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse>""")

DEVICE_INFO_DEFAULT_NS = """<Envelope xmlns="http://www.w3.org/2003/05/soap-envelope"><Body>
<GetDeviceInformationResponse xmlns="http://www.onvif.org/ver10/device/wsdl">
  <Manufacturer>ACME</Manufacturer><Model>Cam-9</Model><FirmwareVersion>2.1</FirmwareVersion>
</GetDeviceInformationResponse></Body></Envelope>"""

FAULT_NOT_AUTHORIZED = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:ter="http://www.onvif.org/ver10/error">
<s:Body><s:Fault><s:Code><s:Value>s:Sender</s:Value><s:Subcode><s:Value>ter:NotAuthorized</s:Value></s:Subcode></s:Code>
<s:Reason><s:Text xml:lang="en">Sender not Authorized</s:Text></s:Reason></s:Fault></s:Body></s:Envelope>"""

FAULT_ACTION_NOT_SUPPORTED = """<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:ter="http://www.onvif.org/ver10/error">
<s:Body><s:Fault><s:Code><s:Value>s:Receiver</s:Value><s:Subcode><s:Value>ter:ActionNotSupported</s:Value></s:Subcode></s:Code>
<s:Reason><s:Text xml:lang="en">Optional Action Not Implemented</s:Text></s:Reason></s:Fault></s:Body></s:Envelope>"""


def _response(status, text):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


class ParsingTest(unittest.TestCase):
    def setUp(self):
        self.client = OnvifClient(ip="10.0.0.5", port=80)

    def _reply(self, text):
        self.client._send_soap_request = lambda url, body, action="": text

    def test_capabilities_without_ptz_block_means_no_ptz(self):
        self._reply(CAPS_MEDIA_ONLY)
        caps = self.client.get_capabilities()
        self.assertFalse(self.client.has_ptz)
        self.assertNotIn("ptz", caps)
        self.assertEqual(self.client.media_service_url, "http://10.0.0.5/onvif/Media")

    def test_capabilities_with_ptz_block(self):
        self._reply(CAPS_WITH_PTZ)
        self.client.get_capabilities()
        self.assertTrue(self.client.has_ptz)
        self.assertEqual(self.client.ptz_service_url, "http://10.0.0.5/onvif/PTZ")

    def test_rejected_ptz_xaddr_does_not_enable_ptz(self):
        self._reply(CAPS_PTZ_EXTERNAL)
        self.client.get_capabilities()
        self.assertFalse(self.client.has_ptz)

    def test_profiles_without_ptz_configuration(self):
        responses = {"GetProfiles": PROFILES_NO_PTZ, "GetStreamUri": STREAM_URI_DAHUA}
        self.client._send_soap_request = lambda url, body, action="": next(
            v for k, v in responses.items() if k in body)
        profiles = self.client.get_profiles()
        self.assertEqual([p["token"] for p in profiles], ["main", "sub"])
        self.assertFalse(any(p["has_ptz"] for p in profiles))
        self.assertFalse(self.client.has_ptz)
        self.assertEqual(profiles[0]["encoding"], "H265")
        self.assertEqual((profiles[0]["width"], profiles[0]["height"]), (1920, 1080))
        self.assertEqual(self.client.substream_profile_token, "sub")

    def test_profile_with_ptz_configuration_enables_ptz(self):
        self.client._send_soap_request = lambda url, body, action="": (
            PROFILES_WITH_PTZ_CONFIG if "GetProfiles" in body else STREAM_URI_DAHUA)
        profiles = self.client.get_profiles()
        self.assertTrue(profiles[0]["has_ptz"])
        self.assertTrue(self.client.has_ptz)

    def test_empty_profiles_has_no_fictitious_profile(self):
        self._reply(ENV.format(ns=NS, body="<trt:GetProfilesResponse/>"))
        self.assertEqual(self.client.get_profiles(), [])
        self.assertEqual(self.client.active_profile_token, "")

    def test_xml_entities_are_decoded_in_stream_uri(self):
        self._reply(STREAM_URI_DAHUA)
        self.assertEqual(
            self.client.get_stream_uri("main"),
            "rtsp://10.0.0.5:554/cam/realmonitor?channel=1&subtype=0",
        )

    def test_default_namespace_is_parsed(self):
        self._reply(DEVICE_INFO_DEFAULT_NS)
        info = self.client.get_device_information()
        self.assertEqual(info["manufacturer"], "ACME")
        self.assertEqual(info["model"], "Cam-9")

    def test_stream_uri_on_external_host_is_rejected(self):
        self._reply(STREAM_URI_EXTERNAL)
        self.assertIsNone(self.client.get_stream_uri("main"))

    def test_fault_in_http_200_raises_fault_error(self):
        self._reply(FAULT_ACTION_NOT_SUPPORTED)
        with self.assertRaises(OnvifFaultError):
            self.client.get_capabilities()

    def test_invalid_xml_raises_response_error(self):
        self._reply("isto não é xml")
        with self.assertRaises(OnvifResponseError):
            self.client.get_capabilities()

    def test_entity_expansion_is_rejected(self):
        bomb = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><l>&b;</l>'
        self._reply(bomb)
        with self.assertRaises(OnvifResponseError):
            self.client.get_device_information()


class TransportErrorsTest(unittest.TestCase):
    def setUp(self):
        self.client = OnvifClient(ip="10.0.0.5", port=80, username="admin", password="x")
        self.client._session = MagicMock()

    def test_closed_port_raises_connection_error(self):
        self.client._session.post.side_effect = requests.exceptions.ConnectionError("refused")
        with self.assertRaises(OnvifConnectionError):
            self.client.get_capabilities()

    def test_timeout_raises_timeout_error(self):
        self.client._session.post.side_effect = requests.exceptions.ReadTimeout("slow")
        with self.assertRaises(OnvifTimeoutError):
            self.client.get_capabilities()

    def test_http_401_raises_auth_error(self):
        self.client._session.post.return_value = _response(401, "")
        with self.assertRaises(OnvifAuthError):
            self.client.get_capabilities()

    def test_not_authorized_fault_raises_auth_error(self):
        self.client._session.post.return_value = _response(400, FAULT_NOT_AUTHORIZED)
        with self.assertRaises(OnvifAuthError):
            self.client.get_capabilities()

    def test_http_500_with_fault_raises_fault_error(self):
        self.client._session.post.return_value = _response(500, FAULT_ACTION_NOT_SUPPORTED)
        with self.assertRaises(OnvifFaultError):
            self.client.ptz_stop()

    def test_http_error_without_fault_raises_response_error(self):
        self.client._session.post.return_value = _response(404, "<html>not found</html>")
        with self.assertRaises(OnvifResponseError):
            self.client.get_capabilities()

    def test_connect_and_initialize_false_when_device_does_not_respond(self):
        self.client._session.post.side_effect = requests.exceptions.ConnectionError("refused")
        self.assertFalse(self.client.connect_and_initialize())
        self.assertEqual(self.client.profiles, [])

    def test_connect_and_initialize_true_with_real_profiles(self):
        def post(url, data, headers, timeout, allow_redirects=True):
            self.assertFalse(allow_redirects)
            body = data.decode("utf-8")
            if "GetDeviceInformation" in body:
                return _response(200, DEVICE_INFO_DEFAULT_NS)
            if "GetCapabilities" in body:
                return _response(200, CAPS_MEDIA_ONLY)
            if "GetProfiles" in body:
                return _response(200, PROFILES_NO_PTZ)
            return _response(200, STREAM_URI_DAHUA)

        self.client._session.post.side_effect = post
        self.assertTrue(self.client.connect_and_initialize())
        self.assertEqual(self.client.device_info["manufacturer"], "ACME")


class _RecordingRedirectAdapter(requests.adapters.BaseAdapter):
    """Adapter HTTP real em memória que simula redirecionamentos e registra todas as requisições."""

    def __init__(self, status_code: int, location: str, target_body: str = DEVICE_INFO_DEFAULT_NS):
        super().__init__()
        self.status_code = status_code
        self.location = location
        self.target_body = target_body
        self.sent_requests = []

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        self.sent_requests.append(request)
        resp = requests.Response()
        resp.url = request.url
        resp.request = request
        resp.encoding = "utf-8"
        if len(self.sent_requests) == 1:
            resp.status_code = self.status_code
            resp.headers["Location"] = self.location
            resp._content = b""
        else:
            resp.status_code = 200
            resp._content = self.target_body.encode("utf-8")
        return resp

    def close(self):
        pass


class _RecordingSoapAdapter(requests.adapters.BaseAdapter):
    """Adapter HTTP real em memória que despacha respostas SOAP por corpo e registra URLs enviadas."""

    def __init__(self, responder):
        super().__init__()
        self.responder = responder
        self.sent_requests = []

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        self.sent_requests.append(request)
        body = request.body.decode("utf-8") if isinstance(request.body, bytes) else (request.body or "")
        status, text = self.responder(request.url, body)
        resp = requests.Response()
        resp.url = request.url
        resp.request = request
        resp.encoding = "utf-8"
        resp.status_code = status
        resp._content = text.encode("utf-8")
        return resp

    def close(self):
        pass


class SecurityRedirectAndXAddrTest(unittest.TestCase):
    """Regressão da Issue #79: bloqueio de redirecionamentos HTTP e validação estrita Anti-SSRF em XAddr."""

    def _client_with_redirect(self, status_code: int, location: str):
        client = OnvifClient(ip="10.0.0.5", port=80, username="admin", password="segredo")
        adapter = _RecordingRedirectAdapter(status_code=status_code, location=location)
        client._session.mount("http://", adapter)
        client._session.mount("https://", adapter)
        return client, adapter

    def test_redirect_error_is_subclass_of_response_error(self):
        self.assertTrue(issubclass(OnvifRedirectError, OnvifResponseError))

    def test_redirect_307_does_not_resend_soap_body_to_other_host(self):
        client, adapter = self._client_with_redirect(307, "http://evil.example.com/steal")
        with self.assertRaises(OnvifRedirectError):
            client.get_device_information()
        self.assertEqual(len(adapter.sent_requests), 1)
        self.assertEqual(adapter.sent_requests[0].url, "http://10.0.0.5:80/onvif/device_service")

    def test_redirect_308_does_not_resend_soap_body_to_other_host(self):
        client, adapter = self._client_with_redirect(308, "http://evil.example.com/steal")
        with self.assertRaises(OnvifRedirectError):
            client.get_device_information()
        self.assertEqual(len(adapter.sent_requests), 1)
        self.assertEqual(adapter.sent_requests[0].url, "http://10.0.0.5:80/onvif/device_service")

    def test_redirect_302_is_rejected_without_following(self):
        client, adapter = self._client_with_redirect(302, "http://127.0.0.1:8080/admin")
        with self.assertRaises(OnvifRedirectError):
            client.get_capabilities()
        self.assertEqual(len(adapter.sent_requests), 1)
        self.assertEqual(adapter.sent_requests[0].url, "http://10.0.0.5:80/onvif/device_service")

    def test_redirect_301_and_303_are_rejected_without_following(self):
        for status in (301, 303):
            with self.subTest(status=status):
                client, adapter = self._client_with_redirect(status, "http://127.0.0.1/interno")
                with self.assertRaises(OnvifRedirectError):
                    client.get_capabilities()
                self.assertEqual(len(adapter.sent_requests), 1)

    def test_validate_xaddr_rejects_non_http_schemes_and_protocol_relative(self):
        client = OnvifClient(ip="10.0.0.5", port=80)
        invalid_xaddrs = [
            "ftp://10.0.0.5/onvif/media_service",
            "file:///etc/passwd",
            "file://10.0.0.5/etc/passwd",
            "//evil.com/onvif/media_service",
            "//10.0.0.5/onvif/media_service",
        ]
        for xaddr in invalid_xaddrs:
            with self.subTest(xaddr=xaddr):
                self.assertIsNone(client._validate_xaddr(xaddr))

    def test_validate_xaddr_rejects_parser_differential_userinfo_and_control_chars(self):
        client = OnvifClient(ip="10.0.0.5", port=80)
        malicious_xaddrs = [
            r"http://evil.com\@10.0.0.5/onvif/media",
            r"http://127.0.0.1:8080\@10.0.0.5/admin",
            "http://user:pass@10.0.0.5/onvif/media",
            "http://@10.0.0.5/x",
            r"http://10.0.0.5\evil/x",
            "http://10.0.0.5/onvif/media service",
            "http://10.0.0.5/onvif/media\n",
            "http://10.0.0.5/onvif/media\r\nHost: evil.com",
            "http://10.0.0.5/onvif/media\t",
            "http://10.0.0.5/onvif/media\x00",
        ]
        for xaddr in malicious_xaddrs:
            with self.subTest(xaddr=xaddr):
                with self.assertLogs("central_nvr.core.onvif_client", level="WARNING") as cm:
                    self.assertIsNone(client._validate_xaddr(xaddr))
                self.assertTrue(any("Anti-SSRF:" in msg for msg in cm.output))

    def test_validate_xaddr_accepts_and_normalizes_valid_urls(self):
        client = OnvifClient(ip="10.0.0.5", port=8080)
        self.assertEqual(
            client._validate_xaddr("http://10.0.0.5:8080/onvif/media_service"),
            "http://10.0.0.5:8080/onvif/media_service",
        )
        self.assertEqual(
            client._validate_xaddr("https://10.0.0.5/onvif/media_service"),
            "https://10.0.0.5/onvif/media_service",
        )
        self.assertEqual(
            client._validate_xaddr("/onvif/media_service"),
            "http://10.0.0.5:8080/onvif/media_service",
        )
        self.assertEqual(
            client._validate_xaddr("HTTP://10.0.0.5:8080/onvif/media_service?v=1#frag"),
            "http://10.0.0.5:8080/onvif/media_service?v=1",
        )
        self.assertEqual(
            client._validate_xaddr("http://10.0.0.5:/onvif/media_service"),
            "http://10.0.0.5/onvif/media_service",
        )

    def test_end_to_end_malicious_media_xaddr_never_requests_other_host(self):
        caps_malicious_media = ENV.format(
            ns=NS,
            body=r"""
<tds:GetCapabilitiesResponse><tds:Capabilities>
  <tt:Media><tt:XAddr>http://evil.com\@10.0.0.5/onvif/media</tt:XAddr></tt:Media>
</tds:Capabilities></tds:GetCapabilitiesResponse>""",
        )

        def responder(url, body):
            if "GetCapabilities" in body:
                return 200, caps_malicious_media
            if "GetProfiles" in body:
                return 200, PROFILES_NO_PTZ
            return 200, STREAM_URI_DAHUA

        client = OnvifClient(ip="10.0.0.5", port=80, username="admin", password="segredo")
        adapter = _RecordingSoapAdapter(responder)
        client._session.mount("http://", adapter)
        client._session.mount("https://", adapter)

        caps = client.get_capabilities()
        self.assertNotIn("media", caps)
        self.assertEqual(client.media_service_url, "http://10.0.0.5:80/onvif/media_service")

        client.get_profiles()
        self.assertGreaterEqual(len(adapter.sent_requests), 2)
        for req in adapter.sent_requests:
            self.assertEqual(urllib3_parse_url(req.url).host, "10.0.0.5")


class IPv6SupportTest(unittest.TestCase):
    """Regressão da Issue #85: URLs de serviço ONVIF e validações Anti-SSRF com endereços IPv6."""

    SERVICE_PATHS = (
        ("device_service_url", "/onvif/device_service"),
        ("media_service_url", "/onvif/media_service"),
        ("ptz_service_url", "/onvif/ptz_service"),
        ("event_service_url", "/onvif/event_service"),
        ("recording_service_url", "/onvif/recording_service"),
        ("search_service_url", "/onvif/search_service"),
        ("replay_service_url", "/onvif/replay_service"),
    )

    def test_init_builds_bracketed_ipv6_urls_accepted_by_requests(self):
        for raw_ip in ("fd00::10", "[fd00::10]"):
            with self.subTest(ip=raw_ip):
                client = OnvifClient(ip=raw_ip, port=80)
                self.assertEqual(client.ip, "fd00::10")
                for attr, path in self.SERVICE_PATHS:
                    url = getattr(client, attr)
                    expected = f"http://[fd00::10]:80{path}"
                    self.assertEqual(url, expected)
                    prepared = requests.Request("POST", url).prepare()
                    self.assertEqual(prepared.url, expected)

    def test_validate_xaddr_accepts_ipv6_absolute_equivalent_and_relative(self):
        client = OnvifClient(ip="fd00::10", port=8080)
        self.assertEqual(
            client._validate_xaddr("http://[fd00::10]:8080/onvif/media_service"),
            "http://[fd00::10]:8080/onvif/media_service",
        )
        self.assertEqual(
            client._validate_xaddr("https://[fd00::10]/onvif/media_service"),
            "https://[fd00::10]/onvif/media_service",
        )
        self.assertEqual(
            client._validate_xaddr("http://[fd00:0::10]:8080/onvif/media_service"),
            "http://[fd00::10]:8080/onvif/media_service",
        )
        self.assertEqual(
            client._validate_xaddr("HTTP://[FD00:0000::0010]:8080/onvif/media_service?v=1#frag"),
            "http://[fd00::10]:8080/onvif/media_service?v=1",
        )
        self.assertEqual(
            client._validate_xaddr("http://[fd00::10]:/onvif/media_service"),
            "http://[fd00::10]/onvif/media_service",
        )
        self.assertEqual(
            client._validate_xaddr("/onvif/media_service"),
            "http://[fd00::10]:8080/onvif/media_service",
        )

    def test_validate_xaddr_rejects_other_ipv6_and_malicious_urls(self):
        client = OnvifClient(ip="fd00::10", port=80)
        rejected = [
            "http://[fd00::11]/onvif/media_service",
            r"http://evil.com\@[fd00::10]/onvif/media_service",
            "http://user:pass@[fd00::10]/onvif/media_service",
            "http://@[fd00::10]/onvif/media_service",
            "http://fd00::10:80/onvif/media_service",
            "ftp://[fd00::10]/onvif/media_service",
            "//[fd00::10]/onvif/media_service",
        ]
        for xaddr in rejected:
            with self.subTest(xaddr=xaddr):
                self.assertIsNone(client._validate_xaddr(xaddr))

    def test_validate_media_uri_ipv6_and_ipv4_isolation(self):
        client = OnvifClient(ip="[fd00::10]", port=80)
        self.assertEqual(
            client._validate_media_uri("rtsp://[fd00::10]:554/stream1"),
            "rtsp://[fd00::10]:554/stream1",
        )
        self.assertEqual(
            client._validate_media_uri("rtsp://[fd00:0::10]:554/stream1"),
            "rtsp://[fd00:0::10]:554/stream1",
        )
        self.assertIsNone(client._validate_media_uri("rtsp://[fd00::11]:554/stream1"))
        self.assertIsNone(client._validate_media_uri("rtsp://evil.example.com/stream1"))
        self.assertIsNone(client._validate_media_uri("ftp://[fd00::10]/stream1"))

        ipv4_client = OnvifClient(ip="10.0.0.5", port=80)
        self.assertIsNone(ipv4_client._validate_xaddr("http://[::ffff:10.0.0.5]/onvif/media_service"))
        self.assertIsNone(ipv4_client._validate_media_uri("rtsp://[::ffff:10.0.0.5]:554/stream1"))

    def test_end_to_end_ipv6_capabilities_profiles_and_ptz(self):
        caps_ipv6 = ENV.format(
            ns=NS,
            body="""
<tds:GetCapabilitiesResponse><tds:Capabilities>
  <tt:Media><tt:XAddr>http://[fd00:0::10]:8080/onvif/Media</tt:XAddr></tt:Media>
  <tt:PTZ><tt:XAddr>/onvif/PTZ</tt:XAddr></tt:PTZ>
</tds:Capabilities></tds:GetCapabilitiesResponse>""",
        )
        stream_ipv6 = ENV.format(
            ns=NS,
            body="""
<trt:GetStreamUriResponse><trt:MediaUri>
  <tt:Uri>rtsp://[fd00:0::10]:554/live/ch0</tt:Uri>
</trt:MediaUri></trt:GetStreamUriResponse>""",
        )
        stop_ok = ENV.format(ns=NS, body="<tptz:StopResponse xmlns:tptz=\"http://www.onvif.org/ver20/ptz/wsdl\"/>")

        def responder(url, body):
            if "GetCapabilities" in body:
                return 200, caps_ipv6
            if "GetProfiles" in body:
                return 200, PROFILES_NO_PTZ
            if "GetStreamUri" in body:
                return 200, stream_ipv6
            return 200, stop_ok

        client = OnvifClient(ip="fd00::10", port=80, username="admin", password="segredo")
        adapter = _RecordingSoapAdapter(responder)
        client._session.mount("http://", adapter)
        client._session.mount("https://", adapter)

        caps = client.get_capabilities()
        self.assertEqual(caps["media"], "http://[fd00::10]:8080/onvif/Media")
        self.assertEqual(caps["ptz"], "http://[fd00::10]:80/onvif/PTZ")
        self.assertTrue(client.has_ptz)

        profiles = client.get_profiles()
        self.assertEqual(profiles[0]["stream_uri"], "rtsp://[fd00:0::10]:554/live/ch0")
        self.assertTrue(client.ptz_stop())

        sent_urls = [req.url for req in adapter.sent_requests]
        self.assertIn("http://[fd00::10]:80/onvif/device_service", sent_urls)
        self.assertIn("http://[fd00::10]:8080/onvif/Media", sent_urls)
        self.assertIn("http://[fd00::10]:80/onvif/PTZ", sent_urls)


class CleanupDecisionsTest(unittest.TestCase):
    """Decisões de triagem da #28: sem zeep e sem a assinatura PullPoint."""

    def test_zeep_removed_from_dependencies(self):
        for name in ("requirements.txt", "pyproject.toml"):
            self.assertNotIn("zeep", (PROJECT_ROOT / name).read_text(encoding="utf-8"), name)

    def test_pull_point_removed(self):
        self.assertFalse(hasattr(OnvifClient, "create_pull_point_subscription"))
        self.assertFalse(hasattr(OnvifClient, "pull_messages"))

    def test_client_does_not_use_regex(self):
        source = (PROJECT_ROOT / "src" / "central_nvr" / "core" / "onvif_client.py").read_text(encoding="utf-8")
        self.assertNotIn("import re\n", source)


if __name__ == "__main__":
    unittest.main()
