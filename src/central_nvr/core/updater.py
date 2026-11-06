#!/usr/bin/env python3
"""
Módulo Central de Atualizações via GitHub Releases para AppImage universal.
Gerencia verificação de versão semântica, consulta à API do GitHub,
download seguro com validação SHA-256 e substituição atômica de executáveis.
"""
from dataclasses import dataclass, field
import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
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

_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")

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
    Retorna o formato padrão e universal de distribuição do projeto ('appimage').
    """
    return "appimage"


def find_best_asset_for_system(assets: List[ReleaseAsset]) -> Optional[ReleaseAsset]:
    """
    Encontra o asset de download adequado para a máquina atual (.AppImage x86_64).
    Retorna None se nenhum AppImage x86_64 for encontrado (rejeitando tarballs, zips e outras arquiteturas).
    """
    if not assets:
        return None

    # Seleção estrita: apenas executável AppImage compatível com arquitetura x86_64 / amd64
    for asset in assets:
        name_lower = asset.name.lower()
        if name_lower.endswith(".appimage") and ("x86_64" in name_lower or "amd64" in name_lower):
            return asset

    return None


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
    expected_size: Optional[int] = None,
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

        part_path = dest_path + ".part"
        with open(part_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if cancel_event and cancel_event.is_set():
                    logger.info("Download cancelado pelo usuário.")
                    f.close()
                    if os.path.exists(part_path):
                        os.remove(part_path)
                    return False

                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed_kbps = (downloaded / 1024) / max(elapsed, 0.001)

                    if progress_callback:
                        progress_callback(downloaded, total_size, speed_kbps)

        expected = expected_size if expected_size else total_size
        if expected and downloaded != expected:
            raise IOError(f"Tamanho incorreto: recebido {downloaded} B, esperado {expected} B.")

        os.replace(part_path, dest_path)
        try:
            os.chmod(dest_path, 0o600)
        except (OSError, PermissionError):
            pass

        return True

    except Exception as e:
        logger.error(f"Erro ao baixar arquivo {url}: {e}")
        for leftover in (dest_path + ".part", dest_path):
            if os.path.exists(leftover):
                try:
                    os.remove(leftover)
                except Exception:
                    pass
        return False



def get_updates_dir():
    """
    Retorna o diretório privado do usuário para download de atualizações (~/.cache/central-nvr/updates).
    Garante permissões estritas 0700 (SEC-02).
    """
    from pathlib import Path
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base_dir = Path(xdg_cache) if xdg_cache else (Path.home() / ".cache")
    updates_dir = base_dir / "central-nvr" / "updates"
    try:
        updates_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(updates_dir, 0o700)
    except (OSError, PermissionError):
        pass
    return updates_dir


def compute_file_sha256(file_path: str) -> str:
    """Calcula o hash SHA-256 hexadecimal de um arquivo no disco (SEC-07)."""
    if not os.path.exists(file_path):
        return ""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def verify_file_sha256(file_path: str, expected_sha256: str) -> bool:
    """Compara o hash SHA-256 real do arquivo com o hash esperado (SEC-07)."""
    if not file_path or not expected_sha256 or not os.path.exists(file_path):
        return False
    actual_hash = compute_file_sha256(file_path)
    return actual_hash.strip().lower() == expected_sha256.strip().lower()


def get_expected_sha256_for_asset(
    release_info: ReleaseInfo,
    asset_name: str,
    token: Optional[str] = None,
) -> Optional[str]:
    """
    Busca o checksum SHA-256 correspondente ao asset da release do GitHub (SEC-07).
    Examina anexos de checksum (ex: sha256sums.txt, <asset>.sha256) e o corpo da release.
    """
    if not release_info:
        return None

    direct_sha_asset = None
    checksum_asset = None

    for a in release_info.assets:
        aname_low = a.name.lower()
        if a.name == f"{asset_name}.sha256" or a.name == f"{asset_name}.sha256sum":
            direct_sha_asset = a
            break
        if aname_low in ("sha256sums.txt", "sha256sums", "checksums.txt", "checksums", "sha256.txt"):
            checksum_asset = a

    headers = {"User-Agent": f"CentralNVR-Updater/{__version__}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if direct_sha_asset:
        try:
            resp = requests.get(direct_sha_asset.download_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                m = re.search(r"([a-fA-F0-9]{64})", resp.text)
                if m:
                    return m.group(1).lower()
        except Exception as e:
            logger.debug(f"Erro ao baixar checksum {direct_sha_asset.name}: {e}")

    if checksum_asset:
        try:
            resp = requests.get(checksum_asset.download_url, headers=headers, timeout=10)
            if resp.status_code == 200:
                for line in resp.text.splitlines():
                    if asset_name in line:
                        m = re.search(r"([a-fA-F0-9]{64})", line)
                        if m:
                            return m.group(1).lower()
        except Exception as e:
            logger.debug(f"Erro ao baixar checksum {checksum_asset.name}: {e}")

    if release_info.body:
        for line in release_info.body.splitlines():
            if asset_name in line:
                m = re.search(r"([a-fA-F0-9]{64})", line)
                if m:
                    return m.group(1).lower()

    return None

def _extract_tar_members_to_local(tar, target_base):
    from pathlib import Path
    resolved_target = target_base.resolve()

    for member in tar.getmembers():
        # Rejeitar links simbólicos ou hard links que apontem para fora de target_base (SEC-03)
        if member.issym() or member.islnk():
            link_target = (target_base / member.linkname).resolve()
            if not (link_target == resolved_target or str(link_target).startswith(str(resolved_target) + os.sep)):
                logger.warning(f"Tentativa de Symlink/Hardlink Traversal bloqueada: {member.name} -> {member.linkname}")
                continue

        rel_parts = Path(member.name).parts
        if len(rel_parts) > 1 and rel_parts[0] in (".", "/"):
            rel_parts = rel_parts[1:]
        if len(rel_parts) > 0 and rel_parts[0] == "usr":
            rel_parts = rel_parts[1:]
        if not rel_parts:
            continue

        try:
            dest_path = target_base.joinpath(*rel_parts).resolve()
        except Exception as e:
            logger.warning(f"Caminho inválido no pacote de atualização ({member.name}): {e}")
            continue

        # SEC-03: Validar contenção canônica estrita para prevenir Tar Slip
        if not (dest_path == resolved_target or str(dest_path).startswith(str(resolved_target) + os.sep)):
            logger.warning(f"Tentativa de Path Traversal bloqueada: {member.name} -> {dest_path}")
            continue

        if member.isdir():
            dest_path.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            f_in = tar.extractfile(member)
            if f_in is not None:
                with open(dest_path, "wb") as out_f:
                    shutil.copyfileobj(f_in, out_f)
            if member.mode:
                try:
                    os.chmod(dest_path, member.mode)
                except Exception:
                    pass


def _ensure_desktop_entry_for_appimage(appimage_path: Path):
    """Cria ou atualiza a entrada de menu (.desktop) apontando para o AppImage."""
    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = apps_dir / "central-nvr.desktop"
    content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Central NVR WiFi
GenericName=NVR IP Camera Viewer
Comment=Sistema de Gerenciamento, Descoberta ONVIF e Streaming RTSP de Baixa Latência
Exec="{appimage_path}" %U
Icon=central-nvr
Terminal=false
Categories=AudioVideo;Video;Network;Monitor;Security;
StartupNotify=true
StartupWMClass=central-nvr
"""
    try:
        with open(desktop_file, "w", encoding="utf-8") as f:
            f.write(content)
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", str(apps_dir)], capture_output=True, timeout=4)
    except Exception as e:
        logger.debug(f"Erro ao registrar atalho desktop: {e}")


