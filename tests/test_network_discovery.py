#!/usr/bin/env python3
"""
Testes da descoberta de rede (Issue #41): vários ProbeMatch, interfaces e sub-redes reais, limite
de tamanho e encerramento da varredura ao fechar o diálogo.
"""
import ipaddress
import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from PySide6.QtWidgets import QApplication

from central_nvr.scanner import discovery
from central_nvr.scanner.discovery import NetworkScanner
from central_nvr.scanner.interfaces import Ipv4Interface, list_ipv4_interfaces, scan_targets
from central_nvr.scanner.parser import (
    _parse_valid_xaddr,
    parse_ws_discovery_fallback,
    parse_ws_discovery_matches,
)

TWO_MATCHES = """<?xml version="1.0"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
<s:Body><d:ProbeMatches>
  <d:ProbeMatch><d:Types>dn:NetworkVideoTransmitter</d:Types>
    <d:Scopes>onvif://www.onvif.org/name/Canal_1</d:Scopes>
    <d:XAddrs>http://192.168.1.60:80/onvif/device_service</d:XAddrs></d:ProbeMatch>
  <d:ProbeMatch><d:Types>dn:NetworkVideoTransmitter</d:Types>
    <d:Scopes>onvif://www.onvif.org/name/Canal_2</d:Scopes>
    <d:XAddrs>http://192.168.1.60:8080/onvif/device_service</d:XAddrs></d:ProbeMatch>
</d:ProbeMatches></s:Body></s:Envelope>"""


def _iface(name, address, netmask):
    return Ipv4Interface(name=name, address=address, netmask=netmask)


