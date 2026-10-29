"""Mining primitives and shape labels (spec: Action taxonomy, mining).

Everything here reads the segment's block_broken events, each carrying the player's feet position
at the break (player_x/y/z) and how many of the block's neighbours were already open
(open_sides). A span starts on the tick its first block began breaking (its destroy_started, see
``Breaks.start``) and ends on its last break.

The excavation shapes (area_clear, strip, tunnel, staircase) group breaks of solid blocks, minus
shaft work (dig_down and dig_up breaks), into jobs: consecutive breaks at most JOB_GAP ticks apart
and at most JOB_STEP blocks (Chebyshev) from the previous break. Within a job these claim breaks
in order, each from what the earlier ones left, so area_clear, strip and tunnel never share one:

    area_clear  1. room: dug columns inside a fully dug SQUARE x SQUARE block of columns (a
                   morphological opening of the job's footprint); runs of breaks in them
                2. perimeter: corridors forming a ring around undug ground at least RING_INNER
                   columns across, on three sides or on four with gaps (clearing edges first)
                3. hollowing: runs of breaks out of space already dug, where most blocks have
                   HOLLOW_OPEN or more open sides (boring into rock opens one or two), which
                   finds rooms cleared across pauses
    strip       a straight corridor at least STRIP_MAIN long with side corridors leaving it at
                two or more interior points, on the same floor
    tunnel      the remaining straight corridors at least TUNNEL_MIN_LEN long, 1-3 wide, 2-4
                high, mostly dug out, dug roughly front to back, with the player inside

Corridors come from a greedy decomposition of a footprint: take the longest straight run of dug
columns (parallel rows beside it merged, up to MAX_WIDTH wide; up to ROW_GAP undug columns
skipped), keep its columns, repeat. Later corridors may run through columns an earlier one kept
(corners, crossings), which count toward their length. A corridor's breaks are cut into time
chunks wherever more than CHUNK_GAP other breaks, or any claimed one, intervene; each chunk is
judged on its own.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import polars as pl

from hbpipe.label.base import Seg, Span, detector, merge, runs
from hbpipe.label.blocks import is_leaves, is_log, is_ore, solid

JOB_GAP = 200  # ticks between consecutive breaks of one job
JOB_STEP = 6  # blocks (Chebyshev) between consecutive breaks of one job
X, Z = 0, 2  # axis indices into a position

SQUARE = 4  # side of the fully dug column square that marks an area
AREA_GAP = 4  # other breaks allowed inside a run of area breaks
AREA_MIN_BREAKS = 24
AREA_MIN_COLUMNS = 12

MAX_WIDTH = 3  # widest corridor, in columns
ROW_GAP = 2  # undug columns a corridor may skip (a cave pocket, an earlier hole)
ROW_DENSITY = 0.75  # dug share of a corridor row that skips columns
WIDEN_SHARE = 0.7  # how much of a corridor's length a parallel row needs to widen it
CHUNK_GAP = 12  # other breaks that split a corridor's breaks into two chunks
TUNNEL_MIN_LEN = 5
TUNNEL_FILL = 0.55  # dug share of the corridor's length x width x height box
TUNNEL_ORDER = 0.6  # rank correlation of break order with position along the corridor
BAND_COVER = 0.85  # share of a corridor's blocks inside its width and height bands
RING_INNER = 4  # smallest undug interior of a perimeter ring, both ways
RING_SOLID = 0.75  # dug share of a side that counts as built
RING_GAPPY = 0.5  # dug share each side needs when all four are there
RING_OVERHANG = 2  # how far an end side may run past the sides it joins
RING_INTERIOR_DUG = 0.15  # dug share of the interior that still counts as undug
HOLLOW_OPEN = 3  # open sides of a block broken out of an existing room
HOLLOW_SHARE = 0.55  # share of such blocks among nearby breaks (a fresh 3x3 tunnel has 4/9)
HOLLOW_MEAN = 2.5  # mean open sides of a hollowing run (a fresh 3x3 tunnel has 2.33)
HOLLOW_WINDOW = 9  # breaks in the sliding window
HOLLOW_MIN = 16  # breaks in a hollowing run
HOLLOW_COLUMNS = 6  # distinct columns in a hollowing run (a shaft has one or two)
HOLLOW_HEIGHT = 5  # tallest hollowing run, in blocks
STRIP_MAIN = 8  # shortest main corridor of a strip mine
STRIP_BRANCH = 3  # shortest side branch
STRIP_MIN_BRANCHES = 2  # distinct interior branch points


@dataclass
class Breaks:
    """block_broken events of one segment, in tick order.

    start   tick the block began breaking: its destroy_started, else the break tick minus the
            event's ticks_taken, else the break tick (an instant break); never before the
            previous break
    sure    2 when ``start`` came from a destroy_started, 1 from ticks_taken, 0 otherwise
    open    neighbours already passable before the break (enriched ``open_sides``); NaN unknown
    """

    tick: np.ndarray
    pos: np.ndarray  # (n, 3) block x, y, z
    block: np.ndarray
    player: np.ndarray  # (n, 3) feet x, y, z; NaN when unknown
    refill: np.ndarray
    start: np.ndarray
    sure: np.ndarray
    open: np.ndarray

    def __len__(self) -> int:
        return len(self.tick)


def breaks(seg: Seg) -> Breaks:
    """The segment's breaks of known positions, in order (computed once per segment)."""
    return seg.memo("mine.breaks", _breaks)


