"""
Cadastro de Dispositivo Descoberto com dados reais do ONVIF (Issue #45).

Antes, o caminho RTSP era deduzido (`/onvif1` ou `/live/ch0`) e o PTZ ficava sempre ativo.
`enrich_from_onvif` consulta GetDeviceInformation / GetCapabilities / GetProfiles / GetStreamUri e
preenche URI principal, sub-stream, PTZ e fabricante/modelo. Se o ONVIF falhar, a câmera mantém o
caminho deduzido e a função devolve o motivo, para que a interface avise o usuário.

Executa chamadas de rede: deve rodar fora da thread da interface.
"""
import logging
import urllib.parse
from typing import Optional

from central_nvr.core.camera import CameraDevice
from central_nvr.core.onvif_client import OnvifAuthError, OnvifClient, OnvifError, OnvifFaultError

logger = logging.getLogger(__name__)

FALLBACK_WARNING = "ONVIF indisponível — caminho RTSP deduzido"


def enrich_from_onvif(camera: CameraDevice, timeout: float = 3.0) -> Optional[str]:
    """
    Preenche `camera` com os dados devolvidos pelo ONVIF.
    Devolve None em caso de sucesso, ou a descrição do motivo pelo qual o caminho foi deduzido.
    """
    client = OnvifClient(
        ip=camera.ip,
        port=camera.port,
        username=camera.username,
        password=camera.password,
        timeout=timeout,
    )
    info = None
    try:
        try:
            info = client.get_device_information()
        except OnvifFaultError:
            pass  # Opcional em várias câmeras
        try:
            client.get_capabilities()
        except OnvifFaultError:
            pass  # Sem GetCapabilities: usa os endpoints padrão
        profiles = client.get_profiles()
    except OnvifAuthError:
        camera.has_ptz = False
        return f"{FALLBACK_WARNING} (credenciais recusadas); PTZ desativado"
    except OnvifError as e:
        logger.debug(f"Consulta ONVIF falhou para {camera.ip}:{camera.port}: {e}")
        camera.has_ptz = False
        return f"{FALLBACK_WARNING}; PTZ desativado"

    main = next((p for p in profiles if p.get("stream_uri")), None)
    if main is None:
        camera.has_ptz = bool(client.has_ptz)
        return f"{FALLBACK_WARNING} (a câmera não informou a URI RTSP)"

    main_uri = main["stream_uri"]
    parsed = urllib.parse.urlparse(main_uri)
    camera.rtsp_url = main_uri
    camera.rtsp_port = parsed.port or 554
    camera.rtsp_path = parsed.path + (f"?{parsed.query}" if parsed.query else "")

    # Menor resolução com URI própria = sub-stream (perfis já vêm ordenados por resolução)
    sub = next((p for p in reversed(profiles) if p.get("stream_uri") and p is not main), None)
    camera.substream_url = sub["stream_uri"] if sub else ""

    camera.has_ptz = bool(client.has_ptz)
    camera.active_profile_token = main["token"]
    camera.substream_profile_token = sub["token"] if sub else main["token"]
    camera.profiles = profiles
    if info:
        if info.get("manufacturer") and info["manufacturer"] != "Genérico":
            camera.manufacturer = info["manufacturer"]
        if info.get("model") and info["model"] != "ONVIF Device":
            camera.model = info["model"]
    return None
