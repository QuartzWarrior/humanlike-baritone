"""Meta behaviors (spec: Action taxonomy, meta): hesitations, fidgets, mistakes and activity
switches. meta.hesitate and meta.switch run in stage 2 and read the act.* spans.

Input, for hesitations: mouse motion of 2+ device counts in a tick (a single count is hand
tremor), key presses and releases (auto-repeat rows are not new input), mouse buttons and
scrolling, GUI cursor motion and slot clicks, and holding a movement, jump, attack or use key.
Holding sneak or sprint alone is not input: players park on them.

A world action, for fidgets and mistakes, is an attack or use (camera.world_actions). Mouse
button 0 is attack and 1 is use (the default bindings). Screens come from camera.screen_shown.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from hbpipe.label.base import Seg, Span, detector, runs
from hbpipe.label.detectors.camera import (
    MOVING_SPEED,
    TURN_MIN,
    rows_of,
    screen_shown,
    world_actions,
)
from hbpipe.label.detectors.movement import moving_keys

HELD_INPUT = ("k_forward", "k_back", "k_left", "k_right", "k_jump", "k_attack", "k_use")
HESITATE = (6, 60)  # ticks without input: 300 ms to 3 s
MOUSE_FLOOR = 2  # mouse counts in a tick below which the motion is hand tremor, not input
CORRECTED = 60  # ticks (3 s) within which a mistake is undone
SWITCH_SIDE = 20  # ticks of each activity a switch span covers
SWITCH_GAP = 200  # ticks of no activity allowed between the two
MIN_PIECE = 20  # ticks an activity must lead for its boundary to count

INTERACTIVE_SUFFIXES = (
    "door",
    "trapdoor",
    "fence_gate",
    "button",
    "lever",
    "bed",
    "chest",
    "barrel",
    "shulker_box",
    "furnace",
    "smoker",
    "table",
    "anvil",
    "hopper",
    "dispenser",
    "dropper",
    "repeater",
    "comparator",
    "note_block",
    "jukebox",
    "cake",
    "composter",
    "lectern",
    "loom",
    "stonecutter",
    "grindstone",
    "brewing_stand",
    "beacon",
    "bell",
    "respawn_anchor",
    "cauldron",
    "campfire",
    "flower_pot",
    "sign",
    "crafter",
    "bookshelf",
    "pot",
)


def _merge(seg: Seg, parts: list[tuple[int, int, float]], label: str) -> list[Span]:
    """Spans from (first row, last row, confidence), overlapping ones merged (highest wins)."""
    out: list[list] = []
    for i0, i1, c in sorted(parts):
        if out and i0 <= out[-1][1]:
            out[-1][1] = max(out[-1][1], i1)
            out[-1][2] = max(out[-1][2], c)
        else:
            out.append([i0, i1, c])
    return [seg.span(i0, i1, label, c) for i0, i1, c in out]


def _clusters(rows: np.ndarray, gap: int, min_count: int) -> list[np.ndarray]:
    """Groups of sorted rows whose neighbours are at most ``gap`` apart, at least
    ``min_count`` long."""
    if rows.size == 0:
        return []
    groups = np.split(rows, np.flatnonzero(np.diff(rows) > gap) + 1)
    return [g for g in groups if g.size >= min_count]


def _covered(seg: Seg, spans: list[Span]) -> np.ndarray:
    mask = np.zeros(len(seg), dtype=bool)
    for s in spans:
        mask[seg.index_of(s.start_tick) : seg.index_of(s.end_tick) + 1] = True
    return mask


def key_changes(seg: Seg) -> np.ndarray:
    """Ticks of key presses and releases; a down row for a key already down is auto-repeat."""
    down: dict[tuple, bool] = {}
    out = []
    for tick, key, scancode, is_down in seg.keys.select(
        "tick", "key", "scancode", "down"
    ).iter_rows():
        if down.get((key, scancode)) != is_down:
            out.append(tick)
        down[(key, scancode)] = is_down
    return np.array(out, dtype=np.int64)


def input_ticks(seg: Seg) -> np.ndarray:
    """Ticks with any input (see the module docstring)."""
    active = np.zeros(len(seg), dtype=bool)
    for c in HELD_INPUT:
        active |= seg.col(c)
    moved = (
        seg.mouse.group_by("tick")
        .agg((pl.col("dx").abs() + pl.col("dy").abs()).sum().alias("counts"))
        .filter(pl.col("counts") >= MOUSE_FLOOR)
    )
    for ticks in (moved["tick"], key_changes(seg), seg.buttons["tick"], seg.gui["tick"]):
        if len(ticks):
            active[rows_of(seg, ticks)] = True
    return active


@detector("meta.hesitate", stage=2)
def hesitate(seg: Seg) -> list[Span]:
    """A run of 6-60 ticks (300 ms to 3 s) with no input, with input on both sides. A segment is
    active play by construction (the recorder pauses after 10 s idle), so every such gap is a
    mid-task stop. Confidence 0.8 for 8-40 ticks, 0.65 nearer the limits (a beat in a rhythm,
    or a look away), each 0.1 lower when the gap isn't inside a recognized activity."""
    during = _covered(seg, seg.labeled("act."))
    n = len(seg)
    lo, hi = HESITATE
    out = []
    for i0, i1 in runs(~input_ticks(seg), min_len=lo):
        length = i1 - i0 + 1
        if i0 == 0 or i1 == n - 1 or length > hi:
            continue
        conf = 0.8 if 8 <= length <= 40 else 0.65
        if not during[i0 : i1 + 1].all():
            conf -= 0.1
        out.append(seg.span(i0, i1, "meta.hesitate", conf))
    return out


