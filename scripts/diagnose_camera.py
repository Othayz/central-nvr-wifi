#!/usr/bin/env python3
"""
Script de diagnóstico de câmera IP / RTSP / ONVIF para Central NVR WiFi.
Testa portas, rotas RTSP e autenticação diretamente.

Uso:
    python3 scripts/diagnose_camera.py <ip>

A senha NÃO é aceita como argumento (ficaria visível em `ps`): é lida da variável de ambiente
CENTRAL_NVR_CAMERA_PASSWORD ou digitada sem eco. O teste de RTSP roda no próprio processo com
PyAV, então a URL com credenciais também não aparece na linha de comando de outro programa.
"""
import getpass
import os
from pathlib import Path
import re
import socket
import sys
import urllib.parse

_SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
if os.path.isdir(_SRC_DIR) and _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

try:
    from central_nvr.core.log_sanitize import sanitize_url_credentials
except ImportError:
    # Fallback autocontido quando o script é executado fora do repositório (sem `src/`).
    _FALLBACK_URL_RE = re.compile(
        r"([A-Za-z][A-Za-z0-9+.-]*://[^:\s<>/@]+):[^\s<>]*@"
    )

    def sanitize_url_credentials(text: str) -> str:
        """Ofusca `esquema://usuario:senha@` até o último `@` antes de um espaço em branco."""
        if not text:
            return ""
        return _FALLBACK_URL_RE.sub(r"\1:****@", text)


def format_rtsp_error(exc: Exception, max_len: int = 120) -> str:
    """Formata a mensagem de erro do PyAV sem expor credenciais da URL RTSP."""
    err = sanitize_url_credentials(str(exc)).replace("\n", " ").strip()
    if not err:
        return "Sem dados recebidos no tempo limite"
    return err[:max_len]

def test_port(ip, port, timeout=1.0):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            res = s.connect_ex((ip, port))
            return res == 0
    except Exception:
        return False

def diagnose(ip, password=""):
    print("=" * 60)
    print(f" Diagnóstico de Conexão: {ip}")
    print("=" * 60)

    # 1. Teste de Portas Comuns
    ports = {
        554: "RTSP (Streaming de Vídeo)",
        5000: "ONVIF (Yoosee / Padrão)",
        8899: "ONVIF (Genérico / Xiongmai)",
        80: "HTTP Web / ONVIF",
        8080: "HTTP Alternativo",
        37777: "Dahua / Intelbras",
        8000: "Hikvision SDK",
    }

    open_ports = []
    print("\n[1] Verificando portas de comunicação...")
    for port, desc in ports.items():
        is_open = test_port(ip, port)
        status = "ABERTA" if is_open else "FECHADA"
        print(f"  - Porta {port:5d} ({desc:28s}): [{status}]")
        if is_open:
            open_ports.append(port)

    # 2. Análise dos Resultados de Portas
    print("\n[2] Análise das portas:")
    if 554 not in open_ports:
        print("  ❌ ATENÇÃO: A porta RTSP (554) está FECHADA no IP " + ip)
        print("     Possíveis causas:")
        print("     - No app Yoosee, a opção 'Conexões NVR / RTSP' não foi ativada ou a câmera precisa ser reiniciada (desligada da tomada por 5s).")
        print("     - A câmera está usando outra porta RTSP.")
    else:
        print("  ✅ Porta RTSP 554 está ABERTA e pronta para transmissão!")

    if 5000 in open_ports or 8899 in open_ports or 80 in open_ports:
        print("  ✅ Serviço ONVIF/HTTP detectado.")

    # 3. Teste de Fluxo de Vídeo com FFprobe (se disponível)
    if 554 in open_ports:
        print("\n[3] Testando fluxos RTSP com autenticação...")
        user = "admin"
        auth = ""
        if user:
            auth_user = urllib.parse.quote(user, safe="")
            if password:
                auth_pass = urllib.parse.quote(password, safe="")
                auth = f"{auth_user}:{auth_pass}@"
            else:
                auth = f"{auth_user}@"
        url = f"rtsp://{auth}{ip}:554/onvif1"
        print("\n  🎯 CÂMERA DETECTADA NO CAMINHO: /onvif1")
        print("  -> Testando combinações de protocolo para Yoosee...")

        tests = [
            ("UDP (Padrão Yoosee)", {"rtsp_transport": "udp", "reorder_queue_size": "0"}),
            ("TCP com Bypass de Mismatch", {"rtsp_flags": "prefer_tcp"}),
            ("TCP com fflags flexíveis", {"rtsp_transport": "tcp", "fflags": "+genpts+discardcorrupt"}),
            ("UDP Multicast / Unicast", {"rtsp_transport": "udp_multicast"}),
        ]

        try:
            import av
        except ImportError:
            print("  ⚠️  PyAV não instalado (pip install av): teste de fluxo RTSP ignorado.")
            return

        for desc, options in tests:
            print(f"\n  [+] Tentando modo: {desc}...")
            opts = dict(options, analyzeduration="5000000", probesize="5000000")
            try:
                with av.open(url, mode="r", options=opts, timeout=8.0) as container:
                    video = next((st for st in container.streams if st.type == "video"), None)
                    if video is None:
                        print("     ⚠️  Conectou, mas sem trilha de vídeo.")
                        continue
                    ctx = video.codec_context
                    print("     🎉🎉🎉 SUCESSO ABSOLUTO! O fluxo de vídeo respondeu perfeitamente:")
                    print(f"        👉 codec_name={ctx.name}")
                    print(f"        👉 width={ctx.width}")
                    print(f"        👉 height={ctx.height}")
                    print(f"        👉 r_frame_rate={video.average_rate}")
                    print("\n  🌟 CONFIGURAÇÃO VENCEDORA:")
                    print(f"     URL:   rtsp://{user}:***@{ip}:554/onvif1")
                    print(f"     Modo:  {desc}")
                    print(f"     Opções: {options}")
                    return
            except av.HTTPUnauthorizedError:
                print("     ❌ Credenciais recusadas pela câmera (RTSP 401).")
                return
            except Exception as e:
                print(f"     ⚠️  Resposta: {format_rtsp_error(e)}")

    print("\n" + "=" * 60)

def read_password() -> str:
    """Senha da câmera sem passar pela linha de comando."""
    env_pwd = os.environ.get("CENTRAL_NVR_CAMERA_PASSWORD")
    if env_pwd is not None:
        return env_pwd
    if sys.stdin.isatty():
        return getpass.getpass("Senha da câmera (Enter para nenhuma): ")
    return ""


if __name__ == "__main__":
    if len(sys.argv) > 2:
        print("A senha não é mais aceita como argumento (ficaria visível em `ps`).")
        print("Use a variável CENTRAL_NVR_CAMERA_PASSWORD ou digite quando solicitado.")
        sys.exit(2)
    target_ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.2"
    diagnose(target_ip, read_password())
