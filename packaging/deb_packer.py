#!/usr/bin/env python3
"""
Empacotador Debian (.deb) nativo em Python puro.
Gera pacotes .deb padrão (ar archive com debian-binary, control.tar.gz e data.tar.gz)
sem depender da ferramenta dpkg-deb externa.
"""
import io
import os
import struct
import sys
import tarfile
import time


def create_ar_header(name: str, size: int, mtime: int = 0, mode: int = 0o100644) -> bytes:
    """Gera um cabeçalho de arquivo no formato Unix ar archive."""
    header = io.BytesIO()
    # Name: 16 chars, space padded
    header.write(f"{name:<16}".encode("ascii"))
    # Timestamp: 12 chars
    header.write(f"{mtime:<12}".encode("ascii"))
    # Owner ID: 6 chars
    header.write(f"{0:<6}".encode("ascii"))
    # Group ID: 6 chars
    header.write(f"{0:<6}".encode("ascii"))
    # File mode (octal): 8 chars
    mode_str = oct(mode)[2:]
    header.write(f"{mode_str:<8}".encode("ascii"))
    # File size: 10 chars
    header.write(f"{size:<10}".encode("ascii"))
    # Magic trailer
    header.write(b"`\n")
    return header.getvalue()


def build_deb_package(source_dir: str, output_deb_path: str):
    """Monta o arquivo .deb a partir da árvore de diretórios fonte."""
    debian_dir = os.path.join(source_dir, "DEBIAN")
    if not os.path.isdir(debian_dir):
        raise ValueError(f"Diretório DEBIAN não encontrado em: {source_dir}")

    # 1. Gerar debian-binary
    debian_binary_content = b"2.0\n"

    # 2. Gerar control.tar.gz
    control_buf = io.BytesIO()
    with tarfile.open(fileobj=control_buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(debian_dir):
            for f in sorted(files):
                full_path = os.path.join(root, f)
                arcname = "./" + os.path.relpath(full_path, debian_dir)
                tar.add(full_path, arcname=arcname)
    control_data = control_buf.getvalue()

    # 3. Gerar data.tar.gz
    data_buf = io.BytesIO()
    with tarfile.open(fileobj=data_buf, mode="w:gz") as tar:
        for item in sorted(os.listdir(source_dir)):
            if item == "DEBIAN":
                continue
            full_path = os.path.join(source_dir, item)
            tar.add(full_path, arcname=f"./{item}")
    data_data = data_buf.getvalue()

    # 4. Escrever o container AR final
    os.makedirs(os.path.dirname(os.path.abspath(output_deb_path)), exist_ok=True)
    with open(output_deb_path, "wb") as deb:
        # Assinatura global do formato AR
        deb.write(b"!<arch>\n")

        # Entrada 1: debian-binary
        deb.write(create_ar_header("debian-binary", len(debian_binary_content)))
        deb.write(debian_binary_content)
        if len(debian_binary_content) % 2 != 0:
            deb.write(b"\n")

        # Entrada 2: control.tar.gz
        deb.write(create_ar_header("control.tar.gz", len(control_data)))
        deb.write(control_data)
        if len(control_data) % 2 != 0:
            deb.write(b"\n")

        # Entrada 3: data.tar.gz
        deb.write(create_ar_header("data.tar.gz", len(data_data)))
        deb.write(data_data)
        if len(data_data) % 2 != 0:
            deb.write(b"\n")

    print(f"[Python DebPacker] Pacote .deb gerado com sucesso: {output_deb_path} ({os.path.getsize(output_deb_path)} bytes)")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: deb_packer.py <source_dir> <output.deb>")
        sys.exit(1)
    build_deb_package(sys.argv[1], sys.argv[2])
