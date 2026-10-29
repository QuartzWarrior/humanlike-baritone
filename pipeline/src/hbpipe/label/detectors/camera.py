"""Camera primitives (spec: Action taxonomy, camera).

Camera motion is read from the ticks' yaw and pitch, i.e. what the camera did. The raw mouse table
is not used for it: that table also logs cursor motion while a screen is open. A tick is turning
when the view moved at least 0.5 degrees (10 deg/s) with no screen open; turning ticks at most 2
apart form one motion. Whether a screen is open comes from screen_shown, not ticks.screen_open.

The crosshair target of a tick is the block position or entity id it rests on (ticks.hit_*), or
nothing on a miss.

Hostile mobs (for look.react) are the ones that attack on sight; neutral mobs such as endermen,
piglins, wolves and iron golems are left out. A mob is in view when it is within 16 blocks and
within 45 degrees of the look direction, measured from the eyes to about the middle of its body:
roughly the central part of a default 70-degree screen. Line of sight isn't recorded, so a mob
behind a wall counts as in view.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from hbpipe.label.base import Seg, Span, detector, runs
from hbpipe.label.detectors.movement import moving_keys
from hbpipe.label.entities import AGGRESSIVE

TURN_MIN = 0.5  # degrees per tick for a tick to count as turning
MOTION_GAP = 2  # still ticks allowed inside one motion
MOVING_SPEED = 0.5  # m/s; slower than this the player is stationary (crouch-walking is 1.3)
HOLD = 3  # ticks a newly acquired target must stay under the crosshair
ACQUIRE_MAX = 20  # an acquisition span is at most the last second of a motion
SETTLE = 10  # ticks past the landing an acquisition span may run while the camera settles
FOLLOW = 20  # ticks after a motion in which an attack or use counts as following it
SCAN_MIN_DEG = 10.0  # degrees of camera path for an idle scan

FAST_RATE = 60.0  # deg/s; a turn for look.react is a run of ticks at least this fast
SNAP_RATE = 200.0  # deg/s peak for a turn to be fast
SNAP_ANGLE = 30.0  # degrees of net view change for a turn to be large
REACT_WINDOW = 10  # ticks (500 ms) from a trigger to the turn's first tick
CLOSE_SOUND = 4.0  # blocks; any sound this close (but not the player's own) is a trigger
VIEW_HALF_ANGLE = 45.0
VIEW_RANGE = 16.0
VIEW_MEMORY = 20  # ticks out of view before a mob counts as entering it again
BODY_MID = 0.9  # blocks above an entity's feet to aim at
EYE, EYE_CROUCHING = 1.62, 1.27

NONDIRECTIONAL_DAMAGE = frozenset(
    """
    fall in_wall starve drown freeze out_of_world outside_border fly_into_wall generic
    generic_kill wither magic cramming dry_out stalagmite on_fire
    """.split()  # noqa: SIM905
)
ACTION_EVENTS = (
    "interaction",
    "destroy_started",
    "block_placed",
    "block_broken",
    "damage_dealt",
    "item_dropped",
)


def rows_of(seg: Seg, ticks) -> np.ndarray:
    """Row index of each tick number, clamped into the segment."""
    t = np.asarray(ticks, dtype=np.int64)
    return np.clip(np.searchsorted(seg.tick, t), 0, len(seg) - 1)


def screen_shown(seg: Seg) -> np.ndarray:
    """Whether a screen is showing on each tick. ticks.screen_open can't be trusted: the cleaned
    screen column keeps its last value after a screen closes. A screen counts as showing from
    the segment start (when ticks.screen is set there), a change of ticks.screen to another
    screen, or a screen_opened event, until a screen_closed event."""
    n = len(seg)
    screen = seg.col("screen")
    marks = [(0, screen[0] is not None)] if n else []
    marks += [
        (i, True) for i in range(1, n) if screen[i] is not None and screen[i] != screen[i - 1]
    ]
    ev = seg.events_of("screen_opened", "screen_closed")
    for r, kind in zip(rows_of(seg, ev["tick"]), ev["kind"], strict=True):
        marks.append((int(r), kind == "screen_opened"))
    out = np.zeros(n, dtype=bool)
    for r, shown in sorted(marks):
        out[r:] = shown
    return out


def turn_deg(seg: Seg) -> np.ndarray:
    """Degrees the view turned on each tick (0 while a screen is open)."""
    deg = np.nan_to_num(np.hypot(seg.col("yaw_rate"), seg.col("pitch_rate"))) / 20.0
    return np.where(screen_shown(seg), 0.0, deg)


def turning(seg: Seg) -> np.ndarray:
    return turn_deg(seg) >= TURN_MIN


def world_actions(seg: Seg) -> np.ndarray:
    """Ticks with an attack or use: the key held, a mouse button pressed outside a screen, or an
    interaction, dig, placement, break, hit or item drop event."""
    act = seg.col("k_attack") | seg.col("k_use")
    b = seg.buttons.filter(
        (pl.col("kind") == "button") & pl.col("down") & ~pl.col("in_gui").fill_null(False)
    )
    act[rows_of(seg, b["tick"])] = True
    act[rows_of(seg, seg.events_of(*ACTION_EVENTS)["tick"])] = True
    return act


def look_vectors(yaw: np.ndarray, pitch: np.ndarray) -> np.ndarray:
    """Unit look directions, shape (n, 3). Yaw 0 faces +z (south), positive pitch looks down."""
    y, p = np.radians(yaw), np.radians(pitch)
    return np.stack([-np.sin(y) * np.cos(p), -np.sin(p), np.cos(y) * np.cos(p)], axis=-1)


def angle_deg(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Angle between row vectors, in degrees."""
    na = np.linalg.norm(a, axis=-1)
    nb = np.linalg.norm(b, axis=-1)
    cos = np.sum(a * b, axis=-1) / np.maximum(na * nb, 1e-9)
    return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0)))