def _breaks(seg: Seg) -> Breaks:
    ev = seg.events_of("block_broken").filter(pl.col("x").is_not_null())
    tick = ev["tick"].to_numpy().astype(np.int64)
    pos = ev.select("x", "y", "z").to_numpy().astype(np.int64).reshape(-1, 3)
    player = (
        ev.select("player_x", "player_y", "player_z")
        .cast(pl.Float64)
        .fill_null(np.nan)
        .to_numpy()
        .reshape(-1, 3)
    )
    taken = ev["ticks_taken"].fill_null(0).to_numpy().astype(np.int64)

    started: dict[tuple[int, int, int], list[int]] = {}
    ds = seg.events_of("destroy_started").filter(pl.col("x").is_not_null())
    for t, x, y, z in ds.select("tick", "x", "y", "z").iter_rows():
        started.setdefault((x, y, z), []).append(int(t))
    start = tick.copy()
    sure = np.zeros(len(tick), dtype=np.int64)
    prev = -(10**18)
    for k in range(len(tick)):
        t = int(tick[k])
        cands = [s for s in started.get(tuple(int(v) for v in pos[k]), ()) if prev < s <= t]
        if cands:
            start[k], sure[k] = max(cands), 2
        elif taken[k] > 0:
            start[k], sure[k] = min(t, max(t - int(taken[k]), prev + 1)), 1
        prev = t
    return Breaks(
        tick=tick,
        pos=pos,
        block=ev["block"].fill_null("").to_numpy(),
        player=player,
        refill=ev["refill"].to_numpy(),
        start=start,
        sure=sure,
        open=ev["open_sides"].cast(pl.Float64).fill_null(np.nan).to_numpy()
        if "open_sides" in ev.columns
        else np.full(len(tick), np.nan),
    )


def jobs(br: Breaks, keep: np.ndarray) -> list[np.ndarray]:
    """Break indices (among ``keep``) grouped into jobs."""
    idx = np.flatnonzero(keep)
    if not idx.size:
        return []
    gap = np.diff(br.tick[idx]) > JOB_GAP
    step = np.abs(np.diff(br.pos[idx], axis=0)).max(axis=1, initial=0) > JOB_STEP
    return np.split(idx, np.flatnonzero(gap | step) + 1)


def solid_breaks(br: Breaks) -> np.ndarray:
    return np.array([solid(b) for b in br.block], dtype=bool)


def _span(seg: Seg, br: Breaks, idx, label: str, confidence: float) -> Span:
    idx = np.asarray(idx)
    return seg.tick_span(int(br.start[idx].min()), int(br.tick[idx].max()), label, confidence)


def _band(values: np.ndarray, max_width: int, cover: float = BAND_COVER) -> tuple[int, int, bool]:
    """Narrowest [lo, hi] at most ``max_width`` wide holding ``cover`` of the values; else the
    fullest band of ``max_width`` and False."""
    best = (0, int(values.min()))
    for w in range(1, max_width + 1):
        best = (0, int(values.min()))
        for lo in range(int(values.min()), int(values.max()) - w + 2):
            n = int(np.count_nonzero((values >= lo) & (values < lo + w)))
            if n > best[0]:
                best = (n, lo)
        if best[0] >= cover * len(values):
            return best[1], best[1] + w - 1, True
    return best[1], best[1] + max_width - 1, False


def _ranks(v: np.ndarray) -> np.ndarray:
    """Ranks with ties sharing their mean rank."""
    _, inv, counts = np.unique(v, return_inverse=True, return_counts=True)
    return (np.cumsum(counts) - (counts + 1) / 2)[inv]


def _rank_corr(a: np.ndarray, b: np.ndarray) -> float:
    """Spearman correlation (0 when either side is constant)."""
    if len(a) < 3 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return 0.0
    return float(np.corrcoef(_ranks(a), _ranks(b))[0, 1])


# Footprints and corridors --------------------------------------------------------------------

Column = tuple[int, int]  # (x, z)


def _components(cells: set[Column]) -> list[set[Column]]:
    out, seen = [], set()
    for c in sorted(cells):
        if c in seen:
            continue
        comp, stack = set(), [c]
        seen.add(c)
        while stack:
            x, z = stack.pop()
            comp.add((x, z))
            for n in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
                if n in cells and n not in seen:
                    seen.add(n)
                    stack.append(n)
        out.append(comp)
    return out


def _areas(cols: set[Column]) -> list[set[Column]]:
    """Connected groups of columns that lie in some fully dug SQUARE x SQUARE square."""
    area: set[Column] = set()
    for x, z in cols:
        square = [(x + i, z + j) for i in range(SQUARE) for j in range(SQUARE)]
        if all(c in cols for c in square):
            area.update(square)
    return _components(area)


