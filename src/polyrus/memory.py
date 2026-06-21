"""검증된 스킬 메모리 (자기개선축) — Polyrus식 학습 루프.

Hermes는 *완료한 일을 다* 스킬로 저장한다(틀린 접근도 저장→재사용→틀린 채로 빨라짐).
Polyrus는 **검증을 통과한 해법만** 저장한다 → *검증된 기억*. 틀린 걸 학습하지 않는다.

흐름: 작업이 No-Pass 검증을 통과 → 그 해법을 스킬로 기록 → 다음 유사 작업에서 recall →
컨텍스트 엔진으로 주입(v2 B의 Select). 쓸수록 똑똑해지되, 검증 게이트가 오류 증폭을 막는다.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from types import TracebackType

from polyrus.scoring import ngram_score

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    goal       TEXT NOT NULL,
    solution   TEXT NOT NULL,
    confidence REAL NOT NULL,
    uses       INTEGER NOT NULL DEFAULT 0
);
"""

# 큐레이션 루프(§4-1)용 추가 컬럼 — 기존 DB에도 안전하게 더한다(ALTER, 멱등).
# verified=1 = HOT(위키가 DEFAULT로 서빙). 미사용은 강등(verified=0=COLD).
_MIGRATIONS = {
    "verified": "ALTER TABLE skills ADD COLUMN verified INTEGER NOT NULL DEFAULT 1",
    "source": "ALTER TABLE skills ADD COLUMN source TEXT NOT NULL DEFAULT ''",
}


@dataclass
class Skill:
    id: int
    kind: str
    goal: str
    solution: str
    confidence: float
    uses: int = 0
    verified: bool = True
    source: str = ""  # '' = 작업에서 학습됨, 'absorbed:<스킬>' = 전문가 스킬 흡수본


class SkillStore:
    """검증된 해법만 담는 자기개선 메모리(SQLite). 영속·교차세션."""

    def __init__(self, path: str = ":memory:", *, check_same_thread: bool = True) -> None:
        # check_same_thread=False: 한 스레드에서 만들고 다른 스레드로 *핸드오프*해 쓸 때(UI 서버).
        # 동시 접근은 아니라 안전 — PlanDriver가 단일 planner 스레드에서만 만진다.
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=check_same_thread)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """기존 DB에 큐레이션 컬럼을 멱등 추가(없을 때만)."""
        existing = {r["name"] for r in self._conn.execute("PRAGMA table_info(skills)").fetchall()}
        for col, ddl in _MIGRATIONS.items():
            if col not in existing:
                self._conn.execute(ddl)

    def record(
        self,
        *,
        kind: str,
        goal: str,
        solution: str,
        confidence: float,
        verified: bool = True,
        source: str = "",
    ) -> int:
        """검증 통과한 해법/흡수한 전문가 스킬만 기록한다(호출자가 '검증됨'을 보장).

        verified=True면 위키가 DEFAULT로 서빙(HOT). source는 출처(흡수본이면 'absorbed:...').
        반환: 새 스킬 id.
        """
        cur = self._conn.execute(
            "INSERT INTO skills (kind, goal, solution, confidence, verified, source) "
            "VALUES (?,?,?,?,?,?)",
            (kind, goal, solution, confidence, int(verified), source),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0])

    def verified_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM skills WHERE verified=1").fetchone()[0])

    def all(self) -> list[Skill]:
        return [self._row(r) for r in self._conn.execute("SELECT * FROM skills ORDER BY id").fetchall()]

    def recall(
        self, query: str, *, k: int = 3, min_confidence: float = 0.0, verified_only: bool = False
    ) -> list[Skill]:
        """질의(작업 목표)와 관련된 스킬 상위 k개. ngram(한글 robust)로 관련도. 사용 시 uses 누적."""
        clause = "confidence >= ?" + (" AND verified=1" if verified_only else "")
        rows = [
            self._row(r)
            for r in self._conn.execute(f"SELECT * FROM skills WHERE {clause}", (min_confidence,)).fetchall()
        ]
        scored = [(ngram_score(query, f"{s.goal} {s.solution}"), s) for s in rows]
        top = [s for score, s in sorted(scored, key=lambda x: x[0], reverse=True) if score > 0][:k]
        for s in top:  # 사용 횟수 누적(어떤 스킬이 값을 하는지 신호 — 큐레이션 입력)
            self._conn.execute("UPDATE skills SET uses = uses + 1 WHERE id = ?", (s.id,))
        self._conn.commit()
        return top

    def recall_default(self, skill_name: str) -> Skill | None:
        """DEFAULT 단계용: 위키에서 *검증된* 스킬을 이름(goal)으로 **정확** 당겨온다(uses 누적).

        퍼지 recall과 달리 정확 매칭 — DEFAULT 단계는 어떤 스킬을 원하는지 정확히 알기 때문에
        엉뚱한 스킬이 섞이면 안 된다(무관 스킬이 전문가 지식 자리를 차지하는 사고 방지).
        """
        row = self._conn.execute(
            "SELECT * FROM skills WHERE goal=? AND verified=1 ORDER BY confidence DESC, id DESC LIMIT 1",
            (skill_name,),
        ).fetchone()
        if row is None:
            return None
        self._conn.execute("UPDATE skills SET uses = uses + 1 WHERE id = ?", (row["id"],))
        self._conn.commit()
        return self._row(row)

    # ── 큐레이션 루프(§4-1): 검증 통과 → 승격, 미사용 → 강등 ──────────────────────
    def promote(self, skill_id: int) -> None:
        """검증을 통과시킨 스킬을 HOT로 승격(서빙 대상)하고 사용 신호를 올린다."""
        self._conn.execute(
            "UPDATE skills SET verified=1, uses=uses+1 WHERE id=?", (skill_id,)
        )
        self._conn.commit()

    def demote(self, skill_id: int) -> None:
        """스킬을 COLD로 강등(위키 서빙 제외 — 삭제는 아님, 흔적 보존)."""
        self._conn.execute("UPDATE skills SET verified=0 WHERE id=?", (skill_id,))
        self._conn.commit()

    def curate(self, *, min_uses: int = 1) -> int:
        """주기 큐레이션: 한 번도 안 쓰인(uses<min_uses) 검증 스킬을 COLD로 강등.

        '나중에 다시 안 볼 건 HOT에 두지 마라'의 위키판. 반환: 강등된 수.
        """
        cur = self._conn.execute(
            "UPDATE skills SET verified=0 WHERE verified=1 AND uses < ?", (min_uses,)
        )
        self._conn.commit()
        return int(cur.rowcount or 0)

    def _row(self, r: sqlite3.Row) -> Skill:
        return Skill(
            r["id"], r["kind"], r["goal"], r["solution"], r["confidence"], r["uses"],
            bool(r["verified"]), r["source"],
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SkillStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
