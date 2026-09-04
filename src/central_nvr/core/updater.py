#!/usr/bin/env python3
"""
Módulo Central de Atualizações via GitHub Releases.
Gerencia verificação de versão semântica, consulta à API do GitHub,
download de pacotes (.deb, .rpm) e execução assíncrona com threads Qt.
"""
from dataclasses import dataclass, field
import datetime
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests
from PySide6.QtCore import QObject, QThread, Signal

from central_nvr import __version__

logger = logging.getLogger(__name__)

DEFAULT_GITHUB_REPO = "Othayz/central-nvr-wifi"


@dataclass
class ReleaseAsset:
    """Representa um anexo binário de uma release do GitHub."""
    name: str
    size: int
    download_url: str
    content_type: str = ""
    created_at: str = ""

    @property
    def formatted_size(self) -> str:
        """Retorna o tamanho formatado em KB ou MB."""
        if self.size < 1024:
            return f"{self.size} B"
        elif self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        else:
            return f"{self.size / (1024 * 1024):.1f} MB"


@dataclass
class ReleaseInfo:
    """Informações completas de uma versão/release obtida do GitHub."""
    tag_name: str
    version: str
    title: str
    body: str
    html_url: str
    published_at: str
    prerelease: bool = False
    assets: List[ReleaseAsset] = field(default_factory=list)
    is_newer: bool = False

    @property
    def formatted_date(self) -> str:
        """Formata a data de publicação no padrão brasileiro."""
        if not self.published_at:
            return ""
        try:
            dt = datetime.datetime.fromisoformat(self.published_at.replace("Z", "+00:00"))
            return dt.strftime("%d/%m/%Y às %H:%M")
        except Exception:
            return self.published_at[:10]


def parse_release_version(tag_name: str, title: str = "") -> str:
    """
    Extrai a melhor versão semântica considerando tanto o título da release quanto a tag git.
    Isso resolve divergências quando uma release é nomeada no GitHub (ex: 'v1.1')
    enquanto a tag subjacente possui outro formato (ex: 'Central_NVR_WiFi_v1.0.0').
    """
    candidates = []
    for s in [title, tag_name]:
        if not s:
            continue
        m = re.search(r"(?:v|ver|version)?\s*([0-9]+(?:\.[0-9]+)+[a-zA-Z0-9_\-]*)", str(s), re.IGNORECASE)
        if m:
            ver_str = m.group(1)
            candidates.append((parse_version(ver_str), ver_str))
        else:
            clean = re.sub(r"^[a-zA-Z_\-]+", "", str(s).strip())
            if clean and clean[0].isdigit():
                candidates.append((parse_version(clean), clean))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    return re.sub(r"^[a-zA-Z_\-]+", "", tag_name) or "1.0.0"


def parse_version(ver_str: str) -> Tuple[int, ...]:
    """
    Converte uma string de versão (ex: 'v1.2.3', '1.0.0-beta', '2.1') em uma tupla comparável.
    """
    if not ver_str:
        return (0, 0, 0)
    
    clean_ver = re.sub(r"^[a-zA-Z_\-]+", "", str(ver_str).strip())
    parts = []
    for part in re.split(r"[.\-+]", clean_ver):
        digits = re.findall(r"\d+", part)
        if digits:
            parts.append(int(digits[0]))
        else:
            break

    while len(parts) < 3:
        parts.append(0)

    return tuple(parts[:4])


def is_version_newer(current_ver: str, remote_ver: str) -> bool:
    """
    Compara a versão atual com a versão remota do GitHub.
    Retorna True se remote_ver for estritamente mais recente que current_ver.
    """
    parsed_curr = parse_version(current_ver)
    parsed_rem = parse_version(remote_ver)
    return parsed_rem > parsed_curr


def detect_system_package_format() -> str:
    """
    Detecta o formato preferencial de pacote nativo da distribuição Linux.
    Retorna 'deb', 'rpm' ou 'tar.gz'.
    """
    if os.path.exists("/etc/os-release"):
        try:
            with open("/etc/os-release", "r", encoding="utf-8") as f:
                content = f.read().lower()
                if any(x in content for x in ["ubuntu", "debian", "mint", "pop!_os", "elementary"]):
                    return "deb"
                if any(x in content for x in ["fedora", "rhel", "centos", "rocky", "almalinux", "opensuse", "suse"]):
                    return "rpm"
        except Exception as e:
            logger.debug(f"Erro ao ler /etc/os-release: {e}")

    if shutil.which("dpkg") or shutil.which("apt"):
        return "deb"
    if shutil.which("rpm") or shutil.which("dnf"):
        return "rpm"

    return "deb"


