"""
Parser para mensagens XML SOAP de WS-Discovery e ONVIF com validação Anti-SSRF.
"""
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

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


def parse_ws_discovery_response(xml_data: str, source_ip: str = "") -> Optional[Dict[str, any]]:
    """
    Realiza o parse de uma mensagem de resposta ProbeMatches do WS-Discovery.
    Extrai XAddrs, Scopes, Tipos de Serviço, IP, Porta, Fabricante e Modelo.
    Valida que os endpoints XAddrs correspondam ao source_ip para prevenir Blind SSRF.
    """
    if not xml_data or "<" not in xml_data:
        return None

    try:
        # Remover declarações de namespace prefixados desconhecidos para parsing robusto
        cleaned_xml = re.sub(r'xmlns(:\w+)?="[^"]+"', '', xml_data, count=0)
        # Parseando o XML original com ElementTree
        root = ET.fromstring(xml_data)

        # Procurar elemento ProbeMatches em qualquer profundidade
        probe_matches = []
        for elem in root.iter():
            if elem.tag.endswith("ProbeMatch") or elem.tag.endswith("ProbeMatches"):
                probe_matches.append(elem)

        xaddrs_raw = ""
        scopes_raw = ""
        types_raw = ""

        for elem in root.iter():
            tag_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag_name == "XAddrs" and elem.text:
                xaddrs_raw = elem.text.strip()
            elif tag_name == "Scopes" and elem.text:
                scopes_raw = elem.text.strip()
            elif tag_name == "Types" and elem.text:
                types_raw = elem.text.strip()

        # Extrair endpoints XAddrs (URLs dos serviços ONVIF)
        endpoints = [x.strip() for x in xaddrs_raw.split() if x.strip().startswith("http")]
        
        # Determinar IP e Porta primários com validação Anti-SSRF
        ip = source_ip
        port = 80
        primary_endpoint = ""

        if endpoints:
            valid_endpoints = []
            for ep in endpoints:
                parsed_url = urllib.parse.urlparse(ep)
                # Se source_ip foi fornecido pelo socket de rede, validar correspondência
                if source_ip and parsed_url.hostname and parsed_url.hostname != source_ip:
                    logger.warning(
                        f"Anti-SSRF: Endpoint XAddr divergente descartado no discovery "
                        f"({parsed_url.hostname} != {source_ip})"
                    )
                    continue
                valid_endpoints.append(ep)

            if valid_endpoints:
                primary_endpoint = valid_endpoints[0]
                parsed_url = urllib.parse.urlparse(primary_endpoint)
                if parsed_url.hostname:
                    ip = parsed_url.hostname
                if parsed_url.port:
                    port = parsed_url.port
                elif parsed_url.scheme == "https":
                    port = 443
            elif source_ip:
                primary_endpoint = f"http://{source_ip}:80/onvif/device_service"
            else:
                primary_endpoint = endpoints[0]
                parsed_url = urllib.parse.urlparse(primary_endpoint)
                if parsed_url.hostname:
                    ip = parsed_url.hostname
                if parsed_url.port:
                    port = parsed_url.port

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
            elif "/Profile/" in decoded_scope:
                pass

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
            "onvif_endpoint": primary_endpoint or f"http://{ip}:{port}/onvif/device_service",
            "endpoints": endpoints,
            "types": types_raw,
            "scopes": scopes_list,
            "raw_xml": xml_data,
        }

    except Exception:
        # Fallback usando Regex caso o XML esteja malformado
        return parse_ws_discovery_fallback(xml_data, source_ip)


def parse_ws_discovery_fallback(xml_data: str, source_ip: str = "") -> Optional[Dict[str, any]]:
    """Fallback com Regex para extrair dados essenciais de XML truncado validando Anti-SSRF."""
    xaddrs_match = re.search(r'<[^:]*:?XAddrs[^>]*>(.*?)</[^:]*:?XAddrs>', xml_data, re.DOTALL)
    scopes_match = re.search(r'<[^:]*:?Scopes[^>]*>(.*?)</[^:]*:?Scopes>', xml_data, re.DOTALL)

    xaddrs = xaddrs_match.group(1).strip() if xaddrs_match else ""
    scopes = scopes_match.group(1).strip() if scopes_match else ""

    endpoints = [x.strip() for x in xaddrs.split() if x.strip().startswith("http")]
    ip = source_ip
    port = 80
    endpoint = ""

    if endpoints:
        valid_endpoints = []
        for ep in endpoints:
            parsed_url = urllib.parse.urlparse(ep)
            if source_ip and parsed_url.hostname and parsed_url.hostname != source_ip:
                continue
            valid_endpoints.append(ep)

        if valid_endpoints:
            endpoint = valid_endpoints[0]
            parsed = urllib.parse.urlparse(endpoint)
            if parsed.hostname:
                ip = parsed.hostname
            if parsed.port:
                port = parsed.port
        elif source_ip:
            endpoint = f"http://{source_ip}:80/onvif/device_service"
        else:
            endpoint = endpoints[0]
            parsed = urllib.parse.urlparse(endpoint)
            if parsed.hostname:
                ip = parsed.hostname
            if parsed.port:
                port = parsed.port

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
        "onvif_endpoint": endpoint or f"http://{ip}:{port}/onvif/device_service",
        "endpoints": endpoints,
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
