"""Stage 1 meta model (spec, Component B: Meta behaviors; meta.hesitate, look.idle_scan,
meta.mistake).

The Java executor makes the bot pause, glance around and slip the way this player does. Pauses
come from a hazard model: a chance of starting one on any tick, depending on the context (the
activity, and whether a block was just broken). Glances are looks away while standing still.
Mistakes are injected at the player's rate and in the player's mix of kinds, each followed by the
usual correction. This module measures those on the player's labeled play and fits the numbers
the executor samples from. Units are minutes of play, milliseconds and degrees of yaw.

Data: human sessions only (bot play never), labels as training sees them (review verdicts applied,
hbpipe.label.corrections), and nothing from play kept out of training (hbpipe.exclusions). Active
play is every tick of a recorded segment (the recorder stops on screens, unfocus and 10 s idle),
except excluded ticks and the last AFK_TICKS of a segment flagged suspect_afk: the recorder flags
a movement, attack or use key held 30 s with no mouse input, which may be the player away. A span
counts only when every tick of it is active play.

Measuring (observations, one row per item):
    active      per segment and context, the active ticks. The context of a tick is the activity
                label over it: mining (act.strip_mine, act.area_clear, act.tunnel, act.tree_farm),
                walking (act.travel, act.explore), building (act.build), other (any other activity,
                or none). Where two overlap the first in that order wins (the activity detector
                keeps these apart; only a review can make them overlap).
    stationary  per segment, the active ticks standing still: under MOVING_SPEED (0.5 m/s), no
                movement key held and no screen showing.
    hesitate    a meta.hesitate span: 300 ms to 3 s with no input at all, with input on both sides
                (the detector's definition). Its context is the one at its first tick; its
                duration is its length in ticks at 50 ms each. after_break: a block broken within
                AFTER_BREAK_TICKS (1 s) before it starts.
    break       a block_broken event in active play with a second of active play after it.
                after_break: a hesitation starts within that second (the "task just finished"
                context: the spec's other contexts, ore spotted, low health, unfamiliar area,
                aren't measured yet).
    glance      a look.idle_scan span (stationary turning through 10+ degrees with no attack or
                use during it or in the second after) whose every tick is stationary as above,
                and that the player doesn't walk out of: moving within SET_OFF_TICKS of its end
                (100 ms, quicker than a reaction) means the walk was planned with the turn. That
                is the turn that starts a walk, which Baritone makes anyway, through the camera
                model; players make many more of those than glances.
                The angle is how far the yaw gets from where it was the tick before the span
                (wrapped: at most 180). The hold is how long the camera stays at that farthest
                point: from the tick it gets there to the first tick it turns again (0.5 degrees
                or more), the player moves or a key goes down, or an attack or use; at 20 ticks a
                second it is known to a tick (a sweep that turns straight back holds one tick),
                and a hold the segment ends during is not measured. A scan that starts within
                RETURN_GAP ticks of a hold ended by turning and brings the yaw back at least
                RETURN_SHARE of the way is the look coming back: part of that glance, not a new
                one. Few scans do: looking back at the work is followed by an attack or use, so
                it isn't an idle scan.
    mistake     a meta.mistake span. The detector merges four kinds into one label, so the kind is
                recovered by running its parts again on the segment (hbpipe.label.detectors.meta):
                wrong_slot (a hotbar slot picked and left unused for another), missed_click (an
                attack or use that hit nothing), overshoot (a move walked back) and
                misplaced_block (a placed block broken again within 3 s). A span takes the kind
                of the most confident part it overlaps; with none (a span added in review, or
                labels made by older detector code) it is unknown.

The model (fit, meta.json):
    hesitation.per_min      hesitations starting per minute of active play in each context. It
                            counts them all, those after a break included: an executor that also
                            applies after_break_p should take those out of the mining rate.
    hesitation.after_break_p    chance a hesitation starts within 1 s of a block breaking. A held
                            attack key is input, so a player who keeps attack held from block to
                            block, as most do through a run of mining, shows few of these.
    hesitation.duration_ms  log-normal (median, sigma of the log) duration, from the mean and sd
                            of the logs with nothing dropped: the detector only sees 300 ms to 3 s,
                            and the long pauses in that range are part of the behavior. Pauses are
                            most common at the 300 ms floor (the distribution goes on below it,
                            where a pause is too short to call a hesitation), so clamp draws into
                            300 ms - 3 s: that reproduces the pile at 300 ms.
    glance.per_min          glances starting per minute of stationary play. Stationary time
                            includes standing while mining, where the player rarely glances, so the
                            executor should apply this to all of its own stationary time.
    glance.angle_deg        log-normal yaw excursion
    glance.hold_ms          log-normal time at the farthest point before coming back or moving on
    mistake.per_min         mistakes per minute of active play
    mistake.kinds           share of each kind among the mistakes whose kind is known

Every rate is an estimate from counts (its relative error is about one over the square root of
the count). The glance log-normals are fit as in the camera model (outliers dropped unless that
would drop too many to be outliers). Any quantity with too little data falls back: a context with
less than MIN_MINUTES of play takes the player's hesitation rate over all active play; otherwise
rates need MIN_MINUTES, log-normals, after_break_p and the kinds MIN_SAMPLES, or they take
DEFAULTS, generic guesses for a typical player, not measurements of this one.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
import polars as pl

from hbpipe import exclusions
from hbpipe.convert.tables import SCHEMAS
from hbpipe.label.base import Seg
from hbpipe.label.corrections import effective_labels
from hbpipe.label.detectors import meta as found
from hbpipe.label.detectors.camera import (
    MOVING_SPEED,
    TURN_MIN,
    rows_of,
    screen_shown,
    turn_deg,
    world_actions,
)
from hbpipe.label.detectors.movement import moving_keys
from hbpipe.models.motor.camera import _lognormal, _rounded
from hbpipe.schema import pb
from hbpipe.store import Store

FILE = "meta.json"
SCHEMA_VERSION = 1
HESITATE, SCAN, MISTAKE = "meta.hesitate", "look.idle_scan", "meta.mistake"

TICK_MS = 50.0
TICKS_PER_MIN = 1200
CONTEXTS = {
    "mining": ("act.strip_mine", "act.area_clear", "act.tunnel", "act.tree_farm"),
    "walking": ("act.travel", "act.explore"),
    "building": ("act.build",),
}
OTHER = "other"
AFTER_BREAK_TICKS = 20  # 1 s
AFK_TICKS = 600  # the 30 s of a held key with no mouse input that flags a segment suspect_afk
SET_OFF_TICKS = 2  # moving this soon after a stationary turn: the turn started a walk
RETURN_GAP = 4  # ticks from the end of a hold to the scan that brings the look back
RETURN_SHARE = 0.5
KINDS = ("wrong_slot", "missed_click", "overshoot", "misplaced_block")
UNKNOWN = "unknown"

MIN_SAMPLES = 20
MIN_MINUTES = 2.0

DEFAULTS: dict = {
    "hesitation": {
        "per_min": {"mining": 2.0, "walking": 1.0, "building": 3.0, "other": 2.0},
        "after_break_p": 0.1,
        "duration_ms": {"median": 500.0, "sigma": 0.5},
    },
    "glance": {
        "per_min": 1.0,
        "angle_deg": {"median": 30.0, "sigma": 0.7},
        "hold_ms": {"median": 400.0, "sigma": 0.8},
    },
    "mistake": {"per_min": 0.3, "kinds": dict.fromkeys(KINDS, 0.25)},
}

OBS_SCHEMA = {
    "session_id": pl.String,
    "segment_id": pl.String,
    "kind": pl.String,  # active | stationary | break | hesitate | glance | mistake
    "tick": pl.Int64,  # where it starts (active, stationary: the segment's first tick)
    "context": pl.String,  # active, hesitate: mining | walking | building | other
    "ticks": pl.Int64,  # active, stationary: how many; hesitate, glance, mistake: span length
    "ms": pl.Float64,  # hesitate: duration; glance: hold (null when the segment ends first)
    "angle_deg": pl.Float64,  # glance: yaw excursion
    "after_break": pl.Boolean,  # break: a hesitation within 1 s; hesitate: a break within 1 s
    "mistake_kind": pl.String,  # mistake: one of KINDS or unknown
}


# --- Measuring --------------------------------------------------------------------------------


def observations(store: Store, reasons: Counter | None = None) -> pl.DataFrame:
    """One row per measured item in the human sessions (OBS_SCHEMA). With ``reasons``, also
    counts the meta.hesitate, look.idle_scan and meta.mistake spans left out, by why."""
    reasons = Counter() if reasons is None else reasons
    rows: list[dict] = []
    for s in store.sessions(source="human"):
        rows += _session(store, s["session_id"], reasons)
    if not rows:
        return pl.DataFrame(schema=OBS_SCHEMA)
    return pl.from_dicts(rows, schema=OBS_SCHEMA).sort("session_id", "segment_id", "tick")


def _read(store: Store, table: str, session_id: str) -> pl.DataFrame:
    path = store.table_path(table, session_id)
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=SCHEMAS[table])


def _session(store: Store, session_id: str, reasons: Counter) -> list[dict]:
    labels = effective_labels(
        store.labels(session_id=session_id), store.corrections(session_id=session_id)
    ).filter(
        pl.col("label").is_in([HESITATE, SCAN, MISTAKE]) | pl.col("label").str.starts_with("act.")
    )
    spans = labels.filter(pl.col("label").is_in([HESITATE, SCAN, MISTAKE]))
    path = store.table_path("ticks_clean", session_id)
    if not path.exists():
        for (label,), g in spans.group_by("label"):
            reasons[f"{label}: session not converted"] += g.height
        return []
    ticks = pl.read_parquet(path)
    events = _read(store, "events", session_id).filter(~pl.col("while_paused").fill_null(False))
    buttons = _read(store, "buttons", session_id)
    excluded = exclusions.for_session(store, session_id)
    afk = {
        r["segment_id"]
        for r in store.db.execute(
            "SELECT segment_id FROM segments WHERE session_id = ? AND suspect_afk", (session_id,)
        )
    }
    out: list[dict] = []
    seen = set()
    for (segment_id,), t in ticks.partition_by("segment_id", as_dict=True).items():
        seen.add(segment_id)
        seg = _seg(session_id, segment_id, t.sort("tick"), events, buttons)
        mine = labels.filter(pl.col("segment_id") == segment_id)
        out += _segment(seg, mine, excluded, segment_id in afk, reasons)
    for (label,), g in spans.filter(~pl.col("segment_id").is_in(list(seen))).group_by("label"):
        reasons[f"{label}: no ticks"] += g.height
    return out


def _seg(
    session_id: str,
    segment_id: str,
    ticks: pl.DataFrame,
    events: pl.DataFrame,
    buttons: pl.DataFrame,
) -> Seg:
    """The segment as the detectors see it, from the cleaned ticks, the events (none from paused
    time) and the mouse buttons: what the meta.mistake parts, world actions and screens read."""
    lo, hi = ticks["tick"][0], ticks["tick"][-1]

    def within(df: pl.DataFrame) -> pl.DataFrame:
        return df.filter(
            (pl.col("segment_id") == segment_id) & pl.col("tick").is_between(lo, hi)
        ).sort("tick", "t_ns")

    return Seg(
        session_id=session_id,
        segment_id=segment_id,
        header=pb.SessionHeader(),
        ticks=ticks,
        events=within(events),
        mouse=pl.DataFrame(schema=SCHEMAS["mouse"]),
        keys=pl.DataFrame(schema=SCHEMAS["keys"]),
        buttons=within(buttons),
        gui=pl.DataFrame(schema=SCHEMAS["gui"]),
    )


def _rows(seg: Seg, labels: pl.DataFrame, *names: str) -> list[tuple[int, int]]:
    """(first row, last row) of each span labeled one of ``names``, in order."""
    g = labels.filter(pl.col("label").is_in(names)).sort("start_tick")
    return [
        (seg.index_of(a), seg.index_of(b))
        for a, b in g.select("start_tick", "end_tick").iter_rows()
    ]


def _contexts(seg: Seg, labels: pl.DataFrame) -> np.ndarray:
    """Index into CONTEXTS of each tick's context; len(CONTEXTS) for other."""
    code = np.full(len(seg), len(CONTEXTS))
    for k, names in reversed(list(enumerate(CONTEXTS.values()))):
        for i0, i1 in _rows(seg, labels, *names):
            code[i0 : i1 + 1] = k
    return code