def _crouch_spam(seg: Seg, act: np.ndarray) -> list[tuple[int, int, float]]:
    """3+ crouches each starting at most 15 ticks after the last, with no attack or use."""
    c = seg.col("crouching")
    rises = np.flatnonzero(c[1:] & ~c[:-1]) + 1
    falls = np.flatnonzero(~c[1:] & c[:-1]) + 1
    out = []
    for g in _clusters(rises, 15, 3):
        after = falls[falls > g[-1]]
        i0, i1 = int(g[0]), int(after[0]) if after.size else len(seg) - 1
        if not act[i0 : i1 + 1].any():
            out.append((i0, i1, 0.85 if g.size >= 4 else 0.75))
    return out


def slot_changes(seg: Seg) -> np.ndarray:
    """Rows where the selected hotbar slot changed, outside screens."""
    slot = seg.col("slot")
    return np.flatnonzero((slot[1:] != slot[:-1]) & ~screen_shown(seg)[1:]) + 1


def _scroll_fidget(seg: Seg, act: np.ndarray) -> list[tuple[int, int, float]]:
    """4+ slot changes at most 10 ticks apart with no attack or use among them, that end on the
    slot they started from (0.75) or with nothing done in the second after (0.6)."""
    slot = seg.col("slot")
    out = []
    for g in _clusters(slot_changes(seg), 10, 4):
        a, b = int(g[0]), int(g[-1])
        if act[a : b + 1].any():
            continue
        if slot[a - 1] == slot[b]:
            out.append((a, b, 0.75))
        elif not act[b : b + 21].any():
            out.append((a, b, 0.6))
    return out


def _swings(axis: np.ndarray) -> list[list]:
    """Same-direction turning on one axis: [first row, last row, signed degrees] over ticks of
    0.5+ degrees, with gaps of up to 4 ticks."""
    out: list[list] = []
    for k in np.flatnonzero(np.abs(axis) >= TURN_MIN):
        if out and np.sign(out[-1][2]) == np.sign(axis[k]) and k - out[-1][1] <= 4:
            out[-1][1] = int(k)
            out[-1][2] += axis[k]
        else:
            out.append([int(k), int(k), float(axis[k])])
    return out