def eye_height(seg: Seg) -> np.ndarray:
    return np.where(seg.col("crouching"), EYE_CROUCHING, EYE)


def targets(seg: Seg) -> tuple[np.ndarray, dict[tuple, int]]:
    """Crosshair target code per tick (-1 = nothing) and the code of each target key,
    ("block", x, y, z) or ("entity", id)."""
    codes: dict[tuple, int] = {}
    out = np.full(len(seg), -1, dtype=np.int64)
    cols = seg.ticks.select("hit_type", "hit_x", "hit_y", "hit_z", "hit_entity_id")
    for i, (kind, x, y, z, eid) in enumerate(cols.iter_rows()):
        if kind == "block" and x is not None:
            key: tuple = ("block", x, y, z)
        elif kind == "entity" and eid is not None:
            key = ("entity", eid)
        else:
            continue
        out[i] = codes.setdefault(key, len(codes))
    return out, codes


def acted_on(seg: Seg, codes: dict[tuple, int]) -> list[tuple[int, int]]:
    """(row, target code) for each event aimed at a known target: interactions, digs, hits, and
    placements (by the clicked block)."""
    out = []
    ev = seg.events_of("interaction", "destroy_started", "block_placed", "damage_dealt")
    cols = ("tick", "kind", "interaction", "x", "y", "z", "clicked_x", "clicked_y", "clicked_z")
    for tick, kind, inter, x, y, z, cx, cy, cz, eid in ev.select(*cols, "entity_id").iter_rows():
        if kind == "damage_dealt" or inter in ("attack_entity", "use_entity"):
            key: tuple = ("entity", eid)
        elif kind == "block_placed":
            key = ("block", cx, cy, cz)
        else:
            key = ("block", x, y, z)
        if key in codes:
            out.append((int(rows_of(seg, [tick])[0]), codes[key]))
    return out


def entities(seg: Seg) -> pl.DataFrame:
    """One row per (tick row, nearby entity): row, id, type (no "minecraft:"), dx, dy, dz from
    the player's feet."""
    return (
        seg.ticks.select(pl.int_range(pl.len()).alias("row"), "entities")
        .explode("entities")
        .drop_nulls("entities")
        .unnest("entities")
        .select(
            "row",
            "id",
            pl.col("type").str.strip_prefix("minecraft:"),
            pl.col("dx").cast(pl.Float64),
            pl.col("dy").cast(pl.Float64),
            pl.col("dz").cast(pl.Float64),
        )
    )


def world_changes(seg: Seg, codes: dict[tuple, int]) -> set[tuple[int, int]]:
    """(row, target code) of blocks broken or placed: target changes the camera didn't make."""
    out = set()
    ev = seg.events_of("block_broken", "block_placed").select("tick", "x", "y", "z")
    for tick, x, y, z in ev.iter_rows():
        code = codes.get(("block", x, y, z))
        if code is not None:
            out.add((int(rows_of(seg, [tick])[0]), code))
    return out