def _segment(
    seg: Seg,
    labels: pl.DataFrame,
    excluded: list[exclusions.Exclusion],
    afk: bool,
    reasons: Counter,
) -> list[dict]:
    n = len(seg)
    tick = seg.tick
    active = np.ones(n, dtype=bool)
    for e in excluded:
        active &= ~((tick >= e.start_tick) & (tick <= e.end_tick))
    if afk:
        active[max(n - AFK_TICKS, 0) :] = False
    context = _contexts(seg, labels)
    stationary = (seg.col("speed") < MOVING_SPEED) & ~moving_keys(seg) & ~screen_shown(seg)
    base = {"session_id": seg.session_id, "segment_id": seg.segment_id}
    first = int(tick[0])
    names = [*CONTEXTS, OTHER]
    out = [
        base
        | {
            "kind": "active",
            "tick": first,
            "context": c,
            "ticks": int((active & (context == k)).sum()),
        }
        for k, c in enumerate(names)
    ]
    out.append(
        base | {"kind": "stationary", "tick": first, "ticks": int((active & stationary).sum())}
    )

    def usable(label: str) -> list[tuple[int, int]]:
        kept = []
        for i0, i1 in _rows(seg, labels, label):
            if active[i0 : i1 + 1].all():
                kept.append((i0, i1))
            else:
                reasons[f"{label}: excluded play"] += 1
        return kept

    # Hesitations, and the blocks broken before them.
    pauses = usable(HESITATE)
    starts = np.array([i0 for i0, _ in pauses], dtype=np.int64)
    breaks = np.sort(rows_of(seg, seg.events_of("block_broken")["tick"]))
    w = AFTER_BREAK_TICKS
    for i0, i1 in pauses:
        out.append(
            base
            | {
                "kind": "hesitate",
                "tick": int(tick[i0]),
                "context": names[context[i0]],
                "ticks": i1 - i0 + 1,
                "ms": (i1 - i0 + 1) * TICK_MS,
                "after_break": bool(((breaks >= i0 - w) & (breaks <= i0)).any()),
            }
        )
    for r in breaks:
        if r + w < n and active[r : r + w + 1].all():
            hit = bool(((starts >= r) & (starts <= r + w)).any())
            out.append(base | {"kind": "break", "tick": int(tick[r]), "after_break": hit})

    out += _glances(seg, usable(SCAN), stationary, active, base, reasons)

    parts = _mistake_parts(seg)
    for i0, i1 in usable(MISTAKE):
        over = [(c, -KINDS.index(k), k) for a, b, c, k in parts if a <= i1 and b >= i0]
        out.append(
            base
            | {
                "kind": "mistake",
                "tick": int(tick[i0]),
                "ticks": i1 - i0 + 1,
                "mistake_kind": max(over)[2] if over else UNKNOWN,
            }
        )
    return out