class ParserTest(unittest.TestCase):
    def test_two_probe_matches_give_two_devices(self):
        devices = parse_ws_discovery_matches(TWO_MATCHES, source_ip="192.168.1.60")
        self.assertEqual(len(devices), 2)
        self.assertEqual([d["name"] for d in devices], ["Canal 1", "Canal 2"])
        self.assertEqual([d["port"] for d in devices], [80, 8080])

    def test_devices_on_same_ip_are_not_collapsed(self):
        scanner = NetworkScanner()
        found = []
        for d in parse_ws_discovery_matches(TWO_MATCHES, source_ip="192.168.1.60"):
            scanner._register(d, found.append)
        self.assertEqual(len(found), 2)

    def test_malformed_xaddrs_discarded_without_error(self):
        """Issue #82: XAddrs malformados ou sem hostname são descartados sem ValueError."""
        bad_xaddrs = [
            "http://[::1/onvif/device_service",
            "http://192.168.1.50:99999/onvif/device_service",
            "http://192.168.1.50:abc/onvif/device_service",
            "http:///evil/path",
        ]
        for bad in bad_xaddrs:
            xml_ok = (
                '<?xml version="1.0"?>'
                '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
                'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
                f"<s:Body><d:ProbeMatches><d:ProbeMatch><d:XAddrs>{bad}</d:XAddrs>"
                "</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"
            )
            xml_truncated = f"<d:ProbeMatch><d:XAddrs>{bad}</d:XAddrs>"

            for label, parse_fn, payload in (
                ("xml", lambda p, ip: parse_ws_discovery_matches(p, source_ip=ip)[0], xml_ok),
                ("fallback", lambda p, ip: parse_ws_discovery_fallback(p, source_ip=ip), xml_truncated),
            ):
                with self.subTest(mode=label, xaddr=bad, with_source_ip=True):
                    dev = parse_fn(payload, "192.168.1.50")
                    self.assertIsNotNone(dev)
                    self.assertEqual(dev["ip"], "192.168.1.50")
                    self.assertEqual(dev["port"], 80)
                    self.assertEqual(dev["endpoints"], [])
                    self.assertEqual(dev["onvif_endpoint"], "http://192.168.1.50:80/onvif/device_service")

                with self.subTest(mode=label, xaddr=bad, with_source_ip=False):
                    dev = parse_fn(payload, "")
                    self.assertIsNotNone(dev)
                    self.assertEqual(dev["ip"], "")
                    self.assertEqual(dev["endpoints"], [])
                    self.assertEqual(dev["onvif_endpoint"], "")

    def test_malformed_xaddr_skipped_when_valid_xaddr_follows(self):
        """Issue #82: XAddr inválido na lista não impede uso do XAddr válido seguinte."""
        xaddrs = "http://[::1/onvif/device_service http:///evil/path http://192.168.1.50:8080/onvif/device_service"
        xml_ok = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
            f"<s:Body><d:ProbeMatches><d:ProbeMatch><d:XAddrs>{xaddrs}</d:XAddrs>"
            "</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"
        )
        xml_truncated = f"<d:ProbeMatch><d:XAddrs>{xaddrs}</d:XAddrs>"

        for dev in (
            parse_ws_discovery_matches(xml_ok, source_ip="192.168.1.50")[0],
            parse_ws_discovery_fallback(xml_truncated, source_ip="192.168.1.50"),
        ):
            self.assertEqual(dev["ip"], "192.168.1.50")
            self.assertEqual(dev["port"], 8080)
            self.assertEqual(dev["onvif_endpoint"], "http://192.168.1.50:8080/onvif/device_service")
            self.assertEqual(dev["endpoints"], ["http://192.168.1.50:8080/onvif/device_service"])

    def test_xaddr_with_userinfo_backslash_or_control_chars_is_rejected(self):
        """Issue #102: XAddrs com userinfo (@), barra invertida ou controles/espaços são descartados."""
        malicious_xaddrs = [
            "http://evil.com@192.168.1.50:80/onvif/device_service",
            "http://user:pass@192.168.1.50/onvif/device_service",
            "http://@192.168.1.50/onvif/device_service",
            "http://:pass@192.168.1.50/onvif/device_service",
            r"http://evil.com\@192.168.1.50/onvif/device_service",
            r"http://127.0.0.1:8080\@192.168.1.50/admin",
            r"http://192.168.1.50\evil/onvif/device_service",
            "http://192.168.1.50/onvif/device service",
            "http://192.168.1.50/onvif/device\u00a0service",
            "http://192.168.1.50/onvif/device\tservice",
            "http://192.168.1.50/onvif/device\n",
            "http://192.168.1.50/onvif/device\r\nHost: evil.com",
            "http://192.168.1.50/onvif/device\x00",
            "http://192.168.1.50/onvif/device\x1f",
            "http://192.168.1.50/onvif/device\x7f",
            "http://192.168.1.50/onvif/device\x80",
            "http://192.168.1.50/onvif/device\x9f",
        ]
        for bad in malicious_xaddrs:
            with self.subTest(direct_xaddr=bad):
                self.assertIsNone(_parse_valid_xaddr(bad, "192.168.1.50"))
                self.assertIsNone(_parse_valid_xaddr(bad, ""))

        # Também validado ponta a ponta no parser XML e no fallback regex (sem persistir URL ambígua)
        xml_safe_malicious = [
            "http://evil.com@192.168.1.50:80/onvif/device_service",
            r"http://evil.com\@192.168.1.50/onvif/device_service",
            "http://192.168.1.50/onvif/device\x7f",
            "http://192.168.1.50/onvif/device\x80",
            "http://192.168.1.50/onvif/device\x9f",
        ]
        for bad in xml_safe_malicious:
            xml_ok = (
                '<?xml version="1.0"?>'
                '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
                'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
                f"<s:Body><d:ProbeMatches><d:ProbeMatch><d:XAddrs>{bad}</d:XAddrs>"
                "</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"
            )
            xml_truncated = f"<d:ProbeMatch><d:XAddrs>{bad}</d:XAddrs>"
            for label, dev in (
                ("xml", parse_ws_discovery_matches(xml_ok, source_ip="192.168.1.50")[0]),
                ("fallback", parse_ws_discovery_fallback(xml_truncated, source_ip="192.168.1.50")),
            ):
                with self.subTest(mode=label, xaddr=bad):
                    self.assertEqual(dev["endpoints"], [])
                    self.assertEqual(dev["onvif_endpoint"], "http://192.168.1.50:80/onvif/device_service")

    def test_valid_xaddr_is_normalized_with_port_and_without_fragment(self):
        """Issue #102: XAddr válido é normalizado (esquema minúsculo, porta explícita, sem fragmento)."""
        cases = [
            (
                "http://192.168.1.50/onvif/device_service",
                "192.168.1.50",
                ("http://192.168.1.50:80/onvif/device_service", "192.168.1.50", 80),
            ),
            (
                "https://192.168.1.50/onvif/device_service",
                "192.168.1.50",
                ("https://192.168.1.50:443/onvif/device_service", "192.168.1.50", 443),
            ),
            (
                "HTTP://192.168.1.50/onvif/device_service?v=1#frag",
                "192.168.1.50",
                ("http://192.168.1.50:80/onvif/device_service?v=1", "192.168.1.50", 80),
            ),
            (
                "HTTPS://192.168.1.50:8443/onvif/device_service#section",
                "192.168.1.50",
                ("https://192.168.1.50:8443/onvif/device_service", "192.168.1.50", 8443),
            ),
            (
                "http://192.168.1.50:/onvif/device_service",
                "192.168.1.50",
                ("http://192.168.1.50:80/onvif/device_service", "192.168.1.50", 80),
            ),
        ]
        for raw, source_ip, expected in cases:
            with self.subTest(xaddr=raw):
                self.assertEqual(_parse_valid_xaddr(raw, source_ip), expected)

        # Lista com XAddr malicioso antes de um válido descarta o malicioso e normaliza o válido
        xaddrs = (
            "http://evil.com@192.168.1.50:80/onvif/device_service "
            r"http://evil.com\@192.168.1.50/onvif/device_service "
            "HTTP://192.168.1.50/onvif/device_service#frag"
        )
        xml_ok = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
            f"<s:Body><d:ProbeMatches><d:ProbeMatch><d:XAddrs>{xaddrs}</d:XAddrs>"
            "</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"
        )
        xml_truncated = f"<d:ProbeMatch><d:XAddrs>{xaddrs}</d:XAddrs>"
        for dev in (
            parse_ws_discovery_matches(xml_ok, source_ip="192.168.1.50")[0],
            parse_ws_discovery_fallback(xml_truncated, source_ip="192.168.1.50"),
        ):
            self.assertEqual(dev["ip"], "192.168.1.50")
            self.assertEqual(dev["port"], 80)
            self.assertEqual(dev["onvif_endpoint"], "http://192.168.1.50:80/onvif/device_service")
            self.assertEqual(dev["endpoints"], ["http://192.168.1.50:80/onvif/device_service"])

    def test_xaddr_rejects_divergent_host_invalid_scheme_and_zero_port(self):
        """Issue #102: mantém rejeição de host divergente, esquema inválido e porta 0."""
        rejected = [
            ("http://10.99.99.99:80/onvif/device_service", "192.168.1.50"),
            ("ftp://192.168.1.50/onvif/device_service", "192.168.1.50"),
            ("file://192.168.1.50/etc/passwd", "192.168.1.50"),
            ("//192.168.1.50/onvif/device_service", "192.168.1.50"),
            ("http://192.168.1.50:0/onvif/device_service", "192.168.1.50"),
            ("http://[::ffff:192.168.1.50]/onvif/device_service", "192.168.1.50"),
            ("", "192.168.1.50"),
        ]
        for xaddr, source_ip in rejected:
            with self.subTest(xaddr=xaddr, source_ip=source_ip):
                self.assertIsNone(_parse_valid_xaddr(xaddr, source_ip))

    def test_xaddr_ipv6_reconstructed_with_brackets_and_validated(self):
        """Issue #102 / #85: XAddr IPv6 válido é normalizado com colchetes e variantes maliciosas são rejeitadas."""
        self.assertEqual(
            _parse_valid_xaddr("http://[fd00::10]/onvif/device_service", "fd00::10"),
            ("http://[fd00::10]:80/onvif/device_service", "fd00::10", 80),
        )
        self.assertEqual(
            _parse_valid_xaddr("HTTPS://[FD00:0000::0010]/onvif/device_service?v=1#frag", "fd00::10"),
            ("https://[fd00::10]:443/onvif/device_service?v=1", "fd00::10", 443),
        )
        self.assertEqual(
            _parse_valid_xaddr("http://[fd00::10]:8080/onvif/device_service", "[fd00::10]"),
            ("http://[fd00::10]:8080/onvif/device_service", "fd00::10", 8080),
        )

        for bad in (
            "http://[fd00::11]/onvif/device_service",
            "http://evil.com@[fd00::10]/onvif/device_service",
            r"http://evil.com\@[fd00::10]/onvif/device_service",
            "http://fd00::10:80/onvif/device_service",
        ):
            with self.subTest(bad_ipv6=bad):
                self.assertIsNone(_parse_valid_xaddr(bad, "fd00::10"))

        xml_bad_ipv6 = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
            "<s:Body><d:ProbeMatches><d:ProbeMatch>"
            "<d:XAddrs>http://evil.com@[fd00::10]/onvif/device_service</d:XAddrs>"
            "</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"
        )
        dev = parse_ws_discovery_matches(xml_bad_ipv6, source_ip="fd00::10")[0]
        self.assertEqual(dev["endpoints"], [])
        self.assertEqual(dev["onvif_endpoint"], "http://[fd00::10]:80/onvif/device_service")


