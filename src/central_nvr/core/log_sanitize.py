"""
Utilitários de sanitização de credenciais em URLs para logs e saída de diagnóstico (Issue #71).

Mantido sem dependência de Qt ou bibliotecas de vídeo para uso seguro na inicialização do
logging (`app.py`) e em scripts auxiliares (`scripts/diagnose_camera.py`).
"""
import logging
import re
import traceback
from typing import Any

# Encontra URLs com qualquer esquema RFC 3986 (rtsp, http, https, ...) em texto livre,
# parando apenas em espaços em branco, delimitadores angulares (`<>`) ou no início de outra URL.
# Não para em aspas para não truncar senhas contendo `'` ou `"` antes do `@`.
_URL_IN_TEXT_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9+.-]*://(?:(?![A-Za-z][A-Za-z0-9+.-]*://)[^\s<>])+"
)


def _mask_single_url(url: str) -> str:
    """
    Ofusca a senha em uma única URL (`esquema://usuario:senha@host` -> `esquema://usuario:****@host`).

    Usa o último `@` do trecho após o esquema como fim das credenciais: assim a senha é mascarada
    mesmo se contiver espaço, aspas, `/`, `:` ou `@` sem codificação. Na dúvida, mascara a mais
    (nunca a menos).
    """
    scheme, sep, rest = url.partition("://")
    if not sep:
        return url
    at = rest.rfind("@")
    if at == -1:
        return url
    userinfo, hostpart = rest[:at], rest[at + 1:]
    if ":" not in userinfo:
        return url  # Só usuário, sem senha
    user = userinfo.split(":", 1)[0]
    return f"{scheme}://{user}:****@{hostpart}"


def sanitize_rtsp_url(url: str) -> str:
    """
    Ofusca a senha em uma única URL RTSP (ou outro esquema com `://`) para gravação segura em logs.

    Usa o último `@` do trecho após o esquema como fim das credenciais, preservando o algoritmo
    original para URLs isoladas (incluindo senhas cruas com espaços, aspas, `/`, `:` ou `@`).
    """
    if not url:
        return ""
    return _mask_single_url(url)


def sanitize_url_credentials(text: str) -> str:
    """
    Ofusca senhas em qualquer URL (rtsp, http, https, etc.) isolada ou embutida em texto livre.

    Suporta senhas com caracteres especiais codificados (`%2F`, `%40`, etc.) ou literais.
    """
    if not text:
        return ""
    if "://" not in text or "@" not in text:
        return text
    return _URL_IN_TEXT_RE.sub(lambda m: _mask_single_url(m.group(0)), text)


def _sanitize_arg(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return sanitize_url_credentials(value)
    return sanitize_url_credentials(str(value))


def _sanitize_args(args: Any) -> Any:
    if isinstance(args, dict):
        return {k: _sanitize_arg(v) for k, v in args.items()}
    if isinstance(args, tuple):
        return tuple(_sanitize_arg(v) for v in args)
    return _sanitize_arg(args)


class CredentialSanitizingFilter(logging.Filter):
    """
    Filtro global de logging que mascara credenciais em URLs em mensagens, argumentos
    (incluindo strings e exceções) e tracebacks (`exc_info` / `exc_text` / `stack_info`).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            try:
                record.msg = sanitize_url_credentials(record.getMessage())
                record.args = None
            except Exception:
                record.msg = sanitize_url_credentials(
                    record.msg if isinstance(record.msg, str) else str(record.msg)
                )
                record.args = _sanitize_args(record.args)
        else:
            record.msg = sanitize_url_credentials(
                record.msg if isinstance(record.msg, str) else str(record.msg)
            )

        if record.exc_text:
            record.exc_text = sanitize_url_credentials(record.exc_text)
        elif record.exc_info and isinstance(record.exc_info, tuple) and any(record.exc_info):
            exc_text = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
            record.exc_text = sanitize_url_credentials(exc_text)

        if isinstance(record.stack_info, str):
            record.stack_info = sanitize_url_credentials(record.stack_info)

        return True


class CredentialSanitizingFormatter(logging.Formatter):
    """Formatter que garante a sanitização de credenciais em URLs na saída final formatada."""

    def format(self, record: logging.LogRecord) -> str:
        return sanitize_url_credentials(super().format(record))