def _wrap(deg: np.ndarray) -> np.ndarray:
    """Angular distance in degrees, 0 to 180."""
    return np.abs((deg + 180.0) % 360.0 - 180.0)


def _glances(
    seg: Seg,
    scans: list[tuple[int, int]],
    stationary: np.ndarray,
    active: np.ndarray,
    base: dict,
    reasons: Counter,
) -> list[dict]:
    yaw = np.cumsum(np.nan_to_num(seg.col("yaw_rate"))) / 20.0  # unwrapped, degrees
    turning = turn_deg(seg) >= TURN_MIN
    quiet = stationary & ~world_actions(seg)
    ends = turning | ~quiet  # what ends a hold
    out: list[dict] = []
    last = None  # (start yaw, angle, row the hold ended) of the last glance
    for i0, i1 in scans:
        if not stationary[i0 : i1 + 1].all():
            reasons[f"{SCAN}: moving or a movement key held"] += 1
            continue
        if last is not None and last[2] is not None:
            y0, angle, q = last
            back = _wrap(yaw[i1] - y0) <= (1 - RETURN_SHARE) * angle
            if q <= i0 <= q + RETURN_GAP and turning[q] and quiet[q:i0].all() and back:
                reasons[f"{SCAN}: the look coming back from a glance"] += 1
                last = None
                continue
        if not stationary[i1 + 1 : i1 + 1 + SET_OFF_TICKS].all():
            reasons[f"{SCAN}: a turn to walk off"] += 1
            continue
        y0 = yaw[i0 - 1] if i0 > 0 else yaw[i0]
        away = _wrap(yaw[i0 : i1 + 1] - y0)
        p = i0 + int(np.argmax(away))
        rest = np.flatnonzero(ends[p + 1 :])
        q = p + 1 + int(rest[0]) if rest.size else None
        hold = (q - p) * TICK_MS if q is not None and active[p : q + 1].all() else None
        out.append(
            base
            | {
                "kind": "glance",
                "tick": int(seg.tick[i0]),
                "ticks": i1 - i0 + 1,
                "ms": hold,
                "angle_deg": float(away.max()),
            }
        )
        last = (y0, float(away.max()), q)
    return out


