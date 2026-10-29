"""The Phase 2 gate numbers.

Gate 1: at least 90% label precision on 200 hand-checked segments, for every set with enough
        data. A set has enough data once the detectors have produced CHECK_SAMPLE spans of it.
        Sets that read one recorded thing directly (DIRECT_SETS: an event, a state flag or a held
        key) need only a SPOT_CHECK: they can only be wrong through a pipeline bug, which a few
        dozen checks find. The full sample is for sets the labeler infers.
        Precision counts review-tool verdicts on spans that still exist: re-running a changed
        detector makes verdicts on spans it no longer produces stale.
Gate 2: other.unlabeled is under 15% of active play time.
"""

from __future__ import annotations

import math

import polars as pl

from hbpipe.label.run import UNLABELED
from hbpipe.store import Store

CHECK_SAMPLE = 200
SPOT_CHECK = 30
DIRECT_SETS = frozenset(
    {
        "build.place",  # a block-placed event
        "mine.block",  # destroy-started to block-broken events
        "inv.hotbar",  # selected-slot changes
        "inv.eat",  # use held with food
        "inv.container",  # a container screen open
        "use.trade",  # a merchant screen open
        "use.bucket",  # bucket use events
        "use.mechanism",  # use on doors, buttons, levers
        "combat.melee",  # hit events
        "move.sprint",  # the sprinting flag on the ground
        "move.sneak",  # the crouching flag while moving
        "move.strafe",  # one side key held while moving
        "move.swim",  # the in-water or swimming flag
        "move.climb",  # the on-ladder flag while moving
        "move.glide",  # the gliding flag
        "move.ride",  # the passenger flag
    }
)
PRECISION_GOAL = 0.90
UNLABELED_GOAL = 0.15


def wilson_lower(correct: int, n: int, z: float = 1.96) -> float:
    """Lower end of the 95% Wilson interval for a proportion."""
    if n == 0:
        return 0.0
    p = correct / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / (1 + z * z / n)


def check_target(label: str) -> int:
    """How many checked spans a set needs for the gate."""
    return SPOT_CHECK if label in DIRECT_SETS else CHECK_SAMPLE


def set_report(store: Store) -> pl.DataFrame:
    """One row per label set: spans, seconds, verdicts, precision, and whether it passes (human
    sessions only; bot sessions are labeled for the discriminator)."""
    labels = store.labels(source="human")
    if labels.is_empty():
        return pl.DataFrame()
    per_set = (
        labels.filter(pl.col("label") != UNLABELED)
        .group_by("label", "level")
        .agg(
            spans=pl.len(),
            seconds=((pl.col("end_tick") - pl.col("start_tick") + 1).sum() / 20).round(1),
        )
    )
    verdicts = store.corrections().filter(pl.col("verdict").is_in(["correct", "wrong"]))
    live = verdicts.join(labels.select("label_id"), on="label_id", how="semi")
    judged = live.group_by("label").agg(
        checked=pl.len(), correct=(pl.col("verdict") == "correct").sum()
    )
    stale = verdicts.join(labels.select("label_id"), on="label_id", how="anti")
    stale = stale.group_by("label").agg(stale=pl.len())
    out = (
        per_set.join(judged, on="label", how="left")
        .join(stale, on="label", how="left")
        .with_columns(pl.col("checked", "correct", "stale").fill_null(0))
        .with_columns(
            precision=pl.when(pl.col("checked") > 0).then(pl.col("correct") / pl.col("checked")),
            enough_data=pl.col("spans") >= CHECK_SAMPLE,
            target=pl.when(pl.col("label").is_in(list(DIRECT_SETS)))
            .then(SPOT_CHECK)
            .otherwise(CHECK_SAMPLE),
        )
    )
    lower = [wilson_lower(c, n) for c, n in out.select("correct", "checked").iter_rows()]
    return (
        out.with_columns(precision_low=pl.Series(lower))
        .with_columns(
            passes=pl.when(~pl.col("enough_data"))
            .then(None)
            .otherwise(
                (pl.col("checked") >= pl.col("target")) & (pl.col("precision") >= PRECISION_GOAL)
            )
        )
        .sort("level", "label")
    )


def unlabeled_share(store: Store) -> float:
    """other.unlabeled ticks over all active ticks, across human sessions."""
    labels = store.labels(source="human")
    if labels.is_empty():
        return 0.0
    spans = labels.with_columns(n=pl.col("end_tick") - pl.col("start_tick") + 1)
    active = (
        spans.group_by("session_id", "segment_id")
        .agg(n=pl.col("end_tick").max() - pl.col("start_tick").min() + 1)["n"]
        .sum()
    )
    unl = spans.filter(pl.col("label") == UNLABELED)["n"].sum()
    return unl / active if active else 0.0


def format_report(store: Store) -> str:
    sets = set_report(store)
    if sets.is_empty():
        return "no labels yet: run hbpipe label first"
    share = unlabeled_share(store)
    lines = [
        f"Gate 2: other.unlabeled is {share:.1%} of active play "
        f"({'pass' if share < UNLABELED_GOAL else 'FAIL'}, goal under {UNLABELED_GOAL:.0%})",
        "",
        f"Gate 1: precision on {CHECK_SAMPLE} checked spans per set with enough data, "
        f"{SPOT_CHECK} for sets read straight from an event or flag (goal {PRECISION_GOAL:.0%})",
        f"  {'set':24s} {'spans':>6s} {'secs':>8s} {'checked':>8s} {'precision':>10s}  status",
    ]
    for r in sets.iter_rows(named=True):
        prec = (
            "" if r["precision"] is None else f"{r['precision']:.0%} (>={r['precision_low']:.0%})"
        )
        if not r["enough_data"]:
            status = f"needs data ({r['spans']}/{CHECK_SAMPLE} spans)"
        elif r["checked"] < r["target"]:
            status = f"check {r['target'] - r['checked']} more"
        else:
            status = "pass" if r["passes"] else "FAIL"
        if r["stale"]:
            status += f", {r['stale']} stale verdicts"
        lines.append(
            f"  {r['label']:24s} {r['spans']:6d} {r['seconds']:8.0f} {r['checked']:8d} "
            f"{prec:>10s}  {status}"
        )
    from hbpipe.exclusions import training_summary

    return "\n".join([*lines, "", *training_summary(store)])