def _acquisitions(seg: Seg) -> list[Span]:
    n = len(seg)
    tgt, codes = targets(seg)
    is_entity = np.array([k[0] == "entity" for k in codes], dtype=bool)
    acted: dict[int, list[int]] = {}
    for r, code in acted_on(seg, codes):
        acted.setdefault(code, []).append(r)
    changed = world_changes(seg, codes)
    out = []
    for i0, i1 in runs(turning(seg), max_gap=MOTION_GAP):
        prev_end = i0 - 1
        j = i0
        while j <= i1:
            code = tgt[j]
            rest = np.flatnonzero(tgt[j:] != code)
            k = j + int(rest[0]) - 1 if rest.size else n - 1  # last row on this target
            last = tgt[j - 1] if j > 0 else code
            by_world = any(
                (r, last) in changed or (r, code) in changed for r in range(max(j - 2, 0), j + 1)
            )
            if code >= 0 and code != last and k - j + 1 >= HOLD and not by_world:
                final = k >= i1
                hit = any(j <= r <= min(k, i1 + FOLLOW) for r in acted.get(code, ()))
                if final or hit:
                    if hit:
                        c = 0.9 if final else 0.8
                    elif is_entity[code]:
                        c = 0.7
                    else:
                        c = 0.6 if k - i1 + 1 >= 10 else 0.5
                    start = max(i0, prev_end + 1, j - ACQUIRE_MAX + 1)
                    prev_end = min(k, i1, j + SETTLE)
                    out.append(seg.span(start, prev_end, "look.acquire", c))
            j = k + 1
    return out


@detector("look.acquire")
def acquire(seg: Seg) -> list[Span]:
    """The crosshair moved by the camera onto a target other than the one before, which stays
    under it for at least 3 ticks, and either the motion ends there or the player digs, uses,
    attacks or places against it (a sweep across blocks while mining). Target changes from a
    block breaking or being placed don't count. The span runs from the motion's start (or the
    previous acquisition, at most a second back) to half a second past the landing, or the
    motion's end if sooner. Confidence 0.9 when acted on and the motion ends there, 0.8 when
    acted on mid-motion; otherwise 0.7 for an entity, 0.6 for a block held 10+ ticks after the
    motion, 0.5 for a block (anywhere in a tunnel the crosshair ends on some block)."""
    return _acquisitions(seg)


@detector("look.travel")
def travel(seg: Seg) -> list[Span]:
    """Turning while moving (0.5 m/s or more), outside acquisitions: leading turns and pitch
    drift. Gaps of up to 3 ticks bridged, at least 4 ticks long. Confidence 0.85 when movement
    keys are held for most of it, else 0.65 (pushed, falling, knocked back)."""
    n = len(seg)
    acq = np.zeros(n, dtype=bool)
    for s in _acquisitions(seg):
        acq[seg.index_of(s.start_tick) : seg.index_of(s.end_tick) + 1] = True
    base = turning(seg) & (seg.col("speed") >= MOVING_SPEED) & ~acq
    mask = np.zeros(n, dtype=bool)
    for i0, i1 in runs(base, max_gap=3):
        mask[i0 : i1 + 1] = True
    keys = moving_keys(seg)

    def confidence(i0: int, i1: int) -> float:
        return 0.85 if keys[i0 : i1 + 1].mean() >= 0.5 else 0.65

    return seg.mask_spans(mask & ~acq, "look.travel", min_len=4, confidence=confidence)


@detector("look.idle_scan")
def idle_scan(seg: Seg) -> list[Span]:
    """Turning while stationary (under 0.5 m/s, gaps of up to 4 ticks bridged) through at least
    10 degrees of camera path, with no attack or use during it or in the second after.
    Confidence 0.8 for 30+ degrees, else 0.65."""
    deg = turn_deg(seg)
    still = (seg.col("speed") < MOVING_SPEED) & ~screen_shown(seg)
    act = world_actions(seg)
    out = []
    for i0, i1 in runs((deg >= TURN_MIN) & still, min_len=3, max_gap=4):
        path = float(deg[i0 : i1 + 1].sum())
        if path < SCAN_MIN_DEG or act[i0 : i1 + FOLLOW + 1].any():
            continue
        out.append(seg.span(i0, i1, "look.idle_scan", 0.8 if path >= 30 else 0.65))
    return out


def _turns(seg: Seg) -> list[tuple[int, int]]:
    """Large fast turns: runs of ticks turning at 60+ deg/s (1-tick gaps bridged) that peak at
    200+ deg/s and change the view by 30+ degrees."""
    rate = turn_deg(seg) * 20.0
    yaw = np.cumsum(np.nan_to_num(seg.col("yaw_rate"))) / 20.0
    pitch = seg.col("pitch")
    out = []
    for i0, i1 in runs(rate >= FAST_RATE, max_gap=1):
        p = max(i0 - 1, 0)
        net = np.hypot(yaw[i1] - yaw[p], pitch[i1] - pitch[p])
        if rate[i0 : i1 + 1].max() >= SNAP_RATE and net >= SNAP_ANGLE:
            out.append((i0, i1))
    return out


