"""Runs the labeling passes over converted sessions.

    1. clean        hbpipe.label.clean (derived features, world context)
    2. primitives   stage-0 detectors, per segment
    3. activities   stage-1 detectors (they also see the primitives)
       ...          stage-2 detectors (they also see activities)
    then every active tick no primitive claims becomes other.unlabeled, unless a set named from a
    discovery cluster claims it (hbpipe.label.discovery).

    4. traces       hbpipe.label.traces, from the mining-job spans (none that touch play kept out
                    of training: hbpipe.exclusions)

Writes tables/labels, tables/ticks_clean, tables/traces and tables/trace_volumes (one Parquet file
per session each), and the labels into index.sqlite. Discovery (pass 5) runs across sessions
afterwards: hbpipe.label.discovery.discover.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from hbpipe import exclusions
from hbpipe.label import detectors  # noqa: F401  (registers every detector)
from hbpipe.label.base import REGISTRY, Seg, Span, run_detector
from hbpipe.label.clean import clean, enrich_events
from hbpipe.label.discovery import NamedSet, claim, named_sets
from hbpipe.label.traces import extract as extract_traces
from hbpipe.schema import pb
from hbpipe.store import Store

LABELER_VERSION = 1
UNLABELED = "other.unlabeled"
LABEL_CODE = Path(__file__).parent  # hbpipe/label: every file in it can change the labels

LABELS_SCHEMA = {
    "label_id": pl.String,
    "session_id": pl.String,
    "segment_id": pl.String,
    "start_tick": pl.UInt64,
    "end_tick": pl.UInt64,
    "start_t_ns": pl.UInt64,
    "end_t_ns": pl.UInt64,
    "label": pl.String,
    "level": pl.String,
    "confidence": pl.Float64,
    "detector_version": pl.Int64,
}


def header_of(store: Store, session_id: str) -> pb.SessionHeader:
    from google.protobuf import json_format

    text = (store.root / "sessions" / session_id / "header.json").read_text(encoding="utf-8")
    return json_format.Parse(text, pb.SessionHeader())


@dataclass
class Prepared:
    header: pb.SessionHeader
    ticks: pl.DataFrame  # cleaned
    events: pl.DataFrame  # enriched
    world: pl.DataFrame
    segments: list[Seg]


def prepare(store: Store, session_id: str) -> Prepared:
    """Pass 1 for a session, and its segments ready for detectors."""
    header = header_of(store, session_id)
    world = store.read("world", session_id)
    ticks = clean(store.read("ticks", session_id), world, header)
    events = enrich_events(store.read("events", session_id), ticks, world)
    events = events.filter(~pl.col("while_paused"))
    inputs = {name: store.read(name, session_id) for name in ("mouse", "keys", "buttons", "gui")}
    segs = []
    for (segment_id,), seg_ticks in ticks.partition_by(
        "segment_id", as_dict=True, maintain_order=True
    ).items():
        seg_ticks = seg_ticks.sort("tick")
        lo, hi = seg_ticks["tick"][0], seg_ticks["tick"][-1]

        def within(df: pl.DataFrame, segment_id=segment_id, lo=lo, hi=hi) -> pl.DataFrame:
            return df.filter(
                (pl.col("segment_id") == segment_id) & pl.col("tick").is_between(lo, hi)
            ).sort("tick", "t_ns")

        segs.append(
            Seg(
                session_id=session_id,
                segment_id=segment_id,
                header=header,
                ticks=seg_ticks,
                events=within(events),
                **{name: within(df) for name, df in inputs.items()},
            )
        )
    return Prepared(header, ticks, events, world, segs)


def unlabeled(seg: Seg, primitives: list[Span]) -> list[Span]:
    """Runs of ticks that no primitive span covers."""
    covered = np.zeros(len(seg), dtype=bool)
    for s in primitives:
        covered[seg.index_of(s.start_tick) : seg.index_of(s.end_tick) + 1] = True
    return seg.mask_spans(~covered, UNLABELED, confidence=1.0)


def label_segment(seg: Seg, named: Sequence[NamedSet] = ()) -> list[tuple[Span, str, int]]:
    """(span, level, detector version) for every label in a segment."""
    out: list[tuple[Span, str, int]] = []
    for stage in sorted({d.stage for d in REGISTRY.values()}):
        found = []
        for info in REGISTRY.values():
            if info.stage == stage:
                found += [(s, info.level, info.version) for s in run_detector(info, seg)]
        seg.labels = seg.labels + [s for s, _, _ in found]
        out += found
    prims = [s for s, level, _ in out if level == "primitive"]
    found, rest = claim(seg, unlabeled(seg, prims), list(named))
    out += [(s, "primitive", LABELER_VERSION) for s in found]
    out += [(s, "other", LABELER_VERSION) for s in rest]
    return out


def label_id(session_id: str, segment_id: str, span: Span) -> str:
    key = f"{session_id}|{segment_id}|{span.start_tick}|{span.end_tick}|{span.label}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def fingerprint(store: Store) -> str:
    """What the labels depend on besides the recording: the labeling code, a trained activity
    model and the sets named from discovery. A session labeled under another fingerprint is
    labeled again by label_all."""
    h = hashlib.sha1(str(LABELER_VERSION).encode())
    for path in sorted(LABEL_CODE.rglob("*.py")):
        h.update(path.relative_to(LABEL_CODE).as_posix().encode())
        h.update(path.read_bytes().replace(b"\r\n", b"\n"))
    from hbpipe.label import activity_model

    model = store.root / activity_model.MODEL_DIR / f"{activity_model.MODEL_NAME}.lgbm"
    if model.exists():
        h.update(model.read_bytes())
    for row in store.db.execute("SELECT name, centroid, radius FROM discovered_sets ORDER BY name"):
        h.update(f"{row['name']}|{row['centroid']}|{row['radius']}".encode())
    return h.hexdigest()[:16]


def label_session(store: Store, session_id: str) -> pl.DataFrame:
    from hbpipe.label import activity_model
    from hbpipe.label.detectors import activities

    activities.use_model(activity_model.load(store.root))
    prepared = prepare(store, session_id)
    named = named_sets(store.db)
    t_ns = dict(zip(prepared.ticks["tick"], prepared.ticks["t_ns"], strict=True))
    rows = []
    seen: set[str] = set()
    for seg in prepared.segments:
        for span, level, version in label_segment(seg, named):
            lid = label_id(session_id, seg.segment_id, span)
            if lid in seen:
                continue  # the same span twice from one detector: one label (the id is the key)
            seen.add(lid)
            rows.append(
                {
                    "label_id": lid,
                    "session_id": session_id,
                    "segment_id": seg.segment_id,
                    "start_tick": span.start_tick,
                    "end_tick": span.end_tick,
                    "start_t_ns": t_ns[span.start_tick],
                    "end_t_ns": t_ns[span.end_tick],
                    "label": span.label,
                    "level": level,
                    "confidence": span.confidence,
                    "detector_version": version,
                }
            )
    labels = (
        pl.from_dicts(rows, schema=LABELS_SCHEMA) if rows else pl.DataFrame(schema=LABELS_SCHEMA)
    )
    traces, volumes = extract_traces(
        session_id, labels, prepared.events, prepared.ticks, prepared.world
    )
    traces, volumes = _drop_excluded_jobs(
        traces, volumes, exclusions.for_session(store, session_id)
    )
    store.write_tables(
        session_id,
        {
            "labels": labels,
            "ticks_clean": prepared.ticks,
            "traces": traces,
            "trace_volumes": volumes,
        },
    )
    store.replace_labels(session_id, labels, LABELER_VERSION, fingerprint(store))
    return labels


def _drop_excluded_jobs(
    traces: pl.DataFrame, volumes: pl.DataFrame, excluded: list[exclusions.Exclusion]
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Traces and volumes without the jobs that touch an exclusion."""
    if not excluded or traces.is_empty():
        return traces, volumes
    jobs = traces.group_by("trace_id").agg(start=pl.col("tick").min(), end=pl.col("tick").max())
    kept = exclusions.drop_excluded(jobs, excluded, "start", "end")["trace_id"].to_list()
    return traces.filter(pl.col("trace_id").is_in(kept)), volumes.filter(
        pl.col("trace_id").is_in(kept)
    )


