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
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from PySide6.QtCore import QObject, QThread, Signal

from central_nvr import __version__

logger = logging.getLogger(__name__)

_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")

DEFAULT_GITHUB_REPO = "Othayz/central-nvr-wifi"

# O token do GitHub só é enviado a estes hosts, comparados pelo hostname (Issue #19)
GITHUB_TOKEN_HOSTS = frozenset({"api.github.com", "github.com"})


@dataclass
class ReleaseAsset:
    """Representa um anexo binário de uma release do GitHub."""
    name: str
    size: int
    download_url: str
    content_type: str = ""
    created_at: str = ""
    # URL de API do asset (/repos/<repo>/releases/assets/<id>): única que aceita o token
    # para baixar de repositório privado, com Accept: application/octet-stream
    api_url: str = ""

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


_VERSION_IN_TAG_RE = re.compile(r"(\d+(?:\.\d+)+)([-+._]?[0-9A-Za-z][0-9A-Za-z.\-+_]*)?")


def parse_release_version(tag_name: str) -> str:
    """
    Extrai a versão da release **somente da tag** (Issue #35). O título é texto livre (ex.:
    "Suporte ao Ubuntu 24.04 (v1.2.2)") e não pode competir com a tag; o workflow de release já
    garante que a tag é igual à versão do projeto.
    """
    m = _VERSION_IN_TAG_RE.search(str(tag_name or ""))
    if m:
        return m.group(1) + (m.group(2) or "")
    return re.sub(r"^[a-zA-Z_\-]+", "", str(tag_name or "")) or "0.0.0"


def _split_version(ver_str: str) -> Tuple[Tuple[int, ...], str]:
    """Separa '1.3.0-rc2+build' em ((1, 3, 0), 'rc2'); metadados de build (+...) são ignorados."""
    clean = re.sub(r"^[a-zA-Z_\-]+", "", str(ver_str or "").strip())
    m = re.match(r"(\d+(?:\.\d+)*)(.*)$", clean)
    if not m:
        return (0, 0, 0), ""
    release = tuple(int(x) for x in m.group(1).split("."))
    suffix = m.group(2).split("+", 1)[0].lstrip("-._")
    return release, suffix


def parse_version(ver_str: str) -> Tuple[int, ...]:
    """
    Componentes numéricos da versão (ex.: 'v1.2.3' → (1, 2, 3); '1.0.0-rc1' → (1, 0, 0)).
    O sufixo de pré-release não entra aqui: veja version_key().
    """
    release, _ = _split_version(ver_str)
    parts = list(release)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_prerelease(ver_str: str) -> bool:
    """True para versões com sufixo de pré-release (1.3.0-beta, 1.3.0rc1...)."""
    return bool(_split_version(ver_str)[1])


def version_key(ver_str: str) -> Tuple[Any, ...]:
    """
    Chave de ordenação com a precedência do SemVer: a pré-release vem **antes** da versão final
    (1.2.1-rc2 < 1.2.1; 1.3.0-beta < 1.3.0) e identificadores numéricos comparam como números
    (rc2 < rc10).
    """
    release, suffix = _split_version(ver_str)
    padded = tuple(release) + (0,) * max(0, 6 - len(release))
    if not suffix:
        return (padded, (1,))
    ids = tuple((0, int(tok), "") if tok.isdigit() else (1, 0, tok.lower())
                for tok in re.findall(r"\d+|[A-Za-z]+", suffix))
    return (padded, (0,) + ids)


def is_version_newer(current_ver: str, remote_ver: str) -> bool:
    """
    Compara a versão atual com a versão remota do GitHub.
    Retorna True se remote_ver for estritamente mais recente que current_ver.
    """
    return version_key(remote_ver) > version_key(current_ver)


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


