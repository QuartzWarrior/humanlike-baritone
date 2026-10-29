"""Labeling pass 4: decision traces for the mining-order model.

For every mining job (a mine.area_clear, mine.tunnel or mine.strip span), rebuild the volume as it
was when the job started and record the ordered breaks: which block, where the player stood and
looked when they chose it, and the tool. The Stage 1 ranker learns "chosen block vs every other
reachable candidate at that moment" from these.

    traces          one row per break: trace_id, step, decision tick (destroy start, or the break
                    for instant breaks), broken position and block, tool, ticks taken, stand
                    position, yaw and pitch at the decision, whether the player moved since the
                    previous break, ticks since it
    trace_volumes   the starting volume: every position in the breaks' bounding box grown by
                    MARGIN blocks, with its block id at the job's start. Positions first seen later
                    in the job take that first sighting (seen_at_start false); a broken block always
                    takes the id from its break event.
"""

from __future__ import annotations

import hashlib
import math

import polars as pl

from hbpipe.label.world import World

TRACE_LABELS = ("mine.area_clear", "mine.tunnel", "mine.strip")
MARGIN = 2
MAX_VOLUME = 64_000  # blocks; a bigger box is a mislabeled span, not a mining job

TRACE_SCHEMA = {
    "trace_id": pl.String,
    "session_id": pl.String,
    "segment_id": pl.String,
    "label": pl.String,
    "step": pl.Int32,
    "tick": pl.UInt64,
    "decision_tick": pl.UInt64,
    "x": pl.Int32,
    "y": pl.Int32,
    "z": pl.Int32,
    "block": pl.String,
    "tool": pl.String,
    "ticks_taken": pl.UInt32,
    "face": pl.String,
    "stand_x": pl.Float64,
    "stand_y": pl.Float64,
    "stand_z": pl.Float64,
    "yaw": pl.Float32,
    "pitch": pl.Float32,
    "moved": pl.Boolean,
    "ticks_since_prev": pl.Int64,
}
VOLUME_SCHEMA = {
    "trace_id": pl.String,
    "x": pl.Int32,
    "y": pl.Int32,
    "z": pl.Int32,
    "block": pl.String,
    "seen_at_start": pl.Boolean,
}


def trace_id(session_id: str, segment_id: str, label: str, start_tick: int) -> str:
    return hashlib.sha1(f"{session_id}|{segment_id}|{label}|{start_tick}".encode()).hexdigest()[:16]


def _decision_ticks(events: pl.DataFrame, breaks: pl.DataFrame) -> list[int]:
    """For each break, the tick of the last destroy_started at that position before it."""
    starts: dict[tuple[int, int, int], list[int]] = {}
    for x, y, z, tick in (
        events.filter(pl.col("kind") == "destroy_started").select("x", "y", "z", "tick").iter_rows()
    ):
        starts.setdefault((x, y, z), []).append(tick)
    out = []
    for x, y, z, tick in breaks.select("x", "y", "z", "tick").iter_rows():
        before = [t for t in starts.get((x, y, z), ()) if t <= tick]
        out.append(max(before) if before else tick)
    return out


