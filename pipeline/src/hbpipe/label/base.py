"""Detector framework: spans, the registry, and the per-segment view detectors read.

A detector is a small pure function from one recording segment to labeled spans:

    @detector("move.swim")
    def swim(seg: Seg) -> list[Span]:
        return seg.mask_spans(seg.col("in_water"), "move.swim", min_len=5, confidence=0.9)

Detectors run in stages. Stage 0 (primitives) sees only the recording. Stage 1 (activities) also
sees stage 0's spans in ``seg.labels``; stage 2 sees both (for labels such as meta.switch that sit
on activity boundaries).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np
import polars as pl

from hbpipe.schema import pb


@dataclass(frozen=True, order=True)
class Span:
    start_tick: int
    end_tick: int  # inclusive
    label: str
    confidence: float

    @property
    def ticks(self) -> int:
        return self.end_tick - self.start_tick + 1

    def overlaps(self, other: Span) -> bool:
        return self.start_tick <= other.end_tick and other.start_tick <= self.end_tick


@dataclass
class Seg:
    """One recording segment. Every frame holds only this segment's rows, sorted by tick.

    ticks    cleaned ticks (hbpipe.label.clean), one row per tick, no gaps
    events   enriched game events (hbpipe.label.clean.enrich_events); none from paused time
    mouse, keys, buttons, gui    the raw input tables
    labels   spans from earlier stages
    """

    session_id: str
    segment_id: str
    header: pb.SessionHeader
    ticks: pl.DataFrame
    events: pl.DataFrame
    mouse: pl.DataFrame
    keys: pl.DataFrame
    buttons: pl.DataFrame
    gui: pl.DataFrame
    labels: list[Span] = field(default_factory=list)
    _cols: dict[str, np.ndarray] = field(default_factory=dict, repr=False)
    _memo: dict = field(default_factory=dict, repr=False)

    def __len__(self) -> int:
        return self.ticks.height

    @cached_property
    def tick(self) -> np.ndarray:
        """Tick number of each row."""
        return self.ticks["tick"].to_numpy().astype(np.int64)

    def col(self, name: str) -> np.ndarray:
        """A tick column as a numpy array (cached). Nulls become NaN for numbers, False for
        booleans and None for strings."""
        if name not in self._cols:
            s = self.ticks[name]
            if s.dtype == pl.Boolean:
                arr = s.fill_null(False).to_numpy()
            elif s.dtype.is_numeric():
                arr = s.cast(pl.Float64).fill_null(np.nan).to_numpy()
            else:
                arr = s.to_numpy()
            self._cols[name] = arr
        return self._cols[name]

    def memo(self, key: str, fn: Callable[[Seg], object]):
        """``fn(self)``, computed once per segment and shared by every detector asking for
        ``key``. Only for results that don't depend on ``labels`` from a later stage."""
        if key not in self._memo:
            self._memo[key] = fn(self)
        return self._memo[key]

    def index_of(self, tick: int) -> int:
        """Row index of a tick, clamped into the segment."""
        i = int(np.searchsorted(self.tick, tick))
        return min(max(i, 0), len(self) - 1)

    def events_of(self, *kinds: str) -> pl.DataFrame:
        return self.events.filter(pl.col("kind").is_in(kinds)) if kinds else self.events

    def labeled(self, *names: str) -> list[Span]:
        """Spans from earlier stages whose label is one of ``names`` (or starts with a name
        ending in '.', e.g. 'mine.')."""
        return [
            s
            for s in self.labels
            if any(s.label == n or (n.endswith(".") and s.label.startswith(n)) for n in names)
        ]

    def span(self, i0: int, i1: int, label: str, confidence: float) -> Span:
        """A span from row indices (inclusive)."""
        return Span(int(self.tick[i0]), int(self.tick[i1]), label, round(float(confidence), 3))

    def tick_span(self, t0: int, t1: int, label: str, confidence: float) -> Span:
        """A span from tick numbers, clamped into the segment."""
        lo, hi = int(self.tick[0]), int(self.tick[-1])
        t0, t1 = max(lo, min(t0, hi)), max(lo, min(t1, hi))
        return Span(min(t0, t1), max(t0, t1), label, round(float(confidence), 3))

    def mask_spans(
        self,
        mask: np.ndarray,
        label: str,
        *,
        min_len: int = 1,
        max_gap: int = 0,
        confidence: float | Callable[[int, int], float] = 0.8,
    ) -> list[Span]:
        """Spans over the runs of True in a per-tick mask. ``confidence`` may be a function of
        the run's row indices."""
        out = []
        for i0, i1 in runs(mask, min_len=min_len, max_gap=max_gap):
            c = confidence(i0, i1) if callable(confidence) else confidence
            out.append(self.span(i0, i1, label, c))
        return out


