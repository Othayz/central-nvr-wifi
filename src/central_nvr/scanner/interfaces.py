"""
Interfaces de rede IPv4 ativas, só com a biblioteca padrão (Issue #41).

Antes o IP local era descoberto conectando a 8.8.8.8 (falha numa LAN de câmeras sem rota padrão) e
a varredura assumia /24 na primeira interface. Aqui cada interface ativa é lida com
`socket.if_nameindex()` + `ioctl` (SIOCGIFFLAGS / SIOCGIFADDR / SIOCGIFNETMASK), com a máscara real.
Somente Linux e IPv4: o aplicativo é distribuído como AppImage e câmeras IP usam IPv4.
"""
import ipaddress
import logging
import socket
import struct
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

SIOCGIFFLAGS = 0x8913
SIOCGIFADDR = 0x8915
SIOCGIFNETMASK = 0x891B
IFF_UP = 0x1
IFF_LOOPBACK = 0x8
IFF_RUNNING = 0x40

# Sub-redes maiores que isto não são sondadas inteiras (1.022 hosts no /22)
MAX_SCAN_PREFIX = 22


@dataclass(frozen=True)
class Ipv4Interface:
    name: str
    address: str
    netmask: str

    @property
    def network(self) -> ipaddress.IPv4Network:
        return ipaddress.IPv4Network(f"{self.address}/{self.netmask}", strict=False)


def _ioctl(sock: socket.socket, request: int, name: str) -> bytes:
    import fcntl  # Somente POSIX

    return fcntl.ioctl(sock.fileno(), request, struct.pack("256s", name[:15].encode("utf-8")))


def list_ipv4_interfaces() -> List[Ipv4Interface]:
    """Interfaces IPv4 ativas (UP e RUNNING), sem loopback."""
    interfaces: List[Ipv4Interface] = []
    try:
        names = [name for _, name in socket.if_nameindex()]
    except (AttributeError, OSError) as e:
        logger.debug(f"if_nameindex indisponível: {e}")
        return interfaces

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for name in names:
            try:
                flags = struct.unpack("H", _ioctl(sock, SIOCGIFFLAGS, name)[16:18])[0]
                if not (flags & IFF_UP and flags & IFF_RUNNING) or flags & IFF_LOOPBACK:
                    continue
                address = socket.inet_ntoa(_ioctl(sock, SIOCGIFADDR, name)[20:24])
                netmask = socket.inet_ntoa(_ioctl(sock, SIOCGIFNETMASK, name)[20:24])
            except (OSError, ImportError):
                continue  # Sem IPv4 configurado
            if address.startswith("127.") or address.startswith("169.254."):
                continue
            interfaces.append(Ipv4Interface(name=name, address=address, netmask=netmask))
    return interfaces


def scan_targets(interface: Ipv4Interface) -> Tuple[Optional[ipaddress.IPv4Network], Optional[str]]:
    """
    Sub-rede a sondar nesta interface e um aviso, se houver:
    - até /22: a sub-rede inteira;
    - maior que /22: só o /24 do próprio IP (não varrer uma rede corporativa inteira sem querer);
    - /31 e /32: nada (sem outros hosts).
    """
    network = interface.network
    if network.prefixlen >= 31:
        return None, None
    if network.prefixlen >= MAX_SCAN_PREFIX:
        return network, None
    reduced = ipaddress.IPv4Network(f"{interface.address}/24", strict=False)
    warning = (
        f"Rede {network} em {interface.name} é grande demais para sondar inteira: "
        f"sondando apenas {reduced}."
    )
    return reduced, warning
