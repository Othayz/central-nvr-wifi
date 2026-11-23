"""
Cliente ONVIF nativo em Python com suporte a Profile S (Live/PTZ) e Profile G (Edge Storage).
Inclui WS-Security OASIS PasswordDigest, validação estrita Anti-SSRF e escape XML de parâmetros.

As respostas SOAP são interpretadas com `defusedxml.ElementTree`, comparando as tags pelo nome
local (independe do prefixo de namespace usado pela câmera). Falhas de transporte, autenticação e
SOAP Fault são devolvidas como exceções tipadas (OnvifError e subclasses), para que quem chama
consiga diferenciar senha errada de câmera fora do ar ou recurso não suportado (Issue #28).
"""
import base64
import datetime
import hashlib
import html
import logging
import os
import urllib.parse
from typing import Any, Callable, Dict, Iterator, List, Optional
from xml.etree.ElementTree import Element, ParseError

import defusedxml.ElementTree as SafeET
import requests
from defusedxml.common import DefusedXmlException
from urllib3.util import parse_url as urllib3_parse_url

logger = logging.getLogger(__name__)


# =========================================================================
# Erros tipados
# =========================================================================

class OnvifError(Exception):
    """Falha ao falar com o dispositivo ONVIF."""


class OnvifConnectionError(OnvifError):
    """O dispositivo não aceitou a conexão (porta fechada, host inalcançável)."""


class OnvifTimeoutError(OnvifError):
    """O dispositivo não respondeu dentro do tempo limite."""


class OnvifAuthError(OnvifError):
    """Credenciais recusadas (HTTP 401 ou SOAP Fault NotAuthorized)."""


class OnvifFaultError(OnvifError):
    """O dispositivo respondeu com SOAP Fault (operação não suportada, argumento inválido...)."""


class OnvifResponseError(OnvifError):
    """Resposta inesperada: status HTTP de erro sem Fault, ou XML inválido."""


class OnvifRedirectError(OnvifResponseError):
    """Redirecionamento HTTP (3xx) recusado por segurança ao comunicar com o dispositivo ONVIF."""


# =========================================================================
# Utilitários de XML
# =========================================================================

