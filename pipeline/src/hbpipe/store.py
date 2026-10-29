"""Where the pipeline keeps its output, next to the recordings in hb-data/.

hb-data/
    sessions/<id>/...               written by the mods
    tables/<table>/<id>.parquet     one file per table per session (hbpipe.convert.tables)
    index.sqlite                    sessions, segments, flags, labels, corrections, discovered sets,
                                    exclusions

Labels are rewritten every time the labeler runs. Corrections made in the review tool live in
their own table, are never touched by the labeler, and win over detector output.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from pathlib import Path

import polars as pl

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    started_unix_ms INTEGER NOT NULL,
    play_day TEXT NOT NULL,              -- local date; train/test splits hold out whole days
    game_version TEXT,
    recorder_version TEXT,
    world_id_hash TEXT,
    multiplayer INTEGER,
    complete INTEGER NOT NULL,
    duration_s REAL,
    records INTEGER,
    bytes INTEGER,
    converter_version INTEGER,
    converted_at INTEGER,
    labeler_version INTEGER,
    labeled_at INTEGER,
    labeler_fingerprint TEXT,            -- detector code, activity model and named sets used
    bot_strength REAL                    -- bot sessions: humanization strength (0 = plain Baritone)
);
CREATE TABLE IF NOT EXISTS segments (
    session_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    start_tick INTEGER, end_tick INTEGER,
    start_t_ns INTEGER, end_t_ns INTEGER,
    end_state TEXT, end_reason TEXT,
    ticks INTEGER,
    degraded INTEGER, suspect_afk INTEGER,
    PRIMARY KEY (session_id, segment_id)
);
CREATE TABLE IF NOT EXISTS flags (
    session_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS labels (
    label_id TEXT PRIMARY KEY,           -- stable across runs while the span is unchanged
    session_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    start_tick INTEGER NOT NULL,
    end_tick INTEGER NOT NULL,           -- inclusive
    start_t_ns INTEGER,
    end_t_ns INTEGER,
    label TEXT NOT NULL,
    level TEXT NOT NULL,                 -- primitive | activity | other
    confidence REAL NOT NULL,
    detector_version INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS labels_by_label ON labels (label);
CREATE INDEX IF NOT EXISTS labels_by_session ON labels (session_id, start_tick);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label_id TEXT,                       -- the detector label being judged; NULL for an added span
    session_id TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    start_tick INTEGER NOT NULL,
    end_tick INTEGER NOT NULL,
    label TEXT NOT NULL,                 -- the label as the detector gave it (or as added)
    verdict TEXT NOT NULL,               -- correct | wrong | added
    new_label TEXT,                      -- for wrong: what it actually was, if known
    note TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS corrections_by_label ON corrections (label);
CREATE TABLE IF NOT EXISTS discovered_sets (
    name TEXT PRIMARY KEY,               -- a set named from a discovery cluster
    created_at INTEGER NOT NULL,
    features TEXT NOT NULL,              -- JSON: feature names, in order
    mean TEXT NOT NULL,                  -- JSON: standardization used when it was named
    scale TEXT NOT NULL,
    centroid TEXT NOT NULL,              -- JSON: standardized cluster centre
    radius REAL NOT NULL,
    examples INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS exclusions (   -- play kept out of training (hbpipe.exclusions)
    session_id TEXT NOT NULL,
    start_tick INTEGER,                  -- NULL: from the session start
    end_tick INTEGER,                    -- inclusive; NULL: to the session end
    reason TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS hack_checks (  -- hack menus or freecam looked into: nothing to exclude
    session_id TEXT PRIMARY KEY,
    note TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
"""


ADDED_COLUMNS = (
    ("sessions", "labeler_fingerprint", "TEXT"),
    ("sessions", "bot_strength", "REAL"),
)