def _area_runs(col: list[Column], comp: set[Column]) -> list[tuple[int, int]]:
    """Runs of a job's breaks inside an area, each carried on through the dug columns touching
    the area (a room's half-dug last row) until AREA_GAP breaks in a row fall elsewhere."""
    marked = np.array([c in comp for c in col])
    edge = comp | {(x + i, z + j) for x, z in comp for i in (-1, 0, 1) for j in (-1, 0, 1)}
    out = []
    for a, b in runs(marked, max_gap=AREA_GAP):
        inner = np.flatnonzero(marked[a : b + 1]) + a
        if len(inner) < AREA_MIN_BREAKS or len({col[i] for i in inner}) < AREA_MIN_COLUMNS:
            continue
        misses = 0
        for j in range(b + 1, len(col)):
            if col[j] in edge:
                b, misses = j, 0
            elif (misses := misses + 1) > AREA_GAP:
                break
        out.append((a, b))
    return out


@dataclass
class Corridor:
    axis: int  # X or Z: the direction it runs
    lat: tuple[int, int]  # band across it (z for an X corridor), inclusive
    lo: int  # extent along it, inclusive, through columns an earlier corridor took
    hi: int
    cols: set[Column]  # its own columns
    own: tuple[int, int]  # extent of its own columns

    @property
    def length(self) -> int:
        return self.hi - self.lo + 1


def _along(c: Column, axis: int) -> int:
    return c[0] if axis == X else c[1]


def _across(c: Column, axis: int) -> int:
    return c[1] if axis == X else c[0]


def _runs_of(vals: list[int], gap: int) -> list[tuple[int, int]]:
    out = []
    lo = prev = vals[0]
    for v in vals[1:]:
        if v - prev > gap + 1:
            out.append((lo, prev))
            lo = v
        prev = v
    out.append((lo, prev))
    return out


def _row_runs(cols: set[Column], axis: int) -> dict[int, list[tuple[int, int]]]:
    """Runs of columns along ``axis``, keyed by the cross coordinate. A run may skip up to
    ROW_GAP columns (air that was already there) while ROW_DENSITY of it is dug."""
    rows: dict[int, list[int]] = {}
    for c in cols:
        rows.setdefault(_across(c, axis), []).append(_along(c, axis))
    out: dict[int, list[tuple[int, int]]] = {}
    for lat, vals in rows.items():
        vals.sort()
        for lo, hi in _runs_of(vals, ROW_GAP):
            dug = [v for v in vals if lo <= v <= hi]
            if len(dug) >= ROW_DENSITY * (hi - lo + 1):
                out.setdefault(lat, []).append((lo, hi))
            else:
                out.setdefault(lat, []).extend(_runs_of(dug, 0))
    return out


def _widen(rows: dict[int, list[tuple[int, int]]], lat: int, lo: int, hi: int) -> tuple[int, int]:
    """Cross band of a corridor: adds a neighbouring row while it is dug beside WIDEN_SHARE of
    the corridor's length (side branches and stray breaks touch it only here and there)."""
    band = [lat, lat]
    grown = True
    while grown and band[1] - band[0] + 1 < MAX_WIDTH:
        grown = False
        for side in (band[0] - 1, band[1] + 1):
            beside = sum(max(0, min(hi, b) - max(lo, a) + 1) for a, b in rows.get(side, ()))
            if beside >= WIDEN_SHARE * (hi - lo + 1):
                band = [min(band[0], side), max(band[1], side)]
                grown = True
                break
    return band[0], band[1]


def corridors(cols: set[Column]) -> list[Corridor]:
    """Greedy decomposition of a footprint into straight corridors, longest first. A later
    corridor may run through columns an earlier one took (a corner, a crossing): they count
    toward its length but stay with the earlier corridor, and at least half of its columns must
    be its own."""
    left, taken = set(cols), set()
    out: list[Corridor] = []
    while left:
        pool = left | taken
        best: Corridor | None = None
        for axis in (X, Z):
            rows = _row_runs(pool, axis)
            for lat, segs in rows.items():
                for lo, hi in segs:
                    if best is not None and hi - lo + 1 < best.length:
                        continue
                    a, b = _widen(rows, lat, lo, hi)
                    inside = {
                        c
                        for c in pool
                        if a <= _across(c, axis) <= b and lo <= _along(c, axis) <= hi
                    }
                    mine = inside & left
                    if not mine or len(mine) < 0.5 * len(inside):
                        continue
                    along = [_along(c, axis) for c in mine]
                    cand = Corridor(axis, (a, b), lo, hi, mine, (min(along), max(along)))
                    if best is None or (cand.length, len(mine)) > (best.length, len(best.cols)):
                        best = cand
        if best is None or best.length < 2:
            break
        out.append(best)
        left -= best.cols
        taken |= best.cols
    return out


@dataclass
class _Side:
    """Collinear corridors: one side of a would-be ring."""

    axis: int
    lat: tuple[int, int]
    along: set[int]
    members: list[int]

    def cover(self, lo: int, hi: int) -> float:
        return len(self.along & set(range(lo, hi + 1))) / (hi - lo + 1)


def _sides(cors: list[Corridor]) -> list[_Side]:
    out: list[_Side] = []
    for i in sorted(range(len(cors)), key=lambda i: (cors[i].axis, cors[i].lat)):
        c = cors[i]
        span = set(range(c.lo, c.hi + 1))
        for s in out:
            if s.axis == c.axis and s.lat[0] <= c.lat[1] and c.lat[0] <= s.lat[1]:
                s.lat = (min(s.lat[0], c.lat[0]), max(s.lat[1], c.lat[1]))
                s.along |= span
                s.members.append(i)
                break
        else:
            out.append(_Side(c.axis, c.lat, span, [i]))
    return out


