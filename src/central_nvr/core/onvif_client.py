"""
Cliente ONVIF nativo em Python com suporte a Profile S (Live/PTZ), Profile T (Eventos/Motion) e Profile G (Edge Storage).
Inclui WS-Security OASIS PasswordDigest, validação estrita Anti-SSRF e escape XML de parâmetros.
"""
import base64
import datetime
import hashlib
import html
import logging
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

logger = logging.getLogger(__name__)


class OnvifClient:
    """
    Cliente ONVIF SOAP completo para CFTV IP:
    - Profile S: Descoberta de perfis de mídia, resolução de URIs RTSP dinâmicas e controle PTZ contínuo/relativo/presets.
    - Profile T: Inscrição de eventos (PullPoint) para detecção de movimento e alarmes nativos da câmera.
    - Profile G: Consulta a gravações salvas em armazenamento local (cartão SD / Edge Storage) e URLs de replay.
    """

    def __init__(
        self,
        ip: str,
        port: int = 80,
        username: str = "",
        password: str = "",
        timeout: float = 4.0,
    ):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        
        self.device_service_url = f"http://{ip}:{port}/onvif/device_service"
        self.media_service_url = f"http://{ip}:{port}/onvif/media_service"
        self.ptz_service_url = f"http://{ip}:{port}/onvif/ptz_service"
        self.event_service_url = f"http://{ip}:{port}/onvif/event_service"
        self.subscription_url = ""
        self.recording_service_url = f"http://{ip}:{port}/onvif/recording_service"
        self.replay_service_url = f"http://{ip}:{port}/onvif/replay_service"
        
        self.profiles: List[Dict[str, Any]] = []
        self.active_profile_token: str = ""
        self.substream_profile_token: str = ""
        self.device_info: Dict[str, str] = {}
        self.has_ptz: bool = False
        self.has_events: bool = False
        self.has_edge_storage: bool = False
        self._session = requests.Session() if HAS_REQUESTS else None

    def _validate_xaddr(self, xaddr: str) -> Optional[str]:
        """
        Valida que o endpoint XAddr recebido possui host estritamente correspondente ao IP
        configurado da câmera, bloqueando requisições SOAP para terceiros (Anti-SSRF).
        """
        if not xaddr:
            return None
        try:
            parsed = urllib.parse.urlparse(xaddr)
            if parsed.hostname and parsed.hostname == self.ip:
                return xaddr
            logger.warning(
                f"Anti-SSRF: Endpoint XAddr rejeitado por divergir do IP da câmera "
                f"({parsed.hostname} != {self.ip}): {xaddr}"
            )
            return None
        except Exception as e:
            logger.warning(f"Anti-SSRF: Erro ao validar XAddr '{xaddr}': {e}")
            return None

    def _build_ws_security_header(self) -> str:
        """
        Gera o cabeçalho WS-Security com UsernameToken e Password Digest conforme WS-Security 1.0:
        PasswordDigest = Base64(SHA-1(Nonce + Created + Password))
        """
        if not self.username:
            return ""

        # Gerar Nonce aleatório de 16 bytes
        nonce_bytes = os.urandom(16)
        nonce_b64 = base64.b64encode(nonce_bytes).decode("ascii")

        # Timestamp UTC ISO 8601
        created_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        created_bytes = created_str.encode("utf-8")

        # Senha codificada
        password_bytes = self.password.encode("utf-8")

        # SHA-1(nonce + created + password)
        hasher = hashlib.sha1()
        hasher.update(nonce_bytes)
        hasher.update(created_bytes)
        hasher.update(password_bytes)
        digest_bytes = hasher.digest()
        password_digest = base64.b64encode(digest_bytes).decode("ascii")

        escaped_user = html.escape(self.username)
        return f"""
        <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd" 
                       xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
            <wsse:UsernameToken>
                <wsse:Username>{escaped_user}</wsse:Username>
                <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{password_digest}</wsse:Password>
                <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>
                <wsu:Created>{created_str}</wsu:Created>
            </wsse:UsernameToken>
        </wsse:Security>"""

    def _send_soap_request(self, service_url: str, body_xml: str, action: str = "") -> Optional[str]:
        """Envia uma requisição SOAP HTTP POST ao endpoint ONVIF."""
        ws_sec = self._build_ws_security_header()
        envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope" 
               xmlns:tds="http://www.onvif.org/ver10/device/wsdl" 
               xmlns:trt="http://www.onvif.org/ver10/media/wsdl" 
               xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" 
               xmlns:tev="http://www.onvif.org/ver10/events/wsdl" 
               xmlns:trc="http://www.onvif.org/ver10/recording/wsdl" 
               xmlns:trp="http://www.onvif.org/ver10/replay/wsdl" 
               xmlns:tt="http://www.onvif.org/ver10/schema">
    <soap:Header>
        {ws_sec}
    </soap:Header>
    <soap:Body>
        {body_xml}
    </soap:Body>
