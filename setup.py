#!/usr/bin/env python3
from setuptools import setup, find_packages

setup(
    name="central-nvr",
    version="1.0.0",
    description="Central NVR WiFi - Gerenciamento, Descoberta ONVIF e Streaming RTSP para Linux",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    entry_points={
        "console_scripts": [
            "central-nvr = central_nvr.app:main",
        ],
    },
    include_package_data=True,
    python_requires=">=3.10",
)
