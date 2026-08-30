#!/usr/bin/env python3
"""
Gerador nativo puro Python de pacotes RPM (formato RPM v3/v4).
Permite construir arquivos .rpm sem depender de rpmbuild.
"""
import gzip
import hashlib
import io
import os
import struct
import sys
import time

# RPM Constants
RPM_MAGIC = b"\xed\xab\xee\xdb"
RPM_HEADER_MAGIC = b"\x8e\xad\xe8\x01"

# RPM Tag Types
TYPE_NULL = 0
TYPE_CHAR = 1
TYPE_INT8 = 2
TYPE_INT16 = 3
TYPE_INT32 = 4
TYPE_INT64 = 5
TYPE_STRING = 6
TYPE_BIN = 7
TYPE_STRING_ARRAY = 8
TYPE_I18NSTRING = 9

# RPM Tags
TAG_NAME = 1000
TAG_VERSION = 1001
TAG_RELEASE = 1002
TAG_SUMMARY = 1004
TAG_DESCRIPTION = 1005
TAG_BUILDTIME = 1006
TAG_BUILDHOST = 1007
TAG_SIZE = 1009
TAG_DISTRIBUTION = 1010
TAG_VENDOR = 1011
TAG_LICENSE = 1014
TAG_GROUP = 1016
TAG_URL = 1020
TAG_OS = 1021
TAG_ARCH = 1022
TAG_PAYLOADFORMAT = 1124
TAG_PAYLOADCOMPRESSOR = 1125
TAG_PAYLOADFLAGS = 1126
TAG_RHNPLATFORM = 1131
TAG_PLATFORM = 1132

TAG_FILENAMES = 1027
TAG_FILESIZES = 1028
TAG_FILEMODES = 1030
TAG_FILEMTIMES = 1034
TAG_FILEMD5S = 1035
TAG_FILELINKTOS = 1036
TAG_FILEFLAGS = 1037
TAG_FILEUSERNAME = 1039
TAG_FILEGROUPNAME = 1040
TAG_FILEDEVICES = 1095
TAG_FILEINODES = 1096
TAG_DIRINDEXES = 1116
TAG_BASENAMES = 1117
TAG_DIRNAMES = 1118
TAG_FILEDIGESTS = 1035
TAG_FILEDIGESTALGO = 5068

# Sig Tags
TAG_SIG_SIZE = 1000
TAG_SIG_MD5 = 1004
TAG_SIG_PAYLOADSIZE = 1007
TAG_SIG_SHA256 = 269


class RpmHeaderBuilder:
    def __init__(self):
        self.entries = []  # (tag, type, count, data_bytes)

    def add_string(self, tag, value: str):
        val_bytes = value.encode("utf-8") + b"\x00"
        self.entries.append((tag, TYPE_STRING, 1, val_bytes))

    def add_i18nstring(self, tag, value: str):
        val_bytes = value.encode("utf-8") + b"\x00"
        self.entries.append((tag, TYPE_I18NSTRING, 1, val_bytes))

    def add_string_array(self, tag, values: list):
        data = bytearray()
        for v in values:
            data.extend(v.encode("utf-8") + b"\x00")
        self.entries.append((tag, TYPE_STRING_ARRAY, len(values), bytes(data)))

    def add_int32(self, tag, values: list):
        data = bytearray()
        for v in values:
            data.extend(struct.pack(">I", v & 0xFFFFFFFF))
        self.entries.append((tag, TYPE_INT32, len(values), bytes(data)))

    def add_int16(self, tag, values: list):
        data = bytearray()
        for v in values:
            data.extend(struct.pack(">H", v & 0xFFFF))
        self.entries.append((tag, TYPE_INT16, len(values), bytes(data)))

    def add_bin(self, tag, data: bytes):
        self.entries.append((tag, TYPE_BIN, len(data), data))

    def build(self) -> bytes:
        # Sort entries by tag number (RPM requires ascending tag order)
        sorted_entries = sorted(self.entries, key=lambda x: x[0])
        
        index_table = bytearray()
        data_table = bytearray()
        
        for tag, typ, count, d_bytes in sorted_entries:
            offset = len(data_table)
            index_table.extend(struct.pack(">iiii", tag, typ, offset, count))
            data_table.extend(d_bytes)
            
            # Align data table based on type
            if typ in (TYPE_INT16,):
                while len(data_table) % 2 != 0:
                    data_table.append(0)
            elif typ in (TYPE_INT32,):
                while len(data_table) % 4 != 0:
                    data_table.append(0)
            elif typ in (TYPE_INT64,):
                while len(data_table) % 8 != 0:
                    data_table.append(0)

        nindex = len(sorted_entries)
        dsize = len(data_table)
        
        header = bytearray(RPM_HEADER_MAGIC)
        header.extend(struct.pack(">iii", 0, nindex, dsize))
        header.extend(index_table)
        header.extend(data_table)
        return bytes(header)