def resolve_github_token(token: Optional[str] = None) -> Optional[str]:
    """Token explícito, ou GITHUB_TOKEN do ambiente, ou `github_token` de settings.json/keyring (somente leitura)."""
    if token and token.strip():
        return token.strip()
    env_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if env_token:
        return env_token
    try:
        from central_nvr.core.config import get_config_dir, get_keyring_pat, is_keyring_available

        settings_path = get_config_dir() / "settings.json"
        if settings_path.exists():
            try:
                saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
                if isinstance(saved_settings, dict):
                    file_token = str(saved_settings.get("github_token") or "").strip()
                    if file_token:
                        return file_token
            except Exception:
                pass

        if is_keyring_available():
            kr_pat = get_keyring_pat()
            if kr_pat and str(kr_pat).strip():
                return str(kr_pat).strip()
    except Exception:
        return None
    return None


def github_auth_headers(url: str, token: Optional[str]) -> Dict[str, str]:
    """
    Cabeçalhos de autenticação para `url`: o token só vai para os hosts do GitHub, comparando o
    hostname (nunca por substring da URL). Na URL de API de asset, pede o binário.
    """
    if not token:
        return {}
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return {}
    if host not in GITHUB_TOKEN_HOSTS:
        return {}
    headers = {"Authorization": f"Bearer {token}"}
    if host == "api.github.com" and "/releases/assets/" in urlparse(url).path:
        headers["Accept"] = "application/octet-stream"
    return headers


def preferred_download_url(asset: "ReleaseAsset", token: Optional[str]) -> str:
    """Com token, baixa pela URL de API (funciona em repositório privado); sem token, pela pública."""
    if token and asset.api_url and (urlparse(asset.api_url).hostname or "").lower() == "api.github.com":
        return asset.api_url
    return asset.download_url


# ETag e corpo JSON da última resposta da API do GitHub, por URL (Issues #37, #73)
_ETAG_CACHE: Dict[str, Tuple[str, Any]] = {}
_GITHUB_NOT_FOUND = object()


def _raise_for_github_status(response: Any, repo: str) -> None:
    """Levanta RuntimeError descritivo para status HTTP de erro da API do GitHub."""
    if response.status_code == 401:
        raise RuntimeError("Token de autenticação do GitHub inválido ou expirado. Verifique as configurações.")
    if response.status_code == 403:
        resp_body = str(getattr(response, "text", "") or "").lower()
        if "rate limit" in resp_body:
            raise RuntimeError(
                "Limite de requisições da API pública do GitHub atingido para seu endereço IP.\n"
                "Configure um GitHub Personal Access Token em Configurações para continuar sem restrições."
            )
        raise RuntimeError(f"Acesso não autorizado ao repositório '{repo}' (HTTP 403).")
    raise RuntimeError(f"Consulta ao GitHub retornou status HTTP {response.status_code}.")


