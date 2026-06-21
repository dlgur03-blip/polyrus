"""Polyrus 로컬 UI 서버 — 개인 도구, 의존성 0(stdlib http.server).

`polyrus serve` → 브라우저에서 선제질문 기획 UX를 바로 사용. 빌드 스텝 없음(자체 완결 HTML).
백엔드는 ProactivePlanner를 PlanDriver로 한 질문씩 구동(코어 그대로 재사용).
"""
from __future__ import annotations

import json
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from polyrus.memory import SkillStore
from polyrus.planner import ProactivePlanner
from polyrus.server.driver import PlanDriver, result_payload
from polyrus.server.page import INDEX_HTML
from polyrus.skeleton import REGISTRY, get_skeleton
from polyrus.skills_seed import ensure_skills_for

_SESSIONS: dict[str, PlanDriver] = {}


def _new_driver(domain: str) -> PlanDriver:
    wiki = SkillStore(":memory:", check_same_thread=False)  # 요청 스레드→planner 스레드 핸드오프
    ensure_skills_for(wiki, domain)
    planner = ProactivePlanner(get_skeleton(domain), wiki=wiki)  # 조사는 UX 끊김 방지 위해 off
    return PlanDriver(planner)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # 조용히 (개인 도구)
        pass

    # ── 라우팅 ──────────────────────────────────────────────────────────────
    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            return self._html(INDEX_HTML)
        if self.path == "/api/domains":
            return self._json({"domains": sorted(REGISTRY)})
        self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        body = self._read_json()
        if self.path == "/api/start":
            return self._start(body)
        if self.path == "/api/answer":
            return self._answer(body)
        self._json({"error": "not found"}, status=404)

    # ── 핸들러 ──────────────────────────────────────────────────────────────
    def _start(self, body: dict[str, Any]) -> None:
        domain = str(body.get("domain", "")).strip()
        if domain not in REGISTRY:
            return self._json({"error": f"미등록 도메인: {domain}"}, status=400)
        driver = _new_driver(domain)
        sid = uuid.uuid4().hex[:12]
        _SESSIONS[sid] = driver
        question = driver.start()
        self._json(self._step_or_done(sid, driver, question))

    def _answer(self, body: dict[str, Any]) -> None:
        sid = str(body.get("session", ""))
        driver = _SESSIONS.get(sid)
        if driver is None:
            return self._json({"error": "세션 없음 — 새로 시작하세요"}, status=400)
        question = driver.submit(str(body.get("answer", "")))
        self._json(self._step_or_done(sid, driver, question))

    def _step_or_done(self, sid: str, driver: PlanDriver, question: dict | None) -> dict[str, Any]:
        if question is not None:
            return {"session": sid, "question": question}
        if driver.error:
            return {"session": sid, "done": True, "error": driver.error}
        payload = result_payload(driver.result) if driver.result is not None else {"error": "결과 없음"}
        return {"session": sid, "done": True, "result": payload}

    # ── 응답 유틸 ────────────────────────────────────────────────────────────
    def _read_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}

    def _json(self, obj: dict[str, Any], *, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), Handler)


def serve(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = True) -> None:  # pragma: no cover
    import webbrowser

    httpd = make_server(host, port)
    url = f"http://{host}:{port}/"
    print(f"🌐 Polyrus UI → {url}  (Ctrl+C로 종료)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
    finally:
        httpd.server_close()