def extract(
    session_id: str,
    labels: pl.DataFrame,
    events: pl.DataFrame,
    ticks: pl.DataFrame,
    world: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Traces and starting volumes for one session. ``events`` are enriched and ``ticks``
    cleaned (hbpipe.label.clean)."""
    steps: list[dict] = []
    volumes: list[dict] = []
    look = {t: (yaw, pitch) for t, yaw, pitch in ticks.select("tick", "yaw", "pitch").iter_rows()}
    jobs = labels.filter(pl.col("label").is_in(TRACE_LABELS)).sort("start_tick")
    for job in jobs.iter_rows(named=True):
        seg_events = events.filter(
            (pl.col("segment_id") == job["segment_id"])
            & pl.col("tick").is_between(job["start_tick"], job["end_tick"])
        )
        breaks = seg_events.filter(pl.col("kind") == "block_broken").sort("tick", "t_ns")
        if breaks.is_empty():
            continue
        xs, ys, zs = breaks["x"], breaks["y"], breaks["z"]
        lo = (xs.min() - MARGIN, ys.min() - MARGIN, zs.min() - MARGIN)
        hi = (xs.max() + MARGIN, ys.max() + MARGIN, zs.max() + MARGIN)
        size = math.prod(h - lo_ + 1 for lo_, h in zip(lo, hi, strict=True))
        if size > MAX_VOLUME:
            continue
        tid = trace_id(session_id, job["segment_id"], job["label"], job["start_tick"])

        prev_tick = prev_block_pos = None
        decisions = _decision_ticks(seg_events, breaks)
        for step, (row, decision) in enumerate(
            zip(breaks.iter_rows(named=True), decisions, strict=True)
        ):
            stand = (row["player_x"], row["player_y"], row["player_z"])
            block_pos = tuple(math.floor(v) for v in stand) if stand[0] is not None else None
            yaw, pitch = look.get(decision, (None, None))
            steps.append(
                {
                    "trace_id": tid,
                    "session_id": session_id,
                    "segment_id": job["segment_id"],
                    "label": job["label"],
                    "step": step,
                    "tick": row["tick"],
                    "decision_tick": decision,
                    "x": row["x"],
                    "y": row["y"],
                    "z": row["z"],
                    "block": row["block"],
                    "tool": row["tool"],
                    "ticks_taken": row["ticks_taken"],
                    "face": row["face"],
                    "stand_x": stand[0],
                    "stand_y": stand[1],
                    "stand_z": stand[2],
                    "yaw": yaw,
                    "pitch": pitch,
                    "moved": prev_block_pos is not None and block_pos != prev_block_pos,
                    "ticks_since_prev": None if prev_tick is None else row["tick"] - prev_tick,
                }
            )
            prev_tick, prev_block_pos = row["tick"], block_pos

        volumes += _volume(tid, world, job["start_tick"], job["end_tick"], lo, hi, breaks)
    traces = (
        pl.from_dicts(steps, schema=TRACE_SCHEMA) if steps else pl.DataFrame(schema=TRACE_SCHEMA)
    )
    vols = (
        pl.from_dicts(volumes, schema=VOLUME_SCHEMA)
        if volumes
        else pl.DataFrame(schema=VOLUME_SCHEMA)
    )
    return traces, vols


def _volume(tid, world, start_tick, end_tick, lo, hi, breaks: pl.DataFrame) -> list[dict]:
    w = World(world)
    w.advance_to(start_tick)
    known: dict[tuple[int, int, int], tuple[str, bool]] = {}
    box = [
        (x, y, z)
        for x in range(lo[0], hi[0] + 1)
        for y in range(lo[1], hi[1] + 1)
        for z in range(lo[2], hi[2] + 1)
    ]
    for p in box:
        b = w.get(*p)
        if b is not None:
            known[p] = (b, True)
    # Fill what wasn't in view at the start from its first sighting during the job.
    missing = {p for p in box if p not in known}
    if missing:
        for tick in (
            world.filter(pl.col("tick").is_between(start_tick + 1, end_tick))["tick"]
            .unique()
            .sort()
        ):
            w.advance_to(tick)
            for p in [p for p in missing if w.get(*p) is not None]:
                known[p] = (w.get(*p), False)
                missing.discard(p)
            if not missing:
                break
    for x, y, z, block in breaks.select("x", "y", "z", "block").iter_rows():
        seen = known.get((x, y, z), (block, False))[1]
        known[(x, y, z)] = (block, seen)
    return [
        {"trace_id": tid, "x": p[0], "y": p[1], "z": p[2], "block": b, "seen_at_start": s}
        for p, (b, s) in sorted(known.items())
    ]
