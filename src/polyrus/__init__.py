"""Polyrus — 검증된 비-패스(No-Pass) 추론 하니스."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("polyrus-agent")  # 배포 메타데이터에서 파생(드리프트 방지)
except PackageNotFoundError:  # 설치 안 된 소스 트리 폴백
    __version__ = "0.1.0"
