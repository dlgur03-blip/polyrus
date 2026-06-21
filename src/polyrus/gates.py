from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from polyrus.types import LedgerItem, RiskLevel


@dataclass
class GateDecision:
    proceed: bool
    note: str = ""


class EvidenceGate:
    """증거조사 게이트 (휴먼인루프). M3 에스컬레이션의 한 인스턴스.

    검증 루프는 비싸다. 자동으로 건너뛰지도, 자동으로 전부 돌리지도 않는다.
    대신 사용자에게 묻는다: '검증 패스를 돌릴까요? 비용 X. 안 돌리면 확신도 Y.'
    경쟁자의 '끄면 묻지 않고 실행'하는 승인 게이트와 달리, 비용에 비례해 묻는다.
    """

    def __init__(self, ask_fn: Callable[[str], bool] | None = None) -> None:
        self.ask_fn = ask_fn

    def should_run_verification(self, item: LedgerItem, est_cost: str) -> GateDecision:
        if item.risk is RiskLevel.LOW:
            return GateDecision(True, "저위험: 자동 진행")
        if self.ask_fn is None:
            return GateDecision(True, "게이트 콜백 미설정: 기본 진행")
        ok = self.ask_fn(f"검증 패스 실행? 예상 비용 {est_cost}")
        return GateDecision(ok, "사용자 결정")