class InterfacesTest(unittest.TestCase):
    def test_lists_real_interfaces(self):
        for iface in list_ipv4_interfaces():
            ipaddress.IPv4Address(iface.address)
            self.assertFalse(iface.address.startswith("127."))
            self.assertIsInstance(iface.network, ipaddress.IPv4Network)

    def test_large_network_scans_only_own_24(self):
        network, warning = scan_targets(_iface("eth0", "10.20.30.40", "255.255.0.0"))
        self.assertEqual(network, ipaddress.IPv4Network("10.20.30.0/24"))
        self.assertIn("10.20.0.0/16", warning)

    def test_slash_23_is_scanned_whole(self):
        network, warning = scan_targets(_iface("wlan0", "192.168.2.10", "255.255.254.0"))
        self.assertEqual(network, ipaddress.IPv4Network("192.168.2.0/23"))
        self.assertIsNone(warning)

    def test_point_to_point_is_skipped(self):
        self.assertEqual(scan_targets(_iface("tun0", "10.8.0.2", "255.255.255.255")), (None, None))


class ScannerTest(unittest.TestCase):
    def test_no_interface_scans_nothing(self):
        scanner = NetworkScanner()
        with patch.object(discovery, "list_ipv4_interfaces", return_value=[]), \
                patch.object(NetworkScanner, "_check_tcp_port") as check, \
                patch.object(NetworkScanner, "_open_probe_socket") as open_sock:
            result = scanner.scan_network(timeout=0.1)
        self.assertEqual(result, [])
        check.assert_not_called()
        open_sock.assert_not_called()
        self.assertTrue(scanner.warnings)

    def test_probe_sent_through_every_interface(self):
        interfaces = [_iface("eth0", "192.168.1.5", "255.255.255.0"),
                      _iface("eth1", "10.0.0.5", "255.255.255.0")]
        created = []

        def fake_socket(*args, **kwargs):
            sock = MagicMock()
            created.append(sock)
            return sock

        scanner = NetworkScanner()
        with patch.object(discovery.socket, "socket", side_effect=fake_socket), \
                patch.object(discovery.select, "select", return_value=([], [], [])):
            scanner._run_ws_discovery(interfaces, timeout=0.05)

        self.assertEqual(len(created), 2)
        bound = [s.bind.call_args[0][0][0] for s in created]
        self.assertEqual(bound, ["192.168.1.5", "10.0.0.5"])
        for sock, iface in zip(created, interfaces):
            mcast_if = [c for c in sock.setsockopt.call_args_list
                        if c[0][1] == discovery.socket.IP_MULTICAST_IF]
            self.assertEqual(mcast_if[0][0][2], discovery.socket.inet_aton(iface.address))
            sock.sendto.assert_called_once()

    def test_port_scan_covers_each_interface_subnet(self):
        interfaces = [_iface("eth0", "192.168.1.5", "255.255.255.252"),   # /30: 1 outro host
                      _iface("eth1", "10.0.0.5", "255.255.255.252")]
        probed = set()

        def check(ip, port, timeout=0.35):
            probed.add(ip)
            return False

        scanner = NetworkScanner()
        with patch.object(NetworkScanner, "_check_tcp_port", side_effect=check):
            scanner._run_port_scan(interfaces)
        self.assertEqual(probed, {"192.168.1.6", "10.0.0.6"})

    def test_stop_cancels_pending_probes_quickly(self):
        interfaces = [_iface("eth0", "192.168.50.5", "255.255.252.0")]  # /22: ~1000 hosts
        scanner = NetworkScanner()

        def slow_check(ip, port, timeout=0.35):
            time.sleep(0.2)
            return False

        with patch.object(NetworkScanner, "_check_tcp_port", side_effect=slow_check):
            t = threading.Thread(target=scanner._run_port_scan, args=(interfaces,))
            t.start()
            time.sleep(0.3)
            started = time.monotonic()
            scanner.stop_scan()
            t.join(5)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - started, 2.0)

    def test_bad_ws_discovery_packet_does_not_block_next_valid_packet(self):
        """Issue #82: pacote WS-Discovery ruim é ignorado e o pacote válido seguinte é processado."""
        interfaces = [_iface("eth0", "192.168.1.5", "255.255.255.0")]
        bad_packet = (
            b'<?xml version="1.0"?>'
            b'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
            b'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">'
            b"<s:Body><d:ProbeMatches><d:ProbeMatch>"
            b"<d:XAddrs>http://[::1/onvif/device_service</d:XAddrs>"
            b"</d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"
        )
        valid_packet = TWO_MATCHES.encode("utf-8")

        fake_sock = MagicMock()
        packets = [
            (bad_packet, ("192.168.1.99", 3702)),
            (valid_packet, ("192.168.1.60", 3702)),
        ]
        fake_sock.recvfrom.side_effect = packets

        select_calls = {"n": 0}

        def fake_select(rlist, wlist, xlist, timeout):
            select_calls["n"] += 1
            if select_calls["n"] <= len(packets):
                return (rlist, [], [])
            scanner.stop_scan()
            return ([], [], [])

        scanner = NetworkScanner()
        with patch.object(discovery, "list_ipv4_interfaces", return_value=interfaces), \
                patch.object(NetworkScanner, "_open_probe_socket", return_value=fake_sock), \
                patch.object(discovery.select, "select", side_effect=fake_select):
            devices = scanner.scan_network(timeout=1.0, enable_port_scan=False)

        self.assertFalse(scanner.is_scanning)
        ips = {d["ip"] for d in devices}
        self.assertIn("192.168.1.60", ips)
        self.assertEqual([d["name"] for d in devices if d["ip"] == "192.168.1.60"], ["Canal 1", "Canal 2"])

    def test_parser_exception_in_ws_discovery_loop_is_isolated(self):
        """Issue #82: exceção inesperada no parser de um pacote não interrompe a varredura."""
        interfaces = [_iface("eth0", "192.168.1.5", "255.255.255.0")]
        fake_sock = MagicMock()
        packets = [
            (b"<bad/>", ("192.168.1.99", 3702)),
            (TWO_MATCHES.encode("utf-8"), ("192.168.1.60", 3702)),
        ]
        fake_sock.recvfrom.side_effect = packets

        real_parse = parse_ws_discovery_matches
        parse_calls = {"n": 0}

        def flaky_parse(xml_str, source_ip=""):
            parse_calls["n"] += 1
            if parse_calls["n"] == 1:
                raise ValueError("pacote corrompido simulado")
            return real_parse(xml_str, source_ip=source_ip)

        select_calls = {"n": 0}

        def fake_select(rlist, wlist, xlist, timeout):
            select_calls["n"] += 1
            if select_calls["n"] <= len(packets):
                return (rlist, [], [])
            scanner.stop_scan()
            return ([], [], [])

        scanner = NetworkScanner()
        with patch.object(discovery, "list_ipv4_interfaces", return_value=interfaces), \
                patch.object(NetworkScanner, "_open_probe_socket", return_value=fake_sock), \
                patch.object(discovery.select, "select", side_effect=fake_select), \
                patch.object(discovery, "parse_ws_discovery_matches", side_effect=flaky_parse):
            devices = scanner.scan_network(timeout=1.0, enable_port_scan=False)

        self.assertFalse(scanner.is_scanning)
        self.assertEqual(len(devices), 2)
        self.assertEqual([d["name"] for d in devices], ["Canal 1", "Canal 2"])

    def test_is_scanning_resets_to_false_on_unexpected_error(self):
        """Issue #82: _is_scanning volta para False mesmo se ocorrer erro inesperado."""
        scanner = NetworkScanner()
        with patch.object(discovery, "list_ipv4_interfaces", side_effect=RuntimeError("falha de rede")):
            with self.assertRaises(RuntimeError):
                scanner.scan_network(timeout=0.1)
        self.assertFalse(scanner.is_scanning)


class DialogLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_closing_dialog_with_any_button_stops_scan(self):
        from central_nvr.core.threads import drain_retired_threads
        from central_nvr.ui.discovery_dialog import DiscoveryDialog
        release = threading.Event()

        def blocking_scan(*args, **kwargs):
            release.wait(5)
            return []

        with patch.object(NetworkScanner, "scan_network", side_effect=blocking_scan):
            for close in ("accept", "reject"):
                dialog = DiscoveryDialog()
                dialog._toggle_scan()
                worker = dialog.worker
                self.assertTrue(worker.isRunning())
                getattr(dialog, close)()  # "Fechar" chama accept(); Esc chama reject()
                self.assertIsNone(dialog.worker)
                self.assertTrue(worker.scanner._stop_requested)
                self.assertTrue(worker.isInterruptionRequested())
                dialog.deleteLater()
            release.set()
            self.assertTrue(drain_retired_threads(5000))

    def test_scanner_worker_emits_scan_finished_and_unlocks_ui_on_error(self):
        """Issue #82: exceção em scan_network() ainda emite scan_finished e destrava o diálogo."""
        from central_nvr.ui.discovery_dialog import DiscoveryDialog

        dialog = DiscoveryDialog()
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)

        finished_payloads = []
        orig_on_finished = dialog._on_scan_finished

        def track_finished(devices):
            finished_payloads.append(devices)
            orig_on_finished(devices)

        dialog._on_scan_finished = track_finished
        with patch.object(NetworkScanner, "scan_network", side_effect=ValueError("URL inválida")):
            dialog._toggle_scan()
            worker = dialog.worker
            self.assertTrue(worker.wait(5000))
            self.app.processEvents()

        self.assertEqual(finished_payloads, [[]])
        self.assertEqual(dialog.btn_scan.text(), "🔍 Iniciar Varredura de Rede")
        self.assertIn("URL inválida", dialog.lbl_scan_status.text())


if __name__ == "__main__":
    unittest.main()