def _local(tag: Any) -> str:
    """Nome local de uma tag ElementTree (`{ns}Nome` → `Nome`)."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _iter_named(elem: Element, name: str) -> Iterator[Element]:
    """Todos os descendentes (incluindo o próprio elemento) com o nome local informado."""
    for el in elem.iter():
        if _local(el.tag) == name:
            yield el


def _find_named(elem: Element, name: str) -> Optional[Element]:
    return next(_iter_named(elem, name), None)


def _child(elem: Element, name: str) -> Optional[Element]:
    """Filho direto com o nome local informado."""
    for el in elem:
        if _local(el.tag) == name:
            return el
    return None


def _text(elem: Optional[Element]) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _parse_xml(text: str) -> Element:
    try:
        return SafeET.fromstring(text)
    except (ParseError, DefusedXmlException, ValueError) as e:
        raise OnvifResponseError(f"Resposta XML inválida: {e}") from e


def _fault_error(root: Element) -> Optional[OnvifError]:
    """Converte um SOAP Fault em exceção tipada (ou None se não houver Fault)."""
    fault = _find_named(root, "Fault")
    if fault is None:
        return None
    codes = " ".join(_text(v) for v in _iter_named(fault, "Value"))
    reason = _text(_find_named(fault, "Text")) or _text(_find_named(fault, "faultstring")) or "SOAP Fault"
    if "NotAuthorized" in codes or "NotAuthorized" in reason:
        return OnvifAuthError(f"Credenciais recusadas: {reason}")
    return OnvifFaultError(f"{reason} ({codes})" if codes else reason)


class OnvifClient:
    """
    Cliente ONVIF SOAP para CFTV IP:
    - Profile S: Descoberta de perfis de mídia, resolução de URIs RTSP dinâmicas e controle PTZ contínuo/relativo/presets.
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
        self.recording_service_url = f"http://{ip}:{port}/onvif/recording_service"
        self.search_service_url = f"http://{ip}:{port}/onvif/search_service"
        self.replay_service_url = f"http://{ip}:{port}/onvif/replay_service"

        self.profiles: List[Dict[str, Any]] = []
        self.active_profile_token: str = ""
        self.substream_profile_token: str = ""
        self.device_info: Dict[str, str] = {}
        self.has_ptz: bool = False
        self.has_events: bool = False
        self.has_edge_storage: bool = False
        self._session = requests.Session()

    # =========================================================================
    # Validações Anti-SSRF
    # =========================================================================

    def _validate_xaddr(self, xaddr: str) -> Optional[str]:
        """
        Valida que o endpoint XAddr recebido usa esquema HTTP(S), não contém caracteres
        inválidos nem userinfo e possui host estritamente correspondente ao IP configurado
        da câmera em ambos os parsers (urllib.parse e urllib3), bloqueando requisições
        SOAP para terceiros (Anti-SSRF). Devolve a URL reconstruída/normalizada.
        """
        if not xaddr:
            return None
        try:
            if any(
                ch == "\\" or ord(ch) <= 0x20 or 0x7F <= ord(ch) <= 0x9F or ch.isspace()
                for ch in xaddr
            ):
                logger.warning(f"Anti-SSRF: XAddr rejeitado por conter caracteres inválidos: {xaddr!r}")
                return None

            candidate = xaddr
            if xaddr.startswith("/") and not xaddr.startswith("//"):
                candidate = f"http://{self.ip}:{self.port}{xaddr}"

            parsed = urllib.parse.urlparse(candidate)
            scheme = parsed.scheme.lower()
            if scheme not in ("http", "https"):
                logger.warning(f"Anti-SSRF: Esquema XAddr rejeitado: {parsed.scheme}")
                return None

            if (
                parsed.username is not None
                or parsed.password is not None
                or "@" in parsed.netloc
            ):
                logger.warning(f"Anti-SSRF: XAddr com userinfo rejeitado: {xaddr}")
                return None

            port = parsed.port
            u3 = urllib3_parse_url(candidate)
            if u3.auth is not None:
                logger.warning(f"Anti-SSRF: XAddr com userinfo rejeitado pelo urllib3: {xaddr}")
                return None
            if (u3.scheme or "").lower() != scheme or u3.port != port:
                logger.warning(
                    f"Anti-SSRF: Divergência de esquema/porta ao validar XAddr: {xaddr}"
                )
                return None

            if parsed.hostname != self.ip or u3.host != self.ip:
                logger.warning(
                    f"Anti-SSRF: Endpoint XAddr rejeitado por divergir do IP da câmera "
                    f"({parsed.hostname} / {u3.host} != {self.ip}): {xaddr}"
                )
                return None

            netloc = f"{self.ip}:{port}" if port is not None else self.ip
            return urllib.parse.urlunparse((scheme, netloc, parsed.path, parsed.params, parsed.query, ""))
        except Exception as e:
            logger.warning(f"Anti-SSRF: Erro ao validar XAddr '{xaddr}': {e}")
            return None

    def _validate_media_uri(self, uri: str) -> Optional[str]:
        """
        Valida uma URI de mídia (GetStreamUri / GetReplayUri) antes de usá-la com credenciais:
        o host precisa ser o IP configurado da câmera.
        """
        if not uri:
            return None
        try:
            parsed = urllib.parse.urlparse(uri)
        except ValueError:
            return None
        if parsed.scheme.lower() not in ("rtsp", "rtsps", "http", "https"):
            logger.warning(f"URI de mídia rejeitada (esquema não suportado): {parsed.scheme}")
            return None
        if parsed.hostname != self.ip:
            logger.warning(
                f"Anti-SSRF: URI de mídia rejeitada por divergir do IP da câmera "
                f"({parsed.hostname} != {self.ip})"
            )
            return None
        return uri

    # =========================================================================
    # Transporte SOAP
    # =========================================================================

    def _build_ws_security_header(self) -> str:
        """
        Gera o cabeçalho WS-Security com UsernameToken e Password Digest conforme WS-Security 1.0:
        PasswordDigest = Base64(SHA-1(Nonce + Created + Password))
        """
        if not self.username:
            return ""

        nonce_bytes = os.urandom(16)
        nonce_b64 = base64.b64encode(nonce_bytes).decode("ascii")

        created_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        created_bytes = created_str.encode("utf-8")
        password_bytes = self.password.encode("utf-8")

        hasher = hashlib.sha1()
        hasher.update(nonce_bytes)
        hasher.update(created_bytes)
        hasher.update(password_bytes)
        password_digest = base64.b64encode(hasher.digest()).decode("ascii")

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

    def _send_soap_request(self, service_url: str, body_xml: str, action: str = "") -> str:
        """
        Envia uma requisição SOAP HTTP POST ao endpoint ONVIF e devolve o corpo da resposta (HTTP 200).
        Lança OnvifConnectionError / OnvifTimeoutError / OnvifAuthError / OnvifFaultError /
        OnvifResponseError conforme a falha.
        """
        ws_sec = self._build_ws_security_header()
        envelope = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:tds="http://www.onvif.org/ver10/device/wsdl"
               xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
               xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"
               xmlns:trc="http://www.onvif.org/ver10/recording/wsdl"
               xmlns:tse="http://www.onvif.org/ver10/search/wsdl"
               xmlns:trp="http://www.onvif.org/ver10/replay/wsdl"
               xmlns:tt="http://www.onvif.org/ver10/schema">
    <soap:Header>
        {ws_sec}
    </soap:Header>
    <soap:Body>
        {body_xml}
    </soap:Body>