def _fetch_github_json_cached(
    url: str,
    headers: Dict[str, str],
    timeout: int,
    repo: str,
    *,
    allow_404: bool = False,
) -> Any:
    """
    Faz GET condicional (If-None-Match) em `url` usando `_ETAG_CACHE` (Issue #37).
    Em 304 reaproveita o corpo em cache; em 200 guarda ETag e JSON no cache.
    Com `allow_404=True`, retorna `_GITHUB_NOT_FOUND` em 404; nos demais status
    de erro levanta `RuntimeError` via `_raise_for_github_status`.
    """
    cached = _ETAG_CACHE.get(url)
    conditional_headers = dict(headers)
    if cached:
        conditional_headers["If-None-Match"] = cached[0]
    response = requests.get(url, headers=conditional_headers, timeout=timeout)

    if response.status_code == 304 and cached:
        return cached[1]
    if allow_404 and response.status_code == 404:
        return _GITHUB_NOT_FOUND
    if response.status_code != 200:
        _raise_for_github_status(response, repo)

    data = response.json()
    try:
        etag = response.headers.get("ETag")
    except AttributeError:
        etag = None
    if isinstance(etag, str) and etag:
        _ETAG_CACHE[url] = (etag, data)
    return data


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
    token = resolve_github_token(token)

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": f"CentralNVR-App/{__version__}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{repo}/releases/latest"

    try:
        if include_prereleases:
            # /releases/latest nunca devolve pré-releases: listar e escolher a maior versão
            list_resp = requests.get(f"https://api.github.com/repos/{repo}/releases", headers=headers, timeout=timeout)
            if list_resp.status_code == 200:
                best = _pick_highest_release(list_resp.json(), include_prereleases=True)
                if best is not None:
                    return _parse_github_release_dict(best)

        # Requisição condicional (Issue #37): 304 não conta na cota da API e evita baixar de novo
        data = _fetch_github_json_cached(url, headers, timeout, repo, allow_404=True)
        if data is _GITHUB_NOT_FOUND:
            # 1. Tentar listar todas as releases
            list_url = f"https://api.github.com/repos/{repo}/releases"
            list_resp = requests.get(list_url, headers=headers, timeout=timeout)

            if list_resp.status_code == 200:
                releases = list_resp.json()
                best = _pick_highest_release(releases, include_prereleases=include_prereleases)
                if best is not None:
                    return _parse_github_release_dict(best)

            # 2. Se a lista de releases também deu 404, verificar se o repositório ou tags existem
            tags_url = f"https://api.github.com/repos/{repo}/tags"
            tags_resp = requests.get(tags_url, headers=headers, timeout=timeout)

            if tags_resp.status_code == 200:
                tags = tags_resp.json()
                # A ordem da API não é por versão nem por data: ordenar e ignorar pré-releases quando desabilitadas
                versioned = [] if not isinstance(tags, list) else [
                    (t.get("name", ""), parse_release_version(t.get("name", "")))
                    for t in tags if isinstance(t, dict) and _VERSION_IN_TAG_RE.search(t.get("name", ""))
                ]
                if not include_prereleases:
                    versioned = [(name, ver) for name, ver in versioned if not is_prerelease(ver)]
                if versioned:
                    tag_name, clean_v = max(versioned, key=lambda nv: version_key(nv[1]))
                    return ReleaseInfo(
                        tag_name=tag_name,
                        version=clean_v,
                        title=f"Central NVR WiFi {tag_name}",
                        body="Versão identificada via tag do repositório.",
                        html_url=f"https://github.com/{repo}/releases/tag/{tag_name}",
                        published_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        prerelease=is_prerelease(clean_v),
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
            return _up_to_date_release_info(repo)

        # Defesa extra (Issue #73): mesmo que uma pré-release tenha sido publicada sem a flag
        # `prerelease=true` e apareça em /releases/latest, ignorá-la quando include_prereleases=False.
        if not include_prereleases and _is_release_prerelease(data):
            list_url = f"https://api.github.com/repos/{repo}/releases"
            releases = _fetch_github_json_cached(list_url, headers, timeout, repo)
            best = _pick_highest_release(releases, include_prereleases=False)
            if best is not None:
                return _parse_github_release_dict(best)
            return _up_to_date_release_info(repo)

        return _parse_github_release_dict(data)

    except requests.exceptions.RequestException as e:
        logger.warning(f"Falha de conexão ao verificar atualizações no GitHub: {e}")
        raise RuntimeError("Não foi possível conectar ao GitHub. Verifique sua conexão com a internet.") from e
    except RuntimeError:
        raise
    except Exception as e:
        logger.error(f"Erro inesperado ao processar dados de release do GitHub: {e}")
        raise RuntimeError(f"Erro ao processar dados da release: {e}") from e


def _up_to_date_release_info(repo: str) -> ReleaseInfo:
    """Retorna ReleaseInfo indicando ausência de atualização estável disponível."""
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


def _is_release_prerelease(release: Any) -> bool:
    """True se a release estiver marcada como pré-release na API ou tiver tag com sufixo de pré-release."""
    if not isinstance(release, dict):
        return False
    return bool(release.get("prerelease", False)) or is_prerelease(
        parse_release_version(release.get("tag_name", ""))
    )


def _pick_highest_release(releases: Any, include_prereleases: bool) -> Optional[Dict[str, Any]]:
    """Release publicada (não rascunho) de maior versão; pré-releases só se permitidas."""
    if not isinstance(releases, list):
        return None
    candidates = [
        r for r in releases
        if isinstance(r, dict) and not r.get("draft", False)
        and (include_prereleases or not _is_release_prerelease(r))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: version_key(parse_release_version(r.get("tag_name", ""))))


def _parse_github_release_dict(data: Dict[str, Any]) -> ReleaseInfo:
    """Extrai campos da estrutura de resposta JSON do GitHub."""
    tag_name = data.get("tag_name", "")
    title = data.get("name") or f"Versão {tag_name}"
    clean_v = parse_release_version(tag_name)
    body = data.get("body") or "Nenhuma nota de versão fornecida."
    html_url = data.get("html_url", "")
    published_at = data.get("published_at", "")
    prerelease = bool(data.get("prerelease", False)) or is_prerelease(clean_v)

    assets_list = []
    for item in data.get("assets", []):
        assets_list.append(
            ReleaseAsset(
                name=item.get("name", ""),
                size=item.get("size", 0),
                download_url=item.get("browser_download_url", ""),
                content_type=item.get("content_type", ""),
                created_at=item.get("created_at", ""),
                api_url=item.get("url", ""),
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
        headers.update(github_auth_headers(url, token))

        response = requests.get(url, stream=True, headers=headers, timeout=30)
        response.raise_for_status()

        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0
        start_time = time.time()

        part_path = dest_path + ".part"
        # Criação exclusiva sem seguir symlinks: um .part pré-existente (resíduo ou link plantado)
        # é removido em vez de ser reaproveitado como destino da escrita.
        try:
            os.unlink(part_path)
        except FileNotFoundError:
            pass
        part_fd = os.open(part_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(part_fd, "wb") as f:
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

    def _get(asset: ReleaseAsset):
        url = preferred_download_url(asset, token)
        headers = {"User-Agent": f"CentralNVR-Updater/{__version__}"}
        headers.update(github_auth_headers(url, token))
        return requests.get(url, headers=headers, timeout=10)

    if direct_sha_asset:
        try:
            resp = _get(direct_sha_asset)
            if resp.status_code == 200:
                m = re.search(r"([a-fA-F0-9]{64})", resp.text)
                if m:
                    return m.group(1).lower()
        except Exception as e:
            logger.debug(f"Erro ao baixar checksum {direct_sha_asset.name}: {e}")

    if checksum_asset:
        try:
            resp = _get(checksum_asset)
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

def desktop_exec_path(path: Path) -> str:
    """
    Caminho como argumento de `Exec=` conforme a Desktop Entry Specification (Issue #38):
    entre aspas, com `"`, `` ` ``, `$` e `\\` escapados; depois o escape de string do arquivo
    (`\\` → `\\\\`) e `%` → `%%` (códigos de campo).
    """
    raw = str(path)
    if any(c in raw for c in "\n\r\t"):
        raise ValueError("Caminho do AppImage contém caractere de controle")
    quoted = raw.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    quoted = quoted.replace("\\", "\\\\").replace("%", "%%")
    return f'"{quoted}"'


def _ensure_desktop_entry_for_appimage(appimage_path: Path):
    """Cria ou atualiza a entrada de menu (.desktop) apontando para o AppImage."""
    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = apps_dir / "central-nvr.desktop"
    try:
        exec_path = desktop_exec_path(appimage_path)
    except ValueError as e:
        logger.warning(f"Entrada .desktop não criada: {e}")
        return
    content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name=Central NVR WiFi
GenericName=NVR IP Camera Viewer
Comment=Sistema de Gerenciamento, Descoberta ONVIF e Streaming RTSP de Baixa Latência
Exec={exec_path}
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


class PackageIntegrityError(Exception):
    """O pacote preparado para instalação não corresponde ao SHA-256 esperado."""


def _require_sha256(path: Path, expected_sha256: str) -> None:
    """Levanta PackageIntegrityError se o arquivo não corresponder ao SHA-256 esperado."""
    if not verify_file_sha256(str(path), expected_sha256):
        raise PackageIntegrityError(
            f"Falha de integridade SHA-256: checksum divergente "
            f"(esperado: {expected_sha256}, calculado: {compute_file_sha256(str(path))})."
        )


def _fsync_path(path: Path) -> None:
    """Força os dados (ou a entrada de diretório) para o disco."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) if path.is_dir() else os.O_RDONLY
    fd = os.open(str(path), flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _stage_verified_copy(source: Path, target_path: Path, expected_sha256: str) -> Path:
    """
    Copia o pacote para um temporário exclusivo ao lado do destino e valida o SHA-256 dessa cópia,
    que é o mesmo arquivo que sofrerá os.replace. Verificar a origem e copiar depois deixaria uma
    janela para o pacote ser trocado entre a verificação e a instalação.
    """
    fd, staged_name = tempfile.mkstemp(prefix=f"{target_path.name}.tmp.", dir=str(target_path.parent))
    staged = Path(staged_name)
    try:
        with os.fdopen(fd, "wb") as out_f, open(source, "rb") as in_f:
            shutil.copyfileobj(in_f, out_f)
            # Dados no disco antes do os.replace: sem isto, numa queda de energia o rename pode
            # persistir antes do conteúdo e deixar um AppImage truncado (Issue #38)
            out_f.flush()
            os.fsync(out_f.fileno())
        _require_sha256(staged, expected_sha256)
        os.chmod(staged, 0o755)
        return staged
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _replace_appimage_atomically(source: Path, target_path: Path, expected_sha256: str) -> None:
    """Atomic Replacement: substitui target_path pela cópia verificada, preservando o anterior em .bak."""
    backup_path = target_path.with_name(f"{target_path.name}.bak")
    staged = _stage_verified_copy(source, target_path, expected_sha256)
    try:
        if target_path.exists():
            shutil.copy2(target_path, backup_path)
            _fsync_path(backup_path)
        os.replace(staged, target_path)
        _fsync_path(target_path.parent)  # Persiste o rename
    except Exception:
        staged.unlink(missing_ok=True)
        if backup_path.exists() and not target_path.exists():
            os.replace(backup_path, target_path)
        raise


def _user_bin_target() -> Path:
    user_bin_dir = Path.home() / ".local" / "bin"
    user_bin_dir.mkdir(parents=True, exist_ok=True)
    return user_bin_dir / "central-nvr.AppImage"


def _running_appimage_path() -> Optional[Path]:
    """
    Retorna o caminho resolvido do AppImage em execução somente quando o pacote
    central_nvr realmente roda a partir desse AppDir (Issue #72).

    A variável APPIMAGE é herdada por processos filhos (ex.: terminal aberto dentro
    de outro AppImage). Para evitar sobrescrever um executável de terceiros — mesmo
    quando esse outro AppImage embute Python no PATH —, exige-se APPDIR absoluto
    definido e que __file__ do pacote central_nvr esteja dentro de Path(APPDIR).resolve().
    """
    running_appimage = os.environ.get("APPIMAGE")
    if not running_appimage:
        return None

    try:
        appimage_path = Path(running_appimage).resolve()
    except OSError as e:
        logger.warning(f"Variável APPIMAGE ({running_appimage!r}) ignorada: caminho inválido ({e}).")
        return None

    if not appimage_path.is_file():
        logger.warning(f"Variável APPIMAGE ({running_appimage}) ignorada: arquivo não encontrado no disco.")
        return None

    appdir_env = os.environ.get("APPDIR")
    if not appdir_env:
        logger.warning(
            f"Variável APPIMAGE herdada ({appimage_path}) ignorada: APPDIR não definido; "
            "instalando em ~/.local/bin."
        )
        return None

    try:
        raw_appdir = Path(appdir_env)
        if not raw_appdir.is_absolute():
            logger.warning(
                f"Variável APPIMAGE herdada ({appimage_path}) ignorada: "
                f"APPDIR ({appdir_env!r}) não é absoluto; instalando em ~/.local/bin."
            )
            return None
        appdir_path = raw_appdir.resolve()
    except OSError as e:
        logger.warning(
            f"Variável APPIMAGE herdada ({appimage_path}) ignorada: "
            f"APPDIR inválido ({e}); instalando em ~/.local/bin."
        )
        return None

    if appdir_path == appdir_path.parent:
        logger.warning(
            f"Variável APPIMAGE herdada ({appimage_path}) ignorada: "
            f"APPDIR ({appdir_path}) aponta para a raiz do sistema; instalando em ~/.local/bin."
        )
        return None

    try:
        module_path = Path(__file__).resolve()
    except OSError as e:
        logger.warning(
            f"Variável APPIMAGE herdada ({appimage_path}) ignorada: "
            f"caminho do pacote inválido ({e}); instalando em ~/.local/bin."
        )
        return None

    if module_path.is_relative_to(appdir_path):
        return appimage_path

    logger.warning(
        f"Variável APPIMAGE herdada ({appimage_path}) ignorada: "
        f"pacote ({module_path}) não pertence ao AppDir ({appdir_path}); "
        "instalando em ~/.local/bin."
    )
    return None


def _rollback_hint(target_path: Path) -> str:
    backup = target_path.with_name(f"{target_path.name}.bak")
    if backup.exists():
        return f" A versão anterior foi preservada em {backup} (para voltar: renomeie-a para {target_path.name})."
    return ""


def install_downloaded_package(
    file_path: str,
    expected_sha256: Optional[str] = None,
    min_size: int = 0,
) -> Tuple[bool, str]:
    """
    Instala ou atualiza o binário portátil AppImage com verificação de integridade SHA-256 (SEC-07).
    Executa substituição atômica no arquivo AppImage atual (Atomic Replacement) ou instala
    em ~/.local/bin/central-nvr.AppImage sem requerer privilégios de root (pkexec).
    O AppImage é o formato único de distribuição (ADR-0001): outros formatos são recusados.
    """
    if not os.path.exists(file_path):
        return False, "Arquivo de instalação não encontrado no disco."

    file_obj = Path(file_path).resolve()
    if not file_obj.name.lower().endswith(".appimage"):
        return False, f"Formato não suportado para atualização: {file_obj.suffix or file_obj.name} (somente .AppImage)."

    if min_size > 0 and file_obj.stat().st_size < min_size:
        return False, f"Pacote com tamanho anômalo ({file_obj.stat().st_size} B). O tamanho mínimo esperado é de {min_size} B."

    # SEC-07: checksum SHA-256 obrigatório; sem ele a instalação é bloqueada
    if not (expected_sha256 and _SHA256_RE.match(expected_sha256.strip())):
        return False, "Release sem checksum SHA-256 válido: instalação bloqueada por segurança."

    expected_hash = expected_sha256.strip()

    try:
        target_path = _running_appimage_path()

        # Rodando a partir de um AppImage existente: Atomic Replacement no próprio arquivo
        if target_path is not None:
            if os.access(target_path.parent, os.W_OK):
                try:
                    _replace_appimage_atomically(file_obj, target_path, expected_hash)
                    return True, (
                        f"Central NVR WiFi atualizada com sucesso em {target_path}. "
                        f"Reinicie o aplicativo para usar a nova versão.{_rollback_hint(target_path)}"
                    )
                except PackageIntegrityError:
                    raise
                except OSError as e:
                    logger.warning(f"Destino {target_path} não gravável ({e}); instalando em ~/.local/bin.")
            else:
                logger.warning(f"Diretório de {target_path} somente leitura; instalando em ~/.local/bin.")

            # Destino somente leitura (/opt, mídia montada): instala no diretório do usuário
            fallback = _user_bin_target()
            _replace_appimage_atomically(file_obj, fallback, expected_hash)
            _ensure_desktop_entry_for_appimage(fallback)
            return True, (
                f"Não foi possível gravar em {target_path.parent} (somente leitura). "
                f"A nova versão foi instalada em {fallback} e o atalho do menu aponta para ela."
                f"{_rollback_hint(fallback)}"
            )

        # Caso contrário, instalar no diretório de binários do usuário (~/.local/bin)
        target_path = _user_bin_target()
        _replace_appimage_atomically(file_obj, target_path, expected_hash)
        _ensure_desktop_entry_for_appimage(target_path)
        return True, f"Central NVR WiFi AppImage instalada com sucesso em {target_path}.{_rollback_hint(target_path)}"

    except Exception as e:
        logger.error(f"Erro ao processar pacote AppImage: {e}")
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
        h = resolve_checksum_or_none(self.release_info, self.asset_name, resolve_github_token(self.token))
        if h:
            self.checksum_ready.emit(h)
        else:
            self.checksum_missing.emit()