def _mistake_parts(seg: Seg) -> list[tuple[int, int, float, str]]:
    """(first row, last row, confidence, kind) of each part meta.mistake merges."""
    act = world_actions(seg)
    parts = [
        ("wrong_slot", found._wrong_slot(seg, act)),
        ("missed_click", found._missed_clicks(seg)),
        ("overshoot", found._overshoots(seg, act)),
        ("misplaced_block", found._undone_placements(seg)),
    ]
    return [(a, b, c, kind) for kind, found_parts in parts for a, b, c in found_parts]


# --- Fitting ----------------------------------------------------------------------------------


def fit(obs: pl.DataFrame, defaulted: list[str] | None = None) -> dict:
    """The meta model (JSON-able) from ``obs`` (observations()). Quantities with too little data
    fall back (see the module docstring); with ``defaulted``, their names are added."""
    defaulted = [] if defaulted is None else defaulted

    def pick(name: str, value, fallback=None):
        if value is not None:
            return value
        defaulted.append(name)
        if fallback is not None:
            return fallback
        out = DEFAULTS
        for key in name.split("."):
            out = out[key]
        return out

    def of(kind: str) -> pl.DataFrame:
        return obs.filter(pl.col("kind") == kind)

    def minutes(df: pl.DataFrame) -> float:
        return float(df["ticks"].sum()) / TICKS_PER_MIN

    active, hes, breaks = of("active"), of("hesitate"), of("break")
    glances, mistakes = of("glance"), of("mistake")
    total = minutes(active)
    still = minutes(of("stationary"))
    pooled = hes.height / total if total >= MIN_MINUTES else None
    per_min = {}
    for c in [*CONTEXTS, OTHER]:
        m = minutes(active.filter(pl.col("context") == c))
        rate = hes.filter(pl.col("context") == c).height / m if m >= MIN_MINUTES else None
        name = f"hesitation.per_min.{c}"
        if rate is None and pooled is not None:
            per_min[c] = pick(f"{name} (all-play rate)", None, pooled)
        else:
            per_min[c] = pick(name, rate)
    known = mistakes.filter(pl.col("mistake_kind") != UNKNOWN)
    kinds = None
    if known.height >= MIN_SAMPLES:
        counts = Counter(known["mistake_kind"])
        kinds = {k: counts[k] / known.height for k in KINDS}

    def values(df: pl.DataFrame, col: str) -> np.ndarray:
        return df[col].drop_nulls().cast(pl.Float64).to_numpy()

    model = {
        "schema": SCHEMA_VERSION,
        "kind": "meta",
        "created": int(time.time()),
        "fitted_on": {
            "sessions": active.filter(pl.col("ticks") > 0)["session_id"].n_unique(),
            "active_minutes": total,
        },
        "hesitation": {
            "per_min": per_min,
            "after_break_p": pick(
                "hesitation.after_break_p",
                float(breaks["after_break"].mean()) if breaks.height >= MIN_SAMPLES else None,
            ),
            "duration_ms": pick("hesitation.duration_ms", _log_moments(values(hes, "ms"))),
        },
        "glance": {
            "per_min": pick(
                "glance.per_min", glances.height / still if still >= MIN_MINUTES else None
            ),
            "angle_deg": pick("glance.angle_deg", _lognormal(values(glances, "angle_deg"))),
            "hold_ms": pick("glance.hold_ms", _lognormal(values(glances, "ms"))),
        },
        "mistake": {
            "per_min": pick(
                "mistake.per_min", mistakes.height / total if total >= MIN_MINUTES else None
            ),
            "kinds": pick("mistake.kinds", kinds),
        },
    }
    return _rounded(model)


