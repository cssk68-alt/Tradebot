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

from tradebot.models import (
    Counterfactual,
    Experience,
    Lesson,
    ManagerDecision,
    Mode,
    Side,
    Trade,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    market_id TEXT, yes_price REAL, ts TEXT, spread REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT, token_id TEXT, question TEXT, side TEXT, is_yes INTEGER,
    entry_price REAL, size REAL, mode TEXT, status TEXT, pnl REAL,
    won INTEGER, resolved_yes INTEGER, brain_score REAL, edge REAL,
    features TEXT, opened_at TEXT, resolved_at TEXT,
    kind TEXT DEFAULT 'resolve', exit_price REAL, exec_style TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    features TEXT, edge REAL, size REAL, brain_score REAL,
    won INTEGER, pnl REAL, mode TEXT, is_yes INTEGER DEFAULT 1,
    is_counterfactual INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS counterfactuals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT, is_yes INTEGER, entry_price REAL, entry_ts TEXT,
    edge REAL, brain_score REAL, features TEXT, source TEXT, reason TEXT,
    take_profit REAL, stop_loss REAL, max_hold REAL,
    status TEXT DEFAULT 'pending', exit_price REAL, pnl REAL DEFAULT 0,
    won INTEGER, exit_reason TEXT, settled_at TEXT, created_at TEXT
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
CREATE TABLE IF NOT EXISTS execution_queue (
    execution_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    is_yes INTEGER NOT NULL,
    order_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    retries INTEGER DEFAULT 0,
    last_error TEXT DEFAULT ''
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
        for col, ddl in (
            ("kind", "TEXT DEFAULT 'resolve'"),
            ("exit_price", "REAL"),
            ("exec_style", "TEXT DEFAULT ''"),
        ):
            try:
                self.conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass  # column already exists
        try:  # the brain now learns the traded side, so experiences carry is_yes
            self.conn.execute("ALTER TABLE experiences ADD COLUMN is_yes INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        try:  # experiences may now be counterfactual (veto/mirror) rather than traded
            self.conn.execute("ALTER TABLE experiences ADD COLUMN is_counterfactual INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:  # snapshots now carry the spread so counterfactuals charge the real cost
            self.conn.execute("ALTER TABLE snapshots ADD COLUMN spread REAL DEFAULT 0")
        except sqlite3.OperationalError:
            pass

    # --- snapshots (for price-move anomaly detection) ---
    def last_yes_price(self, market_id: str) -> Optional[float]:
        row = self.conn.execute(
            "SELECT yes_price FROM snapshots WHERE market_id=? ORDER BY ts DESC LIMIT 1",
            (market_id,),
        ).fetchone()
        return None if row is None else float(row["yes_price"])

    def record_snapshot(self, market_id: str, yes_price: float, spread: float = 0.0) -> None:
        self.conn.execute(
            "INSERT INTO snapshots(market_id, yes_price, ts, spread) VALUES (?,?,?,?)",
            (market_id, yes_price, datetime.now(timezone.utc).isoformat(), float(spread)),
        )
        self.conn.commit()

    def snapshots_between(
        self, market_id: str, t0: datetime, t1: datetime
    ) -> list[tuple[datetime, float, float]]:
        """Price path (ts, yes_price, spread) for a market in (t0, t1], oldest first.
        The real series counterfactual scalps are replayed against."""
        rows = self.conn.execute(
            "SELECT yes_price, ts, spread FROM snapshots WHERE market_id=? AND ts>? AND ts<=? "
            "ORDER BY ts ASC",
            (market_id, t0.isoformat(), t1.isoformat()),
        ).fetchall()
        out: list[tuple[datetime, float, float]] = []
        for r in rows:
            keys = r.keys()
            spread = float(r["spread"]) if "spread" in keys and r["spread"] is not None else 0.0
            out.append((datetime.fromisoformat(r["ts"]), float(r["yes_price"]), spread))
        return out

    # --- trades ---
    def save_trade(self, t: Trade) -> int:
        cur = self.conn.execute(
            """INSERT INTO trades(market_id, token_id, question, side, is_yes,
               entry_price, size, mode, status, pnl, won, resolved_yes,
               brain_score, edge, features, opened_at, resolved_at, kind, exit_price,
               exec_style)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                t.market_id, t.token_id, t.question, t.side.value, int(t.is_yes),
                t.entry_price, t.size, t.mode.value, t.status, t.pnl, _b(t.won),
                _b(t.resolved_yes), t.brain_score, t.edge, json.dumps(t.features),
                t.opened_at.isoformat(), t.resolved_at.isoformat() if t.resolved_at else None,
                t.kind, t.exit_price, t.exec_style,
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
            exec_style=(r["exec_style"] if "exec_style" in keys and r["exec_style"] else ""),
        )

    def open_trades(self, mode: Optional[Mode] = None) -> list[Trade]:
        if mode:
            rows = self.conn.execute(
                "SELECT * FROM trades WHERE status='open' AND mode=?", (mode.value,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM trades WHERE status='open'").fetchall()
        return [self._row_to_trade(r) for r in rows]

    def pending_maker_trades(self, mode: Optional[Mode] = None) -> list[Trade]:
        """Resting paper maker orders awaiting a fill decision (``resolve_pending_makers``).
        Not yet positions — excluded from ``open_trades`` and settlement."""
        if mode:
            rows = self.conn.execute(
                "SELECT * FROM trades WHERE status='pending_maker' AND mode=?", (mode.value,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM trades WHERE status='pending_maker'").fetchall()
        return [self._row_to_trade(r) for r in rows]

    def open_pending_trade(self, t: Trade) -> None:
        """Promote a resting maker order to an open position once its fill is decided:
        persist the confirmed status, entry price, fill time and exec_style."""
        self.conn.execute(
            "UPDATE trades SET status=?, entry_price=?, opened_at=?, exec_style=? WHERE id=?",
            (t.status, t.entry_price, t.opened_at.isoformat(), t.exec_style, t.id),
        )
        self.conn.commit()

    def resolved_trades(self) -> list[Trade]:
        rows = self.conn.execute("SELECT * FROM trades WHERE status='resolved'").fetchall()
        return [self._row_to_trade(r) for r in rows]

    def open_exposure(self, mode: Mode) -> float:
        # Resting maker orders (pending_maker) commit capital at the bid too, so they
        # count toward exposure until they fill or are cancelled — no over-allocation
        # while a bid rests. (No-op for live, which never has pending_maker rows.)
        row = self.conn.execute(
            "SELECT COALESCE(SUM(entry_price*size),0) AS e FROM trades "
            "WHERE status IN ('open','pending_maker') AND mode=?", (mode.value,),
        ).fetchone()
        return float(row["e"])

    def realized_pnl(self, mode: Mode) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(pnl),0) AS p FROM trades WHERE status='resolved' AND mode=?",
            (mode.value,),
        ).fetchone()
        return float(row["p"])

    def realized_pnl_today(self, mode: Mode, now: Optional[datetime] = None) -> float:
        """Realized PnL of trades resolved since 00:00 UTC today (circuit breaker)."""
        now = now or datetime.now(timezone.utc)
        start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        row = self.conn.execute(
            "SELECT COALESCE(SUM(pnl),0) AS p FROM trades "
            "WHERE status='resolved' AND mode=? AND resolved_at >= ?",
            (mode.value, start.isoformat()),
        ).fetchone()
        return float(row["p"])

    def consecutive_losses(self, mode: Mode) -> int:
        """Count of trailing losses among the most recently resolved trades.

        Walks resolved trades newest-first and counts losses until the first win.
        Void/canceled trades (won IS NULL) are excluded — they neither count as a
        loss nor reset the streak."""
        rows = self.conn.execute(
            "SELECT won FROM trades WHERE status='resolved' AND mode=? AND won IS NOT NULL "
            "ORDER BY resolved_at DESC, id DESC",
            (mode.value,),
        ).fetchall()
        streak = 0
        for r in rows:
            if int(r["won"]) == 0:
                streak += 1
            else:
                break
        return streak

    # --- experiences (brain training data; mode-agnostic) ---
    def save_experience(self, e: Experience) -> None:
        self.conn.execute(
            "INSERT INTO experiences(features, edge, size, brain_score, won, pnl, mode, is_yes,"
            " is_counterfactual) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                json.dumps(e.features), e.edge, e.size, e.brain_score, int(e.won),
                e.pnl, e.mode.value, int(e.is_yes), int(e.is_counterfactual),
            ),
        )
        self.conn.commit()

    def load_experiences(self) -> list[Experience]:
        rows = self.conn.execute("SELECT * FROM experiences").fetchall()
        out: list[Experience] = []
        for r in rows:
            keys = r.keys()
            cf = "is_counterfactual" in keys and r["is_counterfactual"]
            out.append(
                Experience(
                    features=json.loads(r["features"]), edge=r["edge"], size=r["size"],
                    brain_score=r["brain_score"], won=bool(r["won"]), pnl=r["pnl"],
                    mode=Mode(r["mode"]),
                    is_yes=bool(r["is_yes"]) if "is_yes" in keys and r["is_yes"] is not None else True,
                    is_counterfactual=bool(cf),
                )
            )
        return out

    # --- counterfactuals (veto/mirror learning data; settled via snapshots) ---
    def save_counterfactual(self, c: Counterfactual) -> int:
        cur = self.conn.execute(
            "INSERT INTO counterfactuals(market_id, is_yes, entry_price, entry_ts, edge,"
            " brain_score, features, source, reason, take_profit, stop_loss, max_hold,"
            " status, exit_price, pnl, won, exit_reason, settled_at, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                c.market_id, int(c.is_yes), c.entry_price, c.entry_ts.isoformat(), c.edge,
                c.brain_score, json.dumps(c.features), c.source, c.reason, c.take_profit,
                c.stop_loss, c.max_hold, c.status, c.exit_price, c.pnl, _b(c.won),
                c.exit_reason, c.settled_at.isoformat() if c.settled_at else None,
                c.created_at.isoformat(),
            ),
        )
        self.conn.commit()
        c.id = int(cur.lastrowid)
        return c.id

    def _row_to_counterfactual(self, r: sqlite3.Row) -> Counterfactual:
        return Counterfactual(
            id=r["id"], market_id=r["market_id"], is_yes=bool(r["is_yes"]),
            entry_price=r["entry_price"], entry_ts=datetime.fromisoformat(r["entry_ts"]),
            edge=r["edge"], brain_score=r["brain_score"],
            features=json.loads(r["features"]) if r["features"] else [],
            source=r["source"] or "veto", reason=r["reason"] or "",
            take_profit=r["take_profit"], stop_loss=r["stop_loss"], max_hold=r["max_hold"],
            status=r["status"] or "pending", exit_price=r["exit_price"], pnl=r["pnl"] or 0.0,
            won=None if r["won"] is None else bool(r["won"]), exit_reason=r["exit_reason"] or "",
            settled_at=datetime.fromisoformat(r["settled_at"]) if r["settled_at"] else None,
            created_at=datetime.fromisoformat(r["created_at"]) if r["created_at"] else datetime.now(timezone.utc),
        )

    def pending_counterfactuals(self) -> list[Counterfactual]:
        rows = self.conn.execute(
            "SELECT * FROM counterfactuals WHERE status='pending' ORDER BY id ASC"
        ).fetchall()
        return [self._row_to_counterfactual(r) for r in rows]

    def update_counterfactual(self, c: Counterfactual) -> None:
        self.conn.execute(
            "UPDATE counterfactuals SET status=?, exit_price=?, pnl=?, won=?, exit_reason=?,"
            " settled_at=? WHERE id=?",
            (
                c.status, c.exit_price, c.pnl, _b(c.won), c.exit_reason,
                c.settled_at.isoformat() if c.settled_at else None, c.id,
            ),
        )
        self.conn.commit()

    def counterfactual_stats(self) -> dict:
        """Counts for the dashboard veto-scoreboard.

        brain_right = vetoed setups (source='veto') that would have LOST (the veto
        saved us); brain_wrong = vetoed setups that would have WON (too strict)."""
        row = self.conn.execute(
            "SELECT "
            " SUM(status='pending') AS pending,"
            " SUM(status='settled') AS settled,"
            " SUM(status='settled' AND source='veto' AND won=0) AS veto_right,"
            " SUM(status='settled' AND source='veto' AND won=1) AS veto_wrong"
            " FROM counterfactuals"
        ).fetchone()
        return {
            "pending": int(row["pending"] or 0),
            "settled": int(row["settled"] or 0),
            "brain_right": int(row["veto_right"] or 0),
            "brain_wrong": int(row["veto_wrong"] or 0),
        }

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

    # --- execution queue ---

    def enqueue_execution(
        self, execution_id: str, market_id: str, is_yes: bool, order_json: str
    ) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO execution_queue"
            "(execution_id, market_id, is_yes, order_json, created_at)"
            " VALUES (?,?,?,?,?)",
            (execution_id, market_id, int(is_yes), order_json,
             datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def pending_executions(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT execution_id, market_id, is_yes, order_json, retries"
            " FROM execution_queue WHERE status='pending' ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_execution_done(self, execution_id: str) -> None:
        self.conn.execute(
            "UPDATE execution_queue SET status='done' WHERE execution_id=?",
            (execution_id,),
        )
        self.conn.commit()

    def mark_execution_failed(self, execution_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE execution_queue SET status='failed', last_error=? WHERE execution_id=?",
            (error[:500], execution_id),
        )
        self.conn.commit()

    def bump_execution_retry(self, execution_id: str, error: str) -> None:
        self.conn.execute(
            "UPDATE execution_queue SET retries=retries+1, last_error=? WHERE execution_id=?",
            (error[:500], execution_id),
        )
        self.conn.commit()

    def has_open_execution(self, market_id: str, is_yes: bool, mode: "Mode") -> bool:
        """True if an open trade for this market+side already exists (idempotency guard)."""
        row = self.conn.execute(
            "SELECT id FROM trades WHERE market_id=? AND is_yes=? AND mode=? AND status='open' LIMIT 1",
            (market_id, int(is_yes), mode.value),
        ).fetchone()
        return row is not None

    def close(self) -> None:
        self.conn.close()
