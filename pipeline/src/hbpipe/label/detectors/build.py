"""Building and placing primitives (spec: Action taxonomy, building and placing).

Everything here reads block_placed events: the placed position (x, y, z), the clicked block and
face, the hand, the player's feet position on that tick (player_x/y/z) and the block that was
under the placed one (``below``). A placement's span starts at its click (``Places.start``).

Placement geometry is relative to the player's feet block: ``feet`` is floor(player_y + 0.01),
so a block placed "under the feet" sits at y == feet - 1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import polars as pl

from hbpipe.label.base import Seg, Span, detector, merge
from hbpipe.label.blocks import is_light, passable
from hbpipe.label.detectors.mining import breaks

PLACE_LEAD = 10  # ticks from a use-key press to the placement it made


@dataclass
class Places:
    """block_placed events of one segment, in tick order.

    start   the placing click: the use-key press if it came at most PLACE_LEAD ticks earlier (and
            after the previous placement), else the placement tick (a held key repeating)
    clicked whether a use_block interaction on the clicked block came with it
    """

    tick: np.ndarray
    pos: np.ndarray  # (n, 3)
    block: np.ndarray
    below: np.ndarray
    hand: np.ndarray
    player: np.ndarray  # (n, 3) feet; NaN when unknown
    start: np.ndarray
    clicked: np.ndarray

    def __len__(self) -> int:
        return len(self.tick)

    def feet(self) -> np.ndarray:
        with np.errstate(invalid="ignore"):
            return np.floor(self.player[:, 1] + 0.01)


def places(seg: Seg) -> Places:
    ev = seg.events_of("block_placed").filter(pl.col("x").is_not_null())
    tick = ev["tick"].to_numpy().astype(np.int64)
    use = seg.events_of("interaction").filter(pl.col("interaction") == "use_block")
    uses = {(int(t), x, y, z) for t, x, y, z in use.select("tick", "x", "y", "z").iter_rows()}
    k_use = seg.col("k_use")
    start = tick.copy()
    clicked = np.zeros(len(tick), dtype=bool)
    prev = -(10**18)
    for k, (t, cx, cy, cz) in enumerate(
        ev.select("tick", "clicked_x", "clicked_y", "clicked_z").iter_rows()
    ):
        clicked[k] = any((t - d, cx, cy, cz) in uses for d in range(5))
        i = seg.index_of(t)
        j = i
        while j > 0 and k_use[j - 1] and i - j < PLACE_LEAD:
            j -= 1
        if k_use[i] and (j == 0 or not k_use[j - 1]) and seg.tick[j] > prev:
            start[k] = seg.tick[j]
        prev = t
    return Places(
        tick=tick,
        pos=ev.select("x", "y", "z").to_numpy().astype(np.int64).reshape(-1, 3),
        block=ev["block"].fill_null("").to_numpy(),
        below=ev["below"].to_numpy(),
        hand=ev["hand"].to_numpy(),
        player=ev.select("player_x", "player_y", "player_z")
        .cast(pl.Float64)
        .fill_null(np.nan)
        .to_numpy()
        .reshape(-1, 3),
        start=start,
        clicked=clicked,
    )


def _span(seg: Seg, pl_: Places, idx, label: str, confidence: float) -> Span:
    idx = np.asarray(idx)
    return seg.tick_span(int(pl_.start[idx].min()), int(pl_.tick[idx].max()), label, confidence)


@detector("build.place")
def place(seg: Seg) -> list[Span]:
    """One span per placement, from its click to the block_placed event. Confidence drops when
    neither the use key nor a use_block interaction backs it."""
    p = places(seg)
    k_use = seg.col("k_use")
    out = []
    for k in range(len(p)):
        backed = p.clicked[k] or k_use[seg.index_of(int(p.tick[k]))]
        out.append(_span(seg, p, [k], "build.place", 0.95 if backed else 0.85))
    return out


PILLAR_GAP = 40  # ticks between placements of one pillar
PILLAR_JUMP = 12  # ticks before a placement to look for the jump
PILLAR_EDGE = 15  # ticks to search for the takeoff before and the landing after


def _pillar_candidates(seg: Seg, p: Places) -> np.ndarray:
    """Placements into the player's own column, just under their feet, during a jump."""
    jump = seg.col("k_jump") | (seg.col("vspeed") > 3.0)
    ground = seg.col("on_ground")
    out = np.zeros(len(p), dtype=bool)
    for k in range(len(p)):
        bx, by, bz = p.pos[k]
        px, py, pz = p.player[k]
        if np.isnan(py) or abs(bx + 0.5 - px) >= 0.8 or abs(bz + 0.5 - pz) >= 0.8:
            continue
        if not -0.05 <= py - (by + 1) <= 1.3:
            continue
        i = seg.index_of(int(p.tick[k]))
        if jump[max(0, i - PILLAR_JUMP) : i + 1].any() and (not ground[i] or jump[i]):
            out[k] = True
    return out


