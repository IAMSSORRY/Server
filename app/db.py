"""판정 / 모션 기록을 SQLite 에 남긴다. 파이썬 기본 sqlite3 만 쓴다.

모든 DB 작업은 스레드 하나(단일 워커 executor)에서 순서대로 돈다.
- 연결이 그 스레드 하나에서만 쓰이므로 잠금이 필요 없다.
- 쓰기는 큐에 넣고 기다리지 않는다(asyncio 루프를 막지 않는다). 실패하면 로그만 남긴다.
- 읽기도 같은 큐를 타므로, 앞서 넣은 쓰기가 반영된 뒤의 상태를 읽는다.

기록은 지우지 않는다. 통계 초기화는 현재 회차(run)를 닫고 새 회차를 여는 것이다.
"""

import asyncio
import json
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at REAL NOT NULL,
    ended_at REAL
);
CREATE TABLE IF NOT EXISTS judges (
    run_id INTEGER,
    id INTEGER,
    grade TEXT,
    confidence REAL,
    v_value REAL NULL,
    threshold REAL NULL,
    bbox TEXT NULL,          -- JSON [x, y, w, h]
    cam TEXT,
    ts REAL,
    roll_detected INTEGER NULL,
    PRIMARY KEY (run_id, id)
);
CREATE TABLE IF NOT EXISTS motions (
    run_id INTEGER,
    judge_id INTEGER NULL,
    approach_speed REAL,
    place_height REAL,
    roll_detected INTEGER,
    ts REAL
);
"""


def _bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def _number(value: float | None) -> float | int | None:
    """REAL 컬럼에서 읽은 182.0 을 원래대로 182 로 돌린다 (재시작 전후로 응답 모양이 같게)."""
    return int(value) if isinstance(value, float) and value.is_integer() else value


def history_entry(row: sqlite3.Row) -> dict:
    """judges 행 → GET /history 와 snapshot recent 의 항목 형식."""
    return {
        "id": row["id"],
        "grade": row["grade"],
        "confidence": row["confidence"],
        "v_value": _number(row["v_value"]),
        "threshold": _number(row["threshold"]),
        "ts": row["ts"],
        "roll_detected": _bool(row["roll_detected"]),
    }


class Store:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlite")
        self._conn: sqlite3.Connection | None = None

    # ---- 실행 ----

    async def call(self, fn: Callable[..., Any], *args) -> Any:
        """DB 스레드에서 fn(conn, *args) 를 돌리고 결과를 기다린다 (읽기용)."""
        return await asyncio.get_running_loop().run_in_executor(self._executor, self._run, fn, args)

    def submit(self, fn: Callable[..., Any], *args) -> None:
        """DB 스레드에 쓰기를 넣고 기다리지 않는다. 실패는 로그만 남긴다."""
        future = self._executor.submit(self._run, fn, args)
        future.add_done_callback(self._log_failure)

    def _run(self, fn, args):
        return fn(self._conn, *args)

    @staticmethod
    def _log_failure(future) -> None:
        if future.exception() is not None:
            log.error("DB 쓰기 실패 (메모리 상태와 브로드캐스트는 계속된다): %s", future.exception())

    # ---- 시작 / 종료 ----

    async def open(self) -> tuple[int, list[dict]]:
        """DB 를 열고 이어 쓸 회차를 정한다. 돌려주는 것: (회차 id, 그 회차의 판정 이력)."""
        return await asyncio.get_running_loop().run_in_executor(self._executor, self._open)

    def _open(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        self._conn = conn

        row = conn.execute("SELECT id FROM runs WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            run_id = conn.execute("INSERT INTO runs (started_at) VALUES (?)", (time.time(),)).lastrowid
            conn.commit()
            log.info("새 회차 %d 시작 (%s)", run_id, self._path)
            return run_id, []
        run_id = row["id"]
        rows = conn.execute("SELECT * FROM judges WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        log.info("회차 %d 이어서 사용: 판정 %d건 복원 (%s)", run_id, len(rows), self._path)
        return run_id, [history_entry(r) for r in rows]

    def close(self) -> None:
        def _close(conn):
            if conn is not None:
                conn.close()
        self._executor.submit(self._run, _close, ()).result(timeout=5)
        self._executor.shutdown(wait=True)

    # ---- 쓰기 (DB 스레드에서 실행) ----

    @staticmethod
    def insert_judge(conn, run_id: int, judge: dict) -> None:
        conn.execute(
            "INSERT INTO judges (run_id, id, grade, confidence, v_value, threshold, bbox, cam, ts, roll_detected)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (run_id, judge["id"], judge["grade"], judge["confidence"], judge["v_value"], judge["threshold"],
             json.dumps(judge["bbox"]), judge["cam"], judge["ts"]),
        )
        conn.commit()

    @staticmethod
    def insert_motion(conn, run_id: int, judge_id: int | None, motion: dict) -> None:
        conn.execute(
            "INSERT INTO motions (run_id, judge_id, approach_speed, place_height, roll_detected, ts)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, judge_id, motion["approach_speed"], motion["place_height"],
             int(motion["roll_detected"]), motion["ts"]),
        )
        if judge_id is not None:
            conn.execute(
                "UPDATE judges SET roll_detected = ? WHERE run_id = ? AND id = ?",
                (int(motion["roll_detected"]), run_id, judge_id),
            )
        conn.commit()

    @staticmethod
    def start_run(conn, ending_run_id: int, new_run_id: int, now: float) -> None:
        conn.execute("UPDATE runs SET ended_at = ? WHERE id = ?", (now, ending_run_id))
        conn.execute("INSERT INTO runs (id, started_at) VALUES (?, ?)", (new_run_id, now))
        conn.commit()

    # ---- 읽기 (DB 스레드에서 실행) ----

    @staticmethod
    def next_run_id(conn) -> int:
        return conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM runs").fetchone()[0]

    @staticmethod
    def list_runs(conn) -> list[dict]:
        rows = conn.execute(
            """
            SELECT r.id, r.started_at, r.ended_at,
                   COALESCE(SUM(j.grade = '상'), 0) AS high,
                   COALESCE(SUM(j.grade = '중'), 0) AS mid,
                   COUNT(j.id) AS total
            FROM runs r LEFT JOIN judges j ON j.run_id = r.id
            GROUP BY r.id ORDER BY r.id DESC
            """
        ).fetchall()
        return [
            {"id": r["id"], "started_at": r["started_at"], "ended_at": r["ended_at"],
             "stats": {"상": r["high"], "중": r["mid"], "total": r["total"]}}
            for r in rows
        ]

    @staticmethod
    def run_exists(conn, run_id: int) -> bool:
        return conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone() is not None

    @staticmethod
    def history(conn, run_id: int) -> list[dict]:
        rows = conn.execute("SELECT * FROM judges WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
        return [history_entry(r) for r in rows]

    @staticmethod
    def export_rows(conn, run_id: int | None) -> list[sqlite3.Row]:
        if run_id is None:
            return conn.execute("SELECT * FROM judges ORDER BY run_id, id").fetchall()
        return conn.execute("SELECT * FROM judges WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
