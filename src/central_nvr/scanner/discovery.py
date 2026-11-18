"""
Módulo de Descoberta de Rede para Câmeras IP e NVRs.
Implementa WS-Discovery via UDP Multicast (239.255.255.250:3702) e sondagem rápida de portas.
"""
import concurrent.futures
import logging
import select
import socket
import struct
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from central_nvr.scanner.interfaces import Ipv4Interface, list_ipv4_interfaces, scan_targets
from central_nvr.scanner.parser import parse_ws_discovery_matches

logger = logging.getLogger(__name__)

# Configurações do protocolo WS-Discovery
WS_MULTICAST_GROUP = "239.255.255.250"
WS_MULTICAST_PORT = 3702

# Envelope SOAP WS-Discovery Probe para busca de transmissores de vídeo ONVIF
WS_PROBE_XML_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<Envelope xmlns:dn="http://www.onvif.org/ver10/network/wsdl" 
          xmlns="http://www.w3.org/2003/05/soap-envelope" 
          xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing">
    <Header>
        <wsa:MessageID>uuid:{msg_uuid}</wsa:MessageID>
        <wsa:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</wsa:To>
        <wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</wsa:Action>
    </Header>
    <Body>
        <Probe xmlns="http://schemas.xmlsoap.org/ws/2005/04/discovery" 
               xmlns:types="http://www.onvif.org/ver10/schema">
            <Types>dn:NetworkVideoTransmitter types:NetworkVideoTransmitter</Types>
        </Probe>
    </Body>
