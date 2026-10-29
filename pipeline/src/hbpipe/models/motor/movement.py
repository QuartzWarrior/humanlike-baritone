"""Stage 1 movement model (spec, Component B: Movement, move.*).

When Baritone walks a path, the Java executor presses the movement keys and steers the camera the
way this player does: it holds keys for this player's durations, lets go of forward and grabs it
again, starts sprinting the way they do, re-jumps after landing on their timing, lets the camera
wander off the path and come back, aims ahead into turns, and walks with their pitch. This module
measures those quantities on the player's own play and fits the numbers the executor samples
from. Units are ticks (20 a second), degrees and blocks.

Data: human sessions only (bot play never), labels as training sees them (review verdicts applied,
hbpipe.label.corrections), and nothing from play kept out of training (hbpipe.exclusions): an
excluded tick is never measured, a measurement spanning several ticks needs all of them outside,
and a label span touching an exclusion is dropped whole. Keys come from the keys table (when each
bound key went down and up, to the nanosecond; repeated downs from OS key repeat are one press),
everything else from the cleaned ticks (hbpipe.label.clean). Two groups of label sets select the
play:
    MOVING_SETS     on-foot movement of any kind (walk, sprint, sprint-jump, head-hitter,
                    strafe, sneak, step-up, parkour): the key timings
    ON_FOOT         walking or sprinting over the ground, jumps included (walk, sprint,
                    sprint-jump): the camera while travelling

Measuring (measurements):
    holds       each press of a bound key, from down to up, in ticks (fractional). Strafe pools
                left and right. A press counts when at least half of the ticks it is held fall
                in MOVING_SETS spans, and as it goes down the player is on foot (not in water,
                climbing, gliding or riding) with no screen open. So mining taps, pillar jumps
                and swimming don't count: they belong to other models. A press still down when
                the recording stops is left out.
    regrips     forward released and pressed again within REGRIP_MAX_TICKS while moving: on a
                MOVING_SETS tick at MOVING_SPEED or faster when it came up, no screen at either
                end. The gap, in ticks; gaps shorter than a tick happen (the game may never see
                them). The cut-off can't tell a regrip of the fingers from a short stop: the
                data are whatever release-to-press gaps fall under it.
    sprint      each tick sprinting begins (not a segment's first tick, not riding). The game
                starts a sprint for one of two inputs: the sprint key is down (held, or toggled
                on when toggle sprint is set: the recorded key state is the toggled one), or
                forward was pressed twice within SPRINT_WINDOW ticks while not sprinting (double
                tap; not sprinting between the presses, else the first press already sprinted).
                A start with neither has no player input behind it: a client-side auto-sprint
                (Wurst's AutoSprint does this) or a mod. Those are left out and counted, so the
                shares are of the starts the player made. ticks_clean's sprint_method names
                starts the same way but calls every keyless start "toggle" when toggle sprint is
                set; that is backwards (a toggled-on key shows as down), so it isn't used here.
    jump waits  inside move.sprint_jump spans, every takeoff after the span's first: how many
                ticks the player stayed on the ground after landing from the jump before (the
                takeoff detector of hbpipe.label.detectors.movement, and ticks_since_landing on
                the last tick on the ground). 0 is the earliest possible jump, on the tick after
                touching down (jump held through the landing). Waits over MAX_JUMP_WAIT are
                sprinting between jumps, not a re-jump, and are left out (sprint_jump_share
                covers them).
    sprint share    every tick sprinting in the open: in a move.sprint_jump span, or in a
                move.sprint span with OPEN_HEADROOM or more blocks free above the head (under a
                lower ceiling the choice is forced). The share of them in move.sprint_jump spans.
    heading     the camera's yaw minus the direction of travel (positive: the camera points
                right of where the player goes; yaw grows turning right), on ON_FOOT ticks with
                forward held and no side or back key, no screen, at MOVING_SPEED or faster,
                where the path runs straight: the direction of travel at a tick is that of the
                displacement from HEADING_HALF ticks before it to HEADING_HALF ticks after, and
                the path counts as straight when that displacement is at least STRAIGHT of the
                distance walked (and at least MIN_PATH blocks; 0.99 lets an arc turn about 28
                degrees over the window). Consecutive such ticks form a series; series of
                MIN_RUN ticks or more are kept. Jumps are included: a player who sprint-jumps
                spends most of the time travelling in the air, and the errors spread alike on
                the ground and in the air. Limits: the player walks where the camera points, so
                drift slower than the window bends the path and is taken for the route, and
                a stricter or looser "straight" selects calmer or wilder stretches (the spread
                roughly halves or doubles between 0.995 and 0.95). This is the camera's wobble
                about the player's own path; measuring the executor's play the same way is the
                like-for-like check.
    look-ahead  on ON_FOOT ticks on the ground with forward held and no side or back key, no
                screen, at MOVING_SPEED or faster, where the path over the next LOOK_HORIZON
                ticks turns by LOOK_MIN_TURN degrees or more: the distance along that future
                path to the point the camera points at, where the bearing from the current
                position to the path crosses the camera's yaw (interpolated between ticks;
                points nearer than LOOK_MIN_DIST are too close for a bearing). This is what a
                pure-pursuit steering law needs: aim at the path point that far ahead and
                corners get cut, and the camera starts turning that far before a turn, the way
                this player does it. When the camera isn't turned into the turn (the nearest
                usable point lies within LOOK_TOL of its direction or on its far side), it aims
                no further than where the path leaves its direction: on a straight run-up to a
                turn every point of the straight has the same bearing, so the look-ahead is
                only known to be shorter than the distance to the bend. When it is aimed past
                the horizon's point, the look-ahead is longer than the horizon's distance. Both
                are kept as censored values (quantities look_ahead_under and look_ahead_over)
                and the log-normal is fitted by maximum likelihood with censoring. About half
                the curve ticks are "under": much of the time the camera simply follows the
                path. A camera turned further from the path than the path itself turns is
                looking elsewhere and is left out. Limits: it is
                measured on the path the player walked, not the one they meant, and the player
                moves where the camera pointed a moment ago (momentum), so a camera that only
                follows the path still measures a few tenths of a block.
    walk pitch  camera pitch on ON_FOOT ticks on the ground with forward held, no side or back
                key, at MOVING_SPEED or faster, no screen. Positive pitch looks down (Minecraft's
                sign).

The model (fit, movement.json):
    fitted_on           human sessions with play outside exclusions, and minutes of that play
    hold_ticks          per key (forward, strafe, back, jump, sneak): log-normal (median, sigma
                        of the log) of how long one press is held, ticks
    regrip_gap_ticks    log-normal of the regrip gaps, ticks
    sprint_method       shares of the player's sprint starts by input: key (held), double_tap,
                        toggle (toggled on)
    jump_after_landing  probability of each wait 0..MAX_JUMP_WAIT ticks between landing and
                        the next takeoff while sprint-jumping (sums to 1)
    sprint_jump_share   share of sprinting in the open spent sprint-jumping
    heading             an Ornstein-Uhlenbeck process for the heading error e (degrees): it
                        wanders about mean_deg with stationary standard deviation sigma_deg and
                        reverts at theta_per_s (de = theta (mean - e) dt + sigma sqrt(2 theta)
                        dW). The executor steps it once a tick (dt = 0.05 s) with e' = mean +
                        (e - mean) exp(-theta dt) + sigma sqrt(1 - exp(-2 theta dt)) N(0, 1) and
                        turns the camera to the path direction plus e.
    look_ahead_blocks   log-normal of the look-ahead distance, blocks
    walk_pitch          mean and sd of the walking pitch, degrees

Fitting: log-normals and the pitch use the camera model's outlier-resistant estimates (values more
than OUTLIER_SDS robust sds from the median are dropped, unless that would drop more than a tenth
of them). The heading's mean and sigma are the mean and sd of the errors after the same trim.
The camera turns smoothly, so the per-tick errors are far smoother than an Ornstein-Uhlenbeck
process: their autocorrelation stays near 1 for a tick or two, then falls off within half a
second. An AR(1) fit on consecutive ticks would see only the smoothness and put theta several
times too low; instead theta is 1 / tau with tau the lag at which the autocorrelation (pooled over
the series, errors centred on the mean) first falls below 1/e, interpolated between ticks: the
process forgets at the same pace as the player's camera. If it never falls that far within
2 HEADING_HALF ticks, tau is that long (the window can't show slower). Per-tick samples are
strongly correlated, so tick-based quantities need MIN_TICKS of them and event-based ones
MIN_SAMPLES; with fewer a quantity falls back to DEFAULTS, generic guesses for a keyboard player,
not this player.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import numpy as np
import polars as pl
from scipy.optimize import minimize
from scipy.special import log_ndtr

from hbpipe import exclusions
from hbpipe.label.base import Seg, runs
from hbpipe.label.corrections import effective_labels
from hbpipe.label.detectors.movement import free_movement, takeoffs
from hbpipe.models.motor.camera import _lognormal, _robust
from hbpipe.store import Store

FILE = "movement.json"
SCHEMA_VERSION = 1
TICK_MS = 50.0
TICKS_PER_S = 20

SPRINT, SPRINT_JUMP = "move.sprint", "move.sprint_jump"
ON_FOOT = ("move.walk", SPRINT, SPRINT_JUMP)
MOVING_SETS = (
    *ON_FOOT,
    "move.head_hitter",
    "move.strafe",
    "move.sneak",
    "move.step_up",
    "move.parkour",
)
KEYS = {
    "forward": ("key.forward",),
    "strafe": ("key.left", "key.right"),
    "back": ("key.back",),
    "jump": ("key.jump",),
    "sneak": ("key.sneak",),
}
METHODS = ("key", "double_tap", "toggle")

MOVING_SPEED = 1.0  # m/s; vanilla walking is 4.3
HOLD_IN_SETS = 0.5  # share of a press's ticks in MOVING_SETS spans
REGRIP_MAX_TICKS = 20.0
SPRINT_WINDOW = 7  # ticks; the game's default double-tap window (options.sprintWindow, unrecorded)
MAX_JUMP_WAIT = 10
OPEN_HEADROOM = 3
HEADING_HALF = 20  # ticks each side of the tick measured
STRAIGHT = 0.99  # displacement over distance walked
MIN_PATH = 2.0  # blocks walked over the heading window
MAX_STEP = 1.5  # blocks in a tick; more is a teleport
MIN_RUN = 10  # ticks in a heading series
LOOK_HORIZON = 60  # ticks
LOOK_MIN_DIST = 0.5  # blocks
LOOK_MIN_TURN = 20.0  # degrees
LOOK_TOL = 2.0  # degrees; a path point this close to the camera's direction is in it

MIN_SAMPLES = 20
MIN_TICKS = 200

DEFAULTS: dict = {
    "hold_ticks": {
        "forward": {"median": 20.0, "sigma": 1.2},
        "strafe": {"median": 6.0, "sigma": 0.8},
        "back": {"median": 5.0, "sigma": 0.7},
        "jump": {"median": 4.0, "sigma": 1.0},
        "sneak": {"median": 30.0, "sigma": 1.3},
    },
    "regrip_gap_ticks": {"median": 4.0, "sigma": 0.6},
    "sprint_method": {"key": 0.6, "double_tap": 0.2, "toggle": 0.2},
    "jump_after_landing": [0.6, 0.25, 0.1, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "sprint_jump_share": 0.5,
    "heading": {"theta_per_s": 3.0, "sigma_deg": 3.0, "mean_deg": 0.0},
    "look_ahead_blocks": {"median": 1.0, "sigma": 1.2},
    "walk_pitch": {"mean": 15.0, "sd": 12.0},
    "mining_sneak": {
        "share": 0.0,
        "bout_ticks": {"median": 100.0, "sigma": 1.0},
        "gap_ticks": {"median": 100.0, "sigma": 1.0},
    },
    "mining_steps": {
        "per_min": 0.0,
        "ticks": {"median": 6.0, "sigma": 1.0},
        "keys": {"forward": 0.4, "back": 0.2, "left": 0.2, "right": 0.2},
    },
    "mining_reach": {"median": 1.5, "q90": 2.7},
}
STEP_KEYS = {"forward": "k_forward", "back": "k_back", "left": "k_left", "right": "k_right"}
# A movement-key burst while mining up to this long (ticks) is a step about the work; longer is a
# walk to the next spot, which the planner does itself.
MINING_STEP_MAX = 40
# Eye height above the feet, blocks: standing, and crouching (sneaking).
EYE = 1.62
EYE_CROUCHING = 1.27
# Mining activities, for sneaking while mining.
MINING_SETS = (
    "act.area_clear",
    "act.strip_mine",
    "act.tunnel",
    "mine.area_clear",
    "mine.strip",
    "mine.tunnel",
)

MEASURE_SCHEMA = {
    "session_id": pl.String,
    "segment_id": pl.String,
    "tick": pl.Int64,  # where measured: the press, the release, the start, the takeoff, the tick
    "quantity": pl.String,  # play, hold.<key>, regrip, sprint_start.<method>, jump_wait,
    #   sprint_open, heading, look_ahead, look_ahead_under, look_ahead_over, walk_pitch,
    #   mining_sneak (per mining tick, 1 or 0), mining_sneak_bout, mining_sneak_gap (ticks),
    #   mining_step.<key> (a burst of movement keys while mining, by its main key; ticks),
    #   mining_reach (eye to the nearest point of each block broken while mining; blocks)
    "value": pl.Float64,  # ticks, degrees or blocks; 1 or 0 for sprint_start and sprint_open
    "run": pl.Int64,  # heading: the series within the segment; null otherwise
}

TICK_COLUMNS = [
    "segment_id",
    "tick",
    "filled",
    "x",
    "y",
    "z",
    "yaw",
    "pitch",
    "on_ground",
    "sprinting",
    "in_water",
    "swimming",
    "on_climbable",
    "gliding",
    "passenger",
    "k_forward",
    "k_back",
    "k_left",
    "k_right",
    "k_jump",
    "k_sprint",
    "speed",
    "vspeed",
    "headroom",
    "ticks_since_landing",
    "screen_open",
    "crouching",
]
KEY_SCHEMA = {
    "segment_id": pl.String,
    "tick": pl.Int64,
    "t_ns": pl.Int64,
    "down": pl.Boolean,
    "binding": pl.String,
}


# --- Measuring --------------------------------------------------------------------------------


def measurements(store: Store, reasons: Counter | None = None) -> pl.DataFrame:
    """Every measurement in the human sessions, one row each (MEASURE_SCHEMA). With
    ``reasons``, also counts what was left out, by why."""
    reasons = Counter() if reasons is None else reasons
    frames: list[pl.DataFrame] = []
    for s in store.sessions(source="human"):
        frames += _session(store, s["session_id"], reasons)
    frames = [f for f in frames if f.height]
    if not frames:
        return pl.DataFrame(schema=MEASURE_SCHEMA)
    return pl.concat(frames).sort("session_id", "segment_id", "quantity", "tick")


def _rows(seg: Seg, quantity: str, tick, value, run: np.ndarray | None = None) -> pl.DataFrame:
    n = len(tick)
    return pl.DataFrame(
        {
            "session_id": [seg.session_id] * n,
            "segment_id": [seg.segment_id] * n,
            "tick": np.asarray(tick, dtype=np.int64),
            "quantity": [quantity] * n,
            "value": np.asarray(value, dtype=np.float64),
            "run": None if run is None else np.asarray(run, dtype=np.int64),
        },
        schema=MEASURE_SCHEMA,
    )


def _toggle_sprint(store: Store, session_id: str, reasons: Counter) -> bool:
    """The session's toggle sprint setting; without a header, taken as off (a sprint key down
    as a sprint starts then counts as held)."""
    from hbpipe.label.run import header_of

    try:
        return bool(header_of(store, session_id).toggle_sprint)
    except FileNotFoundError:
        reasons["no session header: sprint key taken as held"] += 1
        return False


def _session(store: Store, session_id: str, reasons: Counter) -> list[pl.DataFrame]:
    path = store.table_path("ticks_clean", session_id)
    if not path.exists():
        reasons["session not converted"] += 1
        return []
    ticks = pl.read_parquet(path, columns=TICK_COLUMNS).with_columns(pl.col("tick").cast(pl.Int64))
    if ticks.is_empty():
        return []
    keys_path = store.table_path("keys", session_id)
    if keys_path.exists():
        keys = pl.read_parquet(keys_path, columns=list(KEY_SCHEMA)).cast(KEY_SCHEMA)
    else:
        reasons["no keys table: holds not measured"] += 1
        keys = pl.DataFrame(schema=KEY_SCHEMA)
    labels = effective_labels(
        store.labels(session_id=session_id), store.corrections(session_id=session_id)
    ).filter(pl.col("label").is_in(MOVING_SETS + MINING_SETS))
    excluded = exclusions.for_session(store, session_id)
    kept = exclusions.drop_excluded(labels, excluded, "start_tick", "end_tick")
    reasons["label spans touching excluded play"] += labels.height - kept.height
    toggle = _toggle_sprint(store, session_id, reasons)
    breaks = _breaks(store, session_id)
    out = []
    for (segment_id,), t in ticks.group_by("segment_id", maintain_order=True):
        seg = Seg(
            session_id=session_id,
            segment_id=segment_id,
            header=None,  # nothing here reads it
            ticks=t.sort("tick"),
            events=breaks.filter(pl.col("segment_id") == segment_id),
            mouse=pl.DataFrame(),
            keys=keys.filter(pl.col("segment_id") == segment_id).sort("t_ns"),
            buttons=pl.DataFrame(),
            gui=pl.DataFrame(),
        )
        spans = kept.filter(pl.col("segment_id") == segment_id)
        out += _segment(seg, spans, excluded, toggle, reasons)
    return out


def _breaks(store: Store, session_id: str) -> pl.DataFrame:
    """The session's block breaks: segment_id, tick and the block's x, y, z."""
    schema = {
        "segment_id": pl.String,
        "tick": pl.Int64,
        "x": pl.Int64,
        "y": pl.Int64,
        "z": pl.Int64,
    }
    path = store.table_path("events", session_id)
    if not path.exists():
        return pl.DataFrame(schema=schema)
    ev = pl.read_parquet(path, columns=["segment_id", "tick", "kind", "x", "y", "z"])
    return (
        ev.filter((pl.col("kind") == "block_broken") & pl.col("x").is_not_null())
        .select(list(schema))
        .cast(schema)
    )


def _span_mask(tick: np.ndarray, spans: pl.DataFrame, *labels: str) -> np.ndarray:
    """Rows whose tick falls in any span (start_tick..end_tick, inclusive) with one of
    ``labels``."""
    mask = np.zeros(len(tick), dtype=bool)
    chosen = spans.filter(pl.col("label").is_in(labels))
    for a, b in chosen.select("start_tick", "end_tick").iter_rows():
        mask[np.searchsorted(tick, a) : np.searchsorted(tick, b, side="right")] = True
    return mask


def _segment(
    seg: Seg,
    spans: pl.DataFrame,
    excluded: list[exclusions.Exclusion],
    toggle: bool,
    reasons: Counter,
) -> list[pl.DataFrame]:
    tick = seg.tick
    ok = np.ones(len(seg), dtype=bool)
    for e in excluded:
        ok &= ~((tick >= e.start_tick) & (tick <= e.end_tick))
    if not ok.any():
        return []
    moving = _span_mask(tick, spans, *MOVING_SETS) & ok
    on_foot = _span_mask(tick, spans, *ON_FOOT) & ok
    sprint_jump = _span_mask(tick, spans, SPRINT_JUMP) & ok
    sprint = _span_mask(tick, spans, SPRINT) & ok
    open_ = sprint_jump | (sprint & (seg.col("headroom") >= OPEN_HEADROOM))
    steering = _steering(seg, on_foot)
    walking = steering & seg.col("on_ground")
    return [
        _rows(seg, "play", [tick[0]], [ok.sum()]),
        *_holds(seg, ok, moving, reasons),
        *_sprint_starts(seg, ok, toggle, reasons),
        _jump_waits(seg, spans.filter(pl.col("label") == SPRINT_JUMP), reasons),
        _rows(seg, "sprint_open", tick[open_], sprint_jump[open_]),
        _heading(seg, on_foot, steering, reasons),
        *_look_ahead(seg, on_foot, walking, reasons),
        _rows(seg, "walk_pitch", tick[walking], seg.col("pitch")[walking]),
        *_mining_sneak(seg, _span_mask(tick, spans, *MINING_SETS) & ok),
        *_mining_steps(seg, _span_mask(tick, spans, *MINING_SETS) & ok),
        _mining_reach(seg, _span_mask(tick, spans, *MINING_SETS) & ok),
    ]


def _mining_reach(seg: Seg, mining: np.ndarray) -> pl.DataFrame:
    """How far from the eye each block broken while mining was: to the nearest point of the
    block, from where the player stood on the tick it broke (crouching lowers the eye)."""
    tick = seg.tick
    b = seg.events
    if b.is_empty():
        return _rows(seg, "mining_reach", [], [])
    i = np.searchsorted(tick, b["tick"].to_numpy())
    found = i < len(tick)
    found[found] = tick[i[found]] == b["tick"].to_numpy()[found]
    i = i[found]
    keep = mining[i]
    i = i[keep]
    bx, by, bz = (b[c].to_numpy()[found][keep] for c in ("x", "y", "z"))
    eye = seg.col("y")[i] + np.where(seg.col("crouching")[i].astype(bool), EYE_CROUCHING, EYE)

    def gap(p, lo):
        return np.maximum(0.0, np.maximum(lo - p, p - (lo + 1)))

    d = np.sqrt(gap(seg.col("x")[i], bx) ** 2 + gap(eye, by) ** 2 + gap(seg.col("z")[i], bz) ** 2)
    return _rows(seg, "mining_reach", tick[i], d)


def _mining_steps(seg: Seg, mining: np.ndarray) -> list[pl.DataFrame]:
    """Bursts of movement keys while mining, by the key held most in each (whole bursts only, as
    for sneaking)."""
    held = {name: seg.col(col).astype(bool) for name, col in STEP_KEYS.items()}
    any_key = np.logical_or.reduce(list(held.values()))
    found: dict[str, list[tuple[int, int]]] = {name: [] for name in STEP_KEYS}
    for a, b in runs(mining):
        part = any_key[a : b + 1]
        for c, d in runs(part):
            if c > 0 and d < len(part) - 1:
                main = max(STEP_KEYS, key=lambda k: held[k][a + c : a + d + 1].sum())
                found[main].append((seg.tick[a + c], d - c + 1))
    return [
        _rows(seg, f"mining_step.{k}", [t for t, _ in v], [n for _, n in v])
        for k, v in found.items()
    ]


def _mining_sneak(seg: Seg, mining: np.ndarray) -> list[pl.DataFrame]:
    """Sneaking while mining: every mining tick (1 sneaking, 0 not), and the lengths of the
    sneaking stretches and of the gaps between them. Only stretches that start and end inside the
    same run of mining count: one cut by the run's edges is longer than it looks."""
    tick = seg.tick
    sneak = seg.col("crouching").astype(bool)
    bouts: list[tuple[int, int]] = []
    gaps: list[tuple[int, int]] = []
    for a, b in runs(mining):  # inclusive index pairs
        part = sneak[a : b + 1]
        for c, d in runs(part):
            if c > 0 and d < len(part) - 1:
                bouts.append((tick[a + c], d - c + 1))
        for c, d in runs(~part):
            if c > 0 and d < len(part) - 1:
                gaps.append((tick[a + c], d - c + 1))
    return [
        _rows(seg, "mining_sneak", tick[mining], sneak[mining].astype(float)),
        _rows(seg, "mining_sneak_bout", [t for t, _ in bouts], [n for _, n in bouts]),
        _rows(seg, "mining_sneak_gap", [t for t, _ in gaps], [n for _, n in gaps]),
    ]


