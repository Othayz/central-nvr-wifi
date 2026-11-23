"""
Módulo de Configuração e Persistência do Sistema (Padrão XDG).
"""
import copy
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from central_nvr.core.camera import DEFAULT_RTSP_TRANSPORT

try:
    import keyring
    import keyring.errors
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False

KEYRING_SERVICE_NAME = "central-nvr"

logger = logging.getLogger(__name__)


def _safe_keyring_get(username: str) -> Optional[str]:
    """Recupera um segredo do cofre do sistema (Keyring) de forma resiliente."""
    if not HAS_KEYRING or not username:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE_NAME, username)
    except Exception as e:
        logger.debug(f"Falha ao obter '{username}' do keyring: {e}")
        return None


def _safe_keyring_set(username: str, secret: str) -> bool:
    """Armazena ou remove um segredo no cofre do sistema (Keyring)."""
    if not HAS_KEYRING or not username:
        return False
    try:
        if secret:
            keyring.set_password(KEYRING_SERVICE_NAME, username, secret)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE_NAME, username)
            except keyring.errors.PasswordDeleteError:
                pass
        return True
    except Exception as e:
        logger.debug(f"Falha ao persistir '{username}' no keyring: {e}")
        return False


def _safe_keyring_delete(username: str) -> bool:
    """Remove um segredo do cofre do sistema de forma segura."""
    if not HAS_KEYRING or not username:
        return False
    try:
        keyring.delete_password(KEYRING_SERVICE_NAME, username)
        return True
    except Exception:
        return False


def get_keyring_password(camera_id: str) -> Optional[str]:
    """Recupera a senha da câmera do cofre do sistema (Keyring), se disponível."""
    return _safe_keyring_get(f"camera_{camera_id}") if camera_id else None


def set_keyring_password(camera_id: str, password: str) -> bool:
    """Armazena a senha da câmera no cofre do sistema (Keyring), se disponível."""
    return _safe_keyring_set(f"camera_{camera_id}", password) if camera_id else False


def delete_keyring_password(camera_id: str) -> bool:
    """Remove a senha da câmera do cofre do sistema."""
    return _safe_keyring_delete(f"camera_{camera_id}") if camera_id else False


def is_keyring_available() -> bool:
    """Verifica se o subsistema de keyring do sistema operacional está operacional."""
    if not HAS_KEYRING:
        return False
    try:
        kr = keyring.get_keyring()
        priority = getattr(kr, "priority", 1)
        if priority <= 0:
            return False
        kr_name = kr.__class__.__name__.lower()
        if "fail" in kr_name or "null" in kr_name:
            return False
        return True
    except Exception:
        return False


def get_keyring_pat() -> Optional[str]:
    """Recupera o GitHub Personal Access Token (PAT) do cofre do sistema."""
    return _safe_keyring_get("github_token")


def set_keyring_pat(token: str) -> bool:
    """Armazena ou remove o GitHub Personal Access Token (PAT) no cofre do sistema."""
    return _safe_keyring_set("github_token", token)