def _ring_end(ends: list[_Side], at: int, p: _Side, q: _Side) -> _Side | None:
    """The side closing the gap between parallel sides ``p`` and ``q`` near ``at`` along them:
    it crosses most of the gap and stops at them (a strip mine's main tunnel runs on)."""
    a0, a1 = p.lat[1] + 1, q.lat[0] - 1
    best = None
    for e in ends:
        if abs((e.lat[0] + e.lat[1]) / 2 - at) > 2 or e.cover(a0, a1) < RING_GAPPY:
            continue
        if min(e.along) < p.lat[0] - RING_OVERHANG or max(e.along) > q.lat[1] + RING_OVERHANG:
            continue
        if best is None or e.cover(a0, a1) > best.cover(a0, a1):
            best = e
    return best


def _rings(cors: list[Corridor], dug: set[Column]) -> list[tuple[set[int], int]]:
    """(corridor indices, sides) of each perimeter ring: corridors around an undug interior of
    at least RING_INNER x RING_INNER columns, on three sides (each RING_SOLID dug) or on all
    four (each RING_GAPPY dug)."""
    sides = _sides(cors)
    out = []
    for axis in (X, Z):
        par = sorted((s for s in sides if s.axis == axis), key=lambda s: s.lat)
        ends = [s for s in sides if s.axis != axis]
        for i, p in enumerate(par):
            for q in par[i + 1 :]:
                a0, a1 = p.lat[1] + 1, q.lat[0] - 1
                lo, hi = max(min(p.along), min(q.along)), min(max(p.along), max(q.along))
                if a1 - a0 + 1 < RING_INNER or hi - lo + 1 < RING_INNER:
                    continue
                low, high = _ring_end(ends, lo, p, q), _ring_end(ends, hi, p, q)
                b0 = low.lat[1] + 1 if low else lo
                b1 = high.lat[0] - 1 if high else hi
                if b1 - b0 + 1 < RING_INNER:
                    continue
                span = (b0 - 1 if low else b0, b1 + 1 if high else b1)
                cover = [p.cover(*span), q.cover(*span)]
                cover += [e.cover(a0, a1) for e in (low, high) if e is not None]
                closed = len(cover) == 4 and min(cover) >= RING_GAPPY
                if not closed and sum(c >= RING_SOLID for c in cover) < 3:
                    continue
                inside = [
                    (b, a) if axis == X else (a, b)
                    for a in range(a0, a1 + 1)
                    for b in range(b0, b1 + 1)
                ]
                if sum(c in dug for c in inside) > RING_INTERIOR_DUG * len(inside):
                    continue
                members = {m for s in (p, q, low, high) if s is not None for m in s.members}
                out.append((members, len(cover)))
    return out


def _hollows(pos: np.ndarray, open_: np.ndarray, claimed: np.ndarray) -> list[tuple[int, int]]:
    """Runs of unclaimed breaks (job-local, never across a claimed one) that hollow out space
    already dug: in a sliding window of HOLLOW_WINDOW breaks at least HOLLOW_SHARE have
    HOLLOW_OPEN or more open sides, the run's mean is HOLLOW_MEAN or more, and it is a room, not
    a shaft (HOLLOW_COLUMNS columns, at most HOLLOW_HEIGHT tall)."""
    out = []
    kernel = np.ones(HOLLOW_WINDOW)
    for a, b in runs(~claimed):
        o = open_[a : b + 1]
        if len(o) < HOLLOW_MIN or np.isnan(o).all():
            continue
        many = (np.nan_to_num(o) >= HOLLOW_OPEN).astype(float)
        share = np.convolve(many, kernel, "same") / np.convolve(np.ones(len(o)), kernel, "same")
        for r0, r1 in runs(share >= HOLLOW_SHARE, min_len=HOLLOW_MIN, max_gap=2):
            p = pos[a + r0 : a + r1 + 1]
            if (
                np.nanmean(o[r0 : r1 + 1]) >= HOLLOW_MEAN
                and len({(int(x), int(z)) for x, _, z in p}) >= HOLLOW_COLUMNS
                and np.ptp(p[:, 1]) + 1 <= HOLLOW_HEIGHT
            ):
                out.append((a + r0, a + r1))
    return out


def _chunks(members: np.ndarray, claimed: np.ndarray) -> list[np.ndarray]:
    """Breaks cut wherever more than CHUNK_GAP other breaks, or any claimed one, come
    between."""
    cuts = [
        i + 1
        for i in range(len(members) - 1)
        if members[i + 1] - members[i] - 1 > CHUNK_GAP
        or claimed[members[i] + 1 : members[i + 1]].any()
    ]
    return np.split(members, cuts)


@dataclass
class Shapes:
    area: list[Span]
    strip: list[Span]
    tunnel: list[Span]


def _floor(pos: np.ndarray) -> int | None:
    lo, _, ok = _band(pos[:, 1], 4)
    return lo if ok else None