def find_best_asset_for_system(assets: List[ReleaseAsset]) -> Optional[ReleaseAsset]:
    """
    Encontra o asset de download mais adequado para a máquina atual (.deb, .rpm ou tar.gz).
    """
    if not assets:
        return None

    pkg_format = detect_system_package_format()

    # Prioridade 1: Pacote do formato nativo detectado
    for asset in assets:
        if asset.name.lower().endswith(f".{pkg_format}"):
            return asset

    # Prioridade 2: Outros formatos de instalador binário
    for asset in assets:
        if asset.name.lower().endswith(".deb") or asset.name.lower().endswith(".rpm"):
            return asset

    # Prioridade 3: Arquivo comprimido
    for asset in assets:
        if asset.name.lower().endswith((".tar.gz", ".tgz", ".zip")):
            return asset

    return assets[0]


def fetch_latest_release(
    repo: str = DEFAULT_GITHUB_REPO,
    include_prereleases: bool = False,
    timeout: int = 8,
    token: Optional[str] = None,
) -> Optional[ReleaseInfo]:
    """
    Consulta a API pública do GitHub para obter a última release disponível.
    Suporta autenticação por token (PAT) para repositórios privados ou para contornar rate-limit.
    """
    if not token:
        token = os.environ.get("GITHUB_TOKEN", "").strip() or None
        if not token:
            try:
                from central_nvr.core.config import ConfigManager
                token = ConfigManager().get("github_token", "").strip() or None
            except Exception:
                token = None

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": f"CentralNVR-App/{__version__}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{repo}/releases/latest"

    try:
        response = requests.get(url, headers=headers, timeout=timeout)

        # 401: Token inválido
        if response.status_code == 401:
            raise RuntimeError("Token de autenticação do GitHub inválido ou expirado. Verifique as configurações.")

        # 403: Rate limit ou permissão negada
        if response.status_code == 403:
            resp_body = response.text.lower()
            if "rate limit" in resp_body:
                raise RuntimeError(
                    "Limite de requisições da API pública do GitHub atingido para seu endereço IP.\n"
                    "Configure um GitHub Personal Access Token em Configurações para continuar sem restrições."
                )
            raise RuntimeError(f"Acesso não autorizado ao repositório '{repo}' (HTTP 403).")

        # 404: Endpoint /releases/latest pode dar 404 se não houver release publicada OU se o repo for privado/inexistente
        if response.status_code == 404:
            # 1. Tentar listar todas as releases
            list_url = f"https://api.github.com/repos/{repo}/releases"
            list_resp = requests.get(list_url, headers=headers, timeout=timeout)

            if list_resp.status_code == 200:
                releases = list_resp.json()
                if isinstance(releases, list) and len(releases) > 0:
                    candidates = [r for r in releases if include_prereleases or not r.get("prerelease", False)]
                    if candidates:
                        return _parse_github_release_dict(candidates[0])

            # 2. Se a lista de releases também deu 404, verificar se o repositório ou tags existem
            tags_url = f"https://api.github.com/repos/{repo}/tags"
            tags_resp = requests.get(tags_url, headers=headers, timeout=timeout)

            if tags_resp.status_code == 200:
                tags = tags_resp.json()
                if isinstance(tags, list) and len(tags) > 0:
                    first_tag = tags[0]
                    tag_name = first_tag.get("name", "v1.0.0")
                    clean_v = re.sub(r"^[a-zA-Z_\-]+", "", tag_name)
                    return ReleaseInfo(
                        tag_name=tag_name,
                        version=clean_v,
                        title=f"Central NVR WiFi {tag_name}",
                        body="Versão identificada via tag do repositório.",
                        html_url=f"https://github.com/{repo}/releases/tag/{tag_name}",
                        published_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        prerelease=False,
                        assets=[],
                        is_newer=is_version_newer(__version__, clean_v),
                    )

            # Se tanto releases quanto tags retornaram 404, o repositório é privado ou não existe
            if list_resp.status_code == 404 and tags_resp.status_code == 404:
                raise RuntimeError(
                    f"O repositório '{repo}' não foi encontrado ou é PRIVADO no GitHub.\n\n"
                    "• Se o repositório for seu: altere a visibilidade para Público no GitHub, ou\n"
                    "• Informe um GitHub Personal Access Token (PAT) nas Configurações da Central NVR."
                )

            # Se o repositório existe e foi acessado (200), mas está sem nenhuma release ou tag cadastrada
            return ReleaseInfo(
                tag_name=f"v{__version__}",
                version=__version__,
                title="Repositório Sincronizado",
                body="Nenhuma nova versão ou release foi publicada no repositório GitHub ainda. Você já está utilizando a versão mais recente.",
                html_url=f"https://github.com/{repo}",
                published_at="",
                prerelease=False,
                assets=[],
                is_newer=False,
            )

        if response.status_code != 200:
            raise RuntimeError(f"Consulta ao GitHub retornou status HTTP {response.status_code}.")

        data = response.json()
        return _parse_github_release_dict(data)

    except requests.exceptions.RequestException as e:
        logger.warning(f"Falha de conexão ao verificar atualizações no GitHub: {e}")
        raise RuntimeError("Não foi possível conectar ao GitHub. Verifique sua conexão com a internet.") from e
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao processar dados de release do GitHub: {e}")
        raise RuntimeError(f"Erro ao processar dados da release: {e}") from e


