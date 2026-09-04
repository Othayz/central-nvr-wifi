"""
Módulo de Descoberta de Rede para Câmeras IP e NVRs.
Implementa WS-Discovery via UDP Multicast (239.255.255.250:3702) e sondagem rápida de portas.
"""
import asyncio
import concurrent.futures
import ipaddress
import logging
import socket
import struct
import threading
import time
import uuid
from typing import Callable, Dict, List, Optional

from central_nvr.scanner.parser import parse_ws_discovery_response

logger = logging.getLogger(__name__)

# Configurações do protocolo WS-Discovery
WS_MULTICAST_GROUP = "239.255.255.250"
WS_MULTICAST_PORT = 3702
COMMON_CAMERA_PORTS = [554, 80, 8080, 8899, 8000, 37777, 5000, 8081, 10554]

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
    """Obtém os endereços IP das interfaces de rede locais ativas."""
    ip_list = []
    try:
        # Método rápido conectando a um IP público sem tráfego real
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            primary_ip = s.getsockname()[0]
            if primary_ip and not primary_ip.startswith("127."):
                ip_list.append(primary_ip)
    except Exception:
        pass

    # Obter IPs adicionais via hostname
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip not in ip_list and not ip.startswith("127."):
                ip_list.append(ip)
    except Exception:
        pass

    if not ip_list:
        ip_list.append("192.168.1.100")

    return ip_list


