"""
Utilitários para geração de nomes únicos e gravação segura de snapshots (Issue #67).
"""
import datetime
import os
import re
from pathlib import Path
from typing import Any, BinaryIO, Optional, Tuple, Union

_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_COLLISION_ATTEMPTS = 10_000


def sanitize_snapshot_camera_id(camera_id: Any) -> str:
    """Sanitiza o identificador da câmera contra path traversal mantendo o formato seguro."""
    return _SAFE_ID_RE.sub("_", str(camera_id))


def next_snapshot_path(
    snap_dir: Union[str, Path],
    camera_id: Any,
    now: Optional[datetime.datetime] = None,
) -> Path:
    """
    Gera um caminho único para snapshot no formato `snap_<id>_<AAAAMMDD_HHMMSS_mmm>.jpg`,
    adicionando sufixo incremental (`_1`, `_2`, ...) caso o arquivo já exista.
    """
    base_dir = Path(snap_dir)
    moment = now or datetime.datetime.now()
    ts = f"{moment.strftime('%Y%m%d_%H%M%S')}_{moment.microsecond // 1000:03d}"
    safe_id = sanitize_snapshot_camera_id(camera_id)
    stem = f"snap_{safe_id}_{ts}"
    candidate = base_dir / f"{stem}.jpg"
    if not candidate.exists():
        return candidate
    for idx in range(1, _MAX_COLLISION_ATTEMPTS + 1):
        candidate = base_dir / f"{stem}_{idx}.jpg"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Não foi possível gerar nome único para snapshot em '{base_dir}'.")


def open_unique_snapshot(target_path: Union[str, Path]) -> Tuple[Path, BinaryIO]:
    """
    Cria exclusivamente (`O_CREAT | O_EXCL`) o arquivo de snapshot em `target_path`,
    tentando sufixos incrementais (`_1`, `_2`, ...) em caso de colisão para nunca
    sobrescrever um arquivo pré-existente ou concorrente.
    """
    path = Path(target_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stem = path.stem
    suffix = path.suffix or ".jpg"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for idx in range(0, _MAX_COLLISION_ATTEMPTS + 1):
        candidate = path if idx == 0 else path.with_name(f"{stem}_{idx}{suffix}")
        try:
            fd = os.open(candidate, flags, 0o644)
        except FileExistsError:
            continue
        try:
            return candidate, os.fdopen(fd, "wb")
        except Exception:
            os.close(fd)
            try:
                candidate.unlink()
            except OSError:
                pass
            raise
    raise FileExistsError(f"Não foi possível criar arquivo exclusivo para '{path}'.")