def _sound_triggers(seg: Seg) -> list[tuple[int, np.ndarray | None]]:
    """Hostile or player sounds, or any other sound within 4 blocks (but not master or block
    sounds, which near the player are its own chests and doors). Sounds at the player's own
    body (its attacks, eating, level-ups) are skipped. Directions are from the eyes."""
    snd = seg.events_of("sound_heard")
    out = []
    for tick, cat, dx, dy, dz in snd.select("tick", "category", "dx", "dy", "dz").iter_rows():
        if dx is None:
            continue
        own = np.hypot(dx, dz) < 0.75 and -2.0 < dy < 0.3
        close = np.sqrt(dx * dx + dy * dy + dz * dz) <= CLOSE_SOUND
        if own or not (cat in ("hostile", "player") or (close and cat not in ("master", "block"))):
            continue
        out.append((int(rows_of(seg, [tick])[0]), np.array([dx, dy, dz], dtype=float)))
    return out


def _damage_triggers(seg: Seg, ents: pl.DataFrame) -> list[tuple[int, np.ndarray | None]]:
    """Damage from a direction (not falls, suffocation, hunger...), toward the attacker when it
    is among the nearby entities."""
    eye = eye_height(seg)
    out = []
    dmg = seg.events_of("damage_taken").select("tick", "damage_source", "entity_id")
    for tick, source, eid in dmg.iter_rows():
        if (source or "").removeprefix("minecraft:") in NONDIRECTIONAL_DAMAGE:
            continue
        r = int(rows_of(seg, [tick])[0])
        hit = ents.filter((pl.col("row") == r) & (pl.col("id") == eid)) if eid else ents.clear()
        direction = None
        if hit.height:
            dx, dy, dz = hit.row(0)[3:6]
            direction = np.array([dx, dy + BODY_MID - eye[r], dz])
        out.append((r, direction))
    return out


def _view_triggers(seg: Seg, ents: pl.DataFrame) -> list[tuple[int, np.ndarray | None]]:
    """A hostile mob coming into view after at least a second out of it (or absent)."""
    h = ents.filter(pl.col("type").is_in(list(AGGRESSIVE)))
    if h.is_empty():
        return []
    rows = h["row"].to_numpy()
    dy = h["dy"].to_numpy() + BODY_MID - eye_height(seg)[rows]
    vec = np.stack([h["dx"].to_numpy(), dy, h["dz"].to_numpy()], axis=-1)
    look = look_vectors(seg.col("yaw")[rows], seg.col("pitch")[rows])
    inview = (angle_deg(look, vec) <= VIEW_HALF_ANGLE) & (
        np.linalg.norm(vec, axis=-1) <= VIEW_RANGE
    )
    seen = (
        h.with_columns(
            pl.Series("vx", vec[:, 0]), pl.Series("vy", vec[:, 1]), pl.Series("vz", vec[:, 2])
        )
        .filter(pl.Series(inview))
        .sort("id", "row")
        .with_columns(prev=pl.col("row").shift(1).over("id"))
        .filter(
            (pl.col("row") >= 1)
            & (pl.col("prev").is_null() | (pl.col("row") - pl.col("prev") > VIEW_MEMORY))
        )
    )
    return [
        (int(r), np.array([x, y, z]))
        for r, x, y, z in seen.select("row", "vx", "vy", "vz").iter_rows()
    ]


@detector("look.react")
def react(seg: Seg) -> list[Span]:
    """A large fast turn (see _turns) starting 1-10 ticks after a trigger: damage taken, a sound
    (see _sound_triggers) or a hostile mob entering view. The span is the turn. Confidence 0.9
    when the turn brings the source closer to the crosshair (to within 20 degrees, or 40% of
    the way), 0.7 when the source's direction is unknown, 0.5 when it turns elsewhere."""
    turns = _turns(seg)
    if not turns:
        return []
    ents = entities(seg)
    triggers = _sound_triggers(seg) + _damage_triggers(seg, ents) + _view_triggers(seg, ents)
    if not triggers:
        return []
    yaw, pitch = seg.col("yaw"), seg.col("pitch")
    out = []
    for i0, i1 in turns:
        p = max(i0 - 1, 0)
        best = 0.0
        for r, direction in triggers:
            if not i0 - REACT_WINDOW <= r <= i0 - 1:
                continue
            if direction is None:
                best = max(best, 0.7)
                continue
            before, after = angle_deg(
                look_vectors(np.array([yaw[p], yaw[i1]]), np.array([pitch[p], pitch[i1]])),
                np.stack([direction, direction]),
            )
            best = max(best, 0.9 if after <= 20 or after <= 0.6 * before else 0.5)
        if best:
            out.append(seg.span(i0, i1, "look.react", best))
    return out