def _pillars(seg: Seg, p: Places) -> list[list[int]]:
    """Chains of pillar placements: same column, one block higher each time."""
    cand = _pillar_candidates(seg, p)
    chains: list[list[int]] = []
    for k in np.flatnonzero(cand):
        last = chains[-1][-1] if chains else None
        if (
            last is not None
            and last == k - 1
            and p.tick[k] - p.tick[last] <= PILLAR_GAP
            and (p.pos[k] - p.pos[last] == (0, 1, 0)).all()
        ):
            chains[-1].append(int(k))
        else:
            chains.append([int(k)])
    return chains


@detector("build.pillar")
def pillar(seg: Seg) -> list[Span]:
    """Jump-placing straight up: placements into the player's own column, under their feet
    while airborne after a jump (jump key or a takeoff within PILLAR_JUMP ticks), chained while
    each lands one block above the last within PILLAR_GAP ticks. The span runs from the
    takeoff before the first placement to the landing after the last."""
    p = places(seg)
    ground = seg.col("on_ground")
    spans: dict[tuple[int, int], Span] = {}
    for chain in _pillars(seg, p):
        i0 = seg.index_of(int(p.start[chain[0]]))
        i1 = seg.index_of(int(p.tick[chain[-1]]))
        lo = i0
        while lo > 0 and i0 - lo < PILLAR_EDGE and not ground[lo]:
            lo -= 1
        hi = i1
        while hi < len(seg) - 1 and hi - i1 < PILLAR_EDGE and not ground[hi]:
            hi += 1
        # Chains that don't link (one block placed twice in a jump) can share the jump: one span
        span = seg.span(lo, hi, "build.pillar", 0.9 if len(chain) >= 2 else 0.7)
        if (lo, hi) not in spans or spans[(lo, hi)].confidence < span.confidence:
            spans[(lo, hi)] = span
    return list(spans.values())


BRIDGE_GAP = 60  # ticks between placements of one bridge
BRIDGE_KEYS = 10  # ticks either side of a placement to look for back or side keys
BRIDGE_WALK = 20  # ticks after the last placement for the player to step onto the bridge


def _bridge_candidates(seg: Seg, p: Places) -> np.ndarray:
    """Placements at the level under the feet, in or beside the feet's column, over a passable
    block, while back, left or right is held."""
    keys = seg.col("k_back") | seg.col("k_left") | seg.col("k_right")
    feet = p.feet()
    out = np.zeros(len(p), dtype=bool)
    for k in range(len(p)):
        bx, by, bz = p.pos[k]
        px, _, pz = p.player[k]
        below = p.below[k]
        if np.isnan(feet[k]) or below is None or not passable(below) or by != feet[k] - 1:
            continue
        if abs(bx - math.floor(px)) > 1 or abs(bz - math.floor(pz)) > 1:
            continue
        i = seg.index_of(int(p.tick[k]))
        out[k] = keys[max(0, i - BRIDGE_KEYS) : i + BRIDGE_KEYS + 1].any()
    return out


def _bridges(seg: Seg, p: Places) -> list[list[int]]:
    """Runs of bridge candidates that the player walks onto: each block beside the previous
    one at the same height, and the player later standing on one of them (not the last)."""
    cand = _bridge_candidates(seg, p)
    runs: list[list[int]] = []
    for k in np.flatnonzero(cand):
        last = runs[-1][-1] if runs else None
        d = p.pos[k] - p.pos[last] if last is not None else None
        if (
            last is not None
            and k - last <= 2
            and p.tick[k] - p.tick[last] <= BRIDGE_GAP
            and d[1] == 0
            and 0 < max(abs(d[0]), abs(d[2])) <= 1
        ):
            runs[-1].append(int(k))
        else:
            runs.append([int(k)])
    x, y, z = seg.col("x"), seg.col("y"), seg.col("z")
    out = []
    for r in runs:
        if len(r) < 2:
            continue
        i0 = seg.index_of(int(p.tick[r[0]]))
        i1 = seg.index_of(int(p.tick[r[-1]]) + BRIDGE_WALK)
        with np.errstate(invalid="ignore"):
            stood = {
                (int(a), int(b) - 1, int(c))
                for a, b, c in zip(
                    np.floor(x[i0 : i1 + 1]),
                    np.floor(y[i0 : i1 + 1] + 0.01),
                    np.floor(z[i0 : i1 + 1]),
                    strict=True,
                )
            }
        if any(tuple(int(v) for v in p.pos[k]) in stood for k in r[:-1]):
            out.append(r)
    return out


@detector("build.bridge")
def bridge(seg: Seg) -> list[Span]:
    """Two or more placements under or beside the feet over a passable block (``below``) while
    holding back or a side key, each beside the last at the same height (at most BRIDGE_GAP
    ticks apart), that the player then stands on. Crouching through most of it raises the
    confidence (sneak-bridging)."""
    p = places(seg)
    crouch = seg.col("crouching")
    out = []
    for r in _bridges(seg, p):
        sneaking = np.mean([crouch[seg.index_of(int(p.tick[k]))] for k in r]) >= 0.5
        out.append(_span(seg, p, r, "build.bridge", 0.9 if sneaking else 0.8))
    return out


