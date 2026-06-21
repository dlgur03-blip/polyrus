"""모델 비종속(7.2) — 폴백 체인 + OpenAI(호환) 어댑터. 네트워크 0(주입 클라이언트)."""
from __future__ import annotations

import pytest

from polyrus.models import FallbackModel, OpenAIModel


class Good:
    def __init__(self, out: str) -> None:
        self.out = out

    def complete(self, prompt, *, system=None, model=None, temperature=None) -> str:
        return self.out


class Dead:
    def complete(self, prompt, *, system=None, model=None, temperature=None) -> str:
        raise RuntimeError("프로바이더 다운")


# ── 폴백 체인 ──────────────────────────────────────────────────────────────────
def test_fallback_uses_first_working() -> None:
    fb = FallbackModel([Dead(), Good("from-second")])
    assert fb.complete("hi") == "from-second"
    assert isinstance(fb.last_used, Good)


def test_fallback_single_provider() -> None:
    assert FallbackModel([Good("x")]).complete("hi") == "x"


def test_fallback_all_dead_raises() -> None:
    with pytest.raises(RuntimeError):
        FallbackModel([Dead(), Dead()]).complete("hi")


def test_fallback_requires_provider() -> None:
    with pytest.raises(ValueError):
        FallbackModel([])


# ── OpenAI(호환) 어댑터 ────────────────────────────────────────────────────────
class _FakeCompletions:
    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        msg = type("M", (), {"content": self.text})()
        choice = type("C", (), {"message": msg})()
        return type("R", (), {"choices": [choice]})()


class _FakeOpenAI:
    def __init__(self, text: str) -> None:
        self.chat = type("Chat", (), {"completions": _FakeCompletions(text)})()


def test_openai_model_with_injected_client() -> None:
    fake = _FakeOpenAI("openai-out")
    m = OpenAIModel(client=fake, temperature=0.4)
    assert m.complete("안녕", system="sys") == "openai-out"
    kw = fake.chat.completions.kwargs
    assert kw["temperature"] == 0.4
    assert kw["messages"][0] == {"role": "system", "content": "sys"}
    assert kw["messages"][1]["content"] == "안녕"


def test_openai_compatible_base_url_stored() -> None:
    # base_url로 아무 OpenAI-호환 엔드포인트나 가리킨다(OpenRouter·로컬 등).
    m = OpenAIModel(base_url="https://openrouter.ai/api/v1", model="x")
    assert m.base_url.endswith("/v1")


# ── Gemini 어댑터 (주입 클라이언트, 네트워크 0) ───────────────────────────────────
class _FakeGenModels:
    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs: dict | None = None

    def generate_content(self, **kwargs):
        self.kwargs = kwargs
        return type("R", (), {"text": self.text})()


class _FakeGenAI:
    def __init__(self, text: str) -> None:
        self.models = _FakeGenModels(text)


def test_gemini_model_with_injected_client() -> None:
    from polyrus.models import GeminiModel

    fake = _FakeGenAI("gemini-out")
    m = GeminiModel(client=fake, temperature=0.3, model="gemini-2.5-flash")
    assert m.complete("안녕", system="sys") == "gemini-out"
    kw = fake.models.kwargs
    assert kw["model"] == "gemini-2.5-flash"
    assert kw["contents"] == "안녕"
    assert kw["config"]["temperature"] == 0.3
    assert kw["config"]["system_instruction"] == "sys"


def test_gemini_in_fallback_chain() -> None:
    from polyrus.models import GeminiModel

    fb = FallbackModel([Dead(), GeminiModel(client=_FakeGenAI("g"))])
    assert fb.complete("hi") == "g"


def test_recommend_provider_no_gemini() -> None:
    from polyrus.models import recommend_provider

    # 사용자 셋업(Claude+Codex) 기준 — Gemini는 추천하지 않는다.
    assert recommend_provider("code").provider == "claude"
    assert recommend_provider("cheap_divergence").provider == "codex"
    assert recommend_provider("아무거나").provider == "claude"  # 기본
    assert all(recommend_provider(t).provider != "gemini"
               for t in ("code", "long_reasoning", "cheap_divergence", "default"))
