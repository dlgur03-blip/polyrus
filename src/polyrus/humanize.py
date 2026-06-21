"""검증 실패를 *사람 말 한 줄*로 — UX 핵심. pytest 덤프 대신 "기대 0인데 35 나옴".

규칙 기반(결정적)이 기본. model을 주면 LLM이 더 매끄럽게 다듬는다(주입식). 이미 사람말인
재무·검색 검증기 detail은 그대로 통과시킨다.
"""
from __future__ import annotations

import re

from polyrus.models import ModelClient

_ASSERT = re.compile(r"assert\s+(.+?)\s*==\s*(.+?)(?:\n|$)")
_FAILED = re.compile(r"FAILED\s+\S*::(\w+)")
_ERROR = re.compile(r"\b(\w*Error):\s*(.+?)(?:\n|$)")


def humanize(detail: str, *, model: ModelClient | None = None) -> str:
    """검증기 detail → 사람이 읽을 한 줄. model 있으면 LLM 다듬기."""
    plain = _rule_based(detail)
    if model is not None:
        try:
            out = model.complete(
                f"다음 검증 실패를 비개발자도 이해할 한국어 한 문장으로 바꿔라(코드·트레이스백 금지):\n{detail}",
                system="너는 친절한 설명가다. 한 줄로, 무엇이 왜 틀렸는지만.",
                temperature=0.0,
            ).strip()
            if out:
                return out
        except Exception:  # noqa: BLE001 - LLM 실패 시 규칙 기반으로 폴백
            pass
    return plain


def _rule_based(detail: str) -> str:
    text = detail.strip()
    if not text:
        return "검증 실패"
    # 1) 단언 실패: assert 35 == 0  →  기대 0인데 실제 35
    m = _ASSERT.search(text)
    if m:
        actual, expected = m.group(1).strip(), m.group(2).strip()
        return f"기대값은 {expected}인데 실제로 {actual}이(가) 나왔어요"
    # 2) 예외/크래시: ZeroDivisionError: division by zero
    m = _ERROR.search(text)
    if m:
        return f"실행 중 오류({m.group(1)}): {m.group(2).strip()[:80]}"
    # 3) 실패한 테스트 이름들
    fails = _FAILED.findall(text)
    if fails:
        return f"통과 못 한 검사: {', '.join(dict.fromkeys(fails))}"
    # 4) 이미 사람말인 detail(재무 '재계산 …≠…', 검색 '출처 불일치…', 환각 '… 없음') → 앞줄 그대로
    first = text.splitlines()[0]
    # 접두 태그([t1_execution] 등) 제거
    first = re.sub(r"^\[[\w.]+\]\s*", "", first)
    return first[:120]