class NetworkScanner:
    """
    Escaneia a rede local em busca de Câmeras IP e NVRs utilizando
    WS-Discovery (UDP Multicast) e sondagem rápida de portas TCP.
    """

    def __init__(self):
        self._is_scanning = False
        self._stop_requested = False
        self._discovered_devices: Dict[str, Dict[str, any]] = {}
        self._lock = threading.Lock()

    @property
    def is_scanning(self) -> bool:
        return self._is_scanning

    def stop_scan(self):
        """Solicita a interrupção do escaneamento em andamento."""
        self._stop_requested = True

    def scan_network(
        self,
        timeout: float = 4.0,
        enable_port_scan: bool = True,
        on_device_found: Optional[Callable[[Dict[str, any]], None]] = None,
        on_progress: Optional[Callable[[int, str], None]] = None,
    ) -> List[Dict[str, any]]:
        """
        Executa a varredura síncrona/bloqueante de rede.
        Geralmente chamado de dentro de uma QThread ou Worker Thread.
        """
        self._is_scanning = True
        self._stop_requested = False
        self._discovered_devices.clear()

        if on_progress:
            on_progress(5, "Iniciando descoberta WS-Discovery ONVIF...")

        # 1. Enviar WS-Discovery Multicast Probe
        self._run_ws_discovery(timeout=timeout, on_device_found=on_device_found)

        if on_progress:
            on_progress(50, f"ONVIF concluído ({len(self._discovered_devices)} encontrados).")

        # 2. Sondagem de portas TCP nos IPs da sub-rede local (se ativado)
        if enable_port_scan and not self._stop_requested:
            if on_progress:
                on_progress(55, "Sondando portas RTSP e HTTP na sub-rede local...")
            self._run_port_scan(on_device_found=on_device_found, on_progress=on_progress)

        if on_progress:
            on_progress(100, f"Varredura finalizada. Total: {len(self._discovered_devices)} dispositivos.")

        self._is_scanning = False
        return list(self._discovered_devices.values())

    def _run_ws_discovery(
        self,
        timeout: float = 3.0,
        on_device_found: Optional[Callable[[Dict[str, any]], None]] = None,
    ):
        """Dispara mensagens WS-Discovery UDP Multicast e coleta respostas."""
        msg_id = str(uuid.uuid4())
        probe_payload = WS_PROBE_XML_TEMPLATE.format(msg_uuid=msg_id).encode("utf-8")

        local_ips = get_local_ip_addresses()

        # Criar socket UDP para multicast
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 4))
            sock.settimeout(0.3)

            # Enviar para o grupo multicast ONVIF
            sock.sendto(probe_payload, (WS_MULTICAST_GROUP, WS_MULTICAST_PORT))

            # Também enviar broadcast local como garantia
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.sendto(probe_payload, ("255.255.255.255", WS_MULTICAST_PORT))
            except Exception:
                pass

            start_time = time.time()
            while time.time() - start_time < timeout and not self._stop_requested:
                try:
                    data, addr = sock.recvfrom(65535)
                    source_ip = addr[0]
                    xml_str = data.decode("utf-8", errors="ignore")
                    
                    device_info = parse_ws_discovery_response(xml_str, source_ip=source_ip)
                    if device_info:
                        ip = device_info["ip"]
                        with self._lock:
                            if ip not in self._discovered_devices:
                                device_info["source"] = "ONVIF (WS-Discovery)"
                                device_info["status"] = "Online"
                                device_info["rtsp_port"] = 554
                                self._discovered_devices[ip] = device_info
                                if on_device_found:
                                    on_device_found(device_info)
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.debug(f"Erro recebendo datagrama WS-Discovery: {e}")
                    break
        except Exception as e:
            logger.warning(f"Erro ao inicializar socket WS-Discovery: {e}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def _run_port_scan(
        self,
        on_device_found: Optional[Callable[[Dict[str, any]], None]] = None,
        on_progress: Optional[Callable[[int, str], None]] = None,
    ):
        """Sonda rapidamente portas comuns de Câmeras na sub-rede local."""
        local_ips = get_local_ip_addresses()
        if not local_ips:
            return

        # Determinar a sub-rede /24 do primeiro IP local
        primary_ip = local_ips[0]
        try:
            network = ipaddress.ip_network(f"{primary_ip}/24", strict=False)
            hosts = [str(ip) for ip in network.hosts()]
        except Exception:
            return

        total_hosts = len(hosts)
        # Sondagem paralela leve com threads
        threads = []
        max_workers = 32

        def probe_target(ip_addr: str):
            if self._stop_requested:
                return
            
            # Se já descoberto por ONVIF, apenas validar porta RTSP
            with self._lock:
                if ip_addr in self._discovered_devices:
                    return

            # Testar portas dedicadas de streaming de vídeo e NVR (554 RTSP, 8899 ONVIF, 37777 Dahua, 8000 Hikvision)
            has_rtsp = self._check_tcp_port(ip_addr, 554, timeout=0.35)
            has_onvif_port = self._check_tcp_port(ip_addr, 8899, timeout=0.35)
            has_dahua = self._check_tcp_port(ip_addr, 37777, timeout=0.35)
            has_hik = self._check_tcp_port(ip_addr, 8000, timeout=0.35)
            
            if has_rtsp or has_onvif_port or has_dahua or has_hik:
                # Dispositivo de vídeo confirmado
                service_port = 8899 if has_onvif_port else 80
                device_info = {
                    "ip": ip_addr,
                    "port": service_port,
                    "name": f"Câmera IP ({ip_addr})",
                    "model": "Câmera / NVR RTSP",
                    "manufacturer": "Dahua" if has_dahua else ("Hikvision" if has_hik else "Genérico / RTSP"),
                    "location": "Rede Local",
                    "onvif_endpoint": f"http://{ip_addr}:{service_port}/onvif/device_service",
                    "endpoints": [],
                    "types": "RTSP Video Streamer",
                    "scopes": [],
                    "source": "Porta RTSP / CCTV",
                    "status": "Online",
                    "rtsp_port": 554,
                }
                with self._lock:
                    if ip_addr not in self._discovered_devices:
                        self._discovered_devices[ip_addr] = device_info
                        if on_device_found:
                            on_device_found(device_info)

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(probe_target, ip) for ip in hosts]
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                if self._stop_requested:
                    break
                completed += 1
                if on_progress and (completed % 10 == 0 or completed == total_hosts):
                    pct = 55 + int((completed / total_hosts) * 40)
                    on_progress(min(pct, 95), f"Varrendo {completed}/{total_hosts} IPs ({len(self._discovered_devices)} ativos)...")

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
