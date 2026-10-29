"""Stage 1 camera model (spec, Component B: Camera, look.*).

When Baritone wants the camera on a new target, the Java executor turns it the way this player
does: it waits a reaction time, makes one fast primary movement whose duration follows Fitts' law,
then a few corrections, settles near (not on) the target's centre and holds with a little tremor.
This module measures those quantities on the player's own look.acquire spans and fits the numbers
the executor samples from. Units are degrees of camera turn and milliseconds.

Data: human sessions only (bot play never), labels as training sees them (review verdicts applied,
hbpipe.label.corrections), and no span whose window touches play kept out of training
(hbpipe.exclusions). Motion comes from the mouse table's d_yaw and d_pitch, the degrees the camera
actually turned each frame (one row per frame with motion; frames without a row didn't turn).

Measuring one acquisition (acquisitions):
    speed       the frames from PRE_MS before the span to POST_MS after it, summed into GRID_MS
                bins, each axis smoothed with a Gaussian of SMOOTH_MS; speed is the norm. At the
                usual sensitivity one mouse count is 0.15 degrees, and an isolated count smooths
                to about 6 deg/s: under V_REST the camera is holding still.
    strokes     the submovements: stretches at V_REST or faster, cut at a speed minimum that
                falls below SPLIT_RATIO of the lower of the two peaks around it, when both peaks
                reach V_STROKE. A stroke peaks at V_STROKE or faster and turns at least
                MIN_STROKE_DEG. Slower turning is drift: players often creep the camera along at
                20-60 deg/s, a few mouse counts a frame, and at that speed the counts are too
                coarse to tell a deliberate correction from noise. Drift counts toward where the
                camera ends up but not as a correction.
    edges       a stroke runs from where its speed first reaches EDGE_FRAC of its peak (V_REST at
                least) to where it falls back below, or to a minimum it was cut at. Cutting at a
                threshold drops the slow tails of a minimum-jerk profile, so a cut end is moved
                out by the tail a minimum-jerk movement with that peak would have, and back in by
                what the smoothing added to its width. On clean minimum-jerk movements of 100 to
                400 ms this recovers durations to within a few ms and peaks at 0.5.
    movement    the strokes linked by gaps under LINK_MS that are under way when the crosshair
                reaches the span's final target (its landing tick). It is the player's own
                movement onto the target when nothing linked to it comes before it, it settles
                (ends) within the window and MAX_MOVE_MS, and the crosshair still points into the
                target block when it settles (or at the same entity): an acquisition passed
                mid-sweep has no movement of its own. A block broken under the crosshair is
                fine.
    primary     the first stroke that turns at least PRIMARY_SHARE of the movement toward where it
                ends; strokes before it may turn at most LEAD_SHARE, or the movement is a
                staircase of small steps (or two movements back to back) and is dropped. Onset
                is the primary's start, less half a frame (a row's turn happened during the frame
                before it). The distance D runs from there to the settle in mouse space (hypot of
                the yaw and pitch change: what the hand moves; it overstates the visual angle of
                yaw turns at steep pitch). Below MIN_DISTANCE_DEG it is a fidget; in practice
                V_STROKE already keeps out most turns under about 5 degrees.
    corrections the strokes after the primary, and the pause before each (between tail-restored
                edges, floored at one frame: the executor can't wait less).
    target      W is the angular width of the landed-on block from the eyes, 2 atan(0.5 / d) with
                d to the block centre; entities get DEFAULT_WIDTH_DEG. The landing offset is the
                settled look direction minus the direction to the block centre (what Baritone
                aims at), yaw as a horizontal angle on screen (yaw difference times cos pitch;
                the executor divides by cos pitch to apply it).
    reaction    an estimate: when the target appeared isn't recorded. The proxy is the time from
                the player's last block broken or placed to the onset, when no stroke came
                between them and the onset follows within REACT_MAX_MS (in mining the next
                target is the one to look at once the previous block is gone). Movements started
                in anticipation, before the event, can't be measured this way, so this leans long;
                and a player who waits on purpose looks slow.
    tremor      the per-frame turn while holding still (under V_REST) after the settle, for at
                most HOLD_MAX_MS or until the next movement; frames without a row count as zero.
                Also, per axis, how often a turn in the hold goes the same way as the one before.

The model (fit, camera.json):
    reaction_ms         log-normal (median, sigma of the log) delay before the onset; see above
    fitts               primary duration a + b log2(D / W + 1), ms
    duration_sigma      sigma of log(primary duration / Fitts prediction)
    peak_at             when the primary peaks, as a fraction of it (0.5: symmetric minimum jerk)
    primary_gain        the primary's turn toward the settle point over D (< 1 undershoot, > 1
                        overshoot); exactly 1 when no correction follows, so it is a mixture
    corrections         mean count (for a Poisson) and the most seen
    correction_ms       log-normal duration of one correction
    correction_gap_ms   log-normal pause before one; corrections blended into the stroke before
                        (no pause) count as one frame, so this too spans a mixture
    landing_sd_deg      robust sd of the landing offset, yaw (on screen) and pitch
    landing_sd_frac     the same as a fraction of the target's width W: how a landing spreads
                        scales with how big the target looks, so the executor applies this to
                        whatever it's aiming at (the degrees hold for this player's usual range)
    curvature_sd        robust sd of the primary's signed peak deviation from a straight line,
                        over its length
    tremor_deg          per-axis sd of the per-frame turn while holding still, pooled over frames;
                        at 0.15 degrees a count this is mostly slow creep, real tremor is finer
    tremor_same_sign    share of a hold's turns (per axis, rows with a turn) that go the same way
                        as the one before: a slow, steady creep is near 1, jitter near 0.5 or below
    frame_hz            median frames per second while turning

Fitting uses outlier-resistant estimates: log-normals and means are taken after dropping values
more than OUTLIER_SDS robust sds (1.4826 MAD) from the median, unless that would drop more than
OUTLIER_SHARE of them (a mixture, not outliers: then nothing is dropped); spreads are robust sds;
Fitts' law is fit by iteratively reweighted least squares on the relative error with Huber
weights. Any quantity with fewer than MIN_SAMPLES measurements falls back to DEFAULTS, generic
values from the aimed-movement literature, not this player.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from hbpipe import exclusions
from hbpipe.label.base import runs
from hbpipe.label.corrections import effective_labels
from hbpipe.label.detectors.camera import EYE, EYE_CROUCHING, look_vectors
from hbpipe.store import Store

LABEL = "look.acquire"
FILE = "camera.json"
SCHEMA_VERSION = 1

GRID_MS = 2.0
SMOOTH_MS = 10.0
V_REST = 20.0  # deg/s; slower is holding still
V_STROKE = 60.0  # deg/s; a submovement peaks at least this fast, slower turning is drift
EDGE_FRAC = 0.1
SPLIT_RATIO = 0.5
MIN_STROKE_DEG = 0.5
LINK_MS = 250.0  # strokes closer than this are one movement
PRIMARY_SHARE = 0.3
LEAD_SHARE = 0.25
MIN_DISTANCE_DEG = 2.0
MAX_MOVE_MS = 2000.0
PRE_MS = 1500.0  # window before the span: what came before the onset, and the trigger
POST_MS = 1000.0
HOLD_MAX_MS = 1000.0
REACT_MAX_MS = 1000.0
TRIGGERS = ("block_broken", "block_placed")
MIN_FRAME_HZ = 50.0  # below this the frames are too far apart to see a stroke's shape
DEFAULT_WIDTH_DEG = 11.4  # a 0.6-block-wide mob 3 blocks away
BLOCK_MARGIN = 0.1  # blocks
SAME_ONSET_MS = 20.0  # two spans whose onsets are this close measured one movement
TICK_MS = 50.0

MIN_SAMPLES = 20
OUTLIER_SDS = 3.0
OUTLIER_SHARE = 0.1
HUBER_K = 1.5

DEFAULTS: dict = {
    "reaction_ms": {"median": 250.0, "sigma": 0.3},
    "fitts": {"a_ms": 100.0, "b_ms": 100.0},
    "duration_sigma": 0.25,
    "peak_at": {"mean": 0.45, "sd": 0.08},
    "primary_gain": {"mean": 0.95, "sd": 0.1},
    "corrections": {"mean": 0.7, "max": 3},
    "correction_ms": {"median": 120.0, "sigma": 0.4},
    "correction_gap_ms": {"median": 50.0, "sigma": 0.6},
    "landing_sd_deg": {"yaw": 1.5, "pitch": 1.0},
    "landing_sd_frac": {"yaw": 0.15, "pitch": 0.15},
    "curvature_sd": 0.05,
    "tremor_deg": 0.02,
    "tremor_same_sign": 0.8,
    "frame_hz": 60.0,
}

ACQ_SCHEMA = {
    "session_id": pl.String,
    "segment_id": pl.String,
    "start_tick": pl.Int64,  # the look.acquire span
    "end_tick": pl.Int64,
    "onset_ms": pl.Float64,  # since the session start
    "target": pl.String,  # block | entity
    "distance_deg": pl.Float64,
    "width_deg": pl.Float64,
    "difficulty": pl.Float64,  # log2(D / W + 1), bits
    "primary_ms": pl.Float64,
    "total_ms": pl.Float64,  # onset to settle
    "settle_ms": pl.Float64,  # primary end to settle
    "peak_speed": pl.Float64,  # deg/s
    "peak_at": pl.Float64,  # fraction of the primary
    "primary_gain": pl.Float64,
    "curvature": pl.Float64,  # signed peak deviation / chord
    "corrections": pl.Int64,
    "correction_ms": pl.List(pl.Float64),
    "correction_gap_ms": pl.List(pl.Float64),
    "landing_yaw": pl.Float64,  # null when not on a block
    "landing_pitch": pl.Float64,
    "reaction_ms": pl.Float64,  # null without a trigger
    "tremor_deg": pl.Float64,  # null without a hold
    "hold_frames": pl.Float64,
    "hold_pairs": pl.Int64,  # turns in the hold that follow another, per axis, summed
    "hold_same": pl.Int64,  # ...and of those, the ones going the same way
    "frame_hz": pl.Float64,
}

TICK_COLUMNS = [
    "segment_id",
    "tick",
    "t_ns",
    "x",
    "y",
    "z",
    "yaw",
    "pitch",
    "crouching",
    "hit_type",
    "hit_x",
    "hit_y",
    "hit_z",
    "hit_entity_id",
]


# --- Measuring --------------------------------------------------------------------------------


@dataclass
class _Segment:
    tick: np.ndarray
    t: np.ndarray  # ms since the session start
    eye: np.ndarray  # (n, 3)
    yaw: np.ndarray
    pitch: np.ndarray
    target: np.ndarray  # 0 = nothing, else one code per block or entity
    block: np.ndarray  # (n, 3) targeted block, nan when not a block
    mouse_t: np.ndarray  # ms
    mouse_d: np.ndarray  # (m, 2) d_yaw, d_pitch
    triggers: np.ndarray  # ms, sorted


@dataclass
class _Stroke:
    s: int  # bins [s, e) of the speed trace, edges as cut
    e: int
    start: float  # ms in the window, minimum-jerk tails restored
    end: float
    ms: float
    peak: float  # deg/s
    peak_t: float  # ms in the window
    disp: np.ndarray  # (yaw, pitch) turned over [s, e)


def acquisitions(store: Store, reasons: Counter | None = None) -> pl.DataFrame:
    """One row per usable acquisition in the human sessions (ACQ_SCHEMA). With ``reasons``, also
    counts the look.acquire spans left out, by why."""
    reasons = Counter() if reasons is None else reasons
    rows: list[dict] = []
    for s in store.sessions(source="human"):
        rows += _session(store, s["session_id"], reasons)
    if not rows:
        return pl.DataFrame(schema=ACQ_SCHEMA)
    out = pl.from_dicts(rows, schema=ACQ_SCHEMA).sort("session_id", "segment_id", "onset_ms")
    # Spans that land on the same block within one movement measure it alike: keep the last.
    later = pl.col("onset_ms").shift(-1).over("session_id", "segment_id")
    kept = out.filter(later.is_null() | (later - pl.col("onset_ms") >= SAME_ONSET_MS))
    reasons["same movement as another span"] += out.height - kept.height
    return kept


def _spans(store: Store, session_id: str, reasons: Counter) -> pl.DataFrame:
    labels = effective_labels(
        store.labels(session_id=session_id), store.corrections(session_id=session_id)
    ).filter(pl.col("label") == LABEL)
    window = labels.with_columns(
        lo=pl.col("start_tick") - math.ceil((PRE_MS + MAX_MOVE_MS) / TICK_MS),
        hi=pl.col("end_tick") + math.ceil(POST_MS / TICK_MS),
    )
    kept = exclusions.drop_excluded(window, exclusions.for_session(store, session_id), "lo", "hi")
    reasons["excluded play"] += labels.height - kept.height
    return kept.sort("segment_id", "start_tick")


def _session(store: Store, session_id: str, reasons: Counter) -> list[dict]:
    spans = _spans(store, session_id, reasons)
    if spans.is_empty():
        return []
    paths = {name: store.table_path(name, session_id) for name in ("ticks_clean", "mouse")}
    if not all(p.exists() for p in paths.values()):
        reasons["session not converted"] += spans.height
        return []
    ticks = pl.read_parquet(paths["ticks_clean"], columns=TICK_COLUMNS)
    mouse = pl.read_parquet(paths["mouse"], columns=["segment_id", "t_ns", "d_yaw", "d_pitch"])
    events_path = store.table_path("events", session_id)
    events = (
        pl.read_parquet(events_path, columns=["segment_id", "t_ns", "kind"]).filter(
            pl.col("kind").is_in(TRIGGERS)
        )
        if events_path.exists()
        else pl.DataFrame(schema={"segment_id": pl.String, "t_ns": pl.UInt64, "kind": pl.String})
    )
    key = (
        pl.when(pl.col("hit_type") == "block")
        .then(pl.concat_str([pl.lit("b"), "hit_x", "hit_y", "hit_z"], separator=","))
        .when(pl.col("hit_type") == "entity")
        .then(pl.concat_str([pl.lit("e"), "hit_entity_id"], separator=","))
    )
    ticks = ticks.with_columns(target=key.rank("dense").fill_null(0).cast(pl.Int64))
    out = []
    for (segment_id,), g in spans.group_by("segment_id", maintain_order=True):
        seg = _segment(ticks, mouse, events, segment_id)
        for start, end in g.select("start_tick", "end_tick").iter_rows():
            m = _measure(seg, start, end) if seg is not None else "no ticks"
            if isinstance(m, str):
                reasons[m] += 1
                continue
            row = {"session_id": session_id, "segment_id": segment_id}
            out.append(row | {"start_tick": start, "end_tick": end} | m)
    return out


def _segment(
    ticks: pl.DataFrame, mouse: pl.DataFrame, events: pl.DataFrame, segment_id: str
) -> _Segment | None:
    t = ticks.filter(pl.col("segment_id") == segment_id).sort("tick")
    if t.is_empty():
        return None
    m = mouse.filter(pl.col("segment_id") == segment_id).sort("t_ns")
    ev = events.filter(pl.col("segment_id") == segment_id)
    eye = np.where(t["crouching"].fill_null(False).to_numpy(), EYE_CROUCHING, EYE)
    block = t.select(
        pl.when(pl.col("hit_type") == "block").then(pl.col(c).cast(pl.Float64))
        for c in ("hit_x", "hit_y", "hit_z")
    )
    return _Segment(
        tick=t["tick"].cast(pl.Int64).to_numpy(),
        t=t["t_ns"].cast(pl.Float64).to_numpy() / 1e6,
        eye=np.stack([t["x"].to_numpy(), t["y"].to_numpy() + eye, t["z"].to_numpy()], axis=1),
        yaw=t["yaw"].cast(pl.Float64).to_numpy(),
        pitch=t["pitch"].cast(pl.Float64).to_numpy(),
        target=t["target"].to_numpy(),
        block=block.to_numpy().astype(float),
        mouse_t=m["t_ns"].cast(pl.Float64).to_numpy() / 1e6,
        mouse_d=m.select(pl.col("d_yaw", "d_pitch").cast(pl.Float64)).to_numpy(),
        triggers=np.sort(ev["t_ns"].cast(pl.Float64).to_numpy() / 1e6),
    )


def _smooth(v: np.ndarray) -> np.ndarray:
    sd = SMOOTH_MS / GRID_MS
    x = np.arange(-math.ceil(3 * sd), math.ceil(3 * sd) + 1)
    k = np.exp(-0.5 * (x / sd) ** 2)
    k /= k.sum()
    pad = len(x) // 2
    return np.stack(
        [np.convolve(np.pad(v[:, i], pad), k, mode="valid") for i in range(v.shape[1])], axis=1
    )


def _tail(r: float) -> float:
    """Fraction of a minimum-jerk movement's duration spent at one end below r times its peak
    speed (its speed is 16 u^2 (1 - u)^2 times the peak at phase u)."""
    r = min(r, 0.5)
    return (1 - math.sqrt(1 - math.sqrt(r))) / 2


def _split(speed: np.ndarray, s: int, e: int) -> list[tuple[int, int]]:
    """Bins [s, e) cut at speed minima below SPLIT_RATIO of the lower of the highest peaks on
    either side, when both reach V_STROKE; the deepest cut first."""
    v = speed[s:e]
    best, best_ratio = 0, SPLIT_RATIO
    for i in np.flatnonzero((v[1:-1] <= v[:-2]) & (v[1:-1] < v[2:])) + 1:
        lower = min(v[:i].max(), v[i + 1 :].max())
        if lower >= V_STROKE and v[i] / lower <= best_ratio:
            best, best_ratio = int(i), v[i] / lower
    if not best:
        return [(s, e)]
    return _split(speed, s, s + best) + _split(speed, s + best, e)


def _stroke(speed: np.ndarray, cum: np.ndarray, a: int, b: int) -> _Stroke:
    """The stroke peaking in bins [a, b), trimmed to EDGE_FRAC of its peak."""
    p = a + int(np.argmax(speed[a:b]))
    peak = float(speed[p])
    edge = max(EDGE_FRAC * peak, V_REST)
    slow = np.flatnonzero(speed[a:p] < edge)
    s = a + int(slow[-1]) + 1 if len(slow) else a
    slow = np.flatnonzero(speed[p:b] < edge)
    e = p + int(slow[0]) if len(slow) else b
    cut_start = int(s == 0 or speed[s - 1] < edge)  # else it starts at a minimum it was cut at
    cut_end = int(e >= len(speed) or speed[e] < edge)
    tail = _tail(edge / peak)
    wide = (e - s) * GRID_MS / (1 - (cut_start + cut_end) * tail)
    # Smoothing widens a minimum-jerk speed profile (a Beta(3, 3) bump, sd T / sqrt(28)) as if
    # adding its variance: pull each restored end in by half of that.
    pull = (wide - math.sqrt(max(wide**2 - 28 * SMOOTH_MS**2, wide**2 / 4))) / 2
    start = s * GRID_MS - cut_start * (tail * wide - pull)
    end = e * GRID_MS + cut_end * (tail * wide - pull)
    return _Stroke(s, e, start, end, end - start, peak, (p + 0.5) * GRID_MS, cum[e] - cum[s])


def _strokes(speed: np.ndarray, cum: np.ndarray) -> list[_Stroke]:
    out = []
    for r0, r1 in runs(speed >= V_REST):
        if speed[r0 : r1 + 1].max() < V_STROKE:
            continue
        for a, b in _split(speed, r0, r1 + 1):
            stroke = _stroke(speed, cum, a, b)
            if stroke.peak >= V_STROKE and np.linalg.norm(stroke.disp) >= MIN_STROKE_DEG:
                out.append(stroke)
    return out


def _measure(seg: _Segment, start_tick: int, end_tick: int) -> dict | str:
    """The measurements of one span, or why it isn't usable."""
    i0 = int(np.searchsorted(seg.tick, start_tick))
    i1 = int(np.searchsorted(seg.tick, end_tick, side="right")) - 1
    if i1 < i0:
        return "no ticks"
    code = seg.target[i1]
    if code == 0:
        return "no target"
    j = i1  # the landing tick: first of the final target's run
    while j > i0 and seg.target[j - 1] == code:
        j -= 1
    # Bins on a grid shared by every span, so spans sharing a movement measure it alike.
    lo = math.floor(max(seg.t[i0] - PRE_MS, seg.t[0] - TICK_MS) / GRID_MS) * GRID_MS
    hi = min(seg.t[i1] + POST_MS, seg.t[-1])
    a, b = np.searchsorted(seg.mouse_t, [lo, hi])
    mt, md = seg.mouse_t[a:b] - lo, seg.mouse_d[a:b]
    n = int((hi - lo) // GRID_MS) + 1
    v = np.zeros((n, 2))
    bins = (mt // GRID_MS).astype(np.int64)
    np.add.at(v, bins, md)
    cum = np.vstack([np.zeros((1, 2)), np.cumsum(v, axis=0)])  # cum[i]: turn before bin i
    vel = _smooth(v) * (1000.0 / GRID_MS)
    speed = np.hypot(vel[:, 0], vel[:, 1])

    moves: list[list[_Stroke]] = []
    for stroke in _strokes(speed, cum):
        if moves and stroke.start - moves[-1][-1].end < LINK_MS:
            moves[-1].append(stroke)
        else:
            moves.append([stroke])
    land0 = (seg.t[j - 1] if j > 0 else seg.t[j] - TICK_MS) - lo
    land1 = seg.t[j] - lo
    k = max((i for i, mv in enumerate(moves) if mv[0].start <= land1), default=None)
    if k is None or moves[k][-1].end < land0 - LINK_MS:
        return "no movement onto the target"
    move = moves[k]
    first, last = move[0], move[-1]
    if first.start < LINK_MS:
        return "moving before onset"
    if last.end > n * GRID_MS - LINK_MS:
        return "no settle"
    if last.end - first.start > MAX_MOVE_MS:
        return "too long"
    settle = min(int(np.searchsorted(seg.t, lo + last.end)), len(seg.t) - 1)
    if not _still_on(seg, settle, j):
        return "off the target at settle"

    whole = cum[last.e] - cum[first.s]
    size = float(np.linalg.norm(whole))
    if size < MIN_DISTANCE_DEG:
        return "too small"
    toward = [float(s.disp @ whole) / size for s in move]  # each stroke's share, signed
    p = next((i for i, d in enumerate(toward) if d >= PRIMARY_SHARE * size), None)
    if p is None or np.linalg.norm(cum[move[p].s] - cum[first.s]) > LEAD_SHARE * size:
        return "steps, no primary"
    primary, fixes = move[p], move[p + 1 :]
    total = cum[last.e] - cum[primary.s]
    distance = float(np.linalg.norm(total))
    if distance < MIN_DISTANCE_DEG:
        return "too small"

    # While the camera turns every frame has a row, so the row spacing is the frame time.
    dt = np.diff(mt[(mt >= first.s * GRID_MS) & (mt < last.e * GRID_MS)])
    dt = dt[dt < TICK_MS]
    frame_ms = float(np.median(dt)) if len(dt) else math.inf
    if 1000.0 / frame_ms < MIN_FRAME_HZ:
        return "low frame rate"

    chord = primary.disp
    length = float(np.linalg.norm(chord))
    rel = cum[primary.s : primary.e + 1] - cum[primary.s]
    dev = (chord[0] * rel[:, 1] - chord[1] * rel[:, 0]) / length
    width, landing = _target(seg, settle, j)
    onset = primary.start - frame_ms / 2  # a row's turn happened over the frame before it
    before = moves[k - 1][-1].end if k > 0 else 0.0
    after = moves[k + 1][0].start if k + 1 < len(moves) else n * GRID_MS
    tremor, frames, pairs, same = _tremor(speed, bins, md, last.end, after, frame_ms)
    return {
        "onset_ms": lo + onset,
        "target": "entity" if np.isnan(seg.block[j, 0]) else "block",
        "distance_deg": distance,
        "width_deg": width,
        "difficulty": math.log2(distance / width + 1),
        "primary_ms": primary.ms,
        "total_ms": last.end - primary.start,
        "settle_ms": last.end - primary.end,
        "peak_speed": primary.peak,
        "peak_at": (primary.peak_t - primary.start) / primary.ms,
        "primary_gain": float(primary.disp @ total) / distance**2,
        "curvature": float(dev[np.argmax(np.abs(dev))]) / length,
        "corrections": len(fixes),
        "correction_ms": [f.ms for f in fixes],
        "correction_gap_ms": [
            max(f.start - prev.end, frame_ms) for prev, f in zip(move[p:], fixes, strict=False)
        ],
        "landing_yaw": landing[0],
        "landing_pitch": landing[1],
        "reaction_ms": _reaction(seg, lo, onset, before),
        "tremor_deg": tremor,
        "hold_frames": frames,
        "hold_pairs": pairs,
        "hold_same": same,
        "frame_hz": 1000.0 / frame_ms,
    }


def _still_on(seg: _Segment, i: int, landed: int) -> bool:
    """Whether the crosshair at tick row ``i`` is still on what it landed on at row ``landed``:
    the same target, or for a block, a look that still points into its cell (give or take
    BLOCK_MARGIN, for the eye position the game picked from), as when it has been broken."""
    if seg.target[i] == seg.target[landed]:
        return True
    if np.isnan(seg.block[landed, 0]):
        return False
    d = look_vectors(seg.yaw[i : i + 1], seg.pitch[i : i + 1])[0]
    d = np.where(np.abs(d) < 1e-12, 1e-12, d)
    near = (seg.block[landed] - BLOCK_MARGIN - seg.eye[i]) / d
    far = (seg.block[landed] + 1.0 + BLOCK_MARGIN - seg.eye[i]) / d
    enter = np.minimum(near, far).max()
    leave = np.maximum(near, far).min()
    return bool(leave >= max(enter, 0.0))


def _target(seg: _Segment, i: int, landed: int) -> tuple[float, tuple[float | None, float | None]]:
    """Angular width of the target landed on at row ``landed``, seen from row ``i``, and the
    landing offset (yaw, pitch) of row i's look from its centre; no offset unless a block."""
    if np.isnan(seg.block[landed, 0]):
        return DEFAULT_WIDTH_DEG, (None, None)
    to = seg.block[landed] + 0.5 - seg.eye[i]
    dist = float(np.linalg.norm(to))
    width = math.degrees(2 * math.atan(0.5 / max(dist, 0.5)))
    yaw_t = math.degrees(math.atan2(-to[0], to[2]))
    pitch_t = math.degrees(math.atan2(-to[1], math.hypot(to[0], to[2])))
    dyaw = (seg.yaw[i] - yaw_t + 180.0) % 360.0 - 180.0
    return width, (dyaw * math.cos(math.radians(pitch_t)), float(seg.pitch[i] - pitch_t))


def _reaction(seg: _Segment, lo: float, onset: float, before: float) -> float | None:
    """Time from the last block broken or placed to ``onset`` (ms in the window), when no stroke
    came between them (``before``: end of the movement before)."""
    i = int(np.searchsorted(seg.triggers, lo + onset)) - 1
    if i < 0:
        return None
    trigger = seg.triggers[i] - lo
    if trigger < before or onset - trigger > REACT_MAX_MS:
        return None
    return float(onset - trigger)


def _tremor(
    speed: np.ndarray,
    bins: np.ndarray,
    md: np.ndarray,
    settle: float,
    next_move: float,
    frame_ms: float,
) -> tuple[float | None, float, int, int]:
    """Per-axis sd of the per-frame turn while holding still after the settle, the number of
    frames it covers, and per axis (summed) the turns that follow another turn in the hold and how
    many of those go the same way. The hold starts once the smoothing has let go of the last
    stroke."""
    a = math.ceil((settle + 2 * SMOOTH_MS) / GRID_MS)
    b = min(math.floor(min(next_move, settle + HOLD_MAX_MS) / GRID_MS), len(speed))
    if b <= a:
        return None, 0.0, 0, 0
    still = np.zeros(len(speed), dtype=bool)
    still[a:b] = speed[a:b] < V_REST
    frames = float(still.sum()) * GRID_MS / frame_ms
    if frames < 2:
        return None, 0.0, 0, 0
    d = md[still[np.clip(bins, 0, len(speed) - 1)]]
    pairs = same = 0
    for axis in range(2):
        signs = np.sign(d[:, axis])
        signs = signs[signs != 0]
        pairs += max(0, len(signs) - 1)
        same += int((signs[1:] == signs[:-1]).sum())
    return math.sqrt(float((d**2).sum()) / (2 * frames)), frames, pairs, same


# --- Fitting ----------------------------------------------------------------------------------


def fit(acqs: pl.DataFrame, defaulted: list[str] | None = None) -> dict:
    """The camera model (JSON-able) from ``acqs`` (acquisitions()). Quantities with fewer than
    MIN_SAMPLES measurements take their DEFAULTS; with ``defaulted``, their names are added."""
    defaulted = [] if defaulted is None else defaulted

    def pick(name: str, value):
        if value is None:
            defaulted.append(name)
            return DEFAULTS[name]
        return value

    def col(name: str) -> np.ndarray:
        return acqs[name].drop_nulls().cast(pl.Float64).to_numpy()

    def flat(name: str) -> np.ndarray:
        return acqs[name].explode().drop_nulls().cast(pl.Float64).to_numpy()

    fitts = _fitts(col("difficulty"), col("primary_ms"))
    land_yaw, land_pitch = _spread(col("landing_yaw")), _spread(col("landing_pitch"))
    on_block = acqs.filter(pl.col("landing_yaw").is_not_null() & (pl.col("width_deg") > 0))
    frac_yaw = _spread((on_block["landing_yaw"] / on_block["width_deg"]).to_numpy())
    frac_pitch = _spread((on_block["landing_pitch"] / on_block["width_deg"]).to_numpy())
    counts = col("corrections")
    held = acqs.filter(pl.col("tremor_deg").is_not_null() & (pl.col("hold_frames") > 0))
    tremor = None
    if held.height >= MIN_SAMPLES:
        frames, sd = held["hold_frames"].to_numpy(), held["tremor_deg"].to_numpy()
        tremor = math.sqrt(float((frames * sd**2).sum() / frames.sum()))  # pooled over frames
    creeps = acqs.filter(pl.col("hold_pairs") > 0)
    same_sign = None
    if creeps.height >= MIN_SAMPLES:
        same_sign = float(creeps["hold_same"].sum() / creeps["hold_pairs"].sum())
    hz = col("frame_hz")
    model = {
        "schema": SCHEMA_VERSION,
        "kind": "camera",
        "created": int(time.time()),
        "fitted_on": {"sessions": acqs["session_id"].n_unique(), "acquisitions": acqs.height},
        "reaction_ms": pick("reaction_ms", _lognormal(col("reaction_ms"))),
        "fitts": pick("fitts", fitts and {"a_ms": fitts[0], "b_ms": fitts[1]}),
        "duration_sigma": pick("duration_sigma", fitts and fitts[2]),
        "peak_at": pick("peak_at", _mean_sd(col("peak_at"))),
        "primary_gain": pick("primary_gain", _mean_sd(col("primary_gain"))),
        "corrections": pick(
            "corrections",
            {"mean": float(counts.mean()), "max": int(counts.max())}
            if len(counts) >= MIN_SAMPLES
            else None,
        ),
        "correction_ms": pick("correction_ms", _lognormal(flat("correction_ms"))),
        "correction_gap_ms": pick("correction_gap_ms", _lognormal(flat("correction_gap_ms"))),
        "landing_sd_deg": pick(
            "landing_sd_deg",
            {"yaw": land_yaw, "pitch": land_pitch} if land_yaw and land_pitch else None,
        ),
        "landing_sd_frac": pick(
            "landing_sd_frac",
            {"yaw": frac_yaw, "pitch": frac_pitch} if frac_yaw and frac_pitch else None,
        ),
        "curvature_sd": pick("curvature_sd", _spread(col("curvature"))),
        "tremor_deg": pick("tremor_deg", tremor),
        "tremor_same_sign": pick("tremor_same_sign", same_sign),
        "frame_hz": pick("frame_hz", float(np.median(hz)) if len(hz) >= MIN_SAMPLES else None),
    }
    return _rounded(model)


def _robust(x: np.ndarray) -> np.ndarray:
    """``x`` without values more than OUTLIER_SDS robust sds from its median. When that would
    drop more than OUTLIER_SHARE of them, nothing is dropped: the values aren't one bump with a
    few outliers but a mixture (e.g. half the correction pauses are one frame, half are longer)
    whose MAD says nothing about its spread."""
    med = np.median(x)
    mad = 1.4826 * np.median(np.abs(x - med))
    kept = x[np.abs(x - med) <= OUTLIER_SDS * mad]
    return kept if len(kept) >= (1 - OUTLIER_SHARE) * len(x) else x


def _mean_sd(x: np.ndarray) -> dict | None:
    x = x[np.isfinite(x)]
    if len(x) < MIN_SAMPLES:
        return None
    kept = _robust(x)
    return {"mean": float(kept.mean()), "sd": float(kept.std(ddof=1))}


def _spread(x: np.ndarray) -> float | None:
    """Robust sd: 1.4826 times the median absolute deviation."""
    x = x[np.isfinite(x)]
    if len(x) < MIN_SAMPLES:
        return None
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def _lognormal(x: np.ndarray) -> dict | None:
    """Median and sigma of a log-normal: the mean and sd of the logs, outliers dropped."""
    x = x[np.isfinite(x) & (x > 0)]
    if len(x) < MIN_SAMPLES:
        return None
    logs = _robust(np.log(x))
    return {"median": float(np.exp(logs.mean())), "sigma": float(logs.std(ddof=1))}


def _fitts(difficulty: np.ndarray, ms: np.ndarray) -> tuple[float, float, float] | None:
    """(a, b, sigma) for ms = a + b difficulty, by iteratively reweighted least squares on the
    relative error with Huber weights; sigma is the robust sd of log(ms / predicted). A negative
    b is pinned to 0 (a = median), a negative a to 0 (refit through the origin)."""
    ok = np.isfinite(difficulty) & np.isfinite(ms) & (ms > 0)
    x, y = difficulty[ok], ms[ok]
    if len(x) < MIN_SAMPLES:
        return None
    design = np.stack([np.ones_like(x), x], axis=1)
    w = np.ones_like(y)
    coef = np.array([float(np.median(y)), 0.0])
    for _ in range(100):
        sw = np.sqrt(w)
        new = np.linalg.lstsq(design * sw[:, None], y * sw, rcond=None)[0]
        pred = np.maximum(design @ new, 1.0)
        rel = (y - pred) / pred
        scale = 1.4826 * np.median(np.abs(rel - np.median(rel))) or 1e-9
        w = np.minimum(1.0, HUBER_K * scale / np.maximum(np.abs(rel), 1e-12)) / pred**2
        done = np.allclose(new, coef, rtol=1e-7, atol=1e-6)
        coef = new
        if done:
            break
    a, b = float(coef[0]), float(coef[1])
    if b < 0:
        a, b = float(np.median(y)), 0.0
    elif a < 0:
        a, b = 0.0, float((w * x * y).sum() / (w * x * x).sum())
    pred = np.maximum(a + b * x, 1.0)
    return a, b, _spread(np.log(y / pred)) or 0.0


def _rounded(value):
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, float):
        return round(value, 4)
    return value


# --- Output -----------------------------------------------------------------------------------


def write(model: dict, out_dir: str | Path) -> Path:
    """Writes ``model`` to out_dir/camera.json (indent 1, trailing newline)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(model, indent=1) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


def export(store: Store, out_dir: str | Path) -> Path:
    """Fits the camera model on the store's human play and writes it; returns the file."""
    return write(fit(acquisitions(store)), out_dir)


def summary(model: dict) -> list[str]:
    """The fitted numbers in a few report lines."""
    m = model
    return [
        f"reaction     median {m['reaction_ms']['median']:.0f} ms, sigma "
        f"{m['reaction_ms']['sigma']:.2f} (estimated from the last block broken or placed)",
        f"primary      {m['fitts']['a_ms']:.0f} + {m['fitts']['b_ms']:.1f} log2(D/W + 1) ms, "
        f"sigma {m['duration_sigma']:.2f}; peak speed at {m['peak_at']['mean']:.2f} "
        f"+- {m['peak_at']['sd']:.2f} of it",
        f"gain         {m['primary_gain']['mean']:.2f} +- {m['primary_gain']['sd']:.2f} of the "
        f"distance; path curvature sd {m['curvature_sd']:.3f}",
        f"corrections  {m['corrections']['mean']:.2f} on average (max {m['corrections']['max']}), "
        f"{m['correction_ms']['median']:.0f} ms each (sigma {m['correction_ms']['sigma']:.2f}), "
        f"pause {m['correction_gap_ms']['median']:.0f} ms (sigma "
        f"{m['correction_gap_ms']['sigma']:.2f})",
        f"landing      sd {m['landing_sd_deg']['yaw']:.1f} deg yaw, "
        f"{m['landing_sd_deg']['pitch']:.1f} deg pitch from the block centre",
        f"hold         tremor {m['tremor_deg']:.3f} deg per frame at {m['frame_hz']:.0f} Hz; "
        f"{m['tremor_same_sign']:.0%} of its turns go the same way as the one before",
    ]