def label_all(root: str, *, force: bool = False) -> dict[str, pl.DataFrame]:
    """Labels every converted session (human, and bot for the discriminator) whose labels are
    missing or were made under another fingerprint (changed detector code, a newly trained model,
    a newly named set)."""
    store = Store(root)
    try:
        out = {}
        current = fingerprint(store)
        for row in store.sessions():
            if not force and row["labeler_fingerprint"] == current:
                continue
            out[row["session_id"]] = label_session(store, row["session_id"])
        return out
    finally:
        store.close()


def coverage(labels: pl.DataFrame) -> dict[str, float]:
    """Share of active ticks per label (a tick can carry several labels), plus the gate
    number: other.unlabeled's share of active play."""
    spans = labels.with_columns(ticks=(pl.col("end_tick") - pl.col("start_tick") + 1))
    active = _active_ticks(labels)
    by_label = spans.group_by("label").agg(pl.col("ticks").sum()).sort("ticks", descending=True)
    return {lab: n / active for lab, n in by_label.iter_rows()} if active else {}


def _active_ticks(labels: pl.DataFrame) -> int:
    """Ticks covered by any label; every active tick is covered (other.unlabeled fills the
    rest), so this is the segment's tick count."""
    n = 0
    for _, g in labels.group_by("session_id", "segment_id"):
        lo, hi = g["start_tick"].min(), g["end_tick"].max()
        n += int(hi - lo + 1)
    return n
