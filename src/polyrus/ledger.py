from __future__ import annotations

from typing import TYPE_CHECKING

from polyrus.types import AggregateVerdict, LedgerItem, Task

if TYPE_CHECKING:
    from polyrus.dod import DoDGenerator


class Ledger:
    """M1 완료 원장. 외부 상태로서 진행을 추적한다 (까마귀식 외부 기억).

    요구사항 목록을 *모델 밖*(이 원장)에 둔다(5.3) — 까먹은 목록으론 누락 체크 불가.
    task.items가 비어 있으면 DoDGenerator로 요청을 하위 목표로 분해한다.
    """

    def __init__(self, task: Task, decomposer: DoDGenerator | None = None) -> None:
        self._task = task
        if task.items:
            self._items: list[LedgerItem] = list(task.items)
        elif decomposer is not None:
            self._items = list(decomposer.decompose(task))
        else:
            self._items = []

    @property
    def task(self) -> Task:
        return self._task

    def items(self) -> list[LedgerItem]:
        return self._items

    def next_open_item(self) -> LedgerItem | None:
        return next((i for i in self._items if not i.closed and not i.escalated), None)

    def close(self, item: LedgerItem, verdict: AggregateVerdict) -> None:
        item.closed = True
        item.verdict = verdict
        item.confidence = verdict.weighted_confidence

    def mark_escalated(self, item: LedgerItem) -> None:
        item.escalated = True

    def all_items_verified(self) -> bool:
        return all(i.closed for i in self._items)
