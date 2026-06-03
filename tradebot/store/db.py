"""SQLite persistence: snapshots, trades, experiences, lessons.

The store is shared by paper and live modes, so the learned experience and
lessons carry over from paper trading into the real-money window.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tradebot.models import Experience, Lesson, ManagerDecision, Mode, Side, Trade

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    market_id TEXT, yes_price REAL, ts TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT, token_id TEXT, question TEXT, side TEXT, is_yes INTEGER,
    entry_price REAL, size REAL, mode TEXT, status TEXT, pnl REAL,
    won INTEGER, resolved_yes INTEGER, brain_score REAL, edge REAL,
    features TEXT, opened_at TEXT, resolved_at TEXT,
    kind TEXT DEFAULT 'resolve', exit_price REAL
);
CREATE TABLE IF NOT EXISTS experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    features TEXT, edge REAL, size REAL, brain_score REAL,
    won INTEGER, pnl REAL, mode TEXT, is_yes INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER, category TEXT, cause TEXT, recommendation TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS manager_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT, question TEXT, approved INTEGER, reason TEXT,
    model_prob REAL, brain_score REAL, edge REAL, is_yes INTEGER,
    rss_sentiment REAL, reddit_sentiment REAL, created_at TEXT
);
"""


def _b(v: Optional[bool]) -> Optional[int]:
    return None if v is None else int(v)