def _parse_github_release_dict(data: Dict[str, Any]) -> ReleaseInfo:
    """Extrai campos da estrutura de resposta JSON do GitHub."""
    tag_name = data.get("tag_name", "")
    title = data.get("name") or f"Versão {tag_name}"
    clean_v = parse_release_version(tag_name, title)
    body = data.get("body") or "Nenhuma nota de versão fornecida."
    html_url = data.get("html_url", "")
    published_at = data.get("published_at", "")
    prerelease = data.get("prerelease", False)

    assets_list = []
    for item in data.get("assets", []):
        assets_list.append(
            ReleaseAsset(
                name=item.get("name", ""),
                size=item.get("size", 0),
                download_url=item.get("browser_download_url", ""),
                content_type=item.get("content_type", ""),
                created_at=item.get("created_at", ""),
            )
        )

    is_newer = is_version_newer(__version__, clean_v)

    return ReleaseInfo(
        tag_name=tag_name,
        version=clean_v,
        title=title,
        body=body,
        html_url=html_url,
        published_at=published_at,
        prerelease=prerelease,
        assets=assets_list,
        is_newer=is_newer,
    )


def download_asset(
    url: str,
    dest_path: str,
    token: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, float], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> bool:
    """
    Baixa um asset via stream HTTP com notificação de progresso (downloaded_bytes, total_bytes, speed_kBps).
    Suporta autenticação com token para repositórios privados.
    """
    try:
        os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
        headers = {"User-Agent": f"CentralNVR-Updater/{__version__}"}
        if token and "api.github.com" in url:
            headers["Authorization"] = f"Bearer {token}"
            headers["Accept"] = "application/octet-stream"

        response = requests.get(url, stream=True, headers=headers, timeout=30)
        response.raise_for_status()

        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0
        start_time = time.time()

        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if cancel_event and cancel_event.is_set():
                    logger.info("Download cancelado pelo usuário.")
                    f.close()
                    if os.path.exists(dest_path):
                        os.remove(dest_path)
                    return False

                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed_kbps = (downloaded / 1024) / max(elapsed, 0.001)

                    if progress_callback:
                        progress_callback(downloaded, total_size, speed_kbps)

        return True

    except Exception as e:
        logger.error(f"Erro ao baixar arquivo {url}: {e}")
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        return False


def _extract_tar_members_to_local(tar, target_base):
    from pathlib import Path
    for member in tar.getmembers():
        rel_parts = Path(member.name).parts
        if len(rel_parts) > 1 and rel_parts[0] in (".", "/"):
            rel_parts = rel_parts[1:]
        if len(rel_parts) > 0 and rel_parts[0] == "usr":
            rel_parts = rel_parts[1:]
        if not rel_parts:
            continue
        dest_path = target_base.joinpath(*rel_parts)
        if member.isdir():
            dest_path.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as out_f:
                shutil.copyfileobj(tar.extractfile(member), out_f)
            if member.mode:
                try:
                    os.chmod(dest_path, member.mode)
                except Exception:
                    pass


