Name:           central-nvr
Version:        1.0.0
Release:        1%{?dist}
Summary:        Central NVR WiFi - Monitoramento, Descoberta ONVIF e Streaming RTSP para Linux

License:        MIT
URL:            https://centralnvr.local
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.10
Requires:       python3-pyside6
Requires:       python3-opencv
Requires:       ffmpeg
Requires:       libva-utils
Requires:       mesa-va-drivers
Requires:       python3-requests
Requires:       python3-pillow
Requires:       python3-numpy

%description
Central NVR WiFi é um aplicativo desktop moderno para Linux para gerenciamento de
Câmeras IP e NVRs. Inclui varredura de rede local via WS-Discovery ONVIF multicast
(239.255.255.250:3702), player RTSP de baixa latência com decodificação por hardware
(VA-API), controles PTZ direcionais via SOAP e visualização em grade multi-câmeras.

%prep
%autosetup

%build
# Nenhuma compilação binária necessária para pacote puro Python

%install
rm -rf $RPM_BUILD_ROOT
mkdir -p $RPM_BUILD_ROOT/usr/bin
mkdir -p $RPM_BUILD_ROOT/usr/lib/%{name}
mkdir -p $RPM_BUILD_ROOT/usr/share/applications
mkdir -p $RPM_BUILD_ROOT/usr/share/icons/hicolor/scalable/apps

# Instalação dos módulos Python
cp -r src/central_nvr $RPM_BUILD_ROOT/usr/lib/%{name}/

# Script executável launcher
cat << 'EOF' > $RPM_BUILD_ROOT/usr/bin/%{name}
#!/bin/sh
export PYTHONPATH="/usr/lib/central-nvr:${PYTHONPATH}"
exec /usr/bin/python3 -m central_nvr.app "$@"
EOF
chmod 755 $RPM_BUILD_ROOT/usr/bin/%{name}

# Desktop Entry e Ícone
install -m 644 packaging/central-nvr.desktop $RPM_BUILD_ROOT/usr/share/applications/central-nvr.desktop
install -m 644 packaging/icons/central-nvr.svg $RPM_BUILD_ROOT/usr/share/icons/hicolor/scalable/apps/central-nvr.svg

%post
/bin/touch --no-create %{_datadir}/icons/hicolor &>/dev/null || :
/usr/bin/update-desktop-database &>/dev/null || :

%postun
if [ $1 -eq 0 ] ; then
    /bin/touch --no-create %{_datadir}/icons/hicolor &>/dev/null
    /usr/bin/gtk-update-icon-cache %{_datadir}/icons/hicolor &>/dev/null || :
fi
/usr/bin/update-desktop-database &>/dev/null || :

%files
/usr/bin/%{name}
/usr/lib/%{name}
/usr/share/applications/central-nvr.desktop
/usr/share/icons/hicolor/scalable/apps/central-nvr.svg

%changelog
* Sun Aug 16 2026 Othay <developer@centralnvr.local> - 1.0.0-1
- Release inicial da Central NVR WiFi com suporte a ONVIF, VA-API e PTZ.