def runs(mask: np.ndarray, *, min_len: int = 1, max_gap: int = 0) -> list[tuple[int, int]]:
    """Inclusive (start, end) index pairs of True runs. Runs separated by at most ``max_gap``
    False values are merged first; then runs shorter than ``min_len`` are dropped."""
    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    pairs = list(zip(edges[0::2], edges[1::2] - 1, strict=True))
    merged: list[list[int]] = []
    for a, b in pairs:
        if merged and a - merged[-1][1] - 1 <= max_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(int(a), int(b)) for a, b in merged if b - a + 1 >= min_len]


def merge(spans: Iterable[Span], gap: int = 0) -> list[Span]:
    """Joins same-label spans that overlap or sit within ``gap`` ticks; keeps the best
    confidence."""
    out: list[Span] = []
    for s in sorted(spans):
        if out and out[-1].label == s.label and s.start_tick - out[-1].end_tick - 1 <= gap:
            last = out[-1]
            out[-1] = Span(
                last.start_tick,
                max(last.end_tick, s.end_tick),
                s.label,
                max(last.confidence, s.confidence),
            )
        else:
            out.append(s)
    return out


def groups(ticks: Sequence[int] | np.ndarray, gap: int) -> list[np.ndarray]:
    """Sorted ticks split wherever two neighbours are more than ``gap`` apart."""
    t = np.sort(np.asarray(ticks, dtype=np.int64))
    if t.size == 0:
        return []
    return np.split(t, np.flatnonzero(np.diff(t) > gap) + 1)


def clusters(
    seg: Seg,
    ticks: Sequence[int] | np.ndarray,
    label: str,
    confidence: float | Sequence[float],
    *,
    gap: int,
    before: int,
    after: int,
) -> list[Span]:
    """Groups event ticks at most ``gap`` apart; each group spans ``before`` ticks ahead of its
    first event to ``after`` past its last, with the best confidence in it."""
    t = np.asarray(ticks, dtype=np.int64)
    if t.size == 0:
        return []
    conf = np.broadcast_to(np.asarray(confidence, dtype=float), t.shape)
    order = np.argsort(t, kind="stable")
    t, conf = t[order], conf[order]
    out = []
    for idx in np.split(np.arange(t.size), np.flatnonzero(np.diff(t) > gap) + 1):
        g = t[idx]
        out.append(seg.tick_span(int(g[0]) - before, int(g[-1]) + after, label, conf[idx].max()))
    return merge(out)


Detector = Callable[[Seg], Iterable[Span]]


@dataclass(frozen=True)
class DetectorInfo:
    name: str
    level: str  # primitive | activity
    stage: int
    version: int
    fn: Detector


REGISTRY: dict[str, DetectorInfo] = {}


def detector(
    name: str, *, level: str = "primitive", stage: int | None = None, version: int = 1
) -> Callable[[Detector], Detector]:
    """Registers a detector for the label set ``name``. Its spans must carry that label."""
    if level not in ("primitive", "activity"):
        raise ValueError(level)

    def register(fn: Detector) -> Detector:
        if name in REGISTRY:
            raise ValueError(f"two detectors for {name}")
        s = stage if stage is not None else (0 if level == "primitive" else 1)
        REGISTRY[name] = DetectorInfo(name, level, s, version, fn)
        return fn

    return register


def run_detector(info: DetectorInfo, seg: Seg) -> list[Span]:
    spans = sorted(info.fn(seg))
    for s in spans:
        if s.label != info.name:
            raise ValueError(f"{info.name} returned a {s.label} span")
        if s.end_tick < s.start_tick:
            raise ValueError(f"{info.name} returned a reversed span {s}")
    return spans