</Envelope>"""


def get_local_ip_addresses() -> List[str]:
    """IPs das interfaces IPv4 ativas (lista vazia sem rede; nunca um endereço fictício)."""
    return [iface.address for iface in list_ipv4_interfaces()]


class NetworkScanner:
    """
    Escaneia a rede local em busca de Câmeras IP e NVRs utilizando
    WS-Discovery (UDP Multicast, por interface) e sondagem rápida de portas TCP.
    """

    def __init__(self):
        self._is_scanning = False
        self._stop_requested = False
        # Chave: endpoint ONVIF (dispositivos distintos no mesmo IP não colapsam)
        self._discovered_devices: Dict[str, Dict[str, Any]] = {}
        self._discovered_ips: set = set()
        self._lock = threading.Lock()
        self._executor: Optional[concurrent.futures.ThreadPoolExecutor] = None
        self.warnings: List[str] = []

    @property
    def is_scanning(self) -> bool:
        return self._is_scanning

    def stop_scan(self):
        """Solicita a interrupção e cancela as sondas ainda não iniciadas."""
        self._stop_requested = True
        executor = self._executor
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def scan_network(
        self,
        timeout: float = 4.0,
        enable_port_scan: bool = True,
        on_device_found: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_progress: Optional[Callable[[int, str], None]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Executa a varredura síncrona/bloqueante de rede.
        Geralmente chamado de dentro de uma QThread ou Worker Thread.
        """
        self._is_scanning = True
        self._stop_requested = False
        self._discovered_devices.clear()
        self._discovered_ips.clear()
        self.warnings = []

        interfaces = list_ipv4_interfaces()
        if not interfaces:
            self.warnings.append("Nenhuma interface de rede IPv4 ativa: conecte-se à rede das câmeras e tente de novo.")
            if on_progress:
                on_progress(100, self.warnings[-1])
            self._is_scanning = False
            return []

        if on_progress:
            names = ", ".join(f"{i.name} ({i.network})" for i in interfaces)
            on_progress(5, f"Iniciando descoberta WS-Discovery ONVIF em {names}...")

        # 1. WS-Discovery por interface
        self._run_ws_discovery(interfaces, timeout=timeout, on_device_found=on_device_found)

        if on_progress:
            on_progress(50, f"ONVIF concluído ({len(self._discovered_devices)} encontrados).")

        # 2. Sondagem de portas TCP em cada sub-rede (se ativado)
        if enable_port_scan and not self._stop_requested:
            if on_progress:
                on_progress(55, "Sondando portas RTSP e HTTP nas sub-redes locais...")
            self._run_port_scan(interfaces, on_device_found=on_device_found, on_progress=on_progress)

        if on_progress:
            summary = f"Varredura finalizada. Total: {len(self._discovered_devices)} dispositivos."
            if self.warnings:
                summary += " " + " ".join(self.warnings)
            on_progress(100, summary)

        self._is_scanning = False
        return list(self._discovered_devices.values())

    def _register(self, device_info: Dict[str, Any], on_device_found) -> None:
        key = device_info.get("onvif_endpoint") or f"{device_info['ip']}:{device_info.get('port', 80)}"
        with self._lock:
            if key in self._discovered_devices:
                return
            self._discovered_devices[key] = device_info
            self._discovered_ips.add(device_info["ip"])
        if on_device_found:
            on_device_found(device_info)

    @staticmethod
    def _open_probe_socket(interface: Ipv4Interface) -> socket.socket:
        """Socket UDP preso à interface: o probe multicast sai por ela e as respostas voltam nela."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 4))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface.address))
        sock.bind((interface.address, 0))
        sock.setblocking(False)
        return sock

    def _run_ws_discovery(
        self,
        interfaces: List[Ipv4Interface],
        timeout: float = 3.0,
        on_device_found: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        """Envia o Probe WS-Discovery por cada interface e coleta as respostas de todas."""
        probe_payload = WS_PROBE_XML_TEMPLATE.format(msg_uuid=str(uuid.uuid4())).encode("utf-8")
        sockets: List[socket.socket] = []
        try:
            for interface in interfaces:
                try:
                    sock = self._open_probe_socket(interface)
                    sockets.append(sock)
                    sock.sendto(probe_payload, (WS_MULTICAST_GROUP, WS_MULTICAST_PORT))
                except OSError as e:
                    logger.warning(f"WS-Discovery indisponível em {interface.name} ({interface.address}): {e}")

            deadline = time.monotonic() + timeout
            while sockets and not self._stop_requested:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                readable, _, _ = select.select(sockets, [], [], min(0.3, remaining))
                for sock in readable:
                    try:
                        data, addr = sock.recvfrom(65535)
                    except OSError:
                        continue
                    xml_str = data.decode("utf-8", errors="ignore")
                    for device_info in parse_ws_discovery_matches(xml_str, source_ip=addr[0]):
                        device_info["source"] = "ONVIF (WS-Discovery)"
                        device_info["method"] = "ws-discovery"
                        device_info["status"] = "Online"
                        self._register(device_info, on_device_found)
        finally:
            for sock in sockets:
                try:
                    sock.close()
                except OSError:
                    pass

    def _run_port_scan(
        self,
        interfaces: List[Ipv4Interface],
        on_device_found: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_progress: Optional[Callable[[int, str], None]] = None,
    ):
        """Sonda portas comuns de câmeras em cada sub-rede local (com limite de tamanho)."""
        own_addresses = {i.address for i in interfaces}
        hosts: List[str] = []
        for interface in interfaces:
            network, warning = scan_targets(interface)
            if warning:
                self.warnings.append(warning)
                logger.info(warning)
            if network is None:
                continue
            for ip in network.hosts():
                ip_str = str(ip)
                if ip_str not in own_addresses and ip_str not in hosts:
                    hosts.append(ip_str)
        if not hosts:
            return

        total_hosts = len(hosts)

        def probe_target(ip_addr: str):
            # Já descoberto por WS-Discovery: não precisa sondar portas
            with self._lock:
                if ip_addr in self._discovered_ips:
                    return

            # Portas de streaming e NVR (554 RTSP, 8899 ONVIF, 37777 Dahua, 8000 Hikvision)
            open_ports = set()
            for port in (554, 8899, 37777, 8000):
                if self._stop_requested:
                    return
                if self._check_tcp_port(ip_addr, port, timeout=0.35):
                    open_ports.add(port)

            if open_ports:
                service_port = 8899 if 8899 in open_ports else 80
                device_info = {
                    "ip": ip_addr,
                    "port": service_port,
                    "name": f"Câmera IP ({ip_addr})",
                    "model": "Câmera / NVR RTSP",
                    "manufacturer": "Dahua" if 37777 in open_ports else ("Hikvision" if 8000 in open_ports else "Genérico / RTSP"),
                    "location": "Rede Local",
                    "onvif_endpoint": f"http://{ip_addr}:{service_port}/onvif/device_service",
                    "endpoints": [],
                    "types": "RTSP Video Streamer",
                    "scopes": [],
                    "source": "Porta RTSP / CCTV",
                    "method": "port-scan",
                    "status": "Online",
                }
                if 554 in open_ports:
                    device_info["rtsp_port"] = 554
                self._register(device_info, on_device_found)

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=32)
        self._executor = executor
        try:
            futures = [executor.submit(probe_target, ip) for ip in hosts]
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                if self._stop_requested:
                    break
                completed += 1
                if on_progress and (completed % 10 == 0 or completed == total_hosts):
                    pct = 55 + int((completed / total_hosts) * 40)
                    on_progress(min(pct, 95), f"Varrendo {completed}/{total_hosts} IPs ({len(self._discovered_devices)} ativos)...")
        finally:
            # Ao parar, não espera as sondas em andamento nem inicia as pendentes
            executor.shutdown(wait=not self._stop_requested, cancel_futures=True)
            self._executor = None

    @staticmethod
    def _check_tcp_port(ip: str, port: int, timeout: float = 0.35) -> bool:
        """Verifica se uma porta TCP está respondendo no IP alvo."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                result = sock.connect_ex((ip, port))
                return result == 0
        except Exception:
            return False
