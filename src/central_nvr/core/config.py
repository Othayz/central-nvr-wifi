"""
Módulo de Configuração e Persistência do Sistema (Padrão XDG).
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import keyring
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False

KEYRING_SERVICE_NAME = "central-nvr"

logger = logging.getLogger(__name__)


def get_keyring_password(camera_id: str) -> Optional[str]:
    """Recupera a senha da câmera do cofre do sistema (Keyring), se disponível."""
    if not HAS_KEYRING or not camera_id:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE_NAME, f"camera_{camera_id}")
    except Exception as e:
        logger.debug(f"Falha ao obter senha do keyring para camera {camera_id}: {e}")
        return None


def set_keyring_password(camera_id: str, password: str) -> bool:
    """Armazena a senha da câmera no cofre do sistema (Keyring), se disponível."""
    if not HAS_KEYRING or not camera_id:
        return False
    try:
        if password:
            keyring.set_password(KEYRING_SERVICE_NAME, f"camera_{camera_id}", password)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE_NAME, f"camera_{camera_id}")
            except Exception:
                pass
        return True
    except Exception as e:
        logger.debug(f"Falha ao salvar senha no keyring para camera {camera_id}: {e}")
        return False


def delete_keyring_password(camera_id: str) -> bool:
    """Remove a senha da câmera do cofre do sistema."""
    if not HAS_KEYRING or not camera_id:
        return False
    try:
        keyring.delete_password(KEYRING_SERVICE_NAME, f"camera_{camera_id}")
        return True
    except Exception:
        return False

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
    if not HAS_KEYRING:
        return None
    try:
        return keyring.get_password(KEYRING_SERVICE_NAME, "github_token")
    except Exception as e:
        logger.debug(f"Falha ao obter github_token do keyring: {e}")
        return None


def set_keyring_pat(token: str) -> bool:
    """Armazena ou remove o GitHub Personal Access Token (PAT) no cofre do sistema."""
    if not HAS_KEYRING:
        return False
    try:
        if token:
            keyring.set_password(KEYRING_SERVICE_NAME, "github_token", token)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE_NAME, "github_token")
            except Exception:
                pass
        return True
    except Exception as e:
        logger.debug(f"Falha ao salvar github_token no keyring: {e}")
        return False



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
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp_path, 0o600)
        except (OSError, PermissionError):
            pass
        os.replace(tmp_path, filepath)
    except Exception:
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


class ConfigManager:
    """Gerencia leitura e escrita de configurações e lista de câmeras."""

    DEFAULT_SETTINGS = {
        "theme": "dark",
        "grid_layout": "auto",  # 1x1, 2x2, 3x3, 4x4
        "hw_accel": "vaapi",   # vaapi, auto, cpu, cuda
        "rtsp_transport": "tcp", # tcp ou udp
        "buffer_size_ms": 150,
        "auto_reconnect": True,
        "reconnect_interval_sec": 5,
        "default_username": "admin",
        "snapshot_dir": str(get_data_dir() / "snapshots"),
        "recordings_dir": str(get_data_dir() / "recordings"),
        "ws_discovery_timeout": 3.0,
        "enable_port_scan": True,
        "show_osd_fps": True,
        "show_osd_bitrate": True,
        "show_osd_timestamp": True,
        "check_updates_on_startup": True,
        "periodic_update_check": True,
        "periodic_update_interval_min": 10,
        "github_repo": "Othayz/central-nvr-wifi",
        "github_token": "",
    }

    def __init__(self):
        self.config_path = get_config_dir() / "settings.json"
        self.devices_path = get_config_dir() / "devices.json"
        self.settings: Dict[str, Any] = self.DEFAULT_SETTINGS.copy()
        self.devices: List[Dict[str, Any]] = []
        self.load()

    def load(self):
        """Carrega configurações e lista de dispositivos do disco com migração segura de credenciais."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    saved_settings = json.load(f)
                    self.settings.update(saved_settings)

                # SEC-04: Migração de token legado de settings.json para o Keyring
                raw_pat = self.settings.get("github_token", "").strip()
                if raw_pat and is_keyring_available():
                    if set_keyring_pat(raw_pat):
                        logger.info("GitHub PAT migrado com sucesso de settings.json para o Keyring.")
                        self.settings["github_token"] = ""
                        self.save_settings()
            except Exception as e:
                logger.error(f"Erro ao ler {self.config_path}: {e}")

        if self.devices_path.exists():
            try:
                with open(self.devices_path, "r", encoding="utf-8") as f:
                    raw_devices = json.load(f)
                    # Remover qualquer dispositivo de demonstração legado
                    self.devices = [
                        d for d in raw_devices 
                        if not d.get("id", "").startswith("cam-") 
                        and "Sala de Estar" not in d.get("name", "")
                        and "Cozinha" not in d.get("name", "")
                        and "Garagem" not in d.get("name", "")
                        and "Quintal" not in d.get("name", "")
                    ]
                    # SEC-01: Carregar senhas do keyring e migrar senhas legadas em texto puro
                    need_resave = False
                    keyring_active = is_keyring_available()
                    for d in self.devices:
                        dev_id = d.get("id") or d.get("ip")
                        if dev_id:
                            plaintext_pass = d.get("password")
                            if plaintext_pass and keyring_active:
                                set_keyring_password(dev_id, plaintext_pass)
                                need_resave = True
                            kr_pass = get_keyring_password(dev_id)
                            if kr_pass is not None:
                                d["password"] = kr_pass

                    if len(self.devices) != len(raw_devices) or need_resave:
                        self.save_devices()
            except Exception as e:
                logger.error(f"Erro ao ler {self.devices_path}: {e}")

    def save_settings(self):
        """Salva as configurações no disco com permissões estritas 0600, omitindo PAT se no keyring."""
        try:
            settings_to_save = self.settings.copy()
            # SEC-04: Omitir token em texto puro de settings.json caso o keyring esteja ativo
            if is_keyring_available():
                settings_to_save["github_token"] = ""
            _secure_write_json(self.config_path, settings_to_save)
        except (OSError, PermissionError) as e:
            logger.debug(f"Não foi possível salvar configurações: {e}")

    def save_devices(self):
        """Salva a lista de câmeras/dispositivos cadastrados sanitizando senhas se keyring ativo (SEC-01)."""
        try:
            import copy
            devices_to_save = []
            keyring_active = is_keyring_available()

            for d in self.devices:
                dev_id = d.get("id") or d.get("ip")
                d_copy = copy.deepcopy(d)
                raw_pwd = d.get("password", "")

                if dev_id and raw_pwd:
                    saved = set_keyring_password(dev_id, raw_pwd)
                    if keyring_active and saved:
                        d_copy.pop("password", None)
                elif dev_id and not raw_pwd and keyring_active:
                    d_copy.pop("password", None)

                devices_to_save.append(d_copy)

            _secure_write_json(self.devices_path, devices_to_save)
        except (OSError, PermissionError) as e:
            logger.debug(f"Não foi possível salvar dispositivos: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        if key == "github_token":
            kr_pat = get_keyring_pat()
            if kr_pat:
                return kr_pat
        return self.settings.get(key, default)

    def set(self, key: str, value: Any):
        if key == "github_token":
            val_str = str(value or "").strip()
            if is_keyring_available():
                set_keyring_pat(val_str)
                self.settings["github_token"] = ""
                self.save_settings()
                return
        self.settings[key] = value
        self.save_settings()

    def add_or_update_device(self, device: Dict[str, Any]):
        """Adiciona um novo dispositivo ou atualiza um existente por ID."""
        dev_id = device.get("id") or device.get("ip")
        if dev_id and "password" in device:
            set_keyring_password(dev_id, device.get("password", ""))

        for i, d in enumerate(self.devices):
            if d.get("id") == dev_id or (d.get("ip") == device.get("ip") and d.get("port") == device.get("port")):
                self.devices[i] = device
                self.save_devices()
                return

        self.devices.append(device)
        self.save_devices()

    def rename_device(self, dev_id: str, new_name: str) -> bool:
        """Atualiza o nome de um dispositivo existente por ID ou IP e persiste no disco."""
        updated = False
        for dev in self.devices:
            if dev.get("id") == dev_id or dev.get("ip") == dev_id:
                dev["name"] = new_name
                updated = True
        if updated:
            self.save_devices()
        return updated

    def remove_device(self, dev_id: str):
        """Remove um dispositivo pelo seu ID ou IP."""
        delete_keyring_password(dev_id)
        self.devices = [d for d in self.devices if d.get("id") != dev_id and d.get("ip") != dev_id]
        self.save_devices()
