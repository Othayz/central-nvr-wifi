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
from central_nvr.scanner.parser import parse_ws_discovery_matches

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


if __name__ == "__main__":
    unittest.main()
