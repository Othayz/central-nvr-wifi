#!/usr/bin/env python3
"""
Script de diagnóstico de câmera IP / RTSP / ONVIF para Central NVR WiFi.
Testa portas, rotas RTSP e autenticação diretamente.
"""
import socket
import sys
import subprocess
import urllib.request
import urllib.parse
import urllib.error

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
        print(f"\n  🎯 CÂMERA DETECTADA NO CAMINHO: /onvif1")
        print(f"  -> Testando combinações de protocolo para Yoosee...")

        tests = [
            ("UDP (Padrão Yoosee)", ["-rtsp_transport", "udp", "-reorder_queue_size", "0"]),
            ("TCP com Bypass de Mismatch", ["-rtsp_flags", "prefer_tcp"]),
            ("TCP com fflags flexíveis", ["-rtsp_transport", "tcp", "-fflags", "+genpts+discardcorrupt"]),
            ("UDP Multicast / Unicast", ["-rtsp_transport", "udp_multicast"]),
        ]

        for desc, flags in tests:
            print(f"\n  [+] Tentando modo: {desc}...")
            cmd = ["ffprobe", "-v", "warning"] + flags + [
                "-analyzeduration", "5000000",
                "-probesize", "5000000",
                "-show_entries", "stream=codec_name,width,height,r_frame_rate",
                "-of", "default=noprint_wrappers=1",
                url
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                if result.returncode == 0 and ("codec_name" in result.stdout or "width" in result.stdout):
                    print(f"     🎉🎉🎉 SUCESSO ABSOLUTO! O fluxo de vídeo respondeu perfeitamente:")
                    for line in result.stdout.strip().split("\n"):
                        if line:
                            print(f"        👉 {line}")
                    print(f"\n  🌟 CONFIGURAÇÃO VENCEDORA:")
                    print(f"     URL:   rtsp://{user}:***@{ip}:554/onvif1")
                    print(f"     Modo:  {desc}")
                    print(f"     Flags: {' '.join(flags)}")
                    return
                else:
                    err = result.stderr.strip().replace("\n", " ")
                    print(f"     ⚠️  Resposta: {err[:120] if err else 'Sem dados recebidos no tempo limite'}")
            except subprocess.TimeoutExpired:
                print(f"     ⏳ Aguardou 8s por I-frame (Timeout)")

    print("\n" + "=" * 60)

if __name__ == "__main__":
    target_ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.2"
    pwd = sys.argv[2] if len(sys.argv) > 2 else ""
    diagnose(target_ip, pwd)