def install_downloaded_package(
    file_path: str,
    expected_sha256: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Instala ou atualiza o binário portátil AppImage com verificação de integridade SHA-256 (SEC-07).
    Executa substituição atômica no arquivo AppImage atual (Atomic Replacement) ou instala
    em ~/.local/bin/central-nvr.AppImage sem requerer privilégios de root (pkexec).
    """
    if not os.path.exists(file_path):
        return False, "Arquivo de instalação não encontrado no disco."

    # SEC-07: checksum SHA-256 obrigatório; sem ele a instalação é bloqueada
    if not (expected_sha256 and _SHA256_RE.match(expected_sha256.strip())):
        return False, "Release sem checksum SHA-256 válido: instalação bloqueada por segurança."

    expected_hash = expected_sha256.strip()
    if not verify_file_sha256(file_path, expected_hash):
        actual_hash = compute_file_sha256(file_path)
        return False, f"Falha de integridade SHA-256: checksum divergente (esperado: {expected_hash}, calculado: {actual_hash})."

    file_obj = Path(file_path).resolve()
    filename_lower = file_obj.name.lower()

    try:
        # Formato Principal: AppImage universal
        if filename_lower.endswith(".appimage"):
            os.chmod(file_obj, 0o755)
            running_appimage = os.environ.get("APPIMAGE")

            # Se estiver rodando a partir de um AppImage existente, realizar Atomic Replacement
            if running_appimage and os.path.exists(running_appimage):
                target_path = Path(running_appimage).resolve()
                temp_target = target_path.with_name(f"{target_path.name}.tmp.{os.getpid()}")
                backup_path = target_path.with_name(f"{target_path.name}.bak")
                try:
                    shutil.copy2(file_obj, temp_target)
                    os.chmod(temp_target, 0o755)
                    shutil.copy2(target_path, backup_path)
                    os.replace(temp_target, target_path)
                except Exception:
                    if temp_target.exists():
                        temp_target.unlink()
                    if backup_path.exists() and not target_path.exists():
                        os.replace(backup_path, target_path)
                    raise
                return True, f"Central NVR WiFi atualizada com sucesso em {target_path}. Reinicie o aplicativo para usar a nova versão."

            # Caso contrário, instalar no diretório de binários do usuário (~/.local/bin)
            user_bin_dir = Path.home() / ".local" / "bin"
            user_bin_dir.mkdir(parents=True, exist_ok=True)
            target_path = user_bin_dir / "central-nvr.AppImage"
            temp_target = target_path.with_name(f"{target_path.name}.tmp.{os.getpid()}")
            backup_path = target_path.with_name(f"{target_path.name}.bak")
            try:
                shutil.copy2(file_obj, temp_target)
                os.chmod(temp_target, 0o755)
                if target_path.exists():
                    shutil.copy2(target_path, backup_path)
                os.replace(temp_target, target_path)
            except Exception:
                if temp_target.exists():
                    temp_target.unlink()
                if backup_path.exists() and not target_path.exists():
                    os.replace(backup_path, target_path)
                raise
            _ensure_desktop_entry_for_appimage(target_path)
            return True, f"Central NVR WiFi AppImage instalada com sucesso em {target_path}."

        # Formato Secundário: Tarball comprimido (.tar.gz / .tgz) com proteção Tar Slip (SEC-03)
        elif filename_lower.endswith((".tar.gz", ".tgz")):
            target_base = Path.home() / ".local"
            target_base.mkdir(parents=True, exist_ok=True)
            import tarfile
            with tarfile.open(file_obj, "r:*") as tar:
                _extract_tar_members_to_local(tar, target_base)
            return True, "Central NVR WiFi instalada com sucesso em ~/.local."

        return False, f"Formato não suportado para atualização: {file_obj.suffix}"

    except Exception as e:
        logger.error(f"Erro ao processar pacote AppImage: {e}")
        return False, str(e)


def install_package_to_user_local(file_path: str, expected_sha256: Optional[str] = None) -> Tuple[bool, str]:
    """Alias para install_downloaded_package, mantendo compatibilidade de API."""
    return install_downloaded_package(file_path, expected_sha256=expected_sha256)


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
        expected_size: Optional[int] = None,
    ):
        super().__init__(parent)
        self.expected_size = expected_size
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
            expected_size=self.expected_size,
        )

        if self._cancel_event.is_set():
            self.download_failed.emit("Download cancelado pelo usuário.")
        elif success:
            self.download_finished.emit(self.destination_path)
        else:
            self.download_failed.emit("Falha durante a transferência do pacote de atualização.")


def resolve_checksum_or_none(
    release_info: ReleaseInfo,
    asset_name: str,
    token: Optional[str] = None,
) -> Optional[str]:
    """Retorna o SHA-256 válido do asset ou None se ausente/inválido."""
    h = get_expected_sha256_for_asset(release_info, asset_name, token=token)
    if h and _SHA256_RE.match(h.strip()):
        return h.strip().lower()
    return None


class ChecksumFetchWorker(QThread):
    """Busca o checksum SHA-256 em segundo plano antes de iniciar o download."""
    checksum_ready = Signal(str)
    checksum_missing = Signal()

    def __init__(self, release_info, asset_name: str, token: Optional[str] = None, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.release_info = release_info
        self.asset_name = asset_name
        self.token = token

    def run(self):
        h = resolve_checksum_or_none(self.release_info, self.asset_name, self.token)
        if h:
            self.checksum_ready.emit(h)
        else:
            self.checksum_missing.emit()
