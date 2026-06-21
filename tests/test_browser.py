"""브라우저 행동 안전(6.2) — 읽기우선·쓰기게이팅. fake 드라이버(네트워크 0)."""
from __future__ import annotations

from polyrus.actions import ActionExecutor
from polyrus.browser import SafeBrowser
from polyrus.store import Store


class FakeDriver:
    def __init__(self, pages: dict | None = None) -> None:
        self._url = "about:blank"
        self._pages = pages or {}
        self.actions: list[tuple[str, str]] = []  # (op, arg)
        self.fields: dict[str, str] = {}

    def goto(self, url: str) -> None:
        self.actions.append(("goto", url))
        self._url = url

    def text(self, selector=None) -> str:
        return self._pages.get(selector or self._url, "본문")

    def url(self) -> str:
        return self._url

    def click(self, selector: str) -> None:
        self.actions.append(("click", selector))

    def fill(self, selector: str, value: str) -> None:
        self.actions.append(("fill", selector))
        self.fields[selector] = value


# ── 읽기: 자율(게이트 없음) ────────────────────────────────────────────────────
def test_read_is_autonomous() -> None:
    d = FakeDriver({"#title": "제목"})
    b = SafeBrowser(d)  # 승인자 없어도
    assert b.goto("https://x.com") == "https://x.com"
    assert b.read("#title") == "제목"   # 읽기는 막히지 않음


def test_cross_check_read() -> None:
    d = FakeDriver({"#p": "값"})
    text, consistent = SafeBrowser(d).cross_check_read("#p", n=3)
    assert text == "값" and consistent is True


# ── 되돌릴 수 있는 쓰기(fill): 자동, 감사 ───────────────────────────────────────
def test_fill_auto_executes() -> None:
    d = FakeDriver()
    r = SafeBrowser(d).fill("#email", "a@b.c", key="f1")
    assert r.status == "executed" and d.fields["#email"] == "a@b.c"


# ── 되돌릴 수 없는 쓰기(click/submit): 게이트 ──────────────────────────────────
def test_click_defers_without_approver() -> None:
    d = FakeDriver()
    b = SafeBrowser(d)  # 승인자 없음 → 안전한 미완료(보류)
    r = b.click("#buy", key="c1")
    assert r.status == "deferred"
    assert ("click", "#buy") not in d.actions  # 실행 안 됨


def test_click_executes_with_approval() -> None:
    d = FakeDriver()
    b = SafeBrowser(d, approve=lambda a: True)
    r = b.click("#buy", key="c1")
    assert r.status == "executed" and ("click", "#buy") in d.actions


def test_submit_gated_and_idempotent() -> None:
    d = FakeDriver()
    b = SafeBrowser(d, approve=lambda a: True)
    b.submit("#form", key="s1")
    r2 = b.submit("#form", key="s1")  # 재시도
    assert r2.status == "skipped_idempotent"
    assert d.actions.count(("click", "#form")) == 1  # 제출은 한 번만(중복 결제 방지)


def test_write_readback_confirms() -> None:
    # 행동 후 확인: 클릭 뒤 결과 영역을 읽어 돌려준다.
    d = FakeDriver({"#result": "주문 완료"})
    r = SafeBrowser(d, approve=lambda a: True).click("#buy", key="c1", readback="#result")
    assert r.value == "주문 완료"


def test_tray_approves_deferred_writes() -> None:
    d = FakeDriver()
    b = SafeBrowser(d)  # 승인자 없음 → 트레이
    b.click("#a", key="a")
    b.click("#b", key="b")
    assert d.actions == []  # 둘 다 보류
    results = b.approve_tray(approve=lambda a: True)
    assert all(r.status == "executed" for r in results)
    assert ("click", "#a") in d.actions and ("click", "#b") in d.actions


def test_writes_audited_to_store() -> None:
    d = FakeDriver()
    store = Store(":memory:")
    SafeBrowser(d, store=store, executor=ActionExecutor(store, approve=lambda a: True)).click("#x", key="k")
    assert any(row["key"] == "k" and row["status"] == "executed" for row in store.action_rows())
