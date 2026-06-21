"""로컬 UI — PlanDriver(한 질문씩 구동) + 실제 HTTP 왕복(stdlib 서버)."""
from __future__ import annotations

import json
import threading
import urllib.request

from polyrus.memory import SkillStore
from polyrus.planner import ProactivePlanner
from polyrus.server.app import make_server
from polyrus.server.driver import PlanDriver, question_options, result_payload
from polyrus.skeleton import DIGEST, HOMEPAGE
from polyrus.skills_seed import ensure_skills_for


def _planner(skeleton) -> ProactivePlanner:
    w = SkillStore(":memory:", check_same_thread=False)  # 드라이버 스레드로 핸드오프
    ensure_skills_for(w, skeleton.domain)
    return ProactivePlanner(skeleton, wiki=w)


# step.id 기준(드라이버가 step.id를 노출). 실제 답이라 회피로 안 잡힘.
_ANS = {
    "purpose": "문의하기", "references": "stripe.com", "anti_slop": "정돈된",
    "accent": "히어로", "palette": "신뢰", "features": "문의폼",
    "source": "python LLM", "criteria": "스타 급상승", "schedule": "매일 아침 9시",
    "channel": "텔레그램", "length": "짧게",
}


def _drive(driver: PlanDriver) -> None:
    q = driver.start()
    guard = 0
    while q is not None and guard < 20:
        guard += 1
        q = driver.submit(_ANS.get(q["id"], "기본값"))


# ── option 파싱 ────────────────────────────────────────────────────────────────
def test_question_options() -> None:
    assert question_options("행동은? (예: 문의하기·구매·예약)") == ["문의하기", "구매", "예약"]
    assert question_options("골라 (히어로 / 기능 / CTA)") == ["히어로", "기능", "CTA"]
    assert question_options("자유롭게 적어주세요") == []


# ── PlanDriver: 한 질문씩 → 완료 ─────────────────────────────────────────────────
def test_driver_homepage_completes() -> None:
    d = PlanDriver(_planner(HOMEPAGE))
    _drive(d)
    assert d.done and d.result is not None and not d.error
    assert "문의하기" in d.result.brief


def test_driver_digest_completes_and_serializes() -> None:
    d = PlanDriver(_planner(DIGEST))
    _drive(d)
    payload = result_payload(d.result)
    assert payload["domain"] == "digest"
    # 검증(스케줄 cron 등)이 결과에 직렬화됨.
    assert any("cron" in v["detail"] for v in payload["verification"])


def test_driver_recovery_resurfaces_question() -> None:
    d = PlanDriver(_planner(HOMEPAGE))
    q = d.start()
    assert q["id"] == "purpose"
    q2 = d.submit("아무거나")          # 회피 → 회복 질문으로 같은 단계 재등장
    assert q2["id"] == "purpose" and q2["recovery"] is True


# ── 실제 HTTP 왕복 ──────────────────────────────────────────────────────────────
def _post(base: str, path: str, body: dict) -> dict:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def test_http_end_to_end() -> None:
    httpd = make_server("127.0.0.1", 0)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        base = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(base + "/api/domains", timeout=10) as r:
            assert "homepage" in json.loads(r.read().decode())["domains"]
        # 페이지 서빙.
        with urllib.request.urlopen(base + "/", timeout=10) as r:
            assert b"Polyrus" in r.read()
        # 기획 한 바퀴.
        resp = _post(base, "/api/start", {"domain": "homepage"})
        sid = resp["session"]
        guard = 0
        while "question" in resp and guard < 20:
            guard += 1
            resp = _post(base, "/api/answer", {"session": sid, "answer": _ANS.get(resp["question"]["id"], "기본값")})
        assert resp.get("done") and "문의하기" in resp["result"]["brief"]
    finally:
        httpd.shutdown()
        httpd.server_close()
