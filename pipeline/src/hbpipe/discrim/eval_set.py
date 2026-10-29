"""The eval set: clips of human and bot play per action set (spec, Component D).

A clip is up to WINDOW_TICKS (10 s) of one set's ticks in one segment: the ticks the set's spans
cover, in order, cut into pieces of WINDOW_TICKS; pieces under MIN_TICKS are dropped. Human labels
are the effective ones (review verdicts win over detectors), and human play kept out of training
(hbpipe.exclusions) is left out.

Each clip carries its session and play day, so splits never put one session on both sides, and
classes are balanced per set by sampling the larger class down (fixed seed). The Phase 3 clips are
unmatched: the bot played the same kinds of task in another world (the user's choice while their
recordings come from a server the bot can't run on).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from hbpipe import exclusions
from hbpipe.label.corrections import effective_labels
from hbpipe.label.run import UNLABELED
from hbpipe.store import Store

WINDOW_TICKS = 200
MIN_TICKS = 40
SOURCES = ("human", "bot")

CLIP_SCHEMA = {
    "clip_id": pl.String,
    "label": pl.String,
    "level": pl.String,
    "source": pl.String,
    "session_id": pl.String,
    "segment_id": pl.String,
    "play_day": pl.String,
    "start_tick": pl.Int64,
    "end_tick": pl.Int64,
    "n_ticks": pl.Int64,
}


@dataclass
class EvalSet:
    clips: pl.DataFrame  # CLIP_SCHEMA
    ticks: dict[str, np.ndarray]  # clip_id -> the clip's tick numbers, ascending

    def of(self, label: str) -> pl.DataFrame:
        return self.clips.filter(pl.col("label") == label)


def session_labels(store: Store, session_id: str, source: str) -> pl.DataFrame:
    """A session's labels as training sees them: review verdicts applied, excluded play out."""
    labels = store.labels(session_id=session_id)
    if source == "human":
        labels = effective_labels(labels, store.corrections(session_id=session_id))
        labels = exclusions.drop_excluded(
            labels, exclusions.for_session(store, session_id), "start_tick", "end_tick"
        )
    return labels.filter(pl.col("label") != UNLABELED)


def clips_of(labels: pl.DataFrame) -> list[tuple[str, str, str, np.ndarray]]:
    """(label, level, segment_id, ticks) for every clip in one session's labels."""
    out = []
    for (label, level, segment_id), g in labels.group_by(
        "label", "level", "segment_id", maintain_order=True
    ):
        spans = sorted(zip(g["start_tick"].to_list(), g["end_tick"].to_list(), strict=True))
        covered: list[int] = []
        reach = -1
        for a, b in spans:
            a = max(a, reach + 1)
            if a <= b:
                covered.extend(range(a, b + 1))
                reach = b
        ticks = np.asarray(covered, dtype=np.int64)
        for i in range(0, len(ticks), WINDOW_TICKS):
            piece = ticks[i : i + WINDOW_TICKS]
            if len(piece) >= MIN_TICKS:
                out.append((label, level, segment_id, piece))
    return out


BOTS = ("auto", "plain", "humanized", "all")


def bot_kind(session) -> str:
    """A bot session's kind: "humanized" when the executor drove it at a strength above 0,
    else "plain" (Baritone alone; sessions from before strengths were kept were all plain)."""
    strength = session["bot_strength"]
    return "humanized" if strength is not None and strength > 0 else "plain"


def pick_bots(store: Store, bot: str = "auto") -> str:
    """Which bot sessions to compare with: "auto" means the humanized ones when any are labeled
    (from Phase 4 the executor is what's judged), else plain Baritone (the Phase 3 baseline)."""
    if bot != "auto":
        return bot
    kinds = {
        bot_kind(s) for s in store.sessions(source="bot") if s["labeler_fingerprint"] is not None
    }
    return "humanized" if "humanized" in kinds else "plain"


def judged_sessions(
    store: Store,
    bot: str = "auto",
    bot_since_ms: int | None = None,
    bot_until_ms: int | None = None,
) -> list:
    """Every labeled human session and the chosen bot sessions (``bot``: see pick_bots;
    ``bot_since_ms``: only those started then or later, to judge the current executor alone;
    ``bot_until_ms``: only those started before then, to judge an earlier build)."""
    bot = pick_bots(store, bot)
    out = []
    for s in store.sessions():
        source = s["source"]
        if source not in SOURCES or s["labeler_fingerprint"] is None:
            continue
        if source == "bot" and bot != "all" and bot_kind(s) != bot:
            continue
        if source == "bot" and bot_since_ms is not None and s["started_unix_ms"] < bot_since_ms:
            continue
        if source == "bot" and bot_until_ms is not None and s["started_unix_ms"] >= bot_until_ms:
            continue
        out.append(s)
    return out


def build(
    store: Store,
    seed: int = 0,
    bot: str = "auto",
    bot_since_ms: int | None = None,
    bot_until_ms: int | None = None,
) -> EvalSet:
    """Clips for every set from the judged sessions (judged_sessions), balanced per set."""
    rows: list[dict] = []
    ticks: dict[str, np.ndarray] = {}
    for s in judged_sessions(store, bot, bot_since_ms, bot_until_ms):
        source = s["source"]
        labels = session_labels(store, s["session_id"], source)
        for n, (label, level, segment_id, piece) in enumerate(clips_of(labels)):
            clip_id = f"{s['session_id'][:8]}-{n:05d}"
            ticks[clip_id] = piece
            rows.append(
                {
                    "clip_id": clip_id,
                    "label": label,
                    "level": level,
                    "source": source,
                    "session_id": s["session_id"],
                    "segment_id": segment_id,
                    "play_day": s["play_day"],
                    "start_tick": int(piece[0]),
                    "end_tick": int(piece[-1]),
                    "n_ticks": len(piece),
                }
            )
    clips = pl.from_dicts(rows, schema=CLIP_SCHEMA) if rows else pl.DataFrame(schema=CLIP_SCHEMA)
    clips = balance(clips, seed)
    return EvalSet(clips, {c: ticks[c] for c in clips["clip_id"].to_list()})


def balance(clips: pl.DataFrame, seed: int = 0) -> pl.DataFrame:
    """Per set, as many human clips as bot clips: the larger class sampled down, taking clips
    from its sessions in turn so every session stays in (a plain random sample can drop all but
    the biggest session, and session-split scoring then has nothing to split)."""
    out = []
    for _, g in clips.group_by("label", maintain_order=True):
        counts = {src: g.filter(pl.col("source") == src).height for src in SOURCES}
        n = min(counts.values())
        if n == 0:
            continue
        for src in SOURCES:
            part = g.filter(pl.col("source") == src)
            out.append(_round_robin(part, n, seed) if part.height > n else part)
    return pl.concat(out).sort("label", "source", "clip_id") if out else clips.clear()


def _round_robin(clips: pl.DataFrame, n: int, seed: int) -> pl.DataFrame:
    """n clips: each session's clips shuffled, then one from each session in turn."""
    shuffled = clips.sample(fraction=1.0, shuffle=True, seed=seed)
    turn = shuffled.with_columns(turn=pl.int_range(pl.len()).over("session_id"))
    return turn.sort("turn", "session_id").head(n).drop("turn")