def _tunnel_confidence(
    pos: np.ndarray, order: np.ndarray, feet: np.ndarray, c: Corridor
) -> float | None:
    """Confidence that these breaks (some of corridor ``c``'s) are one tunnel, or None if they
    are not. Its length counts the columns it shares with earlier corridors at its ends. The
    player digs a tunnel from inside it: their feet (median block y) stand in its bottom rows,
    not above it (lowering a floor) or below it (cutting a ceiling)."""
    uniq = np.unique(pos, axis=0)
    along, across, y = uniq[:, c.axis], uniq[:, 2 - c.axis], uniq[:, 1]
    dug = int(np.ptp(along)) + 1
    length = dug
    length += c.own[0] - c.lo if along.min() == c.own[0] else 0
    length += c.hi - c.own[1] if along.max() == c.own[1] else 0
    if length < TUNNEL_MIN_LEN:
        return None
    a0, a1, ok_w = _band(across, MAX_WIDTH)
    y0, y1, ok_h = _band(y, 4)
    if not (ok_w and ok_h) or y1 == y0:
        return None
    feet = feet[~np.isnan(feet)]
    if len(feet) and not y0 <= np.median(np.floor(feet)) <= y1 - 1:
        return None
    inside = (across >= a0) & (across <= a1) & (y >= y0) & (y <= y1)
    fill = inside.sum() / (dug * (a1 - a0 + 1) * (y1 - y0 + 1))
    rho = abs(_rank_corr(order.astype(float), pos[:, c.axis].astype(float)))
    if fill < TUNNEL_FILL or rho < TUNNEL_ORDER:
        return None
    return min(0.95, 0.5 + 0.2 * min(fill, 1.0) + 0.15 * min(1.0, (length - 4) / 8) + 0.1 * rho)


def _strip_groups(cors: list[Corridor], floors: list[int | None]) -> list[tuple[list[int], int]]:
    """(corridor indices, number of branch points) of each strip mine: a main corridor with
    branches leaving its sides at STRIP_MIN_BRANCHES or more interior points."""
    used: set[int] = set()
    out = []
    for m in sorted(range(len(cors)), key=lambda i: -cors[i].length):
        main = cors[m]
        if m in used or main.length < STRIP_MAIN or floors[m] is None:
            continue
        points: list[tuple[float, int]] = []
        for i, c in enumerate(cors):
            if i == m or i in used or c.axis == main.axis:
                continue
            if c.own[1] - c.own[0] + 1 < STRIP_BRANCH:
                continue
            if floors[i] is None or abs(floors[i] - floors[m]) > 1:
                continue
            lo, hi = c.own
            touches = (
                1 <= lo - main.lat[1] <= 2
                or 1 <= main.lat[0] - hi <= 2
                or (lo < main.lat[0] and hi > main.lat[1])
            )
            point = (c.lat[0] + c.lat[1]) / 2
            if touches and main.lo + 2 <= point <= main.hi - 2:
                points.append((point, i))
        distinct: list[float] = []
        for p, _ in sorted(points):
            if not distinct or p - distinct[-1] > 1:
                distinct.append(p)
        if len(distinct) >= STRIP_MIN_BRANCHES:
            members = [m] + [i for _, i in points]
            used.update(members)
            out.append((members, len(distinct)))
    return out


def shapes(seg: Seg, br: Breaks | None = None) -> Shapes:
    """area_clear, strip and tunnel spans of a segment, with each break claimed at most once
    (computed once per segment; the three detectors share it)."""
    if br is None:
        return seg.memo("mine.shapes", lambda s: _shapes(s, breaks(s)))
    return _shapes(seg, br)


def _shapes(seg: Seg, br: Breaks) -> Shapes:
    out = Shapes([], [], [])
    for job in jobs(br, solid_breaks(br) & ~vertical_breaks(br)):
        pos = br.pos[job]
        col = [(int(p[0]), int(p[2])) for p in pos]
        claimed = np.zeros(len(job), dtype=bool)

        def area(a: int, b: int, confidence: float, claimed=claimed, job=job) -> None:
            claimed[a : b + 1] = True
            out.area.append(_span(seg, br, job[a : b + 1], "mine.area_clear", confidence))

        for comp in _areas(set(col)):
            bx = [c[0] for c in comp]
            bz = [c[1] for c in comp]
            fill = len(comp) / ((max(bx) - min(bx) + 1) * (max(bz) - min(bz) + 1))
            for a, b in _area_runs(col, comp):
                area(a, b, 0.6 + 0.35 * fill)

        free = np.flatnonzero(~claimed)
        cors = corridors({col[i] for i in free})
        ring: dict[int, int] = {}
        for members, n_sides in _rings(cors, {col[i] for i in free}):
            for m in members:
                ring[m] = max(ring.get(m, 0), n_sides)
        if ring:
            cols = set().union(*(cors[m].cols for m in ring))
            idx = np.array([i for i in free if col[i] in cols], dtype=np.int64)
            conf = 0.85 if max(ring.values()) == 4 else 0.75
            for chunk in _chunks(idx, claimed):
                area(int(chunk[0]), int(chunk[-1]), conf)

        for a, b in _hollows(pos, br.open[job], claimed):
            area(a, b, min(0.9, 0.5 + 0.12 * float(np.nanmean(br.open[job[a : b + 1]]))))

        free = np.flatnonzero(~claimed)
        cors = corridors({col[i] for i in free})
        members = [np.array([i for i in free if col[i] in c.cols], dtype=np.int64) for c in cors]
        floors = [_floor(pos[m]) if len(m) else None for m in members]

        in_strip: set[int] = set()
        for group, n_points in _strip_groups(cors, floors):
            in_strip.update(group)
            idx = np.sort(np.concatenate([members[i] for i in group]))
            conf = min(0.95, 0.75 + 0.05 * (n_points - STRIP_MIN_BRANCHES) + 0.01 * len(group))
            out.strip.append(_span(seg, br, job[idx], "mine.strip", conf))

        for i, c in enumerate(cors):
            if i in in_strip or c.length < TUNNEL_MIN_LEN or not len(members[i]):
                continue
            for chunk in _chunks(members[i], claimed):
                conf = _tunnel_confidence(pos[chunk], chunk, br.player[job[chunk], 1], c)
                if conf is not None:
                    out.tunnel.append(_span(seg, br, job[chunk], "mine.tunnel", conf))
    out.area = merge(out.area)
    return out