def _wiggles(seg: Seg, act: np.ndarray) -> list[tuple[int, int, float]]:
    """Back-and-forth camera: 4+ alternating swings on one axis, each of 2-30 degrees over at
    most 8 ticks and starting at most 4 ticks after the last ended, netting at most 35% of the
    path, with no attack or use during it or in the half second after. Bigger swings are
    glances (look.idle_scan), not wiggles."""
    shown = screen_shown(seg)
    dyaw = np.where(shown, 0.0, np.nan_to_num(seg.col("yaw_rate")) / 20.0)
    dpitch = np.where(shown, 0.0, np.nan_to_num(seg.col("pitch_rate")) / 20.0)
    path = np.hypot(dyaw, dpitch)
    out = []
    for axis in (dyaw, dpitch):
        chain: list[list] = []
        for sw in [*_swings(axis), None]:
            ok = sw is not None and 2 <= abs(sw[2]) <= 30 and sw[1] - sw[0] < 8
            if (
                ok
                and chain
                and np.sign(sw[2]) != np.sign(chain[-1][2])
                and sw[0] - chain[-1][1] <= 4
            ):
                chain.append(sw)
                continue
            if len(chain) >= 4:
                i0, i1 = chain[0][0], chain[-1][1]
                total = path[i0 : i1 + 1].sum()
                net = np.hypot(dyaw[i0 : i1 + 1].sum(), dpitch[i0 : i1 + 1].sum())
                if net <= 0.35 * total and not act[i0 : i1 + 11].any():
                    out.append((i0, i1, 0.6))
            chain = [sw] if ok else []
    return out


@detector("meta.fidget")
def fidget(seg: Seg) -> list[Span]:
    """Repeated input with no world effect: crouch spam, aimless hotbar scrolling and camera
    wiggles (see the helpers for thresholds). Overlapping finds merge."""
    act = world_actions(seg)
    parts = _crouch_spam(seg, act) + _scroll_fidget(seg, act) + _wiggles(seg, act)
    return _merge(seg, parts, "meta.fidget")


def _slot_step(a: float, b: float) -> int:
    """Signed hotbar distance from a to b, the short way round (a scroll notch is +-1)."""
    d = int(b - a) % 9
    return d if d <= 4 else d - 9


def _wrong_slot(seg: Seg, act: np.ndarray) -> list[tuple[int, int, float]]:
    """A slot picked, then left within 3 s without being used, and an attack or use with
    another slot within 2 s of leaving it. Scrolling on past a slot isn't a pick: a slot
    entered and left by the scroll wheel in the same direction. Scrolling back (an overshoot)
    is 0.75; a number-key pick left within half a second 0.75, later 0.6 (a change of mind).
    The span runs from the pick to leaving it."""
    slot = seg.col("slot")
    ch = slot_changes(seg)
    scrolled = np.zeros(len(seg), dtype=bool)
    wheel = seg.buttons.filter(pl.col("kind") == "scroll")["tick"]
    if len(wheel):
        rows = rows_of(seg, wheel)
        scrolled[rows] = True
        scrolled[np.minimum(rows + 1, len(seg) - 1)] = True
    out = []
    for a, b in zip(ch[:-1], ch[1:], strict=True):
        if b - a > CORRECTED or act[a:b].any():
            continue
        into, away = _slot_step(slot[a - 1], slot[a]), _slot_step(slot[a], slot[b])
        by_wheel = scrolled[a] and scrolled[b]
        if by_wheel and np.sign(into) == np.sign(away):
            continue
        used = np.flatnonzero(act[b : b + 41])
        if used.size and slot[b + used[0]] != slot[a]:
            out.append((int(a), int(b), 0.75 if by_wheel or b - a <= 10 else 0.6))
    return out


def _hand_changed(seg: Seg, r0: int, r1: int) -> bool:
    for hand in ("main_hand", "off_hand"):
        s = seg.ticks[hand].slice(r0, r1 - r0 + 1)
        if s.struct.field("id").n_unique() > 1 or s.struct.field("count").n_unique() > 1:
            return True
    return False