LABEL_TYPES = {
    "label_id": pl.String,
    "session_id": pl.String,
    "segment_id": pl.String,
    "start_tick": pl.Int64,
    "end_tick": pl.Int64,
    "start_t_ns": pl.Int64,
    "end_t_ns": pl.Int64,
    "label": pl.String,
    "level": pl.String,
    "confidence": pl.Float64,
    "detector_version": pl.Int64,
}
CORRECTION_TYPES = {
    "id": pl.Int64,
    "label_id": pl.String,
    "session_id": pl.String,
    "segment_id": pl.String,
    "start_tick": pl.Int64,
    "end_tick": pl.Int64,
    "label": pl.String,
    "verdict": pl.String,
    "new_label": pl.String,
    "note": pl.String,
    "created_at": pl.Int64,
}


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.tables_dir = self.root / "tables"
        self.db_path = self.root / "index.sqlite"
        self._db: sqlite3.Connection | None = None

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self.root.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.db_path)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(SCHEMA)
            self._migrate()
        return self._db

    def _migrate(self) -> None:
        """Adds columns introduced after an index was created."""
        for table, column, kind in ADDED_COLUMNS:
            have = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        self._db.commit()

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # --- Parquet tables -------------------------------------------------------------------------

    def table_path(self, table: str, session_id: str) -> Path:
        return self.tables_dir / table / f"{session_id}.parquet"

    def write_tables(self, session_id: str, tables: dict[str, pl.DataFrame]) -> None:
        for name, df in tables.items():
            path = self.table_path(name, session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".parquet.tmp")
            df.write_parquet(tmp, compression="zstd")
            tmp.replace(path)

    def scan(self, table: str, session_ids: Iterable[str] | None = None) -> pl.LazyFrame:
        """One table across sessions (all converted sessions by default)."""
        if session_ids is None:
            files = sorted((self.tables_dir / table).glob("*.parquet"))
        else:
            files = [self.table_path(table, s) for s in session_ids]
            files = [f for f in files if f.exists()]
        if not files:
            raise FileNotFoundError(f"no {table} tables under {self.tables_dir}")
        return pl.scan_parquet(files)

    def read(self, table: str, session_id: str) -> pl.DataFrame:
        return pl.read_parquet(self.table_path(table, session_id))

    # --- Index ----------------------------------------------------------------------------------

    def upsert_session(self, **row) -> None:
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        updates = ", ".join(f"{c} = excluded.{c}" for c in row if c != "session_id")
        self.db.execute(
            f"INSERT INTO sessions ({cols}) VALUES ({marks}) "
            f"ON CONFLICT (session_id) DO UPDATE SET {updates}",
            row,
        )
        self.db.commit()

    def session(self, session_id: str) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()

    def sessions(self, source: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM sessions"
        args: tuple = ()
        if source:
            sql += " WHERE source = ?"
            args = (source,)
        return self.db.execute(sql + " ORDER BY started_unix_ms", args).fetchall()

    def replace_segments(self, session_id: str, segments: pl.DataFrame) -> None:
        db = self.db
        db.execute("DELETE FROM segments WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM flags WHERE session_id = ?", (session_id,))
        for s in segments.iter_rows(named=True):
            db.execute(
                "INSERT INTO segments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    s["segment_id"],
                    s["start_tick"],
                    s["end_tick"],
                    s["start_t_ns"],
                    s["end_t_ns"],
                    s["end_state"],
                    s["end_reason"],
                    s["ticks"],
                    s["degraded"],
                    s["suspect_afk"],
                ),
            )
            for flag in s["flags"]:
                db.execute(
                    "INSERT INTO flags VALUES (?, ?, ?, ?)",
                    (session_id, s["segment_id"], flag["kind"], flag["reason"]),
                )
        db.commit()

    def replace_labels(
        self, session_id: str, labels: pl.DataFrame, labeler_version: int, fingerprint: str = ""
    ) -> None:
        db = self.db
        db.execute("DELETE FROM labels WHERE session_id = ?", (session_id,))
        db.executemany(
            "INSERT INTO labels VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            labels.select(
                "label_id",
                "session_id",
                "segment_id",
                "start_tick",
                "end_tick",
                "start_t_ns",
                "end_t_ns",
                "label",
                "level",
                "confidence",
                "detector_version",
            ).iter_rows(),
        )
        db.execute(
            "UPDATE sessions SET labeler_version = ?, labeled_at = ?, labeler_fingerprint = ? "
            "WHERE session_id = ?",
            (labeler_version, int(time.time()), fingerprint, session_id),
        )
        db.commit()

    def labels(self, source: str | None = None, **where) -> pl.DataFrame:
        """Labels whose columns match ``where``; with ``source`` (human or bot), only labels of
        that source's sessions."""
        clauses = [f"{k} = :{k}" for k in where]
        params = dict(where)
        if source:
            clauses.append("session_id IN (SELECT session_id FROM sessions WHERE source = :source)")
            params["source"] = source
        sql = "SELECT * FROM labels"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return pl.read_database(
            sql, self.db, execute_options={"parameters": params}, schema_overrides=LABEL_TYPES
        )

    # --- Exclusions -----------------------------------------------------------------------------

    def add_exclusion(
        self,
        session_id: str,
        reason: str,
        start_tick: int | None = None,
        end_tick: int | None = None,
    ) -> None:
        """Keeps a stretch of a session (all of it by default) out of training. The session is
        labeled again on the next run, which drops its decision traces there."""
        self.db.execute(
            "INSERT INTO exclusions VALUES (?, ?, ?, ?, ?)",
            (session_id, start_tick, end_tick, reason, int(time.time())),
        )
        self._needs_labels(session_id)

    def clear_exclusions(self, session_id: str) -> int:
        """Drops the session's exclusions and its hack check; returns how many exclusions."""
        n = self.db.execute("DELETE FROM exclusions WHERE session_id = ?", (session_id,)).rowcount
        self.db.execute("DELETE FROM hack_checks WHERE session_id = ?", (session_id,))
        self._needs_labels(session_id)
        return n

    def mark_checked(self, session_id: str, note: str) -> None:
        """Records that the session's hack menus and freecam were looked into and that nothing
        beyond its exclusions needs to stay out of training (e.g. "freecam only")."""
        self.db.execute(
            "INSERT INTO hack_checks VALUES (?, ?, ?) ON CONFLICT (session_id) "
            "DO UPDATE SET note = excluded.note, created_at = excluded.created_at",
            (session_id, note, int(time.time())),
        )
        self.db.commit()

    def hack_checks(self) -> dict[str, str]:
        return {r["session_id"]: r["note"] for r in self.db.execute("SELECT * FROM hack_checks")}

    def exclusions(self, session_id: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM exclusions"
        args: tuple = ()
        if session_id:
            sql += " WHERE session_id = ?"
            args = (session_id,)
        return self.db.execute(sql + " ORDER BY session_id, start_tick", args).fetchall()

    def _needs_labels(self, session_id: str) -> None:
        self.db.execute(
            "UPDATE sessions SET labeler_fingerprint = NULL WHERE session_id = ?", (session_id,)
        )
        self.db.commit()

    # --- Corrections ----------------------------------------------------------------------------

    def add_correction(
        self,
        *,
        session_id: str,
        segment_id: str,
        start_tick: int,
        end_tick: int,
        label: str,
        verdict: str,
        label_id: str | None = None,
        new_label: str | None = None,
        note: str | None = None,
    ) -> int:
        if verdict not in ("correct", "wrong", "added"):
            raise ValueError(f"unknown verdict {verdict!r}")
        cur = self.db.execute(
            "INSERT INTO corrections (label_id, session_id, segment_id, start_tick, end_tick, "
            "label, verdict, new_label, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                label_id,
                session_id,
                segment_id,
                start_tick,
                end_tick,
                label,
                verdict,
                new_label,
                note,
                int(time.time()),
            ),
        )
        self.db.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def corrections(self, **where) -> pl.DataFrame:
        sql = "SELECT * FROM corrections"
        if where:
            sql += " WHERE " + " AND ".join(f"{k} = :{k}" for k in where)
        return pl.read_database(
            sql + " ORDER BY id",
            self.db,
            execute_options={"parameters": where},
            schema_overrides=CORRECTION_TYPES,
        )