# Detectors -----------------------------------------------------------------------------------


@detector("mine.block")
def block(seg: Seg) -> list[Span]:
    """One span per break, from its destroy_started (or the break minus ticks_taken) to the
    break. A destroy_aborted with no break is not a span: the attempt ended without the block
    (aim slips while holding attack show up as zero-progress aborts)."""
    br = breaks(seg)
    conf = {2: 0.95, 1: 0.85, 0: 0.8}
    return [
        seg.tick_span(int(br.start[k]), int(br.tick[k]), "mine.block", conf[int(br.sure[k])])
        for k in range(len(br))
    ]


@detector("mine.area_clear")
def area_clear(seg: Seg) -> list[Span]:
    return shapes(seg).area


@detector("mine.strip")
def strip(seg: Seg) -> list[Span]:
    return shapes(seg).strip


@detector("mine.tunnel")
def tunnel(seg: Seg) -> list[Span]:
    return shapes(seg).tunnel


VEIN_GAP = 200  # ticks between ore breaks of one vein
VEIN_DETOUR = 6  # other breaks allowed between two ore breaks of one vein


def _ore_kind(block: str) -> str:
    return block.removeprefix("deepslate_")


@detector("mine.vein")
def vein(seg: Seg) -> list[Span]:
    """Two or more breaks of one ore, each touching (26-neighbourhood) an earlier ore of the
    same vein, at most VEIN_GAP ticks and VEIN_DETOUR other breaks after the previous ore."""
    br = breaks(seg)
    ore = [k for k in range(len(br)) if is_ore(br.block[k])]
    out = []
    open_veins: list[list[int]] = []
    for k in ore:
        home = None
        for v in open_veins:
            last = v[-1]
            if (
                _ore_kind(br.block[last]) == _ore_kind(br.block[k])
                and br.tick[k] - br.tick[last] <= VEIN_GAP
                and k - last - 1 <= VEIN_DETOUR
                and any(np.abs(br.pos[j] - br.pos[k]).max() <= 1 for j in v)
            ):
                home = v
                break
        if home is None:
            open_veins.append([k])
        else:
            home.append(k)
    for v in open_veins:
        if len(v) >= 2:
            out.append(_span(seg, br, v, "mine.vein", 0.9 if len(v) >= 3 else 0.8))
    return out


STAIR_MIN_STEPS = 4  # consecutive one-block steps


@dataclass
class _Stair:
    last: int  # last new slice (coordinate along the axis)
    direction: int
    lat: tuple[int, int]
    floor: dict[int, int]  # slice -> lowest broken y
    order: list[int]  # slices in first-visit order
    members: list[int]  # job-local break indices


def _stair_runs(pos: np.ndarray, axis: int) -> list[_Stair]:
    """Breaks walking one block at a time along ``axis`` (at most 2 wide), split into runs."""
    out: list[_Stair] = []
    cur: _Stair | None = None
    for k, p in enumerate(pos):
        a, lat, y = int(p[axis]), int(p[2 - axis]), int(p[1])
        if cur is not None:
            band = (min(cur.lat[0], lat), max(cur.lat[1], lat))
            fits = band[1] - band[0] < 2
            step = a - cur.last
            if fits and a in cur.floor:
                cur.floor[a] = min(cur.floor[a], y)
            elif fits and abs(step) == 1 and cur.direction in (0, step):
                cur.direction, cur.last = step, a
                cur.floor[a] = y
                cur.order.append(a)
            else:
                out.append(cur)
                cur = None
            if cur is not None:
                cur.lat = band
                cur.members.append(k)
                continue
        cur = _Stair(a, 0, (lat, lat), {a: y}, [a], [k])
    if cur is not None:
        out.append(cur)
    return out