def _bursts(seg: Seg, button: int) -> list[tuple[int, int]]:
    """(first press row, last release row) of each burst of presses of one mouse button outside
    screens: presses that start at most 3 ticks after the last one ended merge, since a held
    button can show up as a press every 4 ticks (the game's use repeat)."""
    rows = seg.buttons.filter(
        (pl.col("kind") == "button")
        & (pl.col("button") == button)
        & ~pl.col("in_gui").fill_null(False)
    ).select("tick", "down")
    out: list[list[int]] = []
    for tick, down in rows.iter_rows():
        r = int(rows_of(seg, [tick])[0])
        if down:
            if out and r - out[-1][1] <= 3:
                out[-1][1] = len(seg) - 1
            else:
                out.append([r, len(seg) - 1])
        elif out:
            out[-1][1] = r
    return [(a, b) for a, b in out]


def _missed_clicks(seg: Seg) -> list[tuple[int, int, float]]:
    """A burst of attack presses (see _bursts) starting with nothing under the crosshair (on
    the press tick and the next) and no hit while held or within 2 ticks after (0.7). A burst of
    use presses with no effect while held or within 2 ticks after, and at least 5 ticks (no
    placement, screen, entity use, item use in progress, or change in either hand), that was
    aimed at nothing, or at a non-interactive block while holding a block item placed elsewhere
    in the segment (0.6). Holding use through a pillar jump is not a miss: the block goes down
    while it is held. The span is the burst."""
    n = len(seg)
    shown = screen_shown(seg)
    hit = seg.col("hit_type")
    hit_block = seg.col("hit_block")
    held = seg.col("held_item")
    using = seg.col("using_item")
    ev = seg.events
    inter = ev.filter(pl.col("kind") == "interaction")
    hits = rows_of(
        seg,
        pl.concat(
            [
                inter.filter(pl.col("interaction").is_in(["attack_block", "attack_entity"]))[
                    "tick"
                ],
                ev.filter(pl.col("kind") == "damage_dealt")["tick"],
            ]
        ),
    )
    effects = rows_of(
        seg,
        pl.concat(
            [
                ev.filter(pl.col("kind").is_in(["block_placed", "screen_opened"]))["tick"],
                inter.filter(pl.col("interaction") == "use_entity")["tick"],
            ]
        ),
    )
    placeable = set(ev.filter(pl.col("kind") == "block_placed")["block"].drop_nulls())
    out = []
    for r, up in _bursts(seg, 0):
        aimless = hit[r] == "miss" and hit[min(r + 1, n - 1)] == "miss"
        if not shown[r] and aimless and not ((hits >= r) & (hits <= up + 2)).any():
            out.append((r, up, 0.7))
    for r, up in _bursts(seg, 1):
        r1 = min(max(r + 5, up + 2), n - 1)
        if (
            shown[r]
            or ((effects >= r) & (effects <= r1)).any()
            or using[r : r1 + 1].any()
            or _hand_changed(seg, max(r - 1, 0), r1)
        ):
            continue
        aimless = hit[r] == "miss" and hit[min(r + 1, n - 1)] == "miss"
        failed_place = (
            hit[r] == "block"
            and held[r] in placeable
            and not (hit_block[r] or "").endswith(INTERACTIVE_SUFFIXES)
        )
        if aimless or failed_place:
            out.append((r, max(up, r), 0.6))
    return out