def make_cpio_entry(filename, data, mode=0o100644, mtime=None):
    if mtime is None:
        mtime = int(time.time())
    
    filesize = len(data)
    name_clean = "." + filename if filename.startswith("/") else "./" + filename
    name_bytes = name_clean.encode("utf-8") + b"\x00"
    namesize = len(name_bytes)
    
    header = (
        f"070701"
        f"{0:08x}"
        f"{mode:08x}"
        f"{0:08x}"
        f"{0:08x}"
        f"{1:08x}"
        f"{mtime:08x}"
        f"{filesize:08x}"
        f"{0:08x}"
        f"{0:08x}"
        f"{0:08x}"
        f"{0:08x}"
        f"{namesize:08x}"
        f"{0:08x}"
    ).encode("ascii")
    
    buf = bytearray(header)
    buf.extend(name_bytes)
    while len(buf) % 4 != 0:
        buf.append(0)
    
    buf.extend(data)
    while len(buf) % 4 != 0:
        buf.append(0)
        
    return bytes(buf)


def build_rpm_package(root_dir: str, output_rpm_path: str, version: str = "1.0.0", release: str = "1"):
    """Monta o arquivo RPM a partir da árvore de arquivos."""
    files_to_pack = []
    
    # 1. Coletar todos os arquivos a instalar
    # Módulos Python
    src_dir = os.path.join(root_dir, "src", "central_nvr")
    for root, dirs, files in os.walk(src_dir):
        for f in sorted(files):
            full_p = os.path.join(root, f)
            rel_p = os.path.relpath(full_p, root_dir)
            target_path = "/usr/lib/central-nvr/" + os.path.relpath(full_p, src_dir)
            with open(full_p, "rb") as fp:
                data = fp.read()
            files_to_pack.append((target_path, data, 0o100644))
            
    # Executável /usr/bin/central-nvr
    launcher_script = (
        "#!/bin/sh\n"
        "export PYTHONPATH=\"/usr/lib/central-nvr:${PYTHONPATH}\"\n"
        "exec /usr/bin/python3 -m central_nvr.app \"$@\"\n"
    ).encode("utf-8")
    files_to_pack.append(("/usr/bin/central-nvr", launcher_script, 0o100755))
    
    # Desktop Entry
    desktop_file = os.path.join(root_dir, "packaging", "central-nvr.desktop")
    if os.path.exists(desktop_file):
        with open(desktop_file, "rb") as fp:
            files_to_pack.append(("/usr/share/applications/central-nvr.desktop", fp.read(), 0o100644))
            
    # Ícone SVG
    icon_file = os.path.join(root_dir, "packaging", "icons", "central-nvr.svg")
    if os.path.exists(icon_file):
        with open(icon_file, "rb") as fp:
            files_to_pack.append(("/usr/share/icons/hicolor/scalable/apps/central-nvr.svg", fp.read(), 0o100644))
            
    # Ordenar arquivos por caminho
    files_to_pack.sort(key=lambda x: x[0])
    
    # 2. Gerar Payload CPIO comprimido com gzip
    cpio_buf = io.BytesIO()
    dirnames_set = set()
    basenames = []
    dirindexes = []
    filesizes = []
    filemodes = []
    filemtimes = []
    filemd5s = []
    
    now = int(time.time())
    total_size = 0
    
    for path, data, mode in files_to_pack:
        dname = os.path.dirname(path) + "/"
        bname = os.path.basename(path)
        dirnames_set.add(dname)
        
        filesizes.append(len(data))
        filemodes.append(mode)
        filemtimes.append(now)
        filemd5s.append(hashlib.md5(data).hexdigest())
        total_size += len(data)
        
        cpio_buf.write(make_cpio_entry(path, data, mode=mode, mtime=now))
        
    cpio_buf.write(make_cpio_entry("TRAILER!!!", b"", mode=0, mtime=now))
    
    # Comprimir CPIO com GZIP
    payload_gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=payload_gz_buf, mode="wb", mtime=now) as gz:
        gz.write(cpio_buf.getvalue())
    payload_data = payload_gz_buf.getvalue()
    
    # Mapear diretórios
    dirnames = sorted(list(dirnames_set))
    for path, data, mode in files_to_pack:
        dname = os.path.dirname(path) + "/"
        bname = os.path.basename(path)
        dirindexes.append(dirnames.index(dname))
        basenames.append(bname)
        
    # 3. Construir Main Header
    hb = RpmHeaderBuilder()
    hb.add_string(TAG_NAME, "central-nvr")
    hb.add_string(TAG_VERSION, version)
    hb.add_string(TAG_RELEASE, release)
    hb.add_i18nstring(TAG_SUMMARY, "Central NVR WiFi - Monitoramento e Descoberta ONVIF para Linux")
    hb.add_i18nstring(TAG_DESCRIPTION, "Central NVR WiFi é um aplicativo desktop Linux para gerenciamento de Câmeras IP e NVRs.")
    hb.add_string(TAG_LICENSE, "MIT")
    hb.add_string(TAG_GROUP, "Applications/Multimedia")
    hb.add_string(TAG_URL, "https://github.com/Othayz/central-nvr-wifi")
    hb.add_string(TAG_OS, "linux")
    hb.add_string(TAG_ARCH, "noarch")
    hb.add_string(TAG_PAYLOADFORMAT, "cpio")
    hb.add_string(TAG_PAYLOADCOMPRESSOR, "gzip")
    hb.add_string(TAG_PAYLOADFLAGS, "9")
    hb.add_int32(TAG_BUILDTIME, [now])
    hb.add_string(TAG_BUILDHOST, "localhost")
    hb.add_int32(TAG_SIZE, [total_size])
    
    # Metadados de arquivos
    hb.add_string_array(TAG_DIRNAMES, dirnames)
    hb.add_string_array(TAG_BASENAMES, basenames)
    hb.add_int32(TAG_DIRINDEXES, dirindexes)
    hb.add_int32(TAG_FILESIZES, filesizes)
    hb.add_int16(TAG_FILEMODES, filemodes)
    hb.add_int32(TAG_FILEMTIMES, filemtimes)
    hb.add_string_array(TAG_FILEDIGESTS, filemd5s)
    hb.add_string_array(TAG_FILELINKTOS, ["" for _ in files_to_pack])
    hb.add_int32(TAG_FILEFLAGS, [0 for _ in files_to_pack])
    hb.add_string_array(TAG_FILEUSERNAME, ["root" for _ in files_to_pack])
    hb.add_string_array(TAG_FILEGROUPNAME, ["root" for _ in files_to_pack])
    hb.add_int16(TAG_FILEDEVICES, [1 for _ in files_to_pack])
    hb.add_int32(TAG_FILEINODES, list(range(1, len(files_to_pack) + 1)))
    
    main_header_data = hb.build()
    
    # 4. Construir Signature Header
    sb = RpmHeaderBuilder()
    combined_body = main_header_data + payload_data
    sb.add_int32(TAG_SIG_SIZE, [len(combined_body)])
    sb.add_bin(TAG_SIG_MD5, hashlib.md5(combined_body).digest())
    sb.add_string(TAG_SIG_SHA256, hashlib.sha256(main_header_data).hexdigest())
    sb.add_int32(TAG_SIG_PAYLOADSIZE, [len(payload_data)])
    sig_header_data = sb.build()
    
    # Align sig header to 8 bytes boundary
    pad_len = (8 - (len(sig_header_data) % 8)) % 8
    sig_header_data += b"\x00" * pad_len
    
    # 5. Construir Lead (96 bytes)
    pkg_name = f"central-nvr-{version}-{release}".encode("utf-8")[:65]
    name_field = pkg_name + b"\x00" * (66 - len(pkg_name))
    lead = bytearray(RPM_MAGIC)
    lead.extend(struct.pack(">BBhh", 3, 0, 0, 1))  # major 3, minor 0, binary pkg, arch 1
    lead.extend(name_field)
    lead.extend(struct.pack(">hh", 1, 5))          # os 1 (linux), sigtype 5
    lead.extend(b"\x00" * 16)                     # reserved
    
    # 6. Gravar arquivo final RPM
    os.makedirs(os.path.dirname(os.path.abspath(output_rpm_path)), exist_ok=True)
    with open(output_rpm_path, "wb") as f:
        f.write(lead)
        f.write(sig_header_data)
        f.write(main_header_data)
        f.write(payload_data)
        
    print(f"[Python RpmPacker] Pacote .rpm gerado com sucesso: {output_rpm_path} ({os.path.getsize(output_rpm_path)} bytes)")


if __name__ == "__main__":
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    out = os.path.join(root, "dist", "central-nvr-1.0.0-1.noarch.rpm")
    build_rpm_package(root, out)
