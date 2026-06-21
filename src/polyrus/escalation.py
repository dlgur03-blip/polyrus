from __future__ import annotations

from typing import Callable

from polyrus.types import LedgerItem


class Escalator:
    """M3: 포기 대신 에스컬레이션. 조용한 패스 금지.

    '못 해요, 전문가 상담하세요'(패스)가 아니라
    'N단계까지 했고, 구체 블로커는 이것, 경로 두 개 중 뭘 원해?'(에스컬레이션).
    """

    def __init__(self, sink: Callable[[LedgerItem, str], None] | None = None) -> None:
        self.sink = sink
        self.raised: list[tuple[str, str]] = []

    def raise_to_human(self, item: LedgerItem, blocker: str) -> None:
        msg = (
            f"[에스컬레이션] 항목 '{item.goal}' 미완료.\n"
            f"  블로커: {blocker}\n"
            f"  필요: 진행을 위한 결정/정보."
        )
        self.raised.append((item.id, msg))
        if self.sink:
            self.sink(item, msg)
