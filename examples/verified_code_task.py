"""0단계 데모: '검증된 코드 태스크' No-Pass 루프 엔드투엔드.

실제 T1 검증기(ruff+pytest, 결정적 오라클)에 대해 루프를 끝까지 돌린다.
생성기(Arms)만 *데모용 스크립트*다 — 진짜 LLM 병렬 팔은 Phase 1. 검증은 진짜다.

두 시나리오로 Phase 0 완료 기준을 보인다:
  (A) 올바른 후보 → 검증된 완료(VERIFIED_COMPLETE).
  (B) 틀린 후보(반복) → 검증 거부 → 막힘 감지 → 에스컬레이션(조용한 패스 없음).

실행: python examples/verified_code_task.py
"""
from __future__ import annotations

from polyrus.escalation import Escalator
from polyrus.harness import Harness, HarnessConfig
from polyrus.types import Budget, Claim, DoD, LedgerItem, RiskLevel, Task
from polyrus.verifiers.registry import default_code_bank

ACCEPTANCE_TEST = (
    "from solution import sum_even_squares\n"
    "def test_basic():\n"
    "    assert sum_even_squares([1, 2, 3, 4]) == 20\n"
    "def test_empty():\n"
    "    assert sum_even_squares([]) == 0\n"
    "def test_odds_only():\n"
    "    assert sum_even_squares([1, 3, 5]) == 0\n"
)
GOOD = "def sum_even_squares(xs):\n    return sum(x * x for x in xs if x % 2 == 0)\n"
BAD = "def sum_even_squares(xs):\n    return sum(x * x for x in xs)  # 홀수도 더함\n"


class DemoArms:
    """데모용 스크립트 생성기 (진짜 LLM 병렬 팔/콜드스타트는 Phase 1).

    항상 같은 후보를 낸다 — 틀린 후보로 돌리면 '막힘 감지'(팔 다양성 붕괴)를 보여준다.
    """

    def __init__(self, candidate: str) -> None:
        self.candidate = candidate

    def generate(self, item: LedgerItem, k: int) -> list[Claim]:
        return [Claim(id=f"{item.id}-cand", content=self.candidate, meta={"module": "solution.py"})]

    def select(self, candidates: list[Claim]) -> Claim:
        return candidates[0]

    def diversify(self, item: LedgerItem, blocker: str) -> None:
        pass  # 데모 생성기는 다양화하지 않음 → 막힘 감지가 작동하는 걸 보여줌


def run_scenario(label: str, candidate: str) -> None:
    dod = DoD(spec="짝수만 제곱해 합", acceptance_tests=[ACCEPTANCE_TEST], frozen=True)
    task = Task(
        id="t1",
        request="sum_even_squares 구현",
        items=[LedgerItem(id="i1", goal="sum_even_squares 구현", dod=dod, risk=RiskLevel.MEDIUM)],
    )
    harness = Harness(
        DemoArms(candidate),
        default_code_bank(),                       # 진짜 T1 검증기(ruff+pytest)
        Escalator(sink=lambda item, msg: print("  " + msg.replace("\n", "\n  "))),
        cfg=HarnessConfig(max_retries=4, stuck_threshold=2),
    )
    result = harness.run(task, Budget(max_tokens=50_000))

    print(f"\n[{label}] 종료: {result.termination.value} (확신도 {result.weighted_confidence:.2f})")
    for item in result.items:
        status = "완료" if item.closed else ("에스컬레이션" if item.escalated else "미해결")
        print(f"  - {item.goal}: {status} (확신도 {item.confidence:.2f})")
    print(f"  보정 코퍼스: {len(result.corpus_records)}건 emit (리댁션)")


def main() -> None:
    run_scenario("A. 올바른 후보", GOOD)
    run_scenario("B. 틀린 후보(반복)", BAD)


if __name__ == "__main__":
    main()
