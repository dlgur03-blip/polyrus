"""PlanDriver — ProactivePlanner를 *한 질문씩* HTTP로 구동(기존 run() 그대로 재사용).

planner.run(provider)는 동기 루프라, UI처럼 한 번에 한 질문만 주고받으려면 스레드+큐로 감싼다:
백그라운드 스레드가 run()을 돌리고, AnswerProvider가 큐에서 답을 *기다린다*. 서버는
질문 큐에서 다음 질문을 꺼내 보여주고, 사용자가 답하면 답 큐에 넣어 스레드를 재개한다.

이렇게 하면 회피-회복·조사·검증 로직을 *하나도 안 고치고* 그대로 쓴다.
"""
from __future__ import annotations

import queue
import re
import threading
from typing import Any

from polyrus.planner import ProactivePlanner
from polyrus.skeleton import SkeletonStep

_PAREN = re.compile(r"\(([^)]*)\)")


def question_options(prompt: str) -> list[str]:
    """질문 문구의 괄호 예시/선택지를 칩으로 — '(예: 문의하기·구매·예약)' → [문의하기,구매,예약]."""
    m = _PAREN.findall(prompt)
    if not m:
        return []
    inner = m[-1]
    inner = re.sub(r"^\s*예\s*[:：]\s*", "", inner)
    parts = re.split(r"\s*[/·,]\s*", inner)
    opts = [p.strip() for p in parts if p.strip() and len(p.strip()) <= 20]
    return opts if len(opts) >= 2 else []


def step_view(step: SkeletonStep, *, recovery: bool) -> dict[str, Any]:
    prompt = step.question or step.micro_question or step.title
    return {
        "id": step.id,
        "title": step.title,
        "prompt": prompt,
        "options": question_options(prompt),
        "recovery": recovery,
    }


def result_payload(result: Any) -> dict[str, Any]:
    """PlanResult → UI용 JSON(브리프·검증·미해결·난이도경고). 검증은 best-effort."""
    verds: list[dict[str, Any]] = []
    try:
        agg = result.verify({})
        verds = [{"tier": r.tier.value, "verdict": r.verdict.value, "detail": r.detail}
                 for r in agg.results]
    except Exception:  # noqa: BLE001 - 검증 실패해도 브리프는 보여준다
        pass
    return {
        "domain": result.domain,
        "brief": result.brief,
        "blockers": result.blockers,
        "issues": result.question_issues,
        "asked": result.asked,
        "defaulted": result.defaulted,
        "verification": verds,
    }


class PlanDriver:
    """한 기획 세션. start()→첫 질문, submit(답)→다음 질문 또는 완료."""

    _DONE = object()

    def __init__(self, planner: ProactivePlanner, *, timeout: float = 120.0) -> None:
        self.planner = planner
        self.timeout = timeout
        self._q_question: queue.Queue = queue.Queue()
        self._q_answer: queue.Queue = queue.Queue()
        self.result: Any | None = None
        self.error: str = ""
        self._thread: threading.Thread | None = None

    # ── provider: 큐에서 답을 기다린다(스레드 안에서 호출됨) ───────────────────────
    def _provider(self) -> Any:
        driver = self

        class _P:
            def ask(self, step: SkeletonStep, *, recovery: bool = False) -> str:
                driver._q_question.put((step, recovery))
                return str(driver._q_answer.get())

        return _P()

    def _run(self) -> None:
        try:
            self.result = self.planner.run(self._provider())
        except Exception as e:  # noqa: BLE001 - UI엔 친절 메시지로
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self._q_question.put((PlanDriver._DONE, None))

    # ── 공개 API ──────────────────────────────────────────────────────────────
    def start(self) -> dict[str, Any] | None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self._next()

    def submit(self, answer: str) -> dict[str, Any] | None:
        if self.done:
            return None
        self._q_answer.put(answer)
        return self._next()

    def _next(self) -> dict[str, Any] | None:
        try:
            step, recovery = self._q_question.get(timeout=self.timeout)
        except queue.Empty:
            self.error = self.error or "시간 초과"
            return None
        if step is PlanDriver._DONE:
            return None  # 완료 — result/ error 참조
        return step_view(step, recovery=recovery)

    @property
    def done(self) -> bool:
        return self.result is not None or bool(self.error)