def _overshoots(seg: Seg, act: np.ndarray) -> list[tuple[int, int, float]]:
    """A move of 1+ blocks, then within 2 s a move back the opposite way (within ~37 degrees)
    of 0.2-2 blocks and at most 75% as far, ending within 3 s of the first; both driven by
    movement keys, with no attack or use in between. Moves are runs of 3+ ticks at 0.5+ m/s.
    The span is the last half second of the first move through the move back."""
    x, z = seg.col("x"), seg.col("z")
    keys = moving_keys(seg)
    legs = runs(seg.col("speed") >= MOVING_SPEED, min_len=3)
    out = []
    for (a0, a1), (b0, b1) in zip(legs[:-1], legs[1:], strict=True):
        if b0 - a1 > 40 or b1 - a1 > CORRECTED or act[a1 : b0 + 1].any():
            continue
        d1 = np.array([x[a1] - x[max(a0 - 1, 0)], z[a1] - z[max(a0 - 1, 0)]])
        d2 = np.array([x[b1] - x[b0 - 1], z[b1] - z[b0 - 1]])
        n1, n2 = np.linalg.norm(d1), np.linalg.norm(d2)
        if n1 < 1.0 or not 0.2 <= n2 <= min(2.0, 0.75 * n1):
            continue
        if d1 @ d2 / (n1 * n2) > -0.8:
            continue
        if keys[a0 : a1 + 1].mean() < 0.5 or keys[b0 : b1 + 1].mean() < 0.5:
            continue
        out.append((max(a0, a1 - 10), b1, 0.6))
    return out


def _undone_placements(seg: Seg) -> list[tuple[int, int, float]]:
    """A block broken at a position where the player placed one at most 3 s before."""
    placed = seg.events_of("block_placed").select("tick", "x", "y", "z")
    broken = seg.events_of("block_broken").select("tick", "x", "y", "z")
    if placed.is_empty() or broken.is_empty():
        return []
    pairs = (
        placed.join(broken, on=["x", "y", "z"], suffix="_broken")
        .filter(
            (pl.col("tick_broken") > pl.col("tick"))
            & (pl.col("tick_broken") - pl.col("tick") <= CORRECTED)
        )
        .group_by("tick", "x", "y", "z")
        .agg(pl.col("tick_broken").min())
    )
    return [
        (int(rows_of(seg, [p])[0]), int(rows_of(seg, [b])[0]), 0.8)
        for p, b in pairs.select("tick", "tick_broken").iter_rows()
    ]


@detector("meta.mistake")
def mistake(seg: Seg) -> list[Span]:
    """An action undone or corrected within 3 s: a wrong hotbar slot, a missed click, an
    overshoot walked back, or a placed block broken again (see the helpers). Overlapping finds
    merge."""
    act = world_actions(seg)
    parts = (
        _wrong_slot(seg, act)
        + _missed_clicks(seg)
        + _overshoots(seg, act)
        + _undone_placements(seg)
    )
    return _merge(seg, parts, "meta.mistake")


@detector("meta.switch", stage=2)
def switch(seg: Seg) -> list[Span]:
    """The boundary between two different activities. Where activity spans overlap, the most
    confident one leads; a lead shorter than 20 ticks is ignored. The span covers the last
    second of the old activity, any unlabeled gap (at most 10 s) and the first second of the
    new one. Confidence is the lower of the two activities' confidences, times 0.85 when there
    is a gap."""
    acts = sorted(seg.labeled("act."), key=lambda s: s.confidence)
    if len(acts) < 2:
        return []
    names = {name: k for k, name in enumerate(sorted({s.label for s in acts}))}
    lead = np.full(len(seg), -1)
    conf = np.zeros(len(seg))
    for s in acts:
        i0, i1 = seg.index_of(s.start_tick), seg.index_of(s.end_tick)
        lead[i0 : i1 + 1] = names[s.label]
        conf[i0 : i1 + 1] = s.confidence
    pieces = []
    for code in names.values():
        pieces += [(i0, i1, code) for i0, i1 in runs(lead == code, min_len=MIN_PIECE)]
    pieces.sort()
    out = []
    for (a0, a1, ca), (b0, b1, cb) in zip(pieces[:-1], pieces[1:], strict=True):
        gap = b0 - a1 - 1
        if ca == cb or gap > SWITCH_GAP:
            continue
        c = min(conf[a1], conf[b0]) * (1.0 if gap <= 0 else 0.85)
        lo, hi = max(a0, a1 - SWITCH_SIDE + 1), min(b1, b0 + SWITCH_SIDE - 1)
        out.append(seg.span(lo, hi, "meta.switch", c))
    return out
