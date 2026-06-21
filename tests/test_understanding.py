"""이해 검증(5.5) — 팔의 해석 divergence=모호성, 가정 표면화, 해석/실행 확신 분리."""
from __future__ import annotations

from polyrus.understanding import (
    Interpretation,
    Understander,
    assess_understanding,
    interpret_task,
)


class SeqModel:
    """호출마다 다음 응답을 돌려준다(팔의 해석 divergence 시뮬레이트)."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.i = 0

    def complete(self, prompt, *, system=None, model=None, temperature=None) -> str:
        r = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return r


# ── 종합 판정(순수) ────────────────────────────────────────────────────────────
def test_agreement_not_ambiguous() -> None:
    interps = [Interpretation("같은 이해", [], 0.9), Interpretation("같은 이해", [], 0.9)]
    u = assess_understanding(interps)
    assert not u.ambiguous and u.recovery_question == ""


def test_divergence_is_ambiguous() -> None:
    interps = [Interpretation("A로 해석", [], 0.9), Interpretation("B로 해석", [], 0.9)]
    u = assess_understanding(interps)
    assert u.ambiguous and "갈림" in u.recovery_question


def test_low_confidence_is_ambiguous() -> None:
    u = assess_understanding([Interpretation("불확실", [], 0.3)])
    assert u.ambiguous and "확신" in u.recovery_question


def test_assumptions_surfaced_and_unioned() -> None:
    interps = [Interpretation("x", ["빈 입력 허용 가정"], 0.9), Interpretation("x", ["음수 가정"], 0.9)]
    u = assess_understanding(interps)
    assert "빈 입력 허용 가정" in u.assumptions and "음수 가정" in u.assumptions


# ── 해석 파싱(JSON) ────────────────────────────────────────────────────────────
def test_interpret_task_parses_json() -> None:
    model = SeqModel(['{"interpretation":"짝수 제곱합","assumptions":["빈→0"],"confidence":0.8}'])
    interps = interpret_task(model, goal="g", spec="s", k=1)
    assert interps[0].summary == "짝수 제곱합"
    assert interps[0].assumptions == ["빈→0"] and interps[0].confidence == 0.8


# ── 하니스 통합: 모호하면 생성 전에 게이트 ─────────────────────────────────────
def test_harness_gates_on_ambiguity() -> None:
    from polyrus.escalation import Escalator
    from polyrus.harness import Harness
    from polyrus.types import DoD, LedgerItem, Task, Termination

    class BoomArms:  # 생성이 호출되면 안 됨(이해 게이트가 먼저 막아야)
        def generate(self, item, k):
            raise AssertionError("이해 모호인데 생성이 돌았다")

        def select(self, c):
            return c[0]

        def diversify(self, item, b):
            pass

    diverging = SeqModel([
        '{"interpretation":"로그인 UI를 만들어라","confidence":0.9}',
        '{"interpretation":"로그인 API를 만들어라","confidence":0.9}',
    ])
    h = Harness(BoomArms(), bank=None, escalator=Escalator(),
                understander=Understander(diverging, k=2))
    task = Task(id="t", request="로그인",
                items=[LedgerItem(id="i1", goal="로그인 만들어줘", dod=DoD(spec="로그인", frozen=True))])
    res = h.run(task)
    assert res.termination is Termination.ESCALATED
    assert "이해 모호" in res.items[0].escalation_reason
