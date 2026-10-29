"""Converts every new or changed session under an hb-data dir into Parquet tables."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from pathlib import Path

from hbpipe.convert.session import Session, find_sessions
from hbpipe.convert.tables import CONVERTER_VERSION, convert
from hbpipe.schema import pb
from hbpipe.store import Store

# Play days roll over at 4 am local time, so a late-night session stays with its evening.
DAY_ROLLOVER_HOURS = 4


def play_day(started_unix_ms: int) -> str:
    local = dt.datetime.fromtimestamp(started_unix_ms / 1000)
    return (local - dt.timedelta(hours=DAY_ROLLOVER_HOURS)).date().isoformat()


@dataclass
class Converted:
    session_id: str
    skipped: bool
    ticks: int = 0
    segments: int = 0
    seconds: float = 0.0


def up_to_date(store: Store, session: Session) -> bool:
    row = store.session(session.session_id)
    return (
        row is not None
        and row["converter_version"] == CONVERTER_VERSION
        and bool(row["complete"]) == session.complete
        and store.table_path("ticks", session.session_id).exists()
    )


def convert_session(store: Store, session: Session) -> Converted:
    began = time.monotonic()
    tables = convert(session)
    store.write_tables(session.session_id, tables)
    h = session.header
    stats = session.stats or {}
    store.upsert_session(
        session_id=h.session_id,
        source=pb.Source.Name(h.source).removeprefix("SOURCE_").lower(),
        started_unix_ms=h.started_unix_ms,
        play_day=play_day(h.started_unix_ms),
        game_version=h.game_version,
        recorder_version=h.recorder_version,
        world_id_hash=h.world_id_hash,
        multiplayer=int(h.multiplayer),
        complete=int(session.complete),
        duration_s=stats.get("duration_s"),
        # Bot sessions from before strengths were recorded were all plain Baritone.
        bot_strength=stats.get("humanization_strength", 0.0) if h.source == pb.SOURCE_BOT else None,
        records=stats.get("records"),
        bytes=session.bytes_on_disk(),
        converter_version=CONVERTER_VERSION,
        converted_at=int(time.time()),
        labeler_fingerprint=None,  # new tables: the old labels no longer match them
    )
    store.replace_segments(session.session_id, tables["segments"])
    return Converted(
        session.session_id,
        skipped=False,
        ticks=tables["ticks"].height,
        segments=tables["segments"].height,
        seconds=time.monotonic() - began,
    )


def convert_all(root: str | Path, *, force: bool = False) -> list[Converted]:
    """Sessions already converted by this converter version are skipped unless forced. A session
    still being recorded looks like a crashed one (no stats.json); it is converted again once it
    closes."""
    store = Store(root)
    try:
        out = []
        for session in find_sessions(Path(root) / "sessions"):
            if not force and up_to_date(store, session):
                out.append(Converted(session.session_id, skipped=True))
            else:
                out.append(convert_session(store, session))
        return out
    finally:
        store.close()
