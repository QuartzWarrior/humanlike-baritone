"""Movement primitives (spec: Action taxonomy, movement).

Context flags (surface, light level, sprint method) ride along in the cleaned ticks rather than
in the labels: join a span to its ticks to read them.
"""

from __future__ import annotations

import numpy as np

from hbpipe.label.base import Seg, Span, detector, runs

WALK_SPEED = (
    1.0,
    5.0,
)  # m/s on the ground; vanilla walking is 4.317, a one-block step peaks near 3
JUMP_VSPEED = 3.0  # m/s upward on the takeoff tick; a jump starts at 8.4
HEAD_HIT_WINDOW = 8  # ticks after takeoff to look for an upward collision


def moving_keys(seg: Seg) -> np.ndarray:
    return seg.col("k_forward") | seg.col("k_back") | seg.col("k_left") | seg.col("k_right")


def free_movement(seg: Seg) -> np.ndarray:
    """Not swimming, climbing, gliding or riding: the states with their own labels."""
    return ~(
        seg.col("in_water")
        | seg.col("swimming")
        | seg.col("on_climbable")
        | seg.col("gliding")
        | seg.col("passenger")
    )


def takeoffs(seg: Seg) -> np.ndarray:
    """Row indices of jumps: on the ground, then off it either moving up or with the jump key
    held. Under a 2-high ceiling the head hits on the takeoff tick itself, so its recorded
    upward speed is already gone; the key is what's left."""
    g = seg.col("on_ground")
    up = seg.col("vspeed") > JUMP_VSPEED
    jump = seg.col("k_jump")
    lift = up[1:] | jump[1:] | jump[:-1]
    return np.flatnonzero(~g[1:] & g[:-1] & lift) + 1


def landing_after(seg: Seg, i: int) -> int:
    """Row index of the first on-ground tick after row i (or the last row)."""
    g = seg.col("on_ground")
    rest = np.flatnonzero(g[i + 1 :])
    return i + 1 + int(rest[0]) if rest.size else len(seg) - 1


@detector("move.walk")
def walk(seg: Seg) -> list[Span]:
    speed = seg.col("speed")
    lo, hi = WALK_SPEED
    mask = (
        moving_keys(seg)
        & ~seg.col("sprinting")
        & ~seg.col("crouching")
        & free_movement(seg)
        & (speed >= lo)
        & (speed <= hi)
    )

    def confidence(i0: int, i1: int) -> float:
        """Full walking speed for most of it: 0.9. Reaching it at all: 0.8. A short step: 0.7."""
        s = speed[i0 : i1 + 1]
        if 3.5 <= np.median(s) <= 4.8:
            return 0.9
        return 0.8 if s.max() >= 3.5 else 0.7

    return seg.mask_spans(mask, "move.walk", min_len=4, max_gap=4, confidence=confidence)


@detector("move.sprint")
def sprint(seg: Seg) -> list[Span]:
    mask = seg.col("sprinting") & seg.col("on_ground") & free_movement(seg)
    return seg.mask_spans(mask, "move.sprint", min_len=10, confidence=0.9)


def _jump_runs(seg: Seg) -> list[tuple[int, int, np.ndarray, np.ndarray]]:
    """Sprinting stretches with two or more jumps: (first row, last row, takeoff rows, whether
    each jump bonked its head)."""
    sprinting = seg.col("sprinting") & free_movement(seg)
    head_hit = seg.col("head_hit")
    jumps = takeoffs(seg)
    out = []
    for i0, i1 in runs(sprinting, min_len=10, max_gap=3):
        js = jumps[(jumps >= i0) & (jumps <= i1)]
        if js.size < 2:
            continue
        bonked = np.array([head_hit[j : j + HEAD_HIT_WINDOW].any() for j in js])
        end = landing_after(seg, int(js[-1]))
        out.append((max(int(js[0]) - 1, i0), min(end, len(seg) - 1), js, bonked))
    return out


@detector("move.sprint_jump")
def sprint_jump(seg: Seg) -> list[Span]:
    headroom = seg.col("headroom")
    out = []
    for i0, i1, js, bonked in _jump_runs(seg):
        if bonked.mean() >= 0.5:
            continue
        room = headroom[js]
        known = ~np.isnan(room)
        if known.any() and (room[known] >= 3).mean() < 0.5:
            continue  # a low ceiling but no bonks: not open-air sprint-jumping either
        out.append(seg.span(i0, i1, "move.sprint_jump", 0.85 if known.mean() >= 0.5 else 0.65))
    return out


