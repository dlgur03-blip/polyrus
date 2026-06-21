"""브라우저 행동 안전 (6.2) — 읽기우선·쓰기게이팅. 기존 자동화(Playwright)를 감싼다.

읽기·쓰기 비대칭:
  - 읽기(goto·text·url): 부수효과 없음, 교차대조로 검증 가능(T3) → *자율*.
  - 되돌릴 수 있는 쓰기(fill): 로컬 필드 편집, 원격 효과 없음 → 자동(감사만).
  - 되돌릴 수 없는 쓰기(click·submit·전송·결제): 부수효과·되돌림 불가 → *게이트*(승인) + 멱등 + 감사.
    승인자 없으면 '안전한 미완료'(트레이로 보류). 행동 후 read-back으로 결과 확인 가능.

행동 레이어(actions.py) 위에 얹어 멱등·격리·감사를 그대로 상속한다.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Protocol

from polyrus.actions import Action, ActionExecutor, ActionResult
from polyrus.store import Store


class BrowserDriver(Protocol):
    """Playwright/CDP 등을 감싸는 최소 계약(duck-typed). PlaywrightDriver 또는 테스트 페이크."""

    def goto(self, url: str) -> None: ...
    def text(self, selector: str | None = None) -> str: ...
    def url(self) -> str: ...
    def click(self, selector: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...


class SafeBrowser:
    """읽기우선·쓰기게이팅 브라우저. 읽기는 직접(자율), 쓰기는 ActionExecutor 경유(게이트·멱등·감사)."""

    def __init__(
        self,
        driver: BrowserDriver,
        *,
        approve: Callable[[Action], bool] | None = None,
        store: Store | None = None,
        executor: ActionExecutor | None = None,
    ) -> None:
        self.driver = driver
        self.executor = executor or ActionExecutor(store, approve=approve)

    # ── 읽기 (자율) ────────────────────────────────────────────────────────────
    def goto(self, url: str) -> str:
        self.driver.goto(url)
        return self.driver.url()

    def read(self, selector: str | None = None) -> str:
        return self.driver.text(selector)

    def cross_check_read(self, selector: str | None = None, *, n: int = 2) -> tuple[str, bool]:
        """교차대조(T3): 같은 읽기를 n회 해서 일관성 확인. (consistent=True면 신뢰)."""
        reads = [self.driver.text(selector) for _ in range(max(2, n))]
        return reads[0], all(r == reads[0] for r in reads)

    # ── 되돌릴 수 있는 쓰기 (자동, 감사) ───────────────────────────────────────
    def fill(self, selector: str, value: str, *, key: str) -> ActionResult:
        return self.executor.execute(Action(
            key=key, kind="browser_fill", reversible=True,
            run=lambda: self.driver.fill(selector, value), description=f"fill {selector}",
        ))

    # ── 되돌릴 수 없는 쓰기 (게이트·멱등·감사) ─────────────────────────────────
    def click(self, selector: str, *, key: str, readback: str | None = None) -> ActionResult:
        return self._gated_write("browser_click", f"click {selector}", key,
                                 lambda: self.driver.click(selector), readback)

    def submit(self, selector: str = "form", *, key: str, readback: str | None = None) -> ActionResult:
        return self._gated_write("browser_submit", f"submit {selector}", key,
                                 lambda: self.driver.click(selector), readback)

    def _gated_write(
        self, kind: str, desc: str, key: str, run: Callable[[], Any], readback: str | None
    ) -> ActionResult:
        result = self.executor.execute(Action(key=key, kind=kind, reversible=False, run=run, description=desc))
        if result.status == "executed" and readback is not None:
            return replace(result, value=self.driver.text(readback))  # 행동 후 확인
        return result

    def approve_tray(self, approve: Callable[[Action], bool] | None = None) -> list[ActionResult]:
        """보류된 되돌릴 수 없는 쓰기를 끝에 한 번에 승인(6.4 확인 트레이)."""
        return self.executor.approve_tray(approve)


class PlaywrightDriver:  # pragma: no cover - 라이브 브라우저 필요
    """Playwright sync 페이지를 감싸는 어댑터. `pip install polyrus-agent[browser]` + 브라우저."""

    def __init__(self, page: object) -> None:
        self._page = page

    def goto(self, url: str) -> None:
        self._page.goto(url)  # type: ignore[attr-defined]

    def text(self, selector: str | None = None) -> str:
        if selector:
            return self._page.inner_text(selector)  # type: ignore[attr-defined]
        return self._page.content()  # type: ignore[attr-defined]

    def url(self) -> str:
        return self._page.url  # type: ignore[attr-defined]

    def click(self, selector: str) -> None:
        self._page.click(selector)  # type: ignore[attr-defined]

    def fill(self, selector: str, value: str) -> None:
        self._page.fill(selector, value)  # type: ignore[attr-defined]
