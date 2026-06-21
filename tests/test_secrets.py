"""로컬 우선 자격증명(6.3) — env식 계정 파싱·env/.env/키체인 백엔드·전면 리댁션."""
from __future__ import annotations

import pytest

from polyrus.secrets import (
    Backend,
    CredentialResolver,
    DotenvStore,
    EnvSecretStore,
    KeychainStore,
    Redactor,
    SecretRef,
    load_account,
    redact,
    resolve,
)

ACCOUNT = (
    "# 주석\n"
    "POLYRUS_GMAIL_WORK_TOKEN=keychain:polyrus/gmail/work/token\n"
    "POLYRUS_GITHUB_PERSONAL_TOKEN=env:GITHUB_TOKEN\n"
    "POLYRUS_TELEGRAM_DEFAULT_CHAT_ID=env:POLYRUS_TELEGRAM_CHAT_ID\n"
    "INVALID_LINE_NO_POLYRUS=env:X\n"
)


# ── 계정 파싱 ──────────────────────────────────────────────────────────────────
def test_load_account_parses(tmp_path) -> None:
    f = tmp_path / "account.env"
    f.write_text(ACCOUNT, encoding="utf-8")
    refs = {(r.service, r.identity, r.field): r for r in load_account(f)}
    assert ("gmail", "work", "token") in refs
    assert refs[("github", "personal", "token")].backend is Backend.ENV
    # 멀티워드 필드(CHAT_ID) 파싱
    assert ("telegram", "default", "chat_id") in refs
    assert ("invalid", "line", "no_polyrus") not in refs  # POLYRUS_ 아님 → 무시


def test_load_real_example() -> None:
    from pathlib import Path

    refs = load_account(Path(__file__).resolve().parents[1] / "account.env.example")
    services = {r.service for r in refs}
    assert "telegram" in services and "gmail" in services


# ── env / .env 백엔드 ──────────────────────────────────────────────────────────
def test_env_store(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    ref = SecretRef("github", "personal", "token", Backend.ENV, "GITHUB_TOKEN")
    assert resolve(ref, EnvSecretStore()) == "ghp_secret"
    assert EnvSecretStore().exists(ref)


def test_env_store_missing_raises(monkeypatch) -> None:
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    with pytest.raises(KeyError):
        EnvSecretStore().get(SecretRef("x", "y", "z", Backend.ENV, "NOPE_TOKEN"))


def test_dotenv_store(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("API_KEY=plain_secret\n", encoding="utf-8")
    env.chmod(0o600)
    ref = SecretRef("svc", "id", "key", Backend.DOTENV, "API_KEY")
    assert DotenvStore(env).get(ref) == "plain_secret"


def test_dotenv_warns_on_loose_perms(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("K=v\n", encoding="utf-8")
    env.chmod(0o644)  # group/other 읽기 가능 → 경고
    with pytest.warns(UserWarning):
        DotenvStore(env).get(SecretRef("s", "i", "k", Backend.DOTENV, "K"))


# ── 키체인(주입 keyring, 네트워크/OS 0) ────────────────────────────────────────
class FakeKeyring:
    def __init__(self) -> None:
        self.store = {("polyrus:gmail:work", "token"): "kc_secret"}

    def get_password(self, service, username):
        return self.store.get((service, username))


def test_keychain_store_with_injected_keyring() -> None:
    ks = KeychainStore(FakeKeyring())
    ref = SecretRef("gmail", "work", "token", Backend.KEYCHAIN, "polyrus/gmail/work/token")
    assert ks.get(ref) == "kc_secret" and ks.exists(ref)


def test_keychain_missing_raises() -> None:
    ref = SecretRef("x", "y", "z", Backend.KEYCHAIN, "loc")
    with pytest.raises(KeyError):
        KeychainStore(FakeKeyring()).get(ref)


# ── 전면 리댁션(6.3) ────────────────────────────────────────────────────────────
def test_redact_masks_values() -> None:
    out = redact("토큰은 ghp_secret, 키는 sk_abc", ["ghp_secret", "sk_abc"])
    assert "ghp_secret" not in out and "sk_abc" not in out


def test_redactor_accumulates() -> None:
    r = Redactor()
    r.register("SECRET1")
    r.register("SECRET2")
    assert "SECRET1" not in r.scrub("로그: SECRET1 / SECRET2")


# ── 리졸버: 도구 경계 해석 + 자동 리댁션 ───────────────────────────────────────
def test_resolver_resolves_and_auto_redacts(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_live")
    f = tmp_path / "account.env"
    f.write_text("POLYRUS_GITHUB_PERSONAL_TOKEN=env:GITHUB_TOKEN\n", encoding="utf-8")
    r = CredentialResolver.from_account(f)
    assert r.get("github", "personal", "token") == "ghp_live"
    # 해석된 값은 이후 로그에서 자동 마스킹된다.
    assert "ghp_live" not in r.scrub("에러: github 토큰 ghp_live 거부됨")


def test_resolver_undeclared_raises() -> None:
    with pytest.raises(KeyError):
        CredentialResolver([]).get("unknown", "x", "y")


def test_telegram_config_from_resolver(monkeypatch, tmp_path) -> None:
    # 통합 경로: 텔레그램 토큰을 글로벌 계정 → env로 해석.
    from polyrus.notify.telegram import TelegramConfig

    monkeypatch.setenv("POLYRUS_TELEGRAM_TOKEN", "bot_tok")
    monkeypatch.setenv("POLYRUS_TELEGRAM_CHAT_ID", "12345")
    f = tmp_path / "account.env"
    f.write_text(
        "POLYRUS_TELEGRAM_DEFAULT_TOKEN=env:POLYRUS_TELEGRAM_TOKEN\n"
        "POLYRUS_TELEGRAM_DEFAULT_CHAT_ID=env:POLYRUS_TELEGRAM_CHAT_ID\n",
        encoding="utf-8",
    )
    cfg = TelegramConfig.from_resolver(CredentialResolver.from_account(f))
    assert cfg is not None and cfg.token == "bot_tok" and cfg.chat_id == "12345"
