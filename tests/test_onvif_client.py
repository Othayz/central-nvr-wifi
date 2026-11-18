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

from central_nvr.core.onvif_client import (
    OnvifAuthError,
    OnvifClient,
    OnvifConnectionError,
    OnvifFaultError,
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
        def post(url, data, headers, timeout):
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
