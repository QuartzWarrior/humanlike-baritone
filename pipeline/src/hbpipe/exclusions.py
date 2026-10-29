"""Stretches of play kept out of training.

Some recorded play isn't the player's own judgment. With a hack that shows hidden blocks on
(Wurst's Search, X-Ray, ESP), routes and targets come from information no player has, and a bot
that learned from them would look like it cheats. `hbpipe exclude` marks such stretches; they
live in index.sqlite.

Exclusions leave the recordings, the tables and the labels alone: the labels still describe what
happened, so labeling, review and the gate numbers include excluded play. Training data doesn't.
The decision traces leave out mining jobs that touch an exclusion, and anything else that trains
on the recordings reads them through drop_excluded.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from hbpipe.store import Store

END = 2**62  # where an exclusion without an end tick stops


@dataclass(frozen=True)
class Exclusion:
    start_tick: int
    end_tick: int  # inclusive
    reason: str


def for_session(store: Store, session_id: str) -> list[Exclusion]:
    return [
        Exclusion(
            0 if r["start_tick"] is None else r["start_tick"],
            END if r["end_tick"] is None else r["end_tick"],
            r["reason"],
        )
        for r in store.exclusions(session_id)
    ]


def drop_excluded(
    df: pl.DataFrame, excluded: list[Exclusion], start: str = "tick", end: str | None = None
) -> pl.DataFrame:
    """The rows of ``df`` whose tick range (``start`` to ``end``, one tick column by default)
    touches no exclusion."""
    if not excluded or df.is_empty():
        return df
    end = end or start
    hit = pl.lit(False)
    for e in excluded:
        hit = hit | ((pl.col(start) <= e.end_tick) & (pl.col(end) >= e.start_tick))
    return df.filter(~hit)


HACK_SCREENS = ("net.wurstclient.",)  # menus of hack clients: their hacks may be on nearby


def training_summary(store: Store) -> list[str]:
    """Report lines: how much human play is excluded, and the hack menus and freecam pauses in
    play that isn't, for sessions nobody has checked yet (hbpipe exclude --checked)."""
    total = excluded_ticks = stretches = 0
    excluded_sessions = 0
    checks = []
    checked = store.hack_checks()
    for s in store.sessions(source="human"):
        sid = s["session_id"]
        excluded = for_session(store, sid)
        segs = store.db.execute(
            "SELECT start_tick, end_tick, end_t_ns, end_reason FROM segments WHERE session_id = ?",
            (sid,),
        ).fetchall()
        for g in segs:
            total += g["end_tick"] - g["start_tick"] + 1
            excluded_ticks += _covered(g["start_tick"], g["end_tick"], excluded)
        if excluded:
            excluded_sessions += 1
            stretches += len(excluded)

        def inside(tick: int, excluded=excluded) -> bool:
            return any(e.start_tick <= tick <= e.end_tick for e in excluded)

        freecam = [
            g["end_t_ns"]
            for g in segs
            if g["end_reason"] == "camera_detached" and not inside(g["end_tick"])
        ]
        menus = _hack_menus(store, sid).filter(
            ~pl.col("tick").map_elements(inside, return_dtype=pl.Boolean)
        )
        found = []
        if menus.height:
            found.append(f"{menus.height} hack-client menus (first at {_clock(menus['t_ns'][0])})")
        if freecam:
            found.append(f"{len(freecam)} freecam pauses (first at {_clock(min(freecam))})")
        if found and sid not in checked:
            checks.append(f"  {sid[:8]}  " + ", ".join(found))
    lines = [
        f"Training data: {excluded_ticks / 1200:.1f} of {total / 1200:.1f} min of play excluded "
        f"({stretches} stretches in {excluded_sessions} sessions, {len(checked)} sessions checked; "
        "hbpipe exclude --list)"
    ]
    if checks:
        lines += ["Check before training (hack menus or freecam not excluded or checked yet):"]
        lines += checks
    return lines


def _covered(start: int, end: int, excluded: list[Exclusion]) -> int:
    """Ticks of start..end inside any exclusion (overlapping exclusions count once)."""
    spans = sorted((max(start, e.start_tick), min(end, e.end_tick)) for e in excluded)
    n, reach = 0, start - 1
    for a, b in spans:
        a = max(a, reach + 1)
        if a <= b:
            n += b - a + 1
            reach = b
    return n


def _hack_menus(store: Store, session_id: str) -> pl.DataFrame:
    path = store.table_path("events", session_id)
    if not path.exists():
        return pl.DataFrame(schema={"tick": pl.UInt64, "t_ns": pl.UInt64})
    return (
        pl.read_parquet(path, columns=["tick", "t_ns", "kind", "screen"])
        .filter(
            pl.col("kind") == "screen_opened",
            pl.any_horizontal(pl.col("screen").str.starts_with(p) for p in HACK_SCREENS),
        )
        .select("tick", "t_ns")
        .sort("tick")
    )


def _clock(t_ns: int) -> str:
    s = int(t_ns // 1_000_000_000)
    return f"{s // 60}:{s % 60:02d}"
