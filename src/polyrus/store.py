from __future__ import annotations

import sqlite3
from types import TracebackType

from polyrus.types import CorpusRecord, LedgerItem

_SCHEMA = """
CREATE TABLE IF NOT EXISTS corpus (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    item_id     TEXT NOT NULL,
    claim_id    TEXT NOT NULL,
    tier        TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    confidence  REAL NOT NULL,
    reliability REAL NOT NULL,
    locality    TEXT NOT NULL,
    override    TEXT
);
CREATE TABLE IF NOT EXISTS ledger_items (
    task_id          TEXT NOT NULL,
    item_id          TEXT NOT NULL,
    goal             TEXT NOT NULL,
    closed           INTEGER NOT NULL,
    escalated        INTEGER NOT NULL,
    confidence       REAL NOT NULL,
    escalation_reason TEXT NOT NULL,
    PRIMARY KEY (task_id, item_id)
);
CREATE TABLE IF NOT EXISTS actions (
    key    TEXT PRIMARY KEY,
    kind   TEXT NOT NULL,
    status TEXT NOT NULL
);
"""


class Store:
    """SQLite 영속화 — 보정 코퍼스(5.4 해자 플라이휠) + 원장 항목 결과.

    코퍼스는 *리댁션된* 레코드만 담는다(id·티어·판정, 원문/비밀 없음 — 6.3 경계).
    이 코퍼스가 쌓여 검증기 신뢰도 곡선(보정)이 되는 게 진짜 해자다.
    """

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── 쓰기 ──────────────────────────────────────────────────────────────────
    def append_corpus(self, records: list[CorpusRecord]) -> None:
        self._conn.executemany(
            "INSERT INTO corpus "
            "(task_id,item_id,claim_id,tier,verdict,confidence,reliability,locality,override) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (r.task_id, r.item_id, r.claim_id, r.tier, r.verdict,
                 r.confidence, r.reliability, r.locality, r.override)
                for r in records
            ],
        )
        self._conn.commit()

    def save_item(self, task_id: str, item: LedgerItem) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO ledger_items "
            "(task_id,item_id,goal,closed,escalated,confidence,escalation_reason) "
            "VALUES (?,?,?,?,?,?,?)",
            (task_id, item.id, item.goal, int(item.closed), int(item.escalated),
             item.confidence, item.escalation_reason),
        )
        self._conn.commit()

    def set_override(self, corpus_row_id: int, override: str) -> None:
        """사람의 사후 정정 라벨(검증기 위양성/위음성 신호). 보정의 ground-truth가 된다."""
        self._conn.execute("UPDATE corpus SET override=? WHERE id=?", (override, corpus_row_id))
        self._conn.commit()

    # ── 읽기 ──────────────────────────────────────────────────────────────────
    def corpus_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM corpus").fetchone()[0])

    def reliability_summary(self) -> dict[str, dict[str, int]]:
        """티어별 판정 분포 — 보정 곡선의 씨앗. (override 누적 시 위양성/위음성으로 발전.)"""
        rows = self._conn.execute(
            "SELECT tier, verdict, COUNT(*) c FROM corpus GROUP BY tier, verdict"
        ).fetchall()
        out: dict[str, dict[str, int]] = {}
        for row in rows:
            out.setdefault(row["tier"], {})[row["verdict"]] = row["c"]
        return out

    def corpus_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM corpus ORDER BY id").fetchall()

    # ── 액션 멱등성/감사 (6.4 — 되돌릴 수 없는 행동 방어) ──────────────────────
    def action_seen(self, key: str) -> bool:
        return self._conn.execute("SELECT 1 FROM actions WHERE key=?", (key,)).fetchone() is not None

    def action_record(self, key: str, kind: str, status: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO actions (key,kind,status) VALUES (?,?,?)", (key, kind, status)
        )
        self._conn.commit()

    def action_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM actions ORDER BY key").fetchall()

    def items(self, task_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM ledger_items WHERE task_id=? ORDER BY item_id", (task_id,)
        ).fetchall()

    # ── 수명주기 ──────────────────────────────────────────────────────────────
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
