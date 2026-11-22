"""
Parser para mensagens XML SOAP de WS-Discovery e ONVIF com validação Anti-SSRF.
"""
import logging
import re
import urllib.parse
try:
    import defusedxml.ElementTree as SafeET
    from defusedxml.common import DefusedXmlException
    HAS_DEFUSEDXML = True
except ImportError:
    import xml.etree.ElementTree as SafeET
    class DefusedXmlException(Exception):
        pass
    HAS_DEFUSEDXML = False
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Namespaces padrão do WS-Discovery e ONVIF
NAMESPACES = {
    "soap": "http://www.w3.org/2003/05/soap-envelope",
    "wsa": "http://schemas.xmlsoap.org/ws/2004/08/addressing",
    "wsd": "http://schemas.xmlsoap.org/ws/2005/04/discovery",
    "onvif": "http://www.onvif.org/ver10/schema",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
}


def _local_name(tag) -> str:
    return tag.split("}")[-1] if isinstance(tag, str) and "}" in tag else (tag if isinstance(tag, str) else "")


def _match_fields(elem) -> Tuple[str, str, str]:
    """XAddrs, Scopes e Types dentro de um elemento (um ProbeMatch ou o documento inteiro)."""
    xaddrs_raw = scopes_raw = types_raw = ""
    for child in elem.iter():
        tag_name = _local_name(child.tag)
        if tag_name == "XAddrs" and child.text and not xaddrs_raw:
            xaddrs_raw = child.text.strip()
        elif tag_name == "Scopes" and child.text and not scopes_raw:
            scopes_raw = child.text.strip()
        elif tag_name == "Types" and child.text and not types_raw:
            types_raw = child.text.strip()
    return xaddrs_raw, scopes_raw, types_raw


def parse_ws_discovery_matches(xml_data: str, source_ip: str = "") -> List[Dict[str, Any]]:
    """
    Interpreta uma resposta ProbeMatches do WS-Discovery e devolve **um dispositivo por ProbeMatch**
    (Issue #41): um NVR ou gateway pode responder por vários canais na mesma mensagem.
    Valida que os XAddrs correspondam ao source_ip para prevenir Blind SSRF.
    """
    if not xml_data or "<" not in xml_data:
        return []

    # SEC-06: Defesa contra DoS / Billion Laughs / XXE (rejeição de DTD e declarações de entidade)
    if "<!DOCTYPE" in xml_data or "<!ENTITY" in xml_data:
        logger.warning("Ataque DTD/Entity XML bloqueado no WS-Discovery (Billion Laughs / XXE).")
        return []

    try:
        root = SafeET.fromstring(xml_data)
    except DefusedXmlException as e:
        logger.warning(f"Ataque XML bloqueado no WS-Discovery (DefusedXmlException): {e}")
        return []
    except Exception:
        # XML malformado/truncado: extração mínima por regex
        fallback = parse_ws_discovery_fallback(xml_data, source_ip)
        return [fallback] if fallback else []

    matches = [el for el in root.iter() if _local_name(el.tag) == "ProbeMatch"]
    scopes = matches or [root]
    devices = []
    for elem in scopes:
        xaddrs_raw, scopes_raw, types_raw = _match_fields(elem)
        devices.append(_device_from_fields(xaddrs_raw, scopes_raw, types_raw, source_ip, xml_data))
    return devices


def parse_ws_discovery_response(xml_data: str, source_ip: str = "") -> Optional[Dict[str, Any]]:
    """Primeiro dispositivo da resposta (compatibilidade); veja parse_ws_discovery_matches."""
    devices = parse_ws_discovery_matches(xml_data, source_ip)
    return devices[0] if devices else None


def _parse_valid_xaddr(ep: str, source_ip: str) -> Optional[Tuple[str, str, int]]:
    """Valida um XAddr contra URLs malformadas e Anti-SSRF, retornando (endpoint, host, porta)."""
    try:
        parsed = urllib.parse.urlparse(ep)
        scheme = parsed.scheme.lower()
        if scheme not in ("http", "https") or not parsed.hostname:
            logger.debug(f"XAddr inválido ignorado ({ep!r}): esquema ou hostname ausente")
            return None
        if source_ip and parsed.hostname != source_ip:
            logger.warning(
                f"Anti-SSRF: Endpoint XAddr divergente descartado no discovery "
                f"({parsed.hostname} != {source_ip})"
            )
            return None
        port = parsed.port or (443 if scheme == "https" else 80)
        return ep, parsed.hostname, port
    except ValueError as e:
        logger.debug(f"XAddr malformado ignorado ({ep!r}): {e}")
        return None