@detector("mine.staircase")
def staircase(seg: Seg) -> list[Span]:
    """Breaks advancing one block at a time in one direction where each new slice's floor (its
    lowest broken block) is exactly one above or below the last, for STAIR_MIN_STEPS steps or
    more, while the player's feet move the same way by at least two blocks."""
    br = breaks(seg)
    out = []
    for job in jobs(br, solid_breaks(br) & ~vertical_breaks(br)):
        pos = br.pos[job]
        for axis in (X, Z):
            for run in _stair_runs(pos, axis):
                floors = np.array([run.floor[a] for a in run.order])
                d = np.diff(floors)
                best = None
                for sign in (1, -1):
                    for i0, i1 in runs(d == sign, min_len=STAIR_MIN_STEPS):
                        if best is None or i1 - i0 > best[1] - best[0]:
                            best = (i0, i1, sign)
                if best is None:
                    continue
                i0, i1, sign = best
                slices = set(run.order[i0 : i1 + 2])
                idx = [k for k in run.members if int(pos[k][axis]) in slices]
                feet = br.player[job[idx], 1]
                feet = feet[~np.isnan(feet)]
                if len(feet) < 2 or (feet[-1] - feet[0]) * sign < 2:
                    continue
                conf = 0.9 if i1 - i0 + 1 >= 6 else 0.8
                out.append(_span(seg, br, job[idx], "mine.staircase", conf))
    return out


DIG_GAP = 60  # ticks between breaks of one dig-down or dig-up
DIG_SKIP = 2  # other breaks allowed between two breaks of one
UP_REACH = 6  # highest block above the feet block one can break standing under it
UP_CLIMB = 100  # ticks after a dig-up to look for the player climbing into it


def _over_column(br: Breaks) -> np.ndarray:
    """Breaks in a block column the player's feet overlap."""
    px, pz = br.player[:, 0], br.player[:, 2]
    with np.errstate(invalid="ignore"):
        return (np.abs(br.pos[:, 0] + 0.5 - px) < 0.8) & (np.abs(br.pos[:, 2] + 0.5 - pz) < 0.8)


def _under_feet(br: Breaks) -> np.ndarray:
    """Breaks of a block the player's feet stand on (or the one below it, mid-fall)."""
    with np.errstate(invalid="ignore"):
        feet = np.floor(br.player[:, 1] - 0.01)
    by = br.pos[:, 1]
    return ((by == feet) | (by == feet - 1)) & _over_column(br)


def _above_head(br: Breaks) -> np.ndarray:
    """Breaks 2 to UP_REACH blocks above the feet block, over the player: above the head."""
    with np.errstate(invalid="ignore"):
        rise = br.pos[:, 1] - np.floor(br.player[:, 1] + 0.01)
    return (rise >= 2) & (rise <= UP_REACH) & _over_column(br)


def _chains(br: Breaks, mask: np.ndarray, step_ok=lambda last, k: True) -> list[list[int]]:
    """Runs of masked breaks, each at most DIG_GAP ticks and DIG_SKIP other breaks after the
    previous one, while ``step_ok(previous, next)``."""
    out: list[list[int]] = []
    for k in np.flatnonzero(mask):
        last = out[-1][-1] if out else None
        if (
            last is not None
            and br.tick[k] - br.tick[last] <= DIG_GAP
            and k - last - 1 <= DIG_SKIP
            and step_ok(last, k)
        ):
            out[-1].append(int(k))
        else:
            out.append([int(k)])
    return out


def _down_chains(br: Breaks) -> list[list[int]]:
    chains = _chains(
        br, _under_feet(br) & solid_breaks(br), lambda a, b: br.pos[b, 1] <= br.pos[a, 1]
    )
    return [c for c in chains if len(c) >= 3 and br.pos[c[0], 1] - br.pos[c[-1], 1] >= 2]


def _up_chains(br: Breaks) -> list[list[int]]:
    chains = _chains(br, _above_head(br) & solid_breaks(br))
    return [c for c in chains if len(c) >= 3 and np.ptp(br.pos[c, 1]) >= 2]


def vertical_breaks(br: Breaks) -> np.ndarray:
    """Breaks belonging to a dig-down or dig-up: shaft work, which the excavation shapes skip."""
    out = np.zeros(len(br), dtype=bool)
    for chain in _down_chains(br) + _up_chains(br):
        out[chain] = True
    return out


@detector("mine.dig_down")
def dig_down(seg: Seg) -> list[Span]:
    """Three or more breaks under the player's feet, each no higher than the last and at most
    DIG_GAP ticks after it (DIG_SKIP other breaks may come between), going down 2+ blocks."""
    br = breaks(seg)
    out = []
    for chain in _down_chains(br):
        cols = {(int(br.pos[k, 0]), int(br.pos[k, 2])) for k in chain}
        out.append(_span(seg, br, chain, "mine.dig_down", 0.9 if len(cols) <= 2 else 0.8))
    return out


@detector("mine.dig_up")
def dig_up(seg: Seg) -> list[Span]:
    """Three or more breaks above the player's head (2 to UP_REACH blocks above the feet block,
    in a column the feet overlap), each at most DIG_GAP ticks after the last (DIG_SKIP other
    breaks may come between), the highest 2+ blocks above the lowest. Heights need not rise
    break by break: a 2x2 shaft restarts at the bottom of each column.

    A span covers one burst of breaking upward. The jump-placing that lifts the player between
    bursts is build.pillar, so in a pillared shaft the two labels alternate, overlapping only
    where the player breaks and pillars within DIG_GAP ticks. Climbing a block or more (standing
    higher on the ground) by UP_CLIMB ticks after the burst raises the confidence; without it the
    burst may only be clearing a ceiling."""
    br = breaks(seg)
    y, ground = seg.col("y"), seg.col("on_ground")
    out = []
    for chain in _up_chains(br):
        i0 = seg.index_of(int(br.tick[chain[0]]))
        i1 = seg.index_of(int(br.tick[chain[-1]]) + UP_CLIMB)
        stood = y[i0 : i1 + 1][ground[i0 : i1 + 1]]
        climbed = len(stood) and stood.max() - br.player[chain[0], 1] >= 0.9
        out.append(_span(seg, br, chain, "mine.dig_up", 0.9 if climbed else 0.75))
    return out