def get_config_dir() -> Path:
    """Retorna o diretório de configurações do usuário de acordo com a especificação XDG com permissão 0700."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        base_dir = Path(xdg_config)
    else:
        base_dir = Path.home() / ".config"

    config_dir = base_dir / "central-nvr"
    try:
        config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(config_dir, 0o700)
    except (OSError, PermissionError):
        pass
    return config_dir


def get_data_dir() -> Path:
    """Retorna o diretório de dados persistentes (gravações e snapshots) com permissão 0700."""
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        base_dir = Path(xdg_data)
    else:
        base_dir = Path.home() / ".local" / "share"

    data_dir = base_dir / "central-nvr"
    try:
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(data_dir, 0o700)
        rec_dir = data_dir / "recordings"
        rec_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(rec_dir, 0o700)
        snap_dir = data_dir / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(snap_dir, 0o700)
    except (OSError, PermissionError):
        pass
    return data_dir


def _secure_write_json(filepath: Path, data: Any):
    """
    Grava arquivo JSON atomicamente com permissões estritas (0600) e diretório com permissão (0700).
    Grava primeiro em arquivo temporário (.tmp) no mesmo sistema de arquivos e executa
    substituição atômica (os.replace) para garantir integridade caso o processo sofra encerramento abrupto.
    """
    filepath.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(filepath.parent, 0o700)
    except (OSError, PermissionError):
        pass

    tmp_path = filepath.with_suffix(f".tmp.{os.getpid()}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    mode = 0o600
    fd = os.open(tmp_path, flags, mode)
    fd_owned = True
    try:
        with open(fd, "w", encoding="utf-8") as f:
            fd_owned = False
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp_path, 0o600)
        except (OSError, PermissionError):
            pass
        os.replace(tmp_path, filepath)
    except Exception:
        if fd_owned:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def _device_rtsp_port(device: Dict[str, Any]) -> int:
    """Normaliza a porta RTSP de um Dispositivo Cadastrado para comparação."""
    port = device.get("rtsp_port", 554)
    try:
        return int(554 if port is None else port)
    except (TypeError, ValueError):
        return 554


def _channel_key(device: Dict[str, Any]) -> Tuple[int, Any, Any]:
    """Assinatura de canal RTSP para distinguir múltiplos canais no mesmo IP/porta."""
    return (
        _device_rtsp_port(device),
        device.get("rtsp_path", "/live/ch0"),
        device.get("rtsp_url", ""),
    )


def _same_id_channel_compatible(existing: Dict[str, Any], entry: Dict[str, Any]) -> bool:
    """
    Indica se dois registros com o mesmo ID representam o mesmo canal (permitindo atualizar
    rtsp_url/rtsp_path recém-descobertos via ONVIF) ou dois canais colidentes no mesmo IP.
    """
    if existing.get("ip") != entry.get("ip"):
        return True
    if _channel_key(existing) == _channel_key(entry):
        return True
    existing_port = _device_rtsp_port(existing)
    entry_port = _device_rtsp_port(entry)
    if existing_port != entry_port:
        return False
    existing_path = existing.get("rtsp_path", "/live/ch0")
    entry_path = entry.get("rtsp_path", "/live/ch0")
    if existing_path == entry_path:
        return True
    existing_url = existing.get("rtsp_url", "")
    entry_url = entry.get("rtsp_url", "")
    # Permite atualizar câmera cujo rtsp_url antes estava no fallback vazio e foi descoberto via ONVIF
    if bool(existing_url) != bool(entry_url) and (
        existing_path == "/live/ch0" or entry_path == "/live/ch0"
    ):
        return True
    return False


def _device_port(device: Dict[str, Any]) -> int:
    """Normaliza a porta ONVIF/HTTP de um Dispositivo Cadastrado para comparação."""
    port = device.get("port", 80)
    try:
        return int(80 if port is None else port)
    except (TypeError, ValueError):
        return 80


class ConfigManager:
    """Gerencia leitura e escrita de configurações e lista de câmeras."""

    DEFAULT_SETTINGS = {
        "theme": "dark",
        "grid_layout": "auto",  # 1x1, 2x2, 3x3, 4x4
        "hw_accel": "vaapi",   # vaapi, auto, cpu, cuda
        "rtsp_transport": DEFAULT_RTSP_TRANSPORT,  # tcp, udp ou auto
        "buffer_size_ms": 150,
        "enable_motion_detection": True,
        "auto_reconnect": True,
        "reconnect_interval_sec": 5,
        "hidden_camera_timeout_min": 5,
        "default_username": "",
        "snapshot_dir": "",
        "recordings_dir": "",
        "ws_discovery_timeout": 3.0,
        "enable_port_scan": True,
        "show_osd_fps": True,
        "show_osd_bitrate": True,
        "show_osd_timestamp": True,
        "check_updates_on_startup": True,
        "periodic_update_check": True,
        "periodic_update_interval_min": 60,
        "github_repo": "Othayz/central-nvr-wifi",
        "github_token": "",
    }

    def __init__(self):
        self.config_path = get_config_dir() / "settings.json"
        self.devices_path = get_config_dir() / "devices.json"
        data_dir = get_data_dir()
        self.settings: Dict[str, Any] = self.DEFAULT_SETTINGS.copy()
        self.settings["snapshot_dir"] = str(data_dir / "snapshots")
        self.settings["recordings_dir"] = str(data_dir / "recordings")
        self._overrides: Dict[str, Any] = {}
        self.devices: List[Dict[str, Any]] = []
        self._keyring_synced: Dict[str, str] = {}
        self.last_write_error: Optional[str] = None
        self.last_write_warnings: List[str] = []
        self._pat_synced: Optional[str] = None
        self.load()

    def load(self):
        """Carrega configurações e lista de dispositivos do disco com migração segura de credenciais."""
        self._pat_synced = None
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    saved_settings = json.load(f)
                if isinstance(saved_settings, dict):
                    self.settings.update(saved_settings)
                else:
                    logger.warning(f"Formato inválido em {self.config_path}: esperado objeto JSON.")

                # SEC-04: Migração de token legado de settings.json para o Keyring
                raw_pat = str(self.settings.get("github_token", "") or "").strip()
                if raw_pat and is_keyring_available():
                    if set_keyring_pat(raw_pat):
                        logger.info("GitHub PAT migrado com sucesso de settings.json para o Keyring.")
                        self.settings["github_token"] = ""
                        self._pat_synced = raw_pat
                        self.save_settings()
                    else:
                        self._pat_synced = None
            except Exception as e:
                logger.error(f"Erro ao ler {self.config_path}: {e}")

        if self.devices_path.exists():
            try:
                with open(self.devices_path, "r", encoding="utf-8") as f:
                    raw_devices = json.load(f)
                self.devices = (
                    [d for d in raw_devices if isinstance(d, dict)]
                    if isinstance(raw_devices, list)
                    else []
                )
                # SEC-01: Carregar senhas do keyring e migrar senhas legadas em texto puro
                need_resave = False
                keyring_active = is_keyring_available()
                self._keyring_synced = {}
                for d in self.devices:
                    dev_id = d.get("id") or d.get("ip")
                    if dev_id:
                        plaintext_pass = d.get("password")
                        if plaintext_pass and keyring_active:
                            if set_keyring_password(dev_id, plaintext_pass):
                                self._keyring_synced[dev_id] = plaintext_pass
                            need_resave = True
                        kr_pass = get_keyring_password(dev_id)
                        if kr_pass is not None:
                            d["password"] = kr_pass
                            self._keyring_synced[dev_id] = kr_pass

                if need_resave:
                    self.save_devices()
            except Exception as e:
                logger.error(f"Erro ao ler {self.devices_path}: {e}")

    def save_settings(self) -> bool:
        """Salva as configurações no disco com permissões estritas 0600, omitindo PAT se no keyring."""
        self.last_write_error = None
        try:
            settings_to_save = self.settings.copy()
            # SEC-04: Omitir token em texto puro de settings.json apenas se ele estiver efetivamente no keyring
            raw_pat = str(settings_to_save.get("github_token", "") or "").strip()
            if raw_pat:
                pat_stored_in_keyring = raw_pat == self._pat_synced
                if not pat_stored_in_keyring and self._pat_synced is None and is_keyring_available():
                    pat_stored_in_keyring = get_keyring_pat() == raw_pat
                if pat_stored_in_keyring:
                    settings_to_save["github_token"] = ""
            _secure_write_json(self.config_path, settings_to_save)
            return True
        except (OSError, PermissionError) as e:
            self.last_write_error = str(e)
            logger.error(f"Não foi possível salvar configurações: {e}")
            return False

    def _persist_devices(self, devices: List[Dict[str, Any]]) -> bool:
        """
        Persiste a lista de dispositivos de forma transacional: só sincroniza no keyring senhas
        novas/alteradas e reverte alterações de keyring se _secure_write_json() falhar.
        """
        self.last_write_error = None
        keyring_active = is_keyring_available()
        devices_to_save = []
        keyring_rollback: List[Tuple[str, Optional[str]]] = []
        new_synced = dict(self._keyring_synced)

        try:
            for d in devices:
                dev_id = d.get("id") or d.get("ip")
                d_copy = copy.deepcopy(d)
                raw_pwd = d.get("password", "")

                if dev_id and raw_pwd:
                    if new_synced.get(dev_id) == raw_pwd:
                        saved = True
                    else:
                        prev_pwd = new_synced.get(dev_id)
                        saved = set_keyring_password(dev_id, raw_pwd)
                        if saved:
                            keyring_rollback.append((dev_id, prev_pwd))
                            new_synced[dev_id] = raw_pwd
                    if keyring_active and saved:
                        d_copy.pop("password", None)
                elif dev_id and not raw_pwd and keyring_active:
                    if dev_id in new_synced:
                        prev_pwd = new_synced.get(dev_id)
                        if set_keyring_password(dev_id, ""):
                            keyring_rollback.append((dev_id, prev_pwd))
                            new_synced.pop(dev_id, None)
                    d_copy.pop("password", None)

                devices_to_save.append(d_copy)

            _secure_write_json(self.devices_path, devices_to_save)
            self._keyring_synced = new_synced
            return True
        except (OSError, PermissionError) as e:
            for rb_id, prev_pwd in reversed(keyring_rollback):
                if prev_pwd:
                    set_keyring_password(rb_id, prev_pwd)
                else:
                    delete_keyring_password(rb_id)
            self.last_write_error = str(e)
            logger.error(f"Não foi possível salvar dispositivos: {e}")
            return False

    def save_devices(self) -> bool:
        """Salva a lista de câmeras/dispositivos cadastrados sanitizando senhas se keyring ativo (SEC-01)."""
        return self._persist_devices(self.devices)

    def set_override(self, key: str, value: Any) -> None:
        """Define um override apenas em memória para a sessão atual, sem alterar settings.json."""
        self._overrides[key] = value

    def clear_override(self, key: str) -> None:
        """Remove o override em memória de uma chave, se houver."""
        self._overrides.pop(key, None)

    def has_override(self, key: str) -> bool:
        """Retorna True se a chave tiver um override em memória ativo nesta sessão."""
        return key in self._overrides

    def get_override(self, key: str, default: Any = None) -> Any:
        """Retorna o valor do override em memória para a chave, ou default se ausente."""
        return self._overrides.get(key, default)

    def get_persisted(self, key: str, default: Any = None) -> Any:
        """Retorna o valor persistido (ou padrão), ignorando overrides efêmeros da sessão."""
        if key == "github_token":
            file_pat = str(self.settings.get("github_token") or "").strip()
            if file_pat:
                return file_pat
            kr_pat = get_keyring_pat()
            if kr_pat:
                return kr_pat
        return self.settings.get(key, default)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        return self.get_persisted(key, default)

    def _store(self, key: str, value: Any):
        """Grava em memória; o token do GitHub vai para o keyring quando disponível."""
        if key == "github_token":
            token = str(value or "").strip()
            if not token:
                had_token = bool(str(self.get_persisted("github_token") or "").strip()) or bool(self._pat_synced)
                if is_keyring_available():
                    if set_keyring_pat(""):
                        self._pat_synced = None
                    elif had_token:
                        self.last_write_warnings.append(
                            "O cofre de senhas do sistema recusou a remoção do token do GitHub; "
                            "ele pode voltar a ser usado quando o cofre for desbloqueado."
                        )
                        logger.warning(
                            "Cofre de senhas (keyring) recusou a remoção do GitHub PAT; "
                            "o token anterior no cofre pode voltar a valer quando ele for desbloqueado."
                        )
                else:
                    self._pat_synced = None
                self.settings["github_token"] = ""
            elif is_keyring_available():
                if set_keyring_pat(token):
                    self.settings["github_token"] = ""
                    self._pat_synced = token
                else:
                    self.settings["github_token"] = token
                    if self._pat_synced == token:
                        self._pat_synced = None
                    self.last_write_warnings.append(
                        "O cofre de senhas do sistema recusou o token; "
                        "ele foi salvo no arquivo de configuração com permissão restrita."
                    )
                    logger.warning(
                        "Cofre de senhas (keyring) recusou a gravação do GitHub PAT; "
                        "mantendo fallback em settings.json com permissão 0600."
                    )
            else:
                self.settings["github_token"] = token
                self._pat_synced = None
        else:
            self.settings[key] = value

    def update(self, values: Dict[str, Any]) -> bool:
        """Atualiza várias chaves e grava settings.json uma única vez (Issue #34)."""
        previous_settings = self.settings.copy()
        previous_pat_synced = self._pat_synced
        self.last_write_warnings = []
        for key, value in values.items():
            self._store(key, value)
        if not self.save_settings():
            self.settings = previous_settings
            self._pat_synced = previous_pat_synced
            self.last_write_warnings = []
            return False
        return True

    def set(self, key: str, value: Any) -> bool:
        previous_settings = self.settings.copy()
        previous_pat_synced = self._pat_synced
        self.last_write_warnings = []
        self._store(key, value)
        if not self.save_settings():
            self.settings = previous_settings
            self._pat_synced = previous_pat_synced
            self.last_write_warnings = []
            return False
        return True

    def add_or_update_device(self, device: Dict[str, Any]) -> bool:
        """Adiciona um novo Dispositivo Cadastrado ou atualiza o canal correspondente sem sobrescrever outros canais."""
        entry = copy.deepcopy(device)
        if not str(entry.get("id") or "").strip():
            entry["id"] = f"cam_{uuid.uuid4().hex[:12]}"

        entry_ip = entry.get("ip")
        entry_port = _device_port(entry)
        entry_channel = _channel_key(entry)

        match_index: Optional[int] = None
        for i, d in enumerate(self.devices):
            same_endpoint_channel = (
                d.get("ip") == entry_ip
                and _device_port(d) == entry_port
                and _channel_key(d) == entry_channel
            )
            same_id_compatible = (
                d.get("id") == entry["id"]
                and _same_id_channel_compatible(d, entry)
            )
            if same_endpoint_channel or same_id_compatible:
                match_index = i
                break

        candidate_devices = list(self.devices)
        old_keyring_id: Optional[str] = None

        if match_index is not None:
            existing = candidate_devices[match_index]
            old_id = existing.get("id")
            old_keyring_id = old_id or existing.get("ip")
            other_ids = {
                other.get("id")
                for j, other in enumerate(candidate_devices)
                if j != match_index
            }
            if entry["id"] in other_ids:
                if old_id and old_id not in other_ids:
                    entry["id"] = old_id
                else:
                    base_id = entry["id"]
                    while entry["id"] in other_ids:
                        entry["id"] = f"{base_id}_{uuid.uuid4().hex[:6]}"
            candidate_devices[match_index] = entry
        else:
            existing_ids = {d.get("id") for d in candidate_devices}
            if entry["id"] in existing_ids:
                base_id = entry["id"]
                while entry["id"] in existing_ids:
                    entry["id"] = f"{base_id}_{uuid.uuid4().hex[:6]}"
            candidate_devices.append(entry)

        if not self._persist_devices(candidate_devices):
            return False

        self.devices = candidate_devices
        if old_keyring_id and old_keyring_id != entry["id"]:
            delete_keyring_password(old_keyring_id)
            self._keyring_synced.pop(old_keyring_id, None)
        return True

    def rename_device(self, dev_id: str, new_name: str) -> bool:
        """Atualiza o nome de um dispositivo existente por ID ou IP e persiste no disco."""
        candidate_devices = copy.deepcopy(self.devices)
        updated = False
        for dev in candidate_devices:
            if dev.get("id") == dev_id or dev.get("ip") == dev_id:
                dev["name"] = new_name
                updated = True
        if not updated:
            return False
        if not self._persist_devices(candidate_devices):
            return False
        self.devices = candidate_devices
        return True

    def update_device(self, dev_id: str, fields: Dict[str, Any]) -> bool:
        """Atualiza campos de um dispositivo existente (por ID) e persiste no disco."""
        candidate_devices = copy.deepcopy(self.devices)
        updated = False
        for dev in candidate_devices:
            if dev.get("id") == dev_id:
                dev.update(fields)
                updated = True
        if not updated:
            return False
        if not self._persist_devices(candidate_devices):
            return False
        self.devices = candidate_devices
        return True

    def remove_device(self, dev_id: str) -> bool:
        """Remove um Dispositivo Cadastrado pelo seu ID ou IP e limpa sua senha no keyring."""
        removed = [d for d in self.devices if d.get("id") == dev_id or d.get("ip") == dev_id]
        candidate_devices = [d for d in self.devices if d.get("id") != dev_id and d.get("ip") != dev_id]
        if not self._persist_devices(candidate_devices):
            return False

        self.devices = candidate_devices
        keys_to_delete = set()
        for dev in removed:
            for key in (dev.get("id"), dev.get("ip")):
                if key:
                    keys_to_delete.add(key)
        if dev_id:
            keys_to_delete.add(dev_id)
        for key in keys_to_delete:
            delete_keyring_password(key)
            self._keyring_synced.pop(key, None)
        return True