FILL_GAP = 100  # ticks between placements of one fill
FILL_STEP = 4  # blocks (Chebyshev) between consecutive placements of one fill
FILL_MIN = 4  # placements in one plane
FILL_DENSITY = 0.4  # placed share of the plane's bounding rectangle


@detector("build.fill")
def fill(seg: Seg) -> list[Span]:
    """Placements filling a plane: groups of placements at most FILL_GAP ticks and FILL_STEP
    blocks from the previous one, split into planes (one coordinate shared). A plane needs
    FILL_MIN placements, an extent of 2+ both ways, and FILL_DENSITY of its rectangle placed.
    Pillar and bridge placements are left out; a volume shows up as its layers."""
    p = places(seg)
    skip = {k for c in _pillars(seg, p) for k in c} | {k for r in _bridges(seg, p) for k in r}
    idx = np.array([k for k in range(len(p)) if k not in skip], dtype=np.int64)
    if not idx.size:
        return []
    gap = np.diff(p.tick[idx]) > FILL_GAP
    step = np.abs(np.diff(p.pos[idx], axis=0)).max(axis=1, initial=0) > FILL_STEP
    out = []
    for group in np.split(idx, np.flatnonzero(gap | step) + 1):
        left = list(group)
        while len(left) >= FILL_MIN:
            found = False
            pos = p.pos[left]
            planes = [(a, int(v)) for a in range(3) for v in np.unique(pos[:, a])]
            planes.sort(key=lambda av: -int((pos[:, av[0]] == av[1]).sum()))
            for a, v in planes:
                on = [k for k, q in zip(left, pos, strict=True) if q[a] == v]
                if len(on) < FILL_MIN:
                    break
                uniq = np.unique(p.pos[on], axis=0)
                b, c = [ax for ax in range(3) if ax != a]
                e1, e2 = np.ptp(uniq[:, b]) + 1, np.ptp(uniq[:, c]) + 1
                density = len(uniq) / (e1 * e2)
                if e1 >= 2 and e2 >= 2 and density >= FILL_DENSITY:
                    conf = min(0.95, 0.6 + 0.3 * min(density, 1.0) + 0.05 * (len(on) >= 8))
                    out.append(_span(seg, p, on, "build.fill", conf))
                    left = [k for k in left if k not in on]
                    found = True
                    break
            if not found:
                break
    return merge(out)


LIGHT_LEAD = 60  # ticks before a light placement to look for switching to it
LIGHT_TAIL = 40  # ticks after it to look for switching away


@detector("build.light")
def light(seg: Seg) -> list[Span]:
    """Torch, lantern and other light placements (blocks.is_light). The span covers the
    placing routine: from selecting the light in the main hand (if within LIGHT_LEAD ticks
    before) through the placement to selecting something else (if within LIGHT_TAIL ticks
    after); an off-hand light spans just the click. The spacing between lights is left to the
    activity layer (it needs the previous light, often in another segment)."""
    p = places(seg)
    held = seg.col("held_item")
    out = []
    for k in range(len(p)):
        if not is_light(p.block[k]):
            continue
        t0, t1 = int(p.start[k]), int(p.tick[k])
        i = seg.index_of(t1)
        item = held[i]
        if p.hand[k] != "off" and item is not None and is_light(item):
            j = i
            while j > 0 and held[j - 1] == item and i - j < LIGHT_LEAD:
                j -= 1
            if j > 0 and held[j - 1] != item:
                t0 = min(t0, int(seg.tick[j]))
            e = i
            while e < len(seg) - 1 and held[e + 1] == item and e - i < LIGHT_TAIL:
                e += 1
            if e < len(seg) - 1 and held[e + 1] != item:
                t1 = int(seg.tick[e])
        out.append(seg.tick_span(t0, t1, "build.light", 0.95))
    return merge(out)


UNDO_WINDOW = 100  # ticks from placing a block to breaking it


@detector("build.undo")
def undo(seg: Seg) -> list[Span]:
    """Breaking a block within UNDO_WINDOW ticks of placing it at the same position (with no
    other break there in between), from the placing click to the break. Interleaved undos
    (place two, break both) merge into one span."""
    p = places(seg)
    br = breaks(seg)
    placed: dict[tuple[int, int, int], int] = {}
    events = sorted(
        [(int(t), 1, k) for k, t in enumerate(p.tick)]
        + [(int(t), 0, k) for k, t in enumerate(br.tick)]
    )
    out = []
    for t, is_place, k in events:
        if is_place:
            placed[tuple(int(v) for v in p.pos[k])] = k
            continue
        key = tuple(int(v) for v in br.pos[k])
        j = placed.pop(key, None)
        if j is None or t - p.tick[j] > UNDO_WINDOW:
            continue
        same = br.block[k] == p.block[j]
        out.append(seg.tick_span(int(p.start[j]), t, "build.undo", 0.9 if same else 0.75))
    return merge(out)