TREE_GAP = 200  # ticks between log breaks of one tree
TREE_REACH = 4  # horizontal blocks from the first log


@detector("mine.tree")
def tree(seg: Seg) -> list[Span]:
    """Three or more log breaks within TREE_REACH blocks (horizontally) of the first and
    TREE_GAP ticks of each other, spanning at least three heights. Leaves broken during the
    span, or right around it, raise the confidence."""
    br = breaks(seg)
    logs = [k for k in range(len(br)) if is_log(br.block[k])]
    leaves = np.array([is_leaves(b) for b in br.block], dtype=bool)
    groups: list[list[int]] = []
    for k in logs:
        g = groups[-1] if groups else None
        if (
            g is not None
            and br.tick[k] - br.tick[g[-1]] <= TREE_GAP
            and np.abs(br.pos[k, [0, 2]] - br.pos[g[0], [0, 2]]).max() <= TREE_REACH
        ):
            g.append(k)
        else:
            groups.append([k])
    out = []
    for g in groups:
        if len(g) < 3 or np.ptp(br.pos[g, 1]) < 2:
            continue
        near = (br.tick >= br.start[g[0]] - 100) & (br.tick <= br.tick[g[-1]] + 100)
        out.append(_span(seg, br, g, "mine.tree", 0.95 if (leaves & near).any() else 0.85))
    return out


FALL_FOLLOW = 60  # ticks to the next break of the refilled block


@detector("mine.falling")
def falling(seg: Seg) -> list[Span]:
    """A break that a gravity block refilled (the enriched ``refill`` column), through each
    follow-up break of the same position at most FALL_FOLLOW ticks after the last. A refill
    the player leaves alone spans the break plus half a second."""
    br = breaks(seg)
    out = []
    taken = np.zeros(len(br), dtype=bool)
    for k in range(len(br)):
        if taken[k] or br.refill[k] is None:
            continue
        chain = [k]
        while br.refill[chain[-1]] is not None:
            last = chain[-1]
            nxt = [
                j
                for j in range(last + 1, len(br))
                if br.tick[j] - br.tick[last] <= FALL_FOLLOW and (br.pos[j] == br.pos[last]).all()
            ]
            if not nxt:
                break
            chain.append(nxt[0])
        taken[chain] = True
        if len(chain) > 1:
            out.append(_span(seg, br, chain, "mine.falling", 0.9))
        else:
            t = int(br.tick[k])
            out.append(seg.tick_span(int(br.start[k]), t + 10, "mine.falling", 0.75))
    return merge(out)


CLEAR_MAX = 3  # breaks in one obstacle
CLEAR_LINK = 40  # ticks between breaks of one obstacle
CLEAR_QUIET = 100  # ticks with no other break on either side
CLEAR_TRAVEL = 3.0  # blocks travelled in the window before and the window after
CLEAR_WINDOW = 80  # ticks
CLEAR_MIN_WINDOW = 40  # ticks of each window that must lie inside the segment


@detector("mine.clear_path")
def clear_path(seg: Seg) -> list[Span]:
    """One to CLEAR_MAX breaks of solid blocks (or cobwebs) with no other break within
    CLEAR_QUIET ticks, while the player travels: their feet cover CLEAR_TRAVEL blocks in the
    CLEAR_WINDOW ticks before the first break and again in the window after the last."""
    br = breaks(seg)
    if not len(br):
        return []
    groups = np.split(np.arange(len(br)), np.flatnonzero(np.diff(br.tick) > CLEAR_LINK) + 1)
    x, z = seg.col("x"), seg.col("z")
    lo, hi = int(seg.tick[0]), int(seg.tick[-1])
    out = []
    for gi, g in enumerate(groups):
        if len(g) > CLEAR_MAX:
            continue
        if not all(solid(br.block[k]) or br.block[k] == "cobweb" for k in g):
            continue
        t0, t1 = int(br.start[g[0]]), int(br.tick[g[-1]])
        if gi > 0 and t0 - br.tick[groups[gi - 1][-1]] < CLEAR_QUIET:
            continue
        if gi + 1 < len(groups) and br.start[groups[gi + 1][0]] - t1 < CLEAR_QUIET:
            continue
        a0, b1 = max(lo, t0 - CLEAR_WINDOW), min(hi, t1 + CLEAR_WINDOW)
        if t0 - a0 < CLEAR_MIN_WINDOW or b1 - t1 < CLEAR_MIN_WINDOW:
            continue
        i = [seg.index_of(t) for t in (a0, t0, t1, b1)]
        before = math.hypot(x[i[1]] - x[i[0]], z[i[1]] - z[i[0]])
        after = math.hypot(x[i[3]] - x[i[2]], z[i[3]] - z[i[2]])
        if before >= CLEAR_TRAVEL and after >= CLEAR_TRAVEL:
            out.append(_span(seg, br, g, "mine.clear_path", 0.75))
    return out
