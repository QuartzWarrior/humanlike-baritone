"""Corrections win over detector output.

The review tool stores verdicts in their own table (hbpipe.store); the labeler never touches them.
Effective labels, the ones training reads, are the detector's labels with every verdict applied:

    correct     the span stays; source becomes "checked"
    wrong       the span goes; if the reviewer said what it was, that label takes its place
    added       a span the detectors missed, added by hand
"""

from __future__ import annotations

import polars as pl

COLUMNS = (
    "session_id",
    "segment_id",
    "start_tick",
    "end_tick",
    "label",
    "level",
    "confidence",
    "source",
)


def effective_labels(labels: pl.DataFrame, corrections: pl.DataFrame) -> pl.DataFrame:
    """``labels`` from the labels table, ``corrections`` from the corrections table. When a span
    has several verdicts, the newest wins."""
    base = labels.with_columns(source=pl.lit("detector"))
    if corrections.is_empty():
        return base.select(COLUMNS)
    latest = corrections.sort("id").unique("label_id", keep="last", maintain_order=True)
    judged = latest.filter(pl.col("label_id").is_not_null())
    checked = judged.filter(pl.col("verdict") == "correct").select("label_id")
    wrong = judged.filter(pl.col("verdict") == "wrong")

    kept = base.join(wrong.select("label_id"), on="label_id", how="anti").with_columns(
        source=pl.when(pl.col("label_id").is_in(checked["label_id"].implode()))
        .then(pl.lit("checked"))
        .otherwise(pl.col("source"))
    )
    relabeled = wrong.filter(pl.col("new_label").is_not_null()).select(
        "session_id",
        "segment_id",
        "start_tick",
        "end_tick",
        label=pl.col("new_label"),
    )
    added = corrections.filter(pl.col("verdict") == "added").select(
        "session_id", "segment_id", "start_tick", "end_tick", "label"
    )
    by_hand = pl.concat([relabeled, added]).with_columns(
        level=pl.when(pl.col("label").str.starts_with("act."))
        .then(pl.lit("activity"))
        .otherwise(pl.lit("primitive")),
        confidence=pl.lit(1.0),
        source=pl.lit("correction"),
    )
    return pl.concat(
        [
            kept.select(COLUMNS),
            by_hand.select(COLUMNS).cast(dict(kept.select(COLUMNS).schema)),
        ]
    ).sort("session_id", "start_tick")