@detector("move.head_hitter")
def head_hitter(seg: Seg) -> list[Span]:
    headroom = seg.col("headroom")
    out = []
    for i0, i1, js, bonked in _jump_runs(seg):
        if bonked.mean() < 0.5:
            continue
        low = headroom[js] == 0
        out.append(seg.span(i0, i1, "move.head_hitter", 0.9 if low.mean() >= 0.5 else 0.7))
    return out


@detector("move.strafe")
def strafe(seg: Seg) -> list[Span]:
    side = seg.col("k_left") ^ seg.col("k_right")
    mask = side & (seg.col("speed") > 0.5) & ~seg.col("screen_open")
    return seg.mask_spans(mask, "move.strafe", min_len=3, max_gap=2, confidence=0.85)


@detector("move.sneak")
def sneak(seg: Seg) -> list[Span]:
    mask = seg.col("crouching") & (seg.col("speed") > 0.2) & seg.col("on_ground")
    return seg.mask_spans(mask, "move.sneak", min_len=5, max_gap=3, confidence=0.9)


@detector("move.step_up")
def step_up(seg: Seg) -> list[Span]:
    """A rise of 0.4-1.1 blocks between two ground contacts at most 12 ticks apart while moving:
    an instant step onto a slab or stair, or a hop onto a block (but not a jump in place)."""
    g = seg.col("on_ground")
    x, y, z = seg.col("x"), seg.col("y"), seg.col("z")
    speed = seg.col("speed")
    mask = np.zeros(len(seg), dtype=bool)
    prev = -1
    for i in np.flatnonzero(g):
        if prev >= 0 and i - prev <= 12 and 0.4 <= y[i] - y[prev] <= 1.1:
            across = np.hypot(x[i] - x[prev], z[i] - z[prev])
            if speed[i] > 0.5 and (i - prev == 1 or across >= 0.3):
                mask[prev : i + 1] = True
        prev = i
    mask &= free_movement(seg)
    return seg.mask_spans(mask, "move.step_up", max_gap=10, confidence=0.85)


@detector("move.parkour")
def parkour(seg: Seg) -> list[Span]:
    """A jump that clears a gap at least two blocks deep and lands near takeoff height. Deep is
    measured from the lower of the blocks jumped from and landed on, not from the player
    mid-air (on flat ground the two blocks under a jumping player are air too), so a hop down a
    bumpy slope isn't a gap."""
    x, y, z = seg.col("x"), seg.col("y"), seg.col("z")
    depth = seg.col("ground_depth")
    water = seg.col("in_water")
    foot = np.floor(y - 0.01)  # level of the block stood on (or fallen through)
    first_solid = foot - depth  # NaN where the column was never seen
    out = []
    for j in takeoffs(seg):
        take = j - 1
        land = landing_after(seg, int(j))
        if not seg.col("on_ground")[land]:
            continue
        dist = np.hypot(x[land] - x[take], z[land] - z[take])
        drop = y[land] - y[take]
        if dist < 1.5 or not -1.3 <= drop <= 1.3 or water[take : land + 1].any():
            continue
        if (first_solid[j:land] <= min(foot[take], foot[land]) - 2).any():
            out.append(seg.span(take, land, "move.parkour", 0.75))
    return out


@detector("move.climb")
def climb(seg: Seg) -> list[Span]:
    active = (np.abs(seg.col("vspeed")) > 0.5) | moving_keys(seg) | seg.col("k_jump")
    mask = seg.col("on_climbable") & active
    return seg.mask_spans(mask, "move.climb", min_len=5, max_gap=3, confidence=0.9)


@detector("move.swim")
def swim(seg: Seg) -> list[Span]:
    mask = seg.col("in_water") | seg.col("swimming")
    return seg.mask_spans(mask, "move.swim", min_len=5, max_gap=3, confidence=0.9)


@detector("move.fall")
def fall(seg: Seg) -> list[Span]:
    """Airborne stretches whose fall distance passes 4 blocks, through the landing."""
    fd = seg.col("fall_distance")
    airborne = ~seg.col("on_ground") & ~seg.col("gliding") & ~seg.col("on_climbable")
    out = []
    for i0, i1 in runs(airborne):
        if np.nanmax(fd[i0 : i1 + 1]) > 4:
            out.append(seg.span(i0, min(i1 + 1, len(seg) - 1), "move.fall", 0.9))
    return out


@detector("move.glide")
def glide(seg: Seg) -> list[Span]:
    return seg.mask_spans(seg.col("gliding"), "move.glide", min_len=5, max_gap=2, confidence=0.95)


@detector("move.ride")
def ride(seg: Seg) -> list[Span]:
    return seg.mask_spans(seg.col("passenger"), "move.ride", min_len=5, confidence=0.95)
