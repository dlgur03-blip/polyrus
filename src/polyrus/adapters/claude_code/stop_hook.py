"""Claude Code Stop-hook 어댑터 — Polyrus의 첫 웨지(wrap-first).

핵심: 래퍼 모드에서 *생성은 호스트(Claude Code), 검증·게이팅은 Polyrus*다.
Claude Code의 Stop 훅은 `{"decision":"block","reason":...}`를 반환하면 모델을 못 멈추게
하고 reason을 다시 먹여 이어가게 한다. 이게 No-Pass 종료조건 역전의 집행 씨앗이다:

  모델이 "끝" 선언(Stop) → Polyrus가 완료 원장 vs 검증기 뱅크 대조
    → 전부 검증 통과면 stop 허용
    → 미검증이면 block + reason("빠진 항목 X") → 호스트가 이어서 수정
    → continue 예산 소진까지 미해결이면 stop 허용 + 에스컬레이션 (No-Silent-Stop)

호스트가 재시도 루프를 돈다(Polyrus는 매 Stop마다 *단일 검증 패스*만). continue 예산은
세션별 카운터(파일)로 가로질러 추적해 무한 재주입을 막는다.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from polyrus.store import Store
from polyrus.types import (
    Claim,
    CorpusRecord,
    LedgerItem,
    LoopResult,
    Task,
    Termination,
)
from polyrus.verifiers.registry import VerifierBank, default_code_bank

# 세션의 (Task, {item_id: 현재 산출물 Claim})를 호스트 payload에서 해석하는 로더.
Loader = Callable[[dict[str, Any]], tuple[Task, dict[str, Claim]]]


@dataclass
class HookResult:
    """Stop 훅 한 번의 결정. block=True면 호스트가 이어서 한다(아직 종료 아님)."""

    block: bool
    reason: str
    termination: Termination | None  # 허용(stop) 시에만 의미 — 완료/예산경계
    corpus_records: list[CorpusRecord] = field(default_factory=list)

    def as_hook_output(self) -> dict[str, Any]:
        """Claude Code Stop 훅 stdout 스키마."""
        if self.block:
            return {"decision": "block", "reason": self.reason}
        # 허용: 빈 객체 = 정상 종료. 에스컬레이션이면 사람이 보도록 메시지 동반.
        if self.termination is Termination.BUDGET_ESCALATED:
            return {"systemMessage": f"[Polyrus] 미해결 — 사람 확인 필요:\n{self.reason}"}
        return {}


class ClaudeCodeStopAdapter:
    """AgentAdapter 구현. 코어는 이 어댑터를 모른다 — 같은 검증 코어를 호스트에 붙일 뿐."""

    name = "claude_code.stop_hook"

    def __init__(
        self,
        loader: Loader,
        bank: VerifierBank | None = None,
        max_continues: int = 3,
    ) -> None:
        self.loader = loader
        self.bank = bank or default_code_bank()
        self.max_continues = max_continues

    # ── AgentAdapter 경계 ──────────────────────────────────────────────────────
    def build_task(self, payload: dict[str, Any]) -> Task:
        return self.loader(payload)[0]

    def render_continuation(self, result: LoopResult) -> str:
        return "\n".join(f"- {b}" for b in result.open_blockers) or "완료"

    # ── 결정(단일 검증 패스) ───────────────────────────────────────────────────
    def decide(self, payload: dict[str, Any], continues: int = 0) -> HookResult:
        task, artifacts = self.loader(payload)
        blockers: list[str] = []
        records: list[CorpusRecord] = []

        for item in task.items:
            if item.closed:
                continue
            claim = artifacts.get(item.id)
            if claim is None:
                blockers.append(f"{item.goal}: 산출물 없음")
                continue
            verdict = self.bank.run(claim, item.dod)
            records.extend(self._emit(task, item, claim, verdict))
            if verdict.passed:
                item.closed = True
                item.confidence = verdict.weighted_confidence
            else:
                blockers.append(f"{item.goal}: {verdict.blocker}")

        if not blockers:
            return HookResult(False, "전 항목 검증 통과", Termination.VERIFIED_COMPLETE, records)

        if continues >= self.max_continues:
            # continue 예산 소진 → 강제 stop 허용 + 에스컬레이션 (조용한 패스 아님).
            reason = "continue 예산 소진. 미해결:\n" + "\n".join(f"- {b}" for b in blockers)
            return HookResult(False, reason, Termination.BUDGET_ESCALATED, records)

        # 미검증 → 호스트가 이어서 하도록 block + 빠진 항목 재주입.
        reason = (
            "Polyrus: 아직 검증을 통과하지 못했다. 완료 선언 금지. 다음을 해결하라:\n"
            + "\n".join(f"- {b}" for b in blockers)
        )
        return HookResult(True, reason, None, records)

    @staticmethod
    def _emit(task: Task, item: LedgerItem, claim: Claim, verdict: Any) -> list[CorpusRecord]:
        return [
            CorpusRecord(
                task_id=task.id, item_id=item.id, claim_id=claim.id,
                tier=r.tier.value, verdict=r.verdict.value,
                confidence=r.confidence, reliability=r.reliability, locality=r.locality.value,
            )
            for r in verdict.results
        ]


# ─────────────────────────────────────────────────────────────────────────────
# 훅 실행 래퍼 — stdin(JSON) → 결정 → stdout(JSON). continue 카운터를 세션별로 영속.
# ─────────────────────────────────────────────────────────────────────────────
def _count_file(state_dir: Path, session: str) -> Path:
    safe = "".join(c for c in session if c.isalnum() or c in "-_") or "default"
    return state_dir / f"{safe}.count"


def run_hook(
    payload: dict[str, Any],
    adapter: ClaudeCodeStopAdapter,
    state_dir: str | Path = ".polyrus",
    store: Store | None = None,
    notifier: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """payload(이미 파싱됨) → 훅 출력 dict. continue 예산을 세션 카운터로 가로질러 추적.

    notifier: 예산 소진 에스컬레이션 시 호출(예: 텔레그램 폰 알림 6.4). away 유저용.
    """
    sdir = Path(state_dir)
    sdir.mkdir(parents=True, exist_ok=True)
    session = str(payload.get("session_id", "default"))
    cf = _count_file(sdir, session)
    continues = int(cf.read_text()) if cf.exists() else 0

    result = adapter.decide(payload, continues=continues)

    if store is not None and result.corpus_records:
        store.append_corpus(result.corpus_records)

    if result.block:
        cf.write_text(str(continues + 1))  # 다음 Stop을 위해 증가
    else:
        if cf.exists():
            cf.unlink()  # 종료 → 카운터 리셋
        if notifier is not None and result.termination is Termination.BUDGET_ESCALATED:
            notifier(result.reason)  # 폰으로 핑: 사람 결정 필요

    return result.as_hook_output()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - 실제 훅 진입점
    """`polyrus-stop-hook` 진입점. stdin에서 Claude Code Stop payload를 읽는다.

    로더는 cwd의 `.polyrus/task.json`(Task 명세 + 산출물 파일 경로)을 해석한다고 가정.
    실제 배선은 0b 잔여(파일 스키마 확정) — 여기서는 IO 골격만.
    """
    from polyrus.adapters.claude_code.task_file import file_loader  # 지연 import

    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    adapter = ClaudeCodeStopAdapter(loader=file_loader)
    out = run_hook(payload, adapter)
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0