# Holds and regrips


def _presses(keys: pl.DataFrame, binding: str, reasons: Counter) -> np.ndarray:
    """(down t_ns, down tick, up t_ns, up tick) of each press of ``binding``, in order. Repeated
    downs (OS key repeat) are one press; an up with no down before it is a key pressed before the
    segment began."""
    out = []
    start = None
    k = keys.filter(pl.col("binding") == binding)
    for t_ns, tick, down in k.select("t_ns", "tick", "down").iter_rows():
        if down and start is None:
            start = (t_ns, tick)
        elif not down and start is not None:
            out.append((*start, t_ns, tick))
            start = None
    if start is not None:
        reasons["press still down when the segment ends"] += 1
    return np.array(out, dtype=np.int64).reshape(-1, 4)


def _holds(seg: Seg, ok: np.ndarray, moving: np.ndarray, reasons: Counter) -> list[pl.DataFrame]:
    tick = seg.tick
    usable = free_movement(seg) & ~seg.col("screen_open")
    bad = np.concatenate([[0], np.cumsum(~ok)])
    in_sets = np.concatenate([[0], np.cumsum(moving)])

    def clear(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """Rows a..b (inclusive) all outside exclusions."""
        return bad[b + 1] - bad[a] == 0

    out = []
    for name, bindings in KEYS.items():
        at, held = [], []
        for binding in bindings:
            p = _presses(seg.keys, binding, reasons)
            inside = (p[:, 1] >= tick[0]) & (p[:, 3] <= tick[-1])
            reasons["press outside the recorded ticks"] += int((~inside).sum())
            p = p[inside]
            i0, i1 = np.searchsorted(tick, p[:, 1]), np.searchsorted(tick, p[:, 3])
            share = (in_sets[i1 + 1] - in_sets[i0]) / (i1 - i0 + 1)
            usable_press = clear(i0, i1) & usable[i0]
            good = usable_press & (share >= HOLD_IN_SETS)
            reasons[f"{name} press in water, riding, a screen or excluded play"] += int(
                (~usable_press).sum()
            )
            reasons[f"{name} press not part of on-foot movement"] += int(
                (usable_press & ~good).sum()
            )
            at.append(p[good, 1])
            held.append((p[good, 2] - p[good, 0]) / 1e6 / TICK_MS)
            if binding == "key.forward":
                out.append(_regrips(seg, p, i0, i1, moving, clear, reasons))
        out.append(_rows(seg, f"hold.{name}", np.concatenate(at), np.concatenate(held)))
    return out


def _regrips(
    seg: Seg,
    p: np.ndarray,
    i0: np.ndarray,
    i1: np.ndarray,
    moving: np.ndarray,
    clear: Callable[[np.ndarray, np.ndarray], np.ndarray],
    reasons: Counter,
) -> pl.DataFrame:
    """Gaps between one forward press coming up and the next going down, while moving."""
    gap = (p[1:, 0] - p[:-1, 2]) / 1e6 / TICK_MS
    up, down = i1[:-1], i0[1:]
    brief = (gap >= 0) & (gap < REGRIP_MAX_TICKS)
    screen = seg.col("screen_open")
    good = (
        brief
        & moving[up]
        & (seg.col("speed")[up] >= MOVING_SPEED)
        & clear(up, down)
        & ~screen[up]
        & ~screen[down]
    )
    reasons["brief forward gap, not while moving"] += int((brief & ~good).sum())
    return _rows(seg, "regrip", p[:-1, 3][good], gap[good])


# Sprint starts


def _sprint_starts(seg: Seg, ok: np.ndarray, toggle: bool, reasons: Counter) -> list[pl.DataFrame]:
    sprinting = seg.col("sprinting")
    key = seg.col("k_sprint")
    forward = seg.col("k_forward") & ~seg.col("k_back")  # the game's forward impulse
    rises = np.flatnonzero(forward[1:] & ~forward[:-1]) + 1
    starts = np.flatnonzero(sprinting[1:] & ~sprinting[:-1]) + 1
    starts = starts[ok[starts] & ok[starts - 1] & ~seg.col("passenger")[starts]]
    found: dict[str, list[int]] = {m: [] for m in METHODS}
    for i in starts:
        if key[i] or key[i - 1]:
            found["toggle" if toggle else "key"].append(int(seg.tick[i]))
            continue
        # The second press comes down as the sprint starts (a tick of slack for where the game
        # sampled it), the first within the window before it.
        second = rises[(rises >= i - 1) & (rises <= i)]
        first = (
            rises[(rises < second[-1]) & (rises >= second[-1] - SPRINT_WINDOW)]
            if len(second)
            else second
        )
        if len(first) and not sprinting[first[-1] : i].any():
            found["double_tap"].append(int(seg.tick[i]))
        else:
            reasons["sprint started without a sprint key or double tap (auto-sprint)"] += 1
    return [_rows(seg, f"sprint_start.{m}", t, np.ones(len(t))) for m, t in found.items()]


# Sprint-jumping


def _jump_waits(seg: Seg, spans: pl.DataFrame, reasons: Counter) -> pl.DataFrame:
    jumps = takeoffs(seg)
    since = seg.col("ticks_since_landing")
    ticks, waits = [], []
    for a, b in spans.select("start_tick", "end_tick").iter_rows():
        i0, i1 = np.searchsorted(seg.tick, a), np.searchsorted(seg.tick, b, side="right")
        js = jumps[(jumps >= i0) & (jumps < i1)]
        for prev, j in zip(js[:-1], js[1:], strict=True):
            wait = since[j - 1]
            if not np.isfinite(wait) or j - 1 - wait <= prev:
                reasons["takeoff without a landing since the last"] += 1
            elif wait > MAX_JUMP_WAIT:
                reasons[f"takeoff more than {MAX_JUMP_WAIT} ticks after landing"] += 1
            else:
                ticks.append(int(seg.tick[j]))
                waits.append(wait)
    return _rows(seg, "jump_wait", ticks, waits)


# Camera while walking


def _steering(seg: Seg, on_foot: np.ndarray) -> np.ndarray:
    """On foot, forward held without side or back keys, no screen, a recorded tick, moving."""
    return (
        on_foot
        & seg.col("k_forward")
        & ~seg.col("k_back")
        & ~seg.col("k_left")
        & ~seg.col("k_right")
        & ~seg.col("screen_open")
        & ~seg.col("filled")
        & (seg.col("speed") >= MOVING_SPEED)
    )


def _wrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


def _bearing(dx: np.ndarray, dz: np.ndarray) -> np.ndarray:
    """Minecraft yaw of a horizontal direction: 0 faces +z (south), 90 faces -x (west)."""
    return np.degrees(np.arctan2(-dx, dz))


def _path(seg: Seg, on_foot: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-tick step lengths (row i to i + 1), and running counts of the rows a measured path
    may not cross: off foot, a screen, a filled-in row, or a step that's a teleport."""
    step = np.hypot(np.diff(seg.col("x")), np.diff(seg.col("z")))
    blocked = ~on_foot | seg.col("screen_open") | seg.col("filled")
    blocked[:-1] |= step > MAX_STEP
    return step, np.concatenate([[0], np.cumsum(blocked)])


def _heading(seg: Seg, on_foot: np.ndarray, steering: np.ndarray, reasons: Counter) -> pl.DataFrame:
    n, w = len(seg), HEADING_HALF
    if n <= 2 * w:
        return _rows(seg, "heading", [], [], np.array([]))
    x, z, yaw = seg.col("x"), seg.col("z"), seg.col("yaw")
    step, blocked = _path(seg, on_foot)
    dist = np.concatenate([[0.0], np.cumsum(step)])
    i = np.arange(w, n - w)
    a, b = i - w, i + w
    walked = dist[b] - dist[a]
    dx, dz = x[b] - x[a], z[b] - z[a]
    straight = (
        (blocked[b + 1] - blocked[a] == 0)
        & (walked >= MIN_PATH)
        & (np.hypot(dx, dz) >= STRAIGHT * walked)
    )
    use = np.zeros(n, dtype=bool)
    use[i] = straight & steering[i]
    err = np.full(n, np.nan)
    err[i] = _wrap(yaw[i] - _bearing(dx, dz))
    rows = [np.arange(r0, r1 + 1) for r0, r1 in runs(use, min_len=MIN_RUN)]
    kept = np.concatenate(rows) if rows else np.array([], dtype=np.int64)
    reasons["heading ticks in series too short"] += int(use.sum()) - len(kept)
    run = np.concatenate([np.full(len(r), k) for k, r in enumerate(rows)]) if rows else kept
    return _rows(seg, "heading", seg.tick[kept], err[kept], run)


def _look_ahead(
    seg: Seg, on_foot: np.ndarray, walking: np.ndarray, reasons: Counter
) -> list[pl.DataFrame]:
    n, h = len(seg), LOOK_HORIZON
    x, z, yaw = seg.col("x"), seg.col("z"), seg.col("yaw")
    step, blocked = _path(seg, on_foot)
    found: dict[str, tuple[list, list]] = {q: ([], []) for q in ("", "_under", "_over")}
    for i in np.flatnonzero(walking[: max(n - h, 0)]):
        if blocked[i + h + 1] - blocked[i] != 0:
            continue
        s = np.cumsum(step[i : i + h])  # distance walked to rows i+1 .. i+h
        b = _bearing(x[i + 1 : i + h + 1] - x[i], z[i + 1 : i + h + 1] - z[i])
        far = np.flatnonzero(s >= LOOK_MIN_DIST)
        if not len(far):
            continue
        s, b = s[far[0] :], b[far[0] :]
        turn = _wrap(b[-1] - b[0])
        if abs(turn) < LOOK_MIN_TURN:
            continue
        # The path's bearings relative to the camera, signed so the turn runs toward +: the
        # camera points at the path where this crosses zero going up.
        q = np.sign(turn) * _wrap(b - yaw[i])
        up = np.flatnonzero((q[1:] >= 0) & (q[:-1] < 0) & (q[1:] - q[:-1] < 90))
        if q[0] >= -LOOK_TOL:
            # Along the path's first stretch or behind it: aimed no further than where the path
            # leaves the camera's direction.
            past = np.flatnonzero(q > LOOK_TOL)
            kind, value = "_under", s[past[0]] if len(past) else s[-1]
            off = q[0] > abs(turn)
        elif len(up):
            k = int(up[0]) + 1
            kind, value = "", s[k - 1] + (s[k] - s[k - 1]) * -q[k - 1] / (q[k] - q[k - 1])
            off = False
        else:
            kind, value = "_over", s[-1]
            off = -q[-1] > abs(turn)
        if off:
            reasons["look-ahead: camera turned away from the path"] += 1
            continue
        found[kind][0].append(int(seg.tick[i]))
        found[kind][1].append(value)
    return [_rows(seg, f"look_ahead{k}", t, v) for k, (t, v) in found.items()]


# --- Fitting ----------------------------------------------------------------------------------


def fit(measures: pl.DataFrame, defaulted: list[str] | None = None) -> dict:
    """The movement model (JSON-able) from ``measures`` (measurements()). Quantities with too
    few measurements take their DEFAULTS; with ``defaulted``, their names are added (a hold as
    hold_ticks.<key>)."""
    defaulted = [] if defaulted is None else defaulted

    def pick(name: str, value):
        if value is None:
            defaulted.append(name)
            default = DEFAULTS
            for part in name.split("."):
                default = default[part]
            return default
        return value

    def values(quantity: str) -> np.ndarray:
        v = measures.filter(pl.col("quantity") == quantity)["value"].to_numpy()
        return v[np.isfinite(v)]

    play = measures.filter((pl.col("quantity") == "play") & (pl.col("value") > 0))
    starts = {m: len(values(f"sprint_start.{m}")) for m in METHODS}
    total = sum(starts.values())
    waits = values("jump_wait").astype(np.int64)
    share = values("sprint_open")
    pitch = values("walk_pitch")
    sneaking = values("mining_sneak")
    reach = values("mining_reach")
    model = {
        "schema": SCHEMA_VERSION,
        "kind": "movement",
        "created": int(time.time()),
        "fitted_on": {
            "sessions": play["session_id"].n_unique(),
            "minutes": float(play["value"].sum()) / (60 * TICKS_PER_S),
        },
        "hold_ticks": {
            name: pick(f"hold_ticks.{name}", _lognormal(values(f"hold.{name}"))) for name in KEYS
        },
        "regrip_gap_ticks": pick("regrip_gap_ticks", _lognormal(values("regrip"))),
        "sprint_method": pick(
            "sprint_method",
            {m: starts[m] / total for m in METHODS} if total >= MIN_SAMPLES else None,
        ),
        "jump_after_landing": pick(
            "jump_after_landing",
            [float(c) / len(waits) for c in np.bincount(waits, minlength=MAX_JUMP_WAIT + 1)]
            if len(waits) >= MIN_SAMPLES
            else None,
        ),
        "sprint_jump_share": pick(
            "sprint_jump_share", float(share.mean()) if len(share) >= MIN_TICKS else None
        ),
        "heading": pick("heading", _ou(measures.filter(pl.col("quantity") == "heading"))),
        "look_ahead_blocks": pick(
            "look_ahead_blocks",
            _censored_lognormal(
                values("look_ahead"), values("look_ahead_under"), values("look_ahead_over")
            ),
        ),
        "walk_pitch": pick(
            "walk_pitch", _trimmed_mean_sd(pitch) if len(pitch) >= MIN_TICKS else None
        ),
        "mining_sneak": {
            "share": pick(
                "mining_sneak.share",
                float(sneaking.mean()) if len(sneaking) >= MIN_TICKS else None,
            ),
            "bout_ticks": pick("mining_sneak.bout_ticks", _lognormal(values("mining_sneak_bout"))),
            "gap_ticks": pick("mining_sneak.gap_ticks", _lognormal(values("mining_sneak_gap"))),
        },
        "mining_steps": _mining_steps_fit(measures, len(sneaking), pick),
        "mining_reach": pick(
            "mining_reach",
            {"median": float(np.median(reach)), "q90": float(np.quantile(reach, 0.9))}
            if len(reach) >= MIN_SAMPLES
            else None,
        ),
    }
    return _rounded(model)


def _mining_steps_fit(measures: pl.DataFrame, mining_ticks: int, pick: Callable) -> dict:
    """Steps about the work while mining: how many a minute of mining in place (mining minus the
    walks), how long, and which key."""
    steps = {
        k: measures.filter(pl.col("quantity") == f"mining_step.{k}")["value"].to_numpy()
        for k in STEP_KEYS
    }
    every = np.concatenate(list(steps.values()))
    short = every[every <= MINING_STEP_MAX]
    in_place = mining_ticks - every[every > MINING_STEP_MAX].sum()
    enough = len(short) >= MIN_SAMPLES and in_place >= MIN_TICKS
    shares = {k: float((v <= MINING_STEP_MAX).sum()) / max(len(short), 1) for k, v in steps.items()}
    return {
        "per_min": pick(
            "mining_steps.per_min",
            len(short) / (in_place / (60 * TICKS_PER_S)) if enough else None,
        ),
        "ticks": pick("mining_steps.ticks", _lognormal(short)),
        "keys": pick("mining_steps.keys", shares if enough else None),
    }


def _trimmed_mean_sd(x: np.ndarray) -> dict:
    kept = _robust(x)
    return {"mean": float(kept.mean()), "sd": float(kept.std(ddof=1))}


def _ou(series: pl.DataFrame) -> dict | None:
    """Ornstein-Uhlenbeck parameters of the heading series (see the module docstring)."""
    if series.height < MIN_TICKS:
        return None
    s = series.sort("session_id", "segment_id", "run", "tick")
    e = s["value"].to_numpy()
    kept = np.isin(e, _robust(e))
    mean = float(e[kept].mean())
    sd = float(e[kept].std(ddof=1))
    x = np.where(kept, e - mean, np.nan)  # trimmed ticks pair with nothing
    run = s.select(pl.concat_str("session_id", "segment_id", "run", separator="/"))
    run = run.to_series().to_numpy()
    longest = 2 * HEADING_HALF
    acf = [1.0]
    for lag in range(1, longest + 1):
        pair = (run[:-lag] == run[lag:]) & np.isfinite(x[:-lag]) & np.isfinite(x[lag:])
        a, b = x[:-lag][pair], x[lag:][pair]
        if len(a) < MIN_SAMPLES:
            break
        acf.append(float((a * b).sum() / math.sqrt((a * a).sum() * (b * b).sum())))
    below = [k for k, r in enumerate(acf) if r < 1 / math.e]
    if below:
        k = below[0]
        tau = k - 1 + (acf[k - 1] - 1 / math.e) / (acf[k - 1] - acf[k])
    else:
        tau = float(len(acf) - 1)
    if tau <= 0:
        return None
    return {"theta_per_s": TICKS_PER_S / tau, "sigma_deg": sd, "mean_deg": mean}


def _censored_lognormal(exact: np.ndarray, under: np.ndarray, over: np.ndarray) -> dict | None:
    """Maximum-likelihood log-normal (median, sigma of the log) from exact values, values known
    only to lie under a bound, and values known only to lie over one."""
    exact, under, over = (v[np.isfinite(v) & (v > 0)] for v in (exact, under, over))
    if len(exact) < MIN_SAMPLES:
        return None
    le, lu, lo = np.log(exact), np.log(under), np.log(over)

    def nll(p: np.ndarray) -> float:
        mu, sigma = p[0], math.exp(p[1])
        ll = -0.5 * (((le - mu) / sigma) ** 2).sum() - len(le) * p[1]
        ll += log_ndtr((lu - mu) / sigma).sum() + log_ndtr((mu - lo) / sigma).sum()
        return -float(ll)

    every = np.concatenate([le, lu, lo])
    start = np.array([every.mean(), math.log(max(every.std(), 0.1))])
    best = minimize(nll, start, method="Nelder-Mead", options={"xatol": 1e-6, "fatol": 1e-9})
    return {"median": math.exp(best.x[0]), "sigma": math.exp(best.x[1])}


def _rounded(value):
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    if isinstance(value, float):
        return round(value, 4)
    return value


# --- Output -----------------------------------------------------------------------------------


def write(model: dict, out_dir: str | Path) -> Path:
    """Writes ``model`` to out_dir/movement.json (indent 1, trailing newline)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(model, indent=1) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


def export(store: Store, out_dir: str | Path) -> Path:
    """Fits the movement model on the store's human play and writes it; returns the file."""
    return write(fit(measurements(store)), out_dir)


def summary(model: dict) -> list[str]:
    """The fitted numbers in a few report lines."""
    m = model
    holds = ", ".join(f"{k} {v['median']:.1f}" for k, v in m["hold_ticks"].items())
    method = ", ".join(f"{k} {v:.0%}" for k, v in m["sprint_method"].items())
    waits = m["jump_after_landing"]
    h = m["heading"]
    return [
        f"holds        median ticks a press: {holds}",
        f"regrips      forward let go for {m['regrip_gap_ticks']['median']:.1f} ticks (sigma "
        f"{m['regrip_gap_ticks']['sigma']:.2f}) while moving",
        f"sprint       starts by {method}; {m['sprint_jump_share']:.0%} of sprinting in the "
        "open is sprint-jumping",
        f"re-jump      {waits[0]:.0%} on the first tick after landing, {sum(waits[:3]):.0%} "
        "within 2 ticks",
        f"heading      off the path by {h['mean_deg']:+.1f} +- {h['sigma_deg']:.1f} deg, "
        f"reverting at {h['theta_per_s']:.1f}/s",
        f"look-ahead   camera aims {m['look_ahead_blocks']['median']:.1f} blocks along the path "
        f"in turns (sigma {m['look_ahead_blocks']['sigma']:.2f})",
        f"walk pitch   {m['walk_pitch']['mean']:.1f} +- {m['walk_pitch']['sd']:.1f} deg "
        "(positive looks down)",
        f"mining sneak {m['mining_sneak']['share']:.0%} of mining time, stretches of "
        f"{m['mining_sneak']['bout_ticks']['median']:.0f} ticks (sigma "
        f"{m['mining_sneak']['bout_ticks']['sigma']:.2f}) with "
        f"{m['mining_sneak']['gap_ticks']['median']:.0f}-tick gaps between",
        f"mining steps {m['mining_steps']['per_min']:.0f} a minute of mining in place, "
        f"{m['mining_steps']['ticks']['median']:.0f} ticks each (sigma "
        f"{m['mining_steps']['ticks']['sigma']:.2f}): "
        + ", ".join(f"{k} {v:.0%}" for k, v in m["mining_steps"]["keys"].items()),
        f"mining reach: blocks broken {m['mining_reach']['median']:.1f} blocks from the eye "
        f"(median), nine in ten within {m['mining_reach']['q90']:.1f}",
    ]