def _device_from_fields(xaddrs_raw: str, scopes_raw: str, types_raw: str, source_ip: str,
                        xml_data: str) -> Dict[str, Any]:
    """Monta o dicionário do dispositivo a partir dos campos de um ProbeMatch."""
    # Extrair e validar endpoints XAddrs (URLs dos serviços ONVIF) com proteção Anti-SSRF
    raw_endpoints = [x.strip() for x in xaddrs_raw.split() if x.strip()]
    valid_endpoints: List[Tuple[str, str, int]] = []
    for ep in raw_endpoints:
        parsed_ep = _parse_valid_xaddr(ep, source_ip)
        if parsed_ep is not None:
            valid_endpoints.append(parsed_ep)

    # Determinar IP e Porta primários
    ip = source_ip
    port = 80
    primary_endpoint = ""

    if valid_endpoints:
        primary_endpoint, ip, port = valid_endpoints[0]
    elif source_ip:
        primary_endpoint = f"http://{source_ip}:80/onvif/device_service"

    # Parsear Scopes ONVIF (onvif://www.onvif.org/name/..., /hardware/..., /location/...)
    name = "Câmera IP ONVIF"
    hardware = "Generic ONVIF Device"
    location = ""
    scopes_list = scopes_raw.split()

    for scope in scopes_list:
        decoded_scope = urllib.parse.unquote(scope)
        if "/name/" in decoded_scope:
            name = decoded_scope.split("/name/")[-1].replace("_", " ")
        elif "/hardware/" in decoded_scope:
            hardware = decoded_scope.split("/hardware/")[-1].replace("_", " ")
        elif "/location/" in decoded_scope:
            location = decoded_scope.split("/location/")[-1].replace("_", " ")

    # Se não encontramos nome descritivo nos scopes, gerar a partir do IP
    if name == "Câmera IP ONVIF" and ip:
        name = f"Câmera ONVIF ({ip})"

    # Inferir fabricante a partir do hardware ou nome
    manufacturer = detect_manufacturer(hardware + " " + name + " " + scopes_raw)

    return {
        "ip": ip,
        "port": port,
        "name": name,
        "model": hardware,
        "manufacturer": manufacturer,
        "location": location,
        "onvif_endpoint": primary_endpoint or (f"http://{ip}:{port}/onvif/device_service" if ip else ""),
        "endpoints": [ep for ep, _, _ in valid_endpoints],
        "types": types_raw,
        "scopes": scopes_list,
        "raw_xml": xml_data,
    }


def parse_ws_discovery_fallback(xml_data: str, source_ip: str = "") -> Optional[Dict[str, Any]]:
    """Fallback com Regex para extrair dados essenciais de XML truncado validando Anti-SSRF."""
    xaddrs_match = re.search(r'<[^:]*:?XAddrs[^>]*>(.*?)</[^:]*:?XAddrs>', xml_data, re.DOTALL)
    scopes_match = re.search(r'<[^:]*:?Scopes[^>]*>(.*?)</[^:]*:?Scopes>', xml_data, re.DOTALL)

    xaddrs = xaddrs_match.group(1).strip() if xaddrs_match else ""
    scopes = scopes_match.group(1).strip() if scopes_match else ""

    raw_endpoints = [x.strip() for x in xaddrs.split() if x.strip()]
    valid_endpoints: List[Tuple[str, str, int]] = []
    for ep in raw_endpoints:
        parsed_ep = _parse_valid_xaddr(ep, source_ip)
        if parsed_ep is not None:
            valid_endpoints.append(parsed_ep)

    ip = source_ip
    port = 80
    endpoint = ""

    if valid_endpoints:
        endpoint, ip, port = valid_endpoints[0]
    elif source_ip:
        endpoint = f"http://{source_ip}:80/onvif/device_service"

    name = f"Câmera ONVIF ({ip})" if ip else "Dispositivo ONVIF"
    hardware = "ONVIF Camera"

    for item in scopes.split():
        decoded = urllib.parse.unquote(item)
        if "/name/" in decoded:
            name = decoded.split("/name/")[-1].replace("_", " ")
        elif "/hardware/" in decoded:
            hardware = decoded.split("/hardware/")[-1].replace("_", " ")

    return {
        "ip": ip,
        "port": port,
        "name": name,
        "model": hardware,
        "manufacturer": detect_manufacturer(hardware + " " + name + " " + scopes),
        "location": "",
        "onvif_endpoint": endpoint or (f"http://{ip}:{port}/onvif/device_service" if ip else ""),
        "endpoints": [ep for ep, _, _ in valid_endpoints],
        "types": "",
        "scopes": scopes.split(),
        "raw_xml": xml_data,
    }


def detect_manufacturer(text: str) -> str:
    """Detecta o fabricante a partir de assinaturas textuais conhecidas."""
    lower = text.lower()
    if "intelbras" in lower:
        return "Intelbras"
    elif "hikvision" in lower or "hik" in lower:
        return "Hikvision"
    elif "dahua" in lower:
        return "Dahua"
    elif "reolink" in lower:
        return "Reolink"
    elif "axis" in lower:
        return "Axis Communications"
    elif "tp-link" in lower or "tapo" in lower:
        return "TP-Link / Tapo"
    elif "yoosee" in lower:
        return "Yoosee"
    elif "vstarcam" in lower:
        return "VStarcam"
    elif "imou" in lower:
        return "Imou"
    elif "bosch" in lower:
        return "Bosch Security"
    elif "uniview" in lower:
        return "Uniview"
    return "Genérico / ONVIF"