class Store:
    def __init__(self, db_path: Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # WAL + busy_timeout make concurrent bot/settle/server access robust.
        self.conn = sqlite3.connect(str(db_path), timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a DB was first created (old DBs)."""
        for col, ddl in (("kind", "TEXT DEFAULT 'resolve'"), ("exit_price", "REAL")):
            try:
                self.conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass  # column already exists
        try:  # the brain now learns the traded side, so experiences carry is_yes
            self.conn.execute("ALTER TABLE experiences ADD COLUMN is_yes INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass

    # --- snapshots (for price-move anomaly detection) ---
    def last_yes_price(self, market_id: str) -> Optional[float]:
        row = self.conn.execute(
            "SELECT yes_price FROM snapshots WHERE market_id=? ORDER BY ts DESC LIMIT 1",
            (market_id,),
        ).fetchone()
        return None if row is None else float(row["yes_price"])

    def record_snapshot(self, market_id: str, yes_price: float) -> None:
        self.conn.execute(
            "INSERT INTO snapshots(market_id, yes_price, ts) VALUES (?,?,?)",
            (market_id, yes_price, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    # --- trades ---
    def save_trade(self, t: Trade) -> int:
        cur = self.conn.execute(
            """INSERT INTO trades(market_id, token_id, question, side, is_yes,
               entry_price, size, mode, status, pnl, won, resolved_yes,
               brain_score, edge, features, opened_at, resolved_at, kind, exit_price)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t.market_id, t.token_id, t.question, t.side.value, int(t.is_yes),
                t.entry_price, t.size, t.mode.value, t.status, t.pnl, _b(t.won),
                _b(t.resolved_yes), t.brain_score, t.edge, json.dumps(t.features),
                t.opened_at.isoformat(), t.resolved_at.isoformat() if t.resolved_at else None,
                t.kind, t.exit_price,
            ),
        )
        self.conn.commit()
        t.id = int(cur.lastrowid)
        return t.id

    def update_trade(self, t: Trade) -> None:
        self.conn.execute(
            """UPDATE trades SET status=?, pnl=?, won=?, resolved_yes=?,
               resolved_at=?, kind=?, exit_price=? WHERE id=?""",
            (
                t.status, t.pnl, _b(t.won), _b(t.resolved_yes),
                t.resolved_at.isoformat() if t.resolved_at else None,
                t.kind, t.exit_price, t.id,
            ),
        )
        self.conn.commit()

    def _row_to_trade(self, r: sqlite3.Row) -> Trade:
        keys = r.keys()
        return Trade(
            id=r["id"], market_id=r["market_id"], token_id=r["token_id"],
            question=r["question"], side=Side(r["side"]), is_yes=bool(r["is_yes"]),
            entry_price=r["entry_price"], size=r["size"], mode=Mode(r["mode"]),
            status=r["status"], pnl=r["pnl"],
            won=None if r["won"] is None else bool(r["won"]),
            resolved_yes=None if r["resolved_yes"] is None else bool(r["resolved_yes"]),
            brain_score=r["brain_score"], edge=r["edge"],
            features=json.loads(r["features"]) if r["features"] else [],
            opened_at=datetime.fromisoformat(r["opened_at"]),
            resolved_at=datetime.fromisoformat(r["resolved_at"]) if r["resolved_at"] else None,
            kind=(r["kind"] if "kind" in keys and r["kind"] else "resolve"),
            exit_price=(r["exit_price"] if "exit_price" in keys else None),
        )

    def open_trades(self, mode: Optional[Mode] = None) -> list[Trade]:
        if mode:
            rows = self.conn.execute(
                "SELECT * FROM trades WHERE status='open' AND mode=?", (mode.value,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM trades WHERE status='open'").fetchall()
        return [self._row_to_trade(r) for r in rows]

    def resolved_trades(self) -> list[Trade]:
        rows = self.conn.execute("SELECT * FROM trades WHERE status='resolved'").fetchall()
        return [self._row_to_trade(r) for r in rows]

    def open_exposure(self, mode: Mode) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(entry_price*size),0) AS e FROM trades "
            "WHERE status='open' AND mode=?", (mode.value,),
        ).fetchone()
        return float(row["e"])

    def realized_pnl(self, mode: Mode) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(pnl),0) AS p FROM trades WHERE status='resolved' AND mode=?",
            (mode.value,),
        ).fetchone()
        return float(row["p"])

    # --- experiences (brain training data; mode-agnostic) ---
    def save_experience(self, e: Experience) -> None:
        self.conn.execute(
            "INSERT INTO experiences(features, edge, size, brain_score, won, pnl, mode, is_yes)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                json.dumps(e.features), e.edge, e.size, e.brain_score, int(e.won),
                e.pnl, e.mode.value, int(e.is_yes),
            ),
        )
        self.conn.commit()

    def load_experiences(self) -> list[Experience]:
        rows = self.conn.execute("SELECT * FROM experiences").fetchall()
        out: list[Experience] = []
        for r in rows:
            keys = r.keys()
            out.append(
                Experience(
                    features=json.loads(r["features"]), edge=r["edge"], size=r["size"],
                    brain_score=r["brain_score"], won=bool(r["won"]), pnl=r["pnl"],
                    mode=Mode(r["mode"]),
                    is_yes=bool(r["is_yes"]) if "is_yes" in keys and r["is_yes"] is not None else True,
                )
            )
        return out

    # --- manager decisions (BrainManager audit trail) ---
    def save_manager_decision(self, d: ManagerDecision) -> None:
        self.conn.execute(
            "INSERT INTO manager_decisions(market_id, question, approved, reason, model_prob,"
            " brain_score, edge, is_yes, rss_sentiment, reddit_sentiment, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                d.market_id, d.question, int(d.approved), d.reason, d.model_prob,
                d.brain_score, d.edge, int(d.is_yes), d.rss_sentiment, d.reddit_sentiment,
                d.created_at.isoformat(),
            ),
        )
        self.conn.commit()

    # --- lessons ---
    def save_lesson(self, lesson: Lesson) -> None:
        self.conn.execute(
            "INSERT INTO lessons(trade_id, category, cause, recommendation, text)"
            " VALUES (?,?,?,?,?)",
            (lesson.trade_id, lesson.category, lesson.cause, lesson.recommendation, lesson.text),
        )
        self.conn.commit()

    def recent_lessons(self, limit: int = 8) -> list[Lesson]:
        rows = self.conn.execute(
            "SELECT * FROM lessons ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            Lesson(
                trade_id=r["trade_id"], category=r["category"], cause=r["cause"],
                recommendation=r["recommendation"], text=r["text"],
            )
            for r in rows
        ]

    def close(self) -> None:
        self.conn.close()
