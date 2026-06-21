"""로컬 우선 자격증명 해석기 (기획안 6.3).

원칙:
  - 비밀은 사용자 머신 밖으로 절대 나가지 않는다 (서버·검증 플레인·텔레메트리 금지).
  - 'env식 글로벌 계정' 파일은 *선언*만 하고, 실제 값은 OS 키체인에서 해석한다.
  - 비밀은 모델 프롬프트에 들어가지 않는다 — 도구 실행 경계에서만 주입.
  - 모든 원장·로그는 redact()로 마스킹된 값만 본다.
구현은 Phase 1. 지금은 계약(타입/인터페이스)만 고정한다.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Protocol


class Backend(str, Enum):
    KEYCHAIN = "keychain"   # macOS Keychain / Windows Credential Manager / Linux Secret Service
    ENV = "env"             # 환경변수
    DOTENV = "dotenv"       # 평문 .env 폴백 (권한 600 + gitignore)


@dataclass(frozen=True)
class SecretRef:
    """비밀 '참조' — 값이 아니라, 어느 서비스·신원의 어느 백엔드 항목인지만 담는다."""
    service: str            # 예: "gmail", "github", "anthropic"
    identity: str           # 예: "work", "personal", "default"
    field: str              # 예: "token", "api_key", "password"
    backend: Backend
    locator: str            # 키체인 항목 이름 / 환경변수 이름 / .env 키

    def masked(self) -> str:
        return f"{self.service}:{self.identity}:{self.field}=<redacted:{self.backend.value}>"


class SecretStore(Protocol):
    """백엔드 추상화. 강한 검증기 원리처럼, 신뢰 경계는 '세계'(OS 키체인)에 둔다."""
    def get(self, ref: SecretRef) -> str: ...
    def exists(self, ref: SecretRef) -> bool: ...


def load_account(path: str | Path) -> list[SecretRef]:
    """env식 글로벌 계정 파일을 파싱해 SecretRef 목록을 만든다. 값(비밀)은 해석하지 않는다.

    형식: POLYRUS_<SERVICE>_<IDENTITY>_<FIELD>=<backend>:<locator>
    (account.env.example 참고. FIELD는 여러 단어 가능: CHAT_ID 등.)
    """
    refs: list[SecretRef] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name.startswith("POLYRUS_"):
            continue
        parts = name[len("POLYRUS_"):].split("_")
        if len(parts) < 3:
            continue
        service, identity, field = parts[0], parts[1], "_".join(parts[2:])
        backend_str, _, locator = value.strip().partition(":")
        try:
            backend = Backend(backend_str)
        except ValueError:
            continue
        refs.append(SecretRef(service.lower(), identity.lower(), field.lower(), backend, locator))
    return refs


# ── 백엔드 구현 ───────────────────────────────────────────────────────────────
class EnvSecretStore:
    """환경변수 백엔드. locator = 환경변수 이름."""

    def get(self, ref: SecretRef) -> str:
        v = os.environ.get(ref.locator)
        if v is None:
            raise KeyError(f"환경변수 '{ref.locator}' 없음 ({ref.masked()})")
        return v

    def exists(self, ref: SecretRef) -> bool:
        return ref.locator in os.environ


class DotenvStore:
    """평문 .env 폴백. 권한 600 권장(아니면 경고). locator = .env 키."""

    def __init__(self, path: str | Path, *, warn: bool = True) -> None:
        self.path = Path(path)
        self._warn = warn

    def _data(self) -> dict[str, str]:
        if self._warn and self.path.exists():
            mode = stat.S_IMODE(self.path.stat().st_mode)
            if mode & 0o077:  # group/other 비트가 있으면 경고(비밀 평문)
                import warnings

                warnings.warn(f"{self.path} 권한이 느슨함(권장 600) — 비밀 평문 노출 위험", stacklevel=2)
        out: dict[str, str] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
        return out

    def get(self, ref: SecretRef) -> str:
        d = self._data()
        if ref.locator not in d:
            raise KeyError(f".env에 '{ref.locator}' 없음 ({ref.masked()})")
        return d[ref.locator]

    def exists(self, ref: SecretRef) -> bool:
        return ref.locator in self._data()


class KeychainStore:
    """OS 키체인(macOS Keychain / Windows Credential Manager / Linux Secret Service) via `keyring`.

    `keyring`은 선택 의존성 — `pip install polyrus-agent[keychain]`. 테스트는 keyring 객체 주입.
    locator 무관: service='polyrus:<service>:<identity>', username='<field>'로 저장/조회.
    """

    def __init__(self, keyring: object | None = None) -> None:
        self._kr = keyring

    def _ring(self) -> object:
        if self._kr is None:
            try:
                import keyring  # 지연 import
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("keyring 미설치 — `pip install polyrus-agent[keychain]`") from e
            self._kr = keyring
        return self._kr

    def _id(self, ref: SecretRef) -> tuple[str, str]:
        return f"polyrus:{ref.service}:{ref.identity}", ref.field

    def get(self, ref: SecretRef) -> str:
        service, username = self._id(ref)
        v = self._ring().get_password(service, username)  # type: ignore[attr-defined]
        if v is None:
            raise KeyError(f"키체인에 {ref.masked()} 없음")
        return v

    def exists(self, ref: SecretRef) -> bool:
        try:
            return self.get(ref) is not None
        except KeyError:
            return False


def _default_store(backend: Backend) -> SecretStore:
    if backend is Backend.ENV:
        return EnvSecretStore()
    if backend is Backend.KEYCHAIN:
        return KeychainStore()
    return DotenvStore(Path.home() / ".polyrus" / "account.env")


def resolve(ref: SecretRef, store: SecretStore | None = None) -> str:
    """도구 실행 경계에서만 호출. 반환값은 즉시 사용하고 어디에도 기록하지 않는다.

    경고: 이 반환값을 모델 프롬프트나 원장/로그에 절대 넣지 말 것. (CredentialResolver가 자동 리댁션.)
    """
    return (store or _default_store(ref.backend)).get(ref)


def redact(text: str, values: Iterable[str]) -> str:
    """로그·원장·오류·모델 컨텍스트로 나가기 전, 알려진 비밀*값*을 마스킹한다."""
    out = text
    for v in sorted((s for s in values if s), key=len, reverse=True):  # 긴 것부터(부분 마스킹 방지)
        out = out.replace(v, "<redacted>")
    return out


class Redactor:
    """해석된 비밀값을 누적해 이후 모든 출력에서 자동 마스킹."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def register(self, value: str) -> None:
        if value:
            self._values.add(value)

    def scrub(self, text: str) -> str:
        return redact(text, self._values)


class CredentialResolver:
    """글로벌 계정 파일을 로드하고, *도구 경계에서만* 비밀을 해석한다(모델 프롬프트엔 안 들어감).

    해석된 값은 Redactor에 등록돼 이후 로그/원장에서 자동 마스킹된다(전면 리댁션 6.3).
    """

    def __init__(
        self,
        refs: list[SecretRef] | None = None,
        *,
        stores: dict[Backend, SecretStore] | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.refs = {(r.service, r.identity, r.field): r for r in (refs or [])}
        self.stores = stores or {}
        self.redactor = redactor or Redactor()

    @classmethod
    def from_account(cls, path: str | Path, **kw: object) -> CredentialResolver:
        return cls(load_account(path), **kw)  # type: ignore[arg-type]

    def get(self, service: str, identity: str, field: str) -> str:
        key = (service.lower(), identity.lower(), field.lower())
        if key not in self.refs:
            raise KeyError(f"선언되지 않은 자격증명: {key}")
        ref = self.refs[key]
        store = self.stores.get(ref.backend) or _default_store(ref.backend)
        value = store.get(ref)
        self.redactor.register(value)  # 이후 자동 마스킹
        return value

    def scrub(self, text: str) -> str:
        return self.redactor.scrub(text)
