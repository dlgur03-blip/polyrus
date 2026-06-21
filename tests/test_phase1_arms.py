"""Phase 1 — 진짜 LLM Arms(병렬·콜드스타트) + DoD 자동생성. fake 모델로 네트워크 0 실측.

핵심: Arms가 모델로 후보를 *생성*하고, 그게 검증기 뱅크를 통과해 No-Pass 루프가 green까지 간다.
"""
from __future__ import annotations

from polyrus.core.arms import Arms, extract_code
from polyrus.dod import DoDGenerator
from polyrus.escalation import Escalator
from polyrus.harness import Harness, HarnessConfig
from polyrus.models import AnthropicModel, EchoModel
from polyrus.types import Budget, DoD, LedgerItem, RiskLevel, Task, Termination
from polyrus.verifiers.registry import default_code_bank
from tests.test_t1_execution import GOOD, TEST

GOOD_FENCED = f"여기 구현입니다:\n```python\n{GOOD}```\n끝."


class FakeModel:
    """프롬프트를 기록하고 고정 응답을 돌려준다(또는 호출가능 응답 생성기)."""

    def __init__(self, response) -> None:
        self.response = response
        self.calls: list[dict] = []

    def complete(self, prompt, *, system=None, model=None, temperature=None) -> str:
        self.calls.append({"prompt": prompt, "system": system, "temperature": temperature})
        return self.response(prompt) if callable(self.response) else self.response


# ── 코드 추출 ─────────────────────────────────────────────────────────────────
def test_extract_code_strips_fences() -> None:
    assert extract_code("blah\n```python\nx = 1\n```\ntrailing").strip() == "x = 1"


def test_extract_code_without_fence() -> None:
    assert "x = 1" in extract_code("x = 1")


# ── Arms 생성(병렬·콜드스타트·탈상관) ──────────────────────────────────────────
def test_generate_returns_k_candidates() -> None:
    arms = Arms(FakeModel(GOOD_FENCED))
    cands = arms.generate(LedgerItem(id="i", goal="g", dod=DoD(spec="s", frozen=True)), k=3)
    assert len(cands) == 3
    assert all("sum_even_squares" in c.content for c in cands)  # 펜스 추출됨


def test_arm0_is_cold_start() -> None:
    arms = Arms(FakeModel(GOOD_FENCED))
    cands = arms.generate(LedgerItem(id="i", goal="g", dod=DoD(spec="s", frozen=True)), k=3)
    assert cands[0].meta["cold"] is True
    assert all(c.meta["cold"] is False for c in cands[1:])


def test_cold_start_ignores_feedback() -> None:
    model = FakeModel(GOOD_FENCED)
    arms = Arms(model)
    item = LedgerItem(id="i", goal="g", dod=DoD(spec="s", frozen=True))
    arms.diversify(item, blocker="이전 실패: assert 35 == 0")
    arms.generate(item, k=3)
    cold_prompts = [c["prompt"] for c in model.calls if "이전 시도 실패" not in c["prompt"]]
    warm_prompts = [c["prompt"] for c in model.calls if "이전 시도 실패" in c["prompt"]]
    assert cold_prompts and warm_prompts  # 콜드는 피드백 없이, 워밍은 피드백 보고


# ── 엔드투엔드: Arms가 생성 → 검증기 통과 → verified_complete ───────────────────
def test_arms_drive_loop_to_verified() -> None:
    dod = DoD(spec="짝수 제곱합", acceptance_tests=[TEST], frozen=True)
    task = Task(id="t", request="r",
                items=[LedgerItem(id="i1", goal="sum_even_squares", dod=dod, risk=RiskLevel.LOW)])
    harness = Harness(Arms(FakeModel(GOOD_FENCED)), default_code_bank(), Escalator(),
                      cfg=HarnessConfig(max_retries=2))
    res = harness.run(task, Budget(max_tokens=10_000_000))
    assert res.termination is Termination.VERIFIED_COMPLETE
    assert res.corpus_records


# ── DoD 자동생성(LLM) ──────────────────────────────────────────────────────────
def test_dod_synthesizes_tests_with_model() -> None:
    model = FakeModel(f"테스트:\n```python\n{TEST}```")
    dod = DoDGenerator(model=model).derive_dod("정수 리스트의 짝수만 제곱해 합")
    assert dod.frozen and dod.acceptance_tests
    assert "def test" in dod.acceptance_tests[0]
    assert "테스트" in model.calls[0]["system"]  # 구현이 아니라 테스트를 쓰라는 시스템 프롬프트


def test_dod_without_model_stays_empty() -> None:
    dod = DoDGenerator().derive_dod("스펙만")  # 모델 없음 → 빈 수용테스트(INCONCLUSIVE 유발)
    assert dod.frozen and not dod.acceptance_tests


# ── AnthropicModel 어댑터(주입 클라이언트, 네트워크 0) ──────────────────────────
class _FakeAnthropic:
    def __init__(self, text: str) -> None:
        self.messages = self
        self._text = text
        self.kwargs: dict | None = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        block = type("B", (), {"text": self._text})()
        return type("M", (), {"content": [block]})()


def test_anthropic_model_with_injected_client() -> None:
    fake = _FakeAnthropic("생성된 코드")
    out = AnthropicModel(client=fake, temperature=0.3).complete("안녕", system="sys")
    assert out == "생성된 코드"
    assert fake.kwargs["temperature"] == 0.3
    assert fake.kwargs["system"] == "sys"
    assert fake.kwargs["messages"][0]["content"] == "안녕"


def test_echo_model_supports_temperature_kwarg() -> None:
    assert EchoModel().complete("hi", temperature=0.5).startswith("[echo]")