</soap:Envelope>"""

        headers = {
            "Content-Type": "application/soap+xml; charset=utf-8; action="" + action + """,
        }

        data_bytes = envelope.encode("utf-8")

        try:
            if HAS_REQUESTS:
                requester = self._session if self._session else requests
                resp = requester.post(
                    service_url,
                    data=data_bytes,
                    headers=headers,
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    return resp.text
                return resp.text if resp.text else None
            else:
                req = urllib.request.Request(service_url, data=data_bytes, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            logger.debug(f"Falha na comunicação SOAP com {service_url}: {e}")
            return None

    def connect_and_initialize(self) -> bool:
        """
        Conecta à câmera, recupera informações do dispositivo, capacidades,
        perfis de mídia (Dual-Stream), suporte a PTZ, eventos e Edge Storage.
        """
        # 1. GetDeviceInformation
        self.get_device_information()

        # 2. GetCapabilities (Media, PTZ, Events, Recording, Replay)
        self.get_capabilities()

        # 3. GetProfiles (MainStream e SubStream)
        profiles = self.get_profiles()
        if profiles:
            return True
        return False

    def get_device_information(self) -> Dict[str, str]:
        """Obtém Fabricante, Modelo, Firmware e Número de Série."""
        body = "<tds:GetDeviceInformation/>"
        xml_resp = self._send_soap_request(self.device_service_url, body)
        info = {
            "manufacturer": "Genérico",
            "model": "ONVIF Device",
            "firmware_version": "1.0",
            "serial_number": "",
            "hardware_id": "",
        }

        if xml_resp:
            for key, tag in [
                ("manufacturer", "Manufacturer"),
                ("model", "Model"),
                ("firmware_version", "FirmwareVersion"),
                ("serial_number", "SerialNumber"),
                ("hardware_id", "HardwareId"),
            ]:
                match = re.search(rf"<[^:]*:{tag}[^>]*>(.*?)</[^:]*:{tag}>", xml_resp)
                if match:
                    info[key] = match.group(1).strip()

        self.device_info = info
        return info

    def get_capabilities(self) -> Dict[str, str]:
        """Descobre os endpoints dos serviços Media, PTZ, Eventos e Gravação (Profile G)."""
        body = """<tds:GetCapabilities>
            <tds:Category>All</tds:Category>
        </tds:GetCapabilities>"""
        xml_resp = self._send_soap_request(self.device_service_url, body)
        caps = {}

        if xml_resp:
            # 1. Media Service
            media_match = re.search(r"<[^:]*:Media[^>]*>.*?<[^:]*:XAddr[^>]*>(.*?)</[^:]*:XAddr>", xml_resp, re.DOTALL)
            if media_match:
                raw_media = media_match.group(1).strip()
                validated_media = self._validate_xaddr(raw_media)
                if validated_media:
                    self.media_service_url = validated_media
                    caps["media"] = self.media_service_url

            # 2. PTZ Service (Trata correção de endpoints do firmware Yoosee/Xiongmai)
            ptz_match = re.search(r"<[^:]*:PTZ[^>]*>.*?<[^:]*:XAddr[^>]*>(.*?)</[^:]*:XAddr>", xml_resp, re.DOTALL)
            if ptz_match:
                raw_ptz = ptz_match.group(1).strip()
                # Se a câmera retornou deviceio_service no lugar de ptz_service (bug de firmware Yoosee)
                if "deviceio_service" in raw_ptz:
                    raw_ptz = raw_ptz.replace("deviceio_service", "ptz_service")
                validated_ptz = self._validate_xaddr(raw_ptz)
                if validated_ptz:
                    self.ptz_service_url = validated_ptz
                    self.has_ptz = True
                    caps["ptz"] = self.ptz_service_url
                else:
                    self.has_ptz = True
            else:
                self.ptz_service_url = f"http://{self.ip}:{self.port}/onvif/ptz_service"
                self.has_ptz = True

            # 3. Events Service (Profile T)
            events_match = re.search(r"<[^:]*:Events[^>]*>.*?<[^:]*:XAddr[^>]*>(.*?)</[^:]*:XAddr>", xml_resp, re.DOTALL)
            if events_match:
                raw_events = events_match.group(1).strip()
                validated_events = self._validate_xaddr(raw_events)
                if validated_events:
                    self.event_service_url = validated_events
                    self.has_events = True
                    caps["events"] = self.event_service_url

            # 4. Recording Service (Profile G)
            rec_match = re.search(r"<[^:]*:Recording[^>]*>.*?<[^:]*:XAddr[^>]*>(.*?)</[^:]*:XAddr>", xml_resp, re.DOTALL)
            if rec_match:
                raw_rec = rec_match.group(1).strip()
                validated_rec = self._validate_xaddr(raw_rec)
                if validated_rec:
                    self.recording_service_url = validated_rec
                    self.has_edge_storage = True
                    caps["recording"] = self.recording_service_url

            # 5. Replay Service (Profile G)
            replay_match = re.search(r"<[^:]*:Replay[^>]*>.*?<[^:]*:XAddr[^>]*>(.*?)</[^:]*:XAddr>", xml_resp, re.DOTALL)
            if replay_match:
                raw_replay = replay_match.group(1).strip()
                validated_replay = self._validate_xaddr(raw_replay)
                if validated_replay:
                    self.replay_service_url = validated_replay
                    caps["replay"] = self.replay_service_url

        return caps

    def get_profiles(self) -> List[Dict[str, Any]]:
        """
        Recupera e analisa os perfis de mídia de vídeo da câmera,
        classificando automaticamente entre MainStream (alta resolução) e SubStream (baixa resolução).
        """
        body = "<trt:GetProfiles/>"
        xml_resp = self._send_soap_request(self.media_service_url, body)
        profiles = []

        if xml_resp:
            profile_blocks = re.findall(r'<[^:]*:Profiles\s+token="([^"]+)"[^>]*>(.*?)</[^:]*:Profiles>', xml_resp, re.DOTALL)
            for token, block in profile_blocks:
                name_match = re.search(r'<[^:]*:Name[^>]*>(.*?)</[^:]*:Name>', block)
                name = name_match.group(1).strip() if name_match else token
                
                # Resolução
                width_m = re.search(r'<[^:]*:Width[^>]*>(\d+)</[^:]*:Width>', block)
                height_m = re.search(r'<[^:]*:Height[^>]*>(\d+)</[^:]*:Height>', block)
                width = int(width_m.group(1)) if width_m else 0
                height = int(height_m.group(1)) if height_m else 0

                # Codec / Encoding
                enc_m = re.search(r'<[^:]*:Encoding[^>]*>([^<]+)</[^:]*:Encoding>', block)
                encoding = enc_m.group(1).strip() if enc_m else "H264"

                # PTZ
                has_ptz = "<tt:PTZConfiguration" in block or "PTZ" in block
                if has_ptz:
                    self.has_ptz = True

                stream_uri = self.get_stream_uri(token)

                profiles.append({
                    "token": token,
                    "name": name,
                    "width": width,
                    "height": height,
                    "encoding": encoding,
                    "has_ptz": has_ptz,
                    "stream_uri": stream_uri or "",
                })

        if not profiles:
            # Perfil padrão de fallback
            profiles.append({
                "token": "Profile_1",
                "name": "MainStream",
                "width": 1920,
                "height": 1080,
                "encoding": "H264",
                "has_ptz": self.has_ptz,
                "stream_uri": "",
            })

        # Classificar perfis por resolução decrescente (Maior = MainStream, Menor = SubStream)
        profiles_sorted = sorted(profiles, key=lambda p: p["width"] * p["height"], reverse=True)
        self.profiles = profiles_sorted
        self.active_profile_token = profiles_sorted[0]["token"]

        if len(profiles_sorted) > 1:
            self.substream_profile_token = profiles_sorted[-1]["token"]
        else:
            self.substream_profile_token = self.active_profile_token

        return profiles_sorted

    def get_stream_uri(self, profile_token: Optional[str] = None) -> Optional[str]:
        """Obtém a URL RTSP dinâmica fornecida pela câmera para o perfil selecionado com sanitização de token."""
        token = profile_token or self.active_profile_token or "Profile_1"
        escaped_token = html.escape(str(token))
        body = f"""<trt:GetStreamUri>
            <trt:StreamSetup>
                <tt:Stream>RTP-Unicast</tt:Stream>
                <tt:Transport>
                    <tt:Protocol>RTSP</tt:Protocol>
                </tt:Transport>
            </trt:StreamSetup>
            <trt:ProfileToken>{escaped_token}</trt:ProfileToken>
        </trt:GetStreamUri>"""

        xml_resp = self._send_soap_request(self.media_service_url, body)
        if xml_resp:
            uri_match = re.search(r"<[^:]*:Uri[^>]*>(.*?)</[^:]*:Uri>", xml_resp)
            if uri_match:
                uri = uri_match.group(1).strip()
                return uri

        return None

    # =========================================================================
    # Profile T: Serviço de Eventos (Event Service / PullPoint / Motion Detection)
    # =========================================================================

    def create_pull_point_subscription(self) -> Optional[str]:
        """Cria uma subscrição de eventos (PullPoint) para receber alarmes de movimento."""
        body = """<tev:CreatePullPointSubscription>
            <tev:InitialTerminationTime>PT300S</tev:InitialTerminationTime>
        </tev:CreatePullPointSubscription>"""

        xml_resp = self._send_soap_request(self.event_service_url, body)
        if xml_resp:
            addr_m = re.search(r'<[^:]*:Address[^>]*>(.*?)</[^:]*:Address>', xml_resp)
            if addr_m:
                sub_url = addr_m.group(1).strip()
                val_url = self._validate_xaddr(sub_url)
                if val_url:
                    self.subscription_url = val_url
                    return self.subscription_url

        return None

    def pull_messages(self, timeout_sec: float = 5.0) -> List[Dict[str, Any]]:
        """
        Consulta mensagens de eventos pendentes na subscrição PullPoint.
        Detecta eventos de movimento (RuleEngine, CellMotion, MotionAlarm).
        """
        if not self.subscription_url:
            self.create_pull_point_subscription()
            if not self.subscription_url:
                return []

        body = f"""<tev:PullMessages>
            <tev:Timeout>PT{int(timeout_sec)}S</tev:Timeout>
            <tev:MessageLimit>10</tev:MessageLimit>
        </tev:PullMessages>"""

        xml_resp = self._send_soap_request(self.subscription_url, body)
        events = []

        if xml_resp:
            # Buscar tópicos de notificação
            topics = re.findall(r'<[^:]*:Topic[^>]*>(.*?)</[^:]*:Topic>', xml_resp)
            # Buscar estados de valores booleanos
            values = re.findall(r'Name="State"\s+Value="(true|false|1|0)"', xml_resp, re.IGNORECASE)
            
            for i, topic in enumerate(topics):
                is_motion = "motion" in topic.lower() or "cellmotion" in topic.lower() or "ruleengine" in topic.lower()
                state = False
                if i < len(values):
                    state = values[i].lower() in ("true", "1")
                elif "motion" in xml_resp.lower() and "true" in xml_resp.lower():
                    state = True

                events.append({
                    "topic": topic,
                    "is_motion": is_motion,
                    "state": state,
                    "timestamp": datetime.datetime.now().isoformat(),
                })

        return events

    # =========================================================================
    # Profile G: Edge Storage & Replay (Gravação Local no Cartão SD da Câmera)
    # =========================================================================

    def get_recording_summary(self) -> Dict[str, Any]:
        """Obtém resumo das gravações salvas no armazenamento local da câmera (cartão SD)."""
        body = "<trc:GetRecordingSummary/>"
        xml_resp = self._send_soap_request(self.recording_service_url, body)
        summary = {
            "num_recordings": 0,
            "has_storage": False,
            "earliest_time": "",
            "latest_time": "",
        }

        if xml_resp:
            num_m = re.search(r'<[^:]*:NumberRecordings[^>]*>(\d+)</[^:]*:NumberRecordings>', xml_resp)
            if num_m:
                summary["num_recordings"] = int(num_m.group(1))
                summary["has_storage"] = summary["num_recordings"] > 0
                self.has_edge_storage = True

            earliest_m = re.search(r'<[^:]*:DataFrom[^>]*>(.*?)</[^:]*:DataFrom>', xml_resp)
            latest_m = re.search(r'<[^:]*:DataUntil[^>]*>(.*?)</[^:]*:DataUntil>', xml_resp)
            if earliest_m:
                summary["earliest_time"] = earliest_m.group(1).strip()
            if latest_m:
                summary["latest_time"] = latest_m.group(1).strip()

        return summary

    def find_recordings(self, max_matches: int = 20) -> List[Dict[str, Any]]:
        """Busca a lista de gravações mantidas no cartão SD da câmera para preenchimento de lacunas."""
        body = f"""<trc:FindRecordings>
            <trc:Scope>
                <trc:IncludedSources/>
            </trc:Scope>
            <trc:MaxMatches>{max_matches}</trc:MaxMatches>
        </trc:FindRecordings>"""

        xml_resp = self._send_soap_request(self.recording_service_url, body)
        recordings = []

        if xml_resp:
            tokens = re.findall(r'<[^:]*:RecordingToken[^>]*>(.*?)</[^:]*:RecordingToken>', xml_resp)
            for token in tokens:
                replay_uri = self.get_replay_uri(token)
                recordings.append({
                    "token": token,
                    "replay_uri": replay_uri or "",
                })

        return recordings

    def get_replay_uri(self, recording_token: str) -> Optional[str]:
        """Obtém a URL RTSP direta de reprodução de uma gravação mantida no cartão SD."""
        escaped_token = html.escape(str(recording_token))
        body = f"""<trp:GetReplayUri>
            <trp:StreamSetup>
                <tt:Stream>RTP-Unicast</tt:Stream>
                <tt:Transport>
                    <tt:Protocol>RTSP</tt:Protocol>
                </tt:Transport>
            </trp:StreamSetup>
            <trp:RecordingToken>{escaped_token}</trp:RecordingToken>
        </trp:GetReplayUri>"""

        xml_resp = self._send_soap_request(self.replay_service_url, body)
        if xml_resp:
            uri_match = re.search(r"<[^:]*:Uri[^>]*>(.*?)</[^:]*:Uri>", xml_resp)
            if uri_match:
                return uri_match.group(1).strip()

        return None

    # =========================================================================
    # Comandos PTZ (Pan, Tilt, Zoom)
    # =========================================================================

    def ptz_continuous_move(
        self,
        pan_speed: float = 0.0,
        tilt_speed: float = 0.0,
        zoom_speed: float = 0.0,
        profile_token: Optional[str] = None,
    ) -> bool:
        """
        Executa movimento contínuo PTZ com token escapado.
        pan_speed: -1.0 (Esquerda) a 1.0 (Direita)
        tilt_speed: -1.0 (Abaixo) a 1.0 (Acima)
        zoom_speed: -1.0 (Zoom Out) a 1.0 (Zoom In)
        """
        token = profile_token or self.active_profile_token
        if not token and self.profiles:
            token = self.profiles[0].get("token")
        token = token or "IPCProfilesToken0"

        escaped_token = html.escape(str(token))
        
        velocity_elements = f'<tt:PanTilt x="{pan_speed:.2f}" y="{tilt_speed:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>'
        if abs(zoom_speed) > 0.01:
            velocity_elements += f'\n                <tt:Zoom x="{zoom_speed:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>'

        body = f"""<tptz:ContinuousMove>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
            <tptz:Velocity>
                {velocity_elements}
            </tptz:Velocity>
        </tptz:ContinuousMove>"""

        xml_resp = self._send_soap_request(self.ptz_service_url, body)
        return xml_resp is not None and "Fault" not in xml_resp

    def ptz_stop(self, profile_token: Optional[str] = None) -> bool:
        """Para imediatamente qualquer movimento de Pan, Tilt e Zoom com token escapado."""
        token = profile_token or self.active_profile_token
        if not token and self.profiles:
            token = self.profiles[0].get("token")
        token = token or "IPCProfilesToken0"

        escaped_token = html.escape(str(token))
        body = f"""<tptz:Stop>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
            <tptz:PanTilt>true</tptz:PanTilt>
            <tptz:Zoom>true</tptz:Zoom>
        </tptz:Stop>"""

        xml_resp = self._send_soap_request(self.ptz_service_url, body)
        return xml_resp is not None and "Fault" not in xml_resp

    def ptz_relative_move(
        self,
        pan_trans: float = 0.0,
        tilt_trans: float = 0.0,
        zoom_trans: float = 0.0,
        profile_token: Optional[str] = None,
    ) -> bool:
        """Executa um deslocamento relativo de posição com token escapado."""
        token = profile_token or self.active_profile_token or "Profile_1"
        escaped_token = html.escape(str(token))
        body = f"""<tptz:RelativeMove>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
            <tptz:Translation>
                <tt:PanTilt x="{pan_trans:.2f}" y="{tilt_trans:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>
                <tt:Zoom x="{zoom_trans:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>
            </tptz:Translation>
        </tptz:RelativeMove>"""

        xml_resp = self._send_soap_request(self.ptz_service_url, body)
        return xml_resp is not None and "Fault" not in xml_resp

    def ptz_get_presets(self, profile_token: Optional[str] = None) -> List[Dict[str, str]]:
        """Retorna lista de posições pré-programadas (Presets) salvas na câmera."""
        token = profile_token or self.active_profile_token or "Profile_1"
        escaped_token = html.escape(str(token))
        body = f"""<tptz:GetPresets>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
        </tptz:GetPresets>"""

        xml_resp = self._send_soap_request(self.ptz_service_url, body)
        presets = []

        if xml_resp:
            preset_blocks = re.findall(r'<[^:]*:Preset\s+token="([^"]+)"[^>]*>(.*?)</[^:]*:Preset>', xml_resp, re.DOTALL)
            for ptoken, pblock in preset_blocks:
                name_match = re.search(r'<[^:]*:Name[^>]*>(.*?)</[^:]*:Name>', pblock)
                pname = name_match.group(1).strip() if name_match else f"Preset {ptoken}"
                presets.append({"token": ptoken, "name": pname})

        return presets

    def ptz_goto_preset(self, preset_token: str, profile_token: Optional[str] = None) -> bool:
        """Movimenta a câmera para a posição pré-programada (Preset) com tokens escapados."""
        token = profile_token or self.active_profile_token or "Profile_1"
        escaped_token = html.escape(str(token))
        escaped_preset = html.escape(str(preset_token))
        body = f"""<tptz:GotoPreset>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
            <tptz:PresetToken>{escaped_preset}</tptz:PresetToken>
        </tptz:GotoPreset>"""

        xml_resp = self._send_soap_request(self.ptz_service_url, body)
        return xml_resp is not None and "Fault" not in xml_resp

    def ptz_set_preset(self, preset_name: str, profile_token: Optional[str] = None) -> Optional[str]:
        """Salva a posição atual como um novo Preset com escape XML completo."""
        token = profile_token or self.active_profile_token or "Profile_1"
        escaped_token = html.escape(str(token))
        escaped_name = html.escape(str(preset_name))
        body = f"""<tptz:SetPreset>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
            <tptz:PresetName>{escaped_name}</tptz:PresetName>
        </tptz:SetPreset>"""

        xml_resp = self._send_soap_request(self.ptz_service_url, body)
        if xml_resp:
            match = re.search(r'<[^:]*:PresetToken[^>]*>(.*?)</[^:]*:PresetToken>', xml_resp)
            if match:
                return match.group(1).strip()
        return None