def install_package_to_user_local(file_path: str) -> Tuple[bool, str]:
    """
    Instala ou atualiza o pacote diretamente no espaço do usuário (~/.local/).
    Funciona em qualquer distribuição Linux, inclusive sistemas imutáveis (Bazzite, Kinoite, SteamOS),
    sem requerer privilégios de root nem abrir gerenciadores de arquivo (Ark/File Roller).
    """
    import io
    import tarfile
    from pathlib import Path

    target_base = Path.home() / ".local"
    target_base.mkdir(parents=True, exist_ok=True)

    data_tar_bytes = None
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".deb":
        try:
            with open(file_path, "rb") as f:
                magic = f.read(8)
                if magic != b"!<arch>\n":
                    return False, "O arquivo .deb baixado não possui formato ar válido."
                while True:
                    hdr = f.read(60)
                    if len(hdr) < 60:
                        break
                    m_name = hdr[:16].decode("ascii", errors="ignore").strip()
                    m_size = int(hdr[48:58].decode("ascii", errors="ignore").strip())
                    content = f.read(m_size)
                    if m_size % 2 != 0:
                        f.read(1)
                    if "data.tar" in m_name:
                        data_tar_bytes = content
                        break
        except Exception as e:
            return False, f"Falha ao processar pacote .deb: {e}"

        if not data_tar_bytes:
            return False, "Arquivo de dados (data.tar) não encontrado no pacote .deb."

        try:
            with tarfile.open(fileobj=io.BytesIO(data_tar_bytes)) as tar:
                _extract_tar_members_to_local(tar, target_base)
        except Exception as e:
            return False, f"Erro ao extrair arquivos da aplicação: {e}"

    elif file_path.endswith((".tar.gz", ".tgz")):
        try:
            with tarfile.open(file_path, "r:*") as tar:
                _extract_tar_members_to_local(tar, target_base)
        except Exception as e:
            return False, f"Erro ao extrair tarball: {e}"

    else:
        return False, f"Formato '{ext}' não suporta instalação direta no usuário."

    # Configurar executável launcher ~/.local/bin/central-nvr
    bin_path = target_base / "bin" / "central-nvr"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_content = f"""#!/bin/sh
export PYTHONPATH="{target_base / 'lib' / 'central-nvr'}:${{PYTHONPATH}}"
exec /usr/bin/python3 -m central_nvr.app "$@"
"""
    try:
        with open(bin_path, "w", encoding="utf-8") as f:
            f.write(launcher_content)
        os.chmod(bin_path, 0o755)
    except Exception as e:
        logger.warning(f"Erro criando launcher em {bin_path}: {e}")

    # Atualizar lançador .desktop
    desktop_path = target_base / "share" / "applications" / "central-nvr.desktop"
    if desktop_path.exists():
        try:
            with open(desktop_path, "r", encoding="utf-8") as f:
                d_content = f.read()
            d_content = re.sub(r"^Exec=.*$", f"Exec={bin_path}", d_content, flags=re.MULTILINE)
            icon_svg = target_base / "share" / "icons" / "hicolor" / "scalable" / "apps" / "central-nvr.svg"
            icon_png = target_base / "share" / "icons" / "hicolor" / "512x512" / "apps" / "central-nvr.png"
            icon_to_use = icon_svg if icon_svg.exists() else (icon_png if icon_png.exists() else "central-nvr")
            d_content = re.sub(r"^Icon=.*$", f"Icon={icon_to_use}", d_content, flags=re.MULTILINE)
            with open(desktop_path, "w", encoding="utf-8") as f:
                f.write(d_content)
        except Exception as e:
            logger.warning(f"Erro atualizando .desktop: {e}")

    if shutil.which("update-desktop-database"):
        try:
            subprocess.run(["update-desktop-database", str(target_base / "share" / "applications")], capture_output=True, timeout=4)
        except Exception:
            pass

    return True, "Central NVR WiFi instalada e atualizada com sucesso em ~/.local (Menu de Aplicativos atualizado)."