</soap:Envelope>"""

        content_type = f'application/soap+xml; charset=utf-8; action="{action}"' if action else "application/soap+xml; charset=utf-8"
        headers = {"Content-Type": content_type}

        try:
            resp = self._session.post(
                service_url,
                data=envelope.encode("utf-8"),
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )
        except requests.exceptions.Timeout as e:
            raise OnvifTimeoutError(f"Sem resposta de {service_url} em {self.timeout}s") from e
        except requests.exceptions.ConnectionError as e:
            raise OnvifConnectionError(f"Conexão recusada ou host inalcançável: {service_url}") from e
        except requests.exceptions.RequestException as e:
            raise OnvifError(f"Falha na comunicação SOAP com {service_url}: {e}") from e

        if resp.status_code == 200:
            return resp.text
        if 300 <= resp.status_code < 400:
            raise OnvifRedirectError(
                f"Redirecionamento HTTP {resp.status_code} recusado por segurança em {service_url}"
            )
        if resp.status_code == 401:
            raise OnvifAuthError(f"HTTP 401 em {service_url}: credenciais recusadas")

        # 400/500 normalmente trazem um SOAP Fault no corpo
        if resp.text:
            try:
                fault = _fault_error(_parse_xml(resp.text))
            except OnvifResponseError:
                fault = None
            if fault is not None:
                raise fault
        raise OnvifResponseError(f"HTTP {resp.status_code} em {service_url}")

    def _call(self, service_url: str, body_xml: str) -> Element:
        """Envia a requisição e devolve a resposta já interpretada (lança OnvifError em falha)."""
        root = _parse_xml(self._send_soap_request(service_url, body_xml))
        fault = _fault_error(root)
        if fault is not None:
            raise fault
        return root

    # =========================================================================
    # Device / Media
    # =========================================================================

    def connect_and_initialize(self) -> bool:
        """
        Conecta à câmera, recupera informações do dispositivo, capacidades e perfis de mídia.
        Devolve False se o dispositivo não responder ONVIF ou não expuser nenhum perfil real.
        """
        try:
            try:
                self.get_device_information()
            except OnvifFaultError:
                pass  # Opcional: algumas câmeras não implementam GetDeviceInformation
            self.get_capabilities()
            return bool(self.get_profiles())
        except OnvifError as e:
            logger.debug(f"ONVIF indisponível em {self.ip}:{self.port}: {e}")
            return False

    def get_device_information(self) -> Dict[str, str]:
        """Obtém Fabricante, Modelo, Firmware e Número de Série."""
        root = self._call(self.device_service_url, "<tds:GetDeviceInformation/>")
        info = {
            "manufacturer": "Genérico",
            "model": "ONVIF Device",
            "firmware_version": "1.0",
            "serial_number": "",
            "hardware_id": "",
        }
        for key, tag in [
            ("manufacturer", "Manufacturer"),
            ("model", "Model"),
            ("firmware_version", "FirmwareVersion"),
            ("serial_number", "SerialNumber"),
            ("hardware_id", "HardwareId"),
        ]:
            value = _text(_find_named(root, tag))
            if value:
                info[key] = value

        self.device_info = info
        return info

    @staticmethod
    def _service_xaddr(capabilities: Element, service: str) -> str:
        """XAddr do primeiro bloco de capacidade com o nome informado (ex.: Media, PTZ)."""
        for el in _iter_named(capabilities, service):
            xaddr = _text(_child(el, "XAddr"))
            if xaddr:
                return xaddr
        return ""

    def get_capabilities(self) -> Dict[str, str]:
        """Descobre os endpoints dos serviços Media, PTZ, Eventos e Gravação (Profile G)."""
        body = """<tds:GetCapabilities>
            <tds:Category>All</tds:Category>
        </tds:GetCapabilities>"""
        root = self._call(self.device_service_url, body)
        capabilities = _find_named(root, "Capabilities")
        if capabilities is None:
            raise OnvifResponseError("Resposta de GetCapabilities sem o bloco Capabilities")

        caps: Dict[str, str] = {}

        media = self._validate_xaddr(self._service_xaddr(capabilities, "Media"))
        if media:
            self.media_service_url = media
            caps["media"] = media

        # PTZ só é considerado presente com um XAddr válido (Issue #28)
        raw_ptz = self._service_xaddr(capabilities, "PTZ")
        # Bug de firmware Yoosee/Xiongmai: devolve deviceio_service no lugar de ptz_service
        if "deviceio_service" in raw_ptz:
            raw_ptz = raw_ptz.replace("deviceio_service", "ptz_service")
        ptz = self._validate_xaddr(raw_ptz)
        self.has_ptz = bool(ptz)
        if ptz:
            self.ptz_service_url = ptz
            caps["ptz"] = ptz

        events = self._validate_xaddr(self._service_xaddr(capabilities, "Events"))
        if events:
            self.event_service_url = events
            self.has_events = True
            caps["events"] = events

        recording = self._validate_xaddr(self._service_xaddr(capabilities, "Recording"))
        if recording:
            self.recording_service_url = recording
            self.has_edge_storage = True
            caps["recording"] = recording

        search = self._validate_xaddr(self._service_xaddr(capabilities, "Search"))
        if search:
            self.search_service_url = search
            self.has_edge_storage = True
            caps["search"] = search

        replay = self._validate_xaddr(self._service_xaddr(capabilities, "Replay"))
        if replay:
            self.replay_service_url = replay
            caps["replay"] = replay

        return caps

    def get_profiles(self) -> List[Dict[str, Any]]:
        """
        Recupera e analisa os perfis de mídia de vídeo da câmera,
        classificando automaticamente entre MainStream (alta resolução) e SubStream (baixa resolução).
        Devolve lista vazia se a câmera não expuser perfis (sem perfil fictício).
        """
        root = self._call(self.media_service_url, "<trt:GetProfiles/>")
        profiles = []

        for prof in _iter_named(root, "Profiles"):
            token = prof.get("token", "").strip()
            if not token:
                continue
            name = _text(_child(prof, "Name")) or token

            encoder = _find_named(prof, "VideoEncoderConfiguration")
            scope = encoder if encoder is not None else prof
            width_s = _text(_find_named(scope, "Width"))
            height_s = _text(_find_named(scope, "Height"))
            width = int(width_s) if width_s.isdigit() else 0
            height = int(height_s) if height_s.isdigit() else 0
            encoding = _text(_find_named(scope, "Encoding")) or "H264"

            has_ptz = _find_named(prof, "PTZConfiguration") is not None
            if has_ptz:
                self.has_ptz = True

            try:
                stream_uri = self.get_stream_uri(token)
            except OnvifFaultError as e:
                logger.debug(f"GetStreamUri falhou para o perfil {token}: {e}")
                stream_uri = None

            profiles.append({
                "token": token,
                "name": name,
                "width": width,
                "height": height,
                "encoding": encoding,
                "has_ptz": has_ptz,
                "stream_uri": stream_uri or "",
            })

        profiles_sorted = sorted(profiles, key=lambda p: p["width"] * p["height"], reverse=True)
        self.profiles = profiles_sorted
        if profiles_sorted:
            self.active_profile_token = profiles_sorted[0]["token"]
            self.substream_profile_token = profiles_sorted[-1]["token"]
        else:
            self.active_profile_token = ""
            self.substream_profile_token = ""

        return profiles_sorted

    def get_stream_uri(self, profile_token: Optional[str] = None) -> Optional[str]:
        """
        Obtém a URL RTSP fornecida pela câmera para o perfil selecionado.
        Devolve None se a resposta não trouxer URI ou se o host divergir do IP da câmera.
        """
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

        root = self._call(self.media_service_url, body)
        return self._validate_media_uri(_text(_find_named(root, "Uri")))

    # =========================================================================
    # Profile G: Edge Storage & Replay (Gravação Local no Cartão SD da Câmera)
    # =========================================================================

    def get_recording_summary(self) -> Dict[str, Any]:
        """Obtém resumo das gravações salvas no armazenamento local da câmera (cartão SD)."""
        try:
            root = self._call(self.search_service_url, "<tse:GetRecordingSummary/>")
        except OnvifRedirectError:
            raise
        except (OnvifFaultError, OnvifResponseError):
            root = self._call(self.recording_service_url, "<trc:GetRecordingSummary/>")

        summary: Dict[str, Any] = {
            "num_recordings": 0,
            "has_storage": False,
            "earliest_time": "",
            "latest_time": "",
        }

        num = _text(_find_named(root, "NumberRecordings"))
        if num.isdigit():
            summary["num_recordings"] = int(num)
            summary["has_storage"] = summary["num_recordings"] > 0
            self.has_edge_storage = True
        summary["earliest_time"] = _text(_find_named(root, "DataFrom"))
        summary["latest_time"] = _text(_find_named(root, "DataUntil"))
        return summary

    def find_recordings(
        self,
        max_matches: int = 20,
        should_abort: Optional[Callable[[], bool]] = None,
        max_search_rounds: int = 10,
    ) -> List[Dict[str, Any]]:
        """Busca a lista de gravações mantidas no cartão SD da câmera via ONVIF Recording Search (tse)."""
        aborted = should_abort or (lambda: False)
        max_matches_int = int(max_matches)
        if max_matches_int <= 0 or aborted():
            return []

        body = f"""<tse:FindRecordings>
            <tse:Scope/>
            <tse:MaxMatches>{max_matches_int}</tse:MaxMatches>
            <tse:KeepAliveTime>PT10S</tse:KeepAliveTime>
        </tse:FindRecordings>"""

        active_search_url = self.search_service_url
        try:
            root = self._call(active_search_url, body)
        except OnvifRedirectError:
            raise
        except (OnvifFaultError, OnvifResponseError):
            if aborted():
                return []
            active_search_url = self.recording_service_url
            legacy_body = f"""<trc:FindRecordings>
                <trc:Scope/>
                <trc:MaxMatches>{max_matches_int}</trc:MaxMatches>
            </trc:FindRecordings>"""
            root = self._call(active_search_url, legacy_body)

        search_token = _text(_find_named(root, "SearchToken"))
        tokens: List[str] = []

        if search_token:
            escaped_search_token = html.escape(search_token)
            try:
                for _ in range(max(1, int(max_search_rounds))):
                    if aborted() or len(tokens) >= max_matches_int:
                        break
                    remaining = max_matches_int - len(tokens)
                    results_body = f"""<tse:GetRecordingSearchResults>
                        <tse:SearchToken>{escaped_search_token}</tse:SearchToken>
                        <tse:MinResults>1</tse:MinResults>
                        <tse:MaxResults>{remaining}</tse:MaxResults>
                        <tse:WaitTime>PT2S</tse:WaitTime>
                    </tse:GetRecordingSearchResults>"""
                    results_root = self._call(active_search_url, results_body)
                    for el in _iter_named(results_root, "RecordingToken"):
                        token = _text(el)
                        if token and token not in tokens:
                            tokens.append(token)
                            if len(tokens) >= max_matches_int:
                                break
                    search_state = _text(_find_named(results_root, "SearchState")).lower()
                    if search_state not in ("searching", "queued"):
                        break
            finally:
                if not aborted():
                    end_body = f"""<tse:EndSearch>
                        <tse:SearchToken>{escaped_search_token}</tse:SearchToken>
                    </tse:EndSearch>"""
                    try:
                        self._call(active_search_url, end_body)
                    except OnvifError as e:
                        logger.debug(f"EndSearch falhou para {search_token}: {e}")
        else:
            for el in _iter_named(root, "RecordingToken"):
                token = _text(el)
                if token and token not in tokens:
                    tokens.append(token)
                    if len(tokens) >= max_matches_int:
                        break

        recordings: List[Dict[str, Any]] = []
        for token in tokens:
            if aborted():
                break
            try:
                replay_uri = self.get_replay_uri(token)
            except OnvifError as e:
                logger.debug(f"GetReplayUri falhou para {token}: {e}")
                replay_uri = None
            recordings.append({"token": token, "replay_uri": replay_uri or ""})
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

        root = self._call(self.replay_service_url, body)
        return self._validate_media_uri(_text(_find_named(root, "Uri")))

    # =========================================================================
    # Comandos PTZ (Pan, Tilt, Zoom)
    # Devolvem True em caso de sucesso e lançam OnvifError em caso de falha.
    # =========================================================================

    def _ptz_profile_token(self, profile_token: Optional[str]) -> str:
        token = profile_token or self.active_profile_token
        if not token and self.profiles:
            token = self.profiles[0].get("token")
        return html.escape(str(token or "IPCProfilesToken0"))

    def ptz_continuous_move(
        self,
        pan_speed: float = 0.0,
        tilt_speed: float = 0.0,
        zoom_speed: float = 0.0,
        profile_token: Optional[str] = None,
        timeout_sec: int = 2,
    ) -> bool:
        """
        Executa movimento contínuo PTZ com token escapado.
        pan_speed: -1.0 (Esquerda) a 1.0 (Direita)
        tilt_speed: -1.0 (Abaixo) a 1.0 (Acima)
        zoom_speed: -1.0 (Zoom Out) a 1.0 (Zoom In)
        timeout_sec: a câmera para sozinha após esse tempo se o Stop se perder; quem mantém o
        movimento deve reenviar o comando antes de expirar.
        """
        escaped_token = self._ptz_profile_token(profile_token)

        velocity_elements = f'<tt:PanTilt x="{pan_speed:.2f}" y="{tilt_speed:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>'
        if abs(zoom_speed) > 0.01:
            velocity_elements += f'\n                <tt:Zoom x="{zoom_speed:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>'

        body = f"""<tptz:ContinuousMove>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
            <tptz:Velocity>
                {velocity_elements}
            </tptz:Velocity>
            <tptz:Timeout>PT{int(timeout_sec)}S</tptz:Timeout>
        </tptz:ContinuousMove>"""

        self._call(self.ptz_service_url, body)
        return True

    def ptz_stop(self, profile_token: Optional[str] = None) -> bool:
        """Para imediatamente qualquer movimento de Pan, Tilt e Zoom com token escapado."""
        escaped_token = self._ptz_profile_token(profile_token)
        body = f"""<tptz:Stop>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
            <tptz:PanTilt>true</tptz:PanTilt>
            <tptz:Zoom>true</tptz:Zoom>
        </tptz:Stop>"""

        self._call(self.ptz_service_url, body)
        return True

    def ptz_relative_move(
        self,
        pan_trans: float = 0.0,
        tilt_trans: float = 0.0,
        zoom_trans: float = 0.0,
        profile_token: Optional[str] = None,
    ) -> bool:
        """Executa um deslocamento relativo de posição com token escapado."""
        escaped_token = self._ptz_profile_token(profile_token)
        body = f"""<tptz:RelativeMove>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
            <tptz:Translation>
                <tt:PanTilt x="{pan_trans:.2f}" y="{tilt_trans:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>
                <tt:Zoom x="{zoom_trans:.2f}" xmlns:tt="http://www.onvif.org/ver10/schema"/>
            </tptz:Translation>
        </tptz:RelativeMove>"""

        self._call(self.ptz_service_url, body)
        return True

    def ptz_get_presets(self, profile_token: Optional[str] = None) -> List[Dict[str, str]]:
        """Retorna lista de posições pré-programadas (Presets) salvas na câmera."""
        escaped_token = self._ptz_profile_token(profile_token)
        body = f"""<tptz:GetPresets>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
        </tptz:GetPresets>"""

        root = self._call(self.ptz_service_url, body)
        presets = []
        for preset in _iter_named(root, "Preset"):
            ptoken = preset.get("token", "").strip()
            if not ptoken:
                continue
            pname = _text(_child(preset, "Name")) or f"Preset {ptoken}"
            presets.append({"token": ptoken, "name": pname})
        return presets

    def ptz_goto_preset(self, preset_token: str, profile_token: Optional[str] = None) -> bool:
        """Movimenta a câmera para a posição pré-programada (Preset) com tokens escapados."""
        escaped_token = self._ptz_profile_token(profile_token)
        escaped_preset = html.escape(str(preset_token))
        body = f"""<tptz:GotoPreset>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
            <tptz:PresetToken>{escaped_preset}</tptz:PresetToken>
        </tptz:GotoPreset>"""

        self._call(self.ptz_service_url, body)
        return True

    def ptz_set_preset(self, preset_name: str, profile_token: Optional[str] = None) -> Optional[str]:
        """
        Salva a posição atual como um novo Preset com escape XML completo.
        Devolve o token criado, ou None se a resposta não trouxer token.
        """
        escaped_token = self._ptz_profile_token(profile_token)
        escaped_name = html.escape(str(preset_name))
        body = f"""<tptz:SetPreset>
            <tptz:ProfileToken>{escaped_token}</tptz:ProfileToken>
            <tptz:PresetName>{escaped_name}</tptz:PresetName>
        </tptz:SetPreset>"""

        root = self._call(self.ptz_service_url, body)
        return _text(_find_named(root, "PresetToken")) or None