def _log_moments(x: np.ndarray) -> dict | None:
    """Median and sigma of a log-normal from the mean and sd of all the logs (no outliers
    dropped: for values the detector already bounds)."""
    x = x[np.isfinite(x) & (x > 0)]
    if len(x) < MIN_SAMPLES:
        return None
    logs = np.log(x)
    return {"median": float(np.exp(logs.mean())), "sigma": float(logs.std(ddof=1))}


# --- Output -----------------------------------------------------------------------------------


def write(model: dict, out_dir: str | Path) -> Path:
    """Writes ``model`` to out_dir/meta.json (indent 1, trailing newline)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(model, indent=1) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


def export(store: Store, out_dir: str | Path) -> Path:
    """Fits the meta model on the store's human play and writes it; returns the file."""
    return write(fit(observations(store)), out_dir)


def summary(model: dict) -> list[str]:
    """The fitted numbers in a few report lines."""
    h, g, m = model["hesitation"], model["glance"], model["mistake"]
    rates = ", ".join(f"{c} {r:.2f}" for c, r in h["per_min"].items())
    kinds = ", ".join(f"{k} {share:.0%}" for k, share in m["kinds"].items())
    return [
        f"hesitation   per active minute: {rates}; {h['after_break_p']:.0%} of broken blocks "
        f"followed by one within 1 s",
        f"             lasting median {h['duration_ms']['median']:.0f} ms, sigma "
        f"{h['duration_ms']['sigma']:.2f}",
        f"glance       {g['per_min']:.2f} per stationary minute, median "
        f"{g['angle_deg']['median']:.0f} deg of yaw away (sigma {g['angle_deg']['sigma']:.2f}), "
        f"held median {g['hold_ms']['median']:.0f} ms (sigma {g['hold_ms']['sigma']:.2f})",
        f"mistake      {m['per_min']:.2f} per active minute: {kinds}",
        f"fitted on    {model['fitted_on']['active_minutes']:.1f} active minutes",
    ]