def install_downloaded_package(file_path: str) -> Tuple[bool, str]:
    """
    Inicia a instalação do arquivo de atualização baixado (.deb, .rpm ou .tar.gz).
    Prioriza instalação nativa do sistema em distros compatíveis ou realiza a instalação
    direta no espaço do usuário (~/.local), prevenindo abertura indevida em gerenciadores de arquivo (Ark).
    """
    if not os.path.exists(file_path):
        return False, "Arquivo de instalação não encontrado no disco."

    ext = os.path.splitext(file_path)[1].lower()
    is_ostree = os.path.exists("/run/ostree-booted")

    try:
        # Se for Ubuntu/Debian padrão (com apt e sem sistema imutável)
        if ext == ".deb" and not is_ostree and shutil.which("pkexec") and shutil.which("apt"):
            subprocess.Popen(["pkexec", "apt", "install", "-y", os.path.abspath(file_path)])
            return True, "Assistente de instalação iniciado via apt/pkexec."

        # Se for Fedora/RHEL tradicional (com dnf e sem sistema imutável)
        if ext == ".rpm" and not is_ostree and shutil.which("pkexec") and shutil.which("dnf"):
            subprocess.Popen(["pkexec", "dnf", "install", "-y", os.path.abspath(file_path)])
            return True, "Assistente de instalação iniciado via dnf/pkexec."

        # Para sistemas imutáveis (Bazzite, Kinoite, SteamOS) ou quando apt/dnf não estiverem disponíveis:
        # Instala diretamente no espaço do usuário (~/.local), sem root e sem abrir gerenciadores como o Ark.
        success, msg = install_package_to_user_local(file_path)
        if success:
            return True, msg

        return False, f"Falha na instalação automática: {msg}"

    except Exception as e:
        logger.error(f"Erro ao acionar instalador: {e}")
        return False, str(e)


# =============================================================================
# Workers Assíncronos em QThread (PySide6)
# =============================================================================

class UpdateCheckWorker(QThread):
    """
    Thread em segundo plano para consultar a API do GitHub sem travar a interface.
    """
    update_available = Signal(object)      # Emite ReleaseInfo quando houver versão nova
    no_update_available = Signal(object)   # Emite ReleaseInfo ou None quando app estiver atualizado
    check_failed = Signal(str)             # Emite mensagem de erro em caso de falha de conexão

    def __init__(
        self,
        repo: str = DEFAULT_GITHUB_REPO,
        include_prereleases: bool = False,
        token: Optional[str] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.include_prereleases = include_prereleases
        self.token = token

    def run(self):
        try:
            release_info = fetch_latest_release(
                repo=self.repo,
                include_prereleases=self.include_prereleases,
                token=self.token,
                timeout=8,
            )

            if release_info is None:
                self.check_failed.emit("Não foi possível obter dados de versão do repositório.")
                return

            if release_info.is_newer:
                logger.info(f"Nova versão disponível encontrada: v{release_info.version} (Atual: v{__version__})")
                self.update_available.emit(release_info)
            else:
                logger.debug(f"Aplicativo está atualizado na versão v{__version__}.")
                self.no_update_available.emit(release_info)

        except Exception as e:
            logger.error(f"Exceção no worker de verificação de atualização: {e}")
            self.check_failed.emit(str(e))


class AssetDownloadWorker(QThread):
    """
    Thread em segundo plano para download de arquivo de atualização.
    """
    progress = Signal(int, int, float)    # downloaded, total, speed_kBps
    download_finished = Signal(str)       # destination file path
    download_failed = Signal(str)         # error message

    def __init__(
        self,
        download_url: str,
        destination_path: str,
        token: Optional[str] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self.download_url = download_url
        self.destination_path = destination_path
        self.token = token
        self._cancel_event = threading.Event()

    def cancel(self):
        """Sinaliza cancelamento do download."""
        self._cancel_event.set()

    def run(self):
        def _on_progress(dl: int, tot: int, spd: float):
            self.progress.emit(dl, tot, spd)

        success = download_asset(
            url=self.download_url,
            dest_path=self.destination_path,
            token=self.token,
            progress_callback=_on_progress,
            cancel_event=self._cancel_event,
        )

        if self._cancel_event.is_set():
            self.download_failed.emit("Download cancelado pelo usuário.")
        elif success:
            self.download_finished.emit(self.destination_path)
        else:
            self.download_failed.emit("Falha durante a transferência do pacote de atualização.")
