"""Activities (spec: Action taxonomy, activities; labeling pass 3).

Goal-level labels from windows sliding over the segment: WINDOW_TICKS long (60 s), STEP_TICKS
apart (15 s). A segment no longer than one window gets a single window covering all of it.

1. ``window_features`` turns a window into a flat dict of numeric evidence: break and placement
   geometry computed from the events themselves, movement, camera, combat, screens, farm work,
   and the share of each primitive family in ``seg.labels``. The rules read these, and the
   gradient-boosted classifier that later replaces the rules trains on exactly these keys
   (FEATURE_KEYS), so add keys rather than renaming them.
2. Each rule scores its activity in [0, 1]; a score of at least FIRE claims the window. Primary
   activities (PRIMARY) exclude each other: when two fire, the top one must lead by MARGIN or
   the window stays unlabeled, because precision matters more than recall. Overlays (fight,
   manage_inventory, smelt_craft) may sit on top of any primary.
3. A window labels only its core, the STEP_TICKS around its center (the first and last cores
   reach the segment's ends), so cores tile the segment. Consecutive cores with the same label
   merge, and the result is cut down to that label's own evidence (ticks near a break, a
   placement, a screen, a hit...), with short evidence gaps closed. Window context decides the
   label; evidence decides where it lies.

Break shape works on columns: the distinct (x, z) of broken blocks. A leg is a straight run of
columns along x or z, at least LEG_MIN long; legs on adjacent lines with similar extents merge
into a corridor. A corridor up to 3 wide is narrow (a tunnel); 4 or wider is an area being cleared.
break_fill_xyz measures height between the 5th and 95th percentile of break y, so a few breaks
on the way down a shaft don't stretch the box.

act.area_clear comes three ways, because rooms get cleared edges-first: a window of breaks that
fills a bounded volume; a ring, or three sides of one, of narrow corridors around an interior
4x4 or larger (the loops dug before hollowing); or hollowing out an existing space, seen in the
events' open_sides (neighbours already passable before each break: boring into rock opens one
or two, clearing a room's inside three or more). Open-side stats leave out blocks the player
placed this segment, blocks under the player's feet (digging down, often through an old
pillar), logs, leaves and passable plants. Breaks under the feet are not mining evidence for
any mining activity either.

Travel vs explore. Travel is directed: 20+ m of path whose net displacement is 70%+ of it (or
40%+ when every 10 s piece is 85%+ straight, a winding road), moving 60%+ of the time, at most 3
stops a minute. Explore wanders: 30+ m of path that goes nowhere (straightness <= 0.5, 10 s
pieces <= 0.8), spread out (radius of gyration >= 5 m, 8+ distinct 4x4 cells) while looking
around (look.idle_scan, the camera off the heading, or turning while standing). Straightness
between 0.5 and 0.7 is neither, so the window stays unlabeled. Both need a window free of other
work: few breaks, placements, screens or farm actions.

The cleaned ``screen`` column forward-fills nulls, so it never shows a screen closing. Screen
time here comes from screen_opened / screen_closed events instead (plus the first tick's screen
for one already open when the segment starts).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from hbpipe.label import blocks
from hbpipe.label.base import Seg, Span, detector, runs
from hbpipe.label.entities import HOSTILE as _MOBS
from hbpipe.label.entities import LIVESTOCK

WINDOW_TICKS = 1200  # 60 s
STEP_TICKS = 300  # 15 s
FIRE = 0.6
MARGIN = 0.15
LEG_MIN = 4  # columns in a straight run before it counts as a leg
NARROW = 3  # widest corridor that is still a tunnel
MOVING = 1.0  # m/s; slower is standing, sneaking in place or pushing a wall
HOSTILE_RANGE = 8.0  # blocks

PRIMARY = (
    "act.strip_mine",
    "act.tunnel",
    "act.area_clear",
    "act.tree_farm",
    "act.build",
    "act.farm",
    "act.travel",
    "act.explore",
)
OVERLAY = ("act.fight", "act.manage_inventory", "act.smelt_craft")

FAMILIES = ("move.", "look.", "mine.", "build.", "combat.", "inv.", "use.")
PRIMITIVES = (
    "look.idle_scan",
    "mine.strip",
    "mine.tunnel",
    "mine.area_clear",
    "mine.tree",
    "mine.clear_path",
    "build.fill",
    "build.pillar",
    "build.bridge",
    "use.farm",
    "use.trade",
    "inv.craft",
    "inv.container",
    "inv.organize",
    "combat.melee",
)
MENU_KINDS = ("inventory", "container", "smelt", "craft", "merchant", "other")

HOSTILE = _MOBS | {"player"}  # fight targets, players included
FARM_ANIMALS = frozenset(
    """
    cow mooshroom sheep pig chicken rabbit goat horse donkey mule llama trader_llama camel
    turtle bee frog armadillo sniffer
    """.split()  # noqa: SIM905
)
FEED = frozenset(
    """
    wheat carrot potato beetroot wheat_seeds beetroot_seeds melon_seeds pumpkin_seeds
    torchflower_seeds pitcher_pod golden_carrot golden_apple hay_block dandelion seagrass
    slime_ball cactus spider_eye shears bucket
    """.split()  # noqa: SIM905
)
PLANTING = frozenset(
    """
    wheat_seeds beetroot_seeds melon_seeds pumpkin_seeds torchflower_seeds pitcher_pod carrot
    potato nether_wart sweet_berries cocoa_beans bone_meal
    """.split()  # noqa: SIM905
)
TILLABLE = frozenset(("dirt", "grass_block", "dirt_path", "coarse_dirt", "rooted_dirt"))
STONE = frozenset(
    """
    stone deepslate tuff andesite diorite granite calcite dripstone_block netherrack blackstone
    basalt smooth_basalt end_stone
    """.split()  # noqa: SIM905
)
SOIL = frozenset(
    """
    dirt grass_block coarse_dirt rooted_dirt podzol mycelium mud sand red_sand gravel clay
    soul_sand soul_soil snow_block
    """.split()  # noqa: SIM905
)
CONTAINERS = frozenset(
    """
    chest trapped_chest barrel ender_chest hopper dispenser dropper shulker_box crafter
    """.split()  # noqa: SIM905
)
RANGED = frozenset(("bow", "crossbow", "trident"))

_BREAK_KEYS = """
    breaks break_rate break_cols bbox_long bbox_short break_height break_y_std break_floor_std
    floor_share col_height col_height_std break_lin break_fill_xz break_fill_xyz break_advance
    legs corridor_main corridor_width corridor_main_share corridor_cover wide_cover teeth
    branches turns perim_share break_reach break_down_share break_up_share rebroken_share refills
    log_breaks log_share leaves_share logs_per_col log_height ore_share stone_share soil_share
    crop_share
"""
_PLACE_KEYS = """
    places place_rate build_places build_rate build_cols build_extent build_height
    place_scaffold place_light place_removed place_crop
"""
_MOTION_KEYS = """
    path_h disp_h straight straight_10s speed_mean frac_moving frac_sprint frac_crouch frac_swim
    stops rg_h cells_4 y_range dy_net yaw_speed pitch_speed look_off_heading frac_look_still
    hotbar_changes
"""
_COMBAT_KEYS = """
    attacks attacks_hostile dmg_dealt_n dmg_dealt_hostile dmg_dealt_livestock dmg_dealt
    dmg_taken_n dmg_taken_mob
    dmg_taken shots hostile_frac deaths
"""
_ITEM_KEYS = """
    clicks_throw results_craft results_smelt results_trade results_other pickups drops
    uses_container uses_villager farm_breaks farm_uses farm_ops
"""
_LATER_KEYS = """
    open_n open_mean open3_share open1_share ring_sides ring_interior ring_interior_dug
"""
FEATURE_KEYS: tuple[str, ...] = (
    "dur_s",
    *_BREAK_KEYS.split(),
    *_PLACE_KEYS.split(),
    *_MOTION_KEYS.split(),
    *_COMBAT_KEYS.split(),
    "screen_s",
    *(f"screen_s_{k}" for k in MENU_KINDS),
    "clicks",
    *(f"clicks_{k}" for k in MENU_KINDS),
    *_ITEM_KEYS.split(),
    *(f"p_{f[:-1]}" for f in FAMILIES),
    *(f"p_{p.replace('.', '_')}" for p in PRIMITIVES),
    *_LATER_KEYS.split(),  # added later: always append, never reorder
)


# ---------------------------------------------------------------- per-segment context


@dataclass
class Episode:
    """One screen being open: rows i0..i1 (inclusive), its menu kind, and what happened."""

    i0: int
    i1: int
    kind: str
    clicks: int = 0
    results: Counter = field(default_factory=Counter)


def menu_kind(menu: str | None, screen: str | None = None) -> str:
    """inventory | container | smelt | craft | merchant | other, from a menu type id, else from
    the screen's class name."""
    m = (menu or "").removeprefix("minecraft:")
    if m:
        if m == "inventory":
            return "inventory"
        if m.startswith("generic_") or m in ("shulker_box", "hopper"):
            return "container"
        if m in ("furnace", "blast_furnace", "smoker"):
            return "smelt"
        if m in ("crafting", "crafter_3x3"):
            return "craft"
        if m == "merchant":
            return "merchant"
        return "other"
    s = (screen or "").rsplit(".", 1)[-1]
    if s in ("InventoryScreen", "CreativeModeInventoryScreen"):
        return "inventory"
    if s in ("ContainerScreen", "ShulkerBoxScreen", "HopperScreen", "DispenserScreen"):
        return "container"
    if s in ("FurnaceScreen", "BlastFurnaceScreen", "SmokerScreen"):
        return "smelt"
    if s in ("CraftingScreen", "CrafterScreen"):
        return "craft"
    if "Merchant" in s:
        return "merchant"
    return "other"


def _result_kind(kind: str | None) -> str:
    return kind if kind in ("craft", "smelt", "trade") else "other"


class _Events:
    """One event kind's columns as numpy arrays, sliceable by tick range."""

    def __init__(self, df: pl.DataFrame, columns: tuple[str, ...]):
        self.tick = df["tick"].to_numpy().astype(np.int64)
        self.cols = {c: df[c].to_numpy() for c in columns}
        self.n = df.height

    def window(self, t0: int, t1: int) -> dict[str, np.ndarray]:
        lo = int(np.searchsorted(self.tick, t0, "left"))
        hi = int(np.searchsorted(self.tick, t1, "right"))
        return {c: v[lo:hi] for c, v in self.cols.items()}


@dataclass
class _Context:
    seg: Seg
    episodes: list[Episode]
    screen_kind: np.ndarray  # per row: menu kind or None
    in_screen: np.ndarray
    hostile_near: np.ndarray
    breaks: pl.DataFrame
    places: pl.DataFrame
    brk: _Events
    plc: _Events
    inter: _Events
    dealt: _Events
    taken: _Events
    results: _Events
    clicks: _Events
    misc: _Events
    coverage: dict[str, np.ndarray]  # prefix or label -> cumulative covered rows


def _screen_episodes(seg: Seg) -> list[Episode]:
    n = len(seg)
    ev = seg.events_of("screen_opened", "screen_closed")
    first = seg.ticks["screen"][0] if n else None
    cur = (0, menu_kind(None, first)) if first is not None else None
    out: list[Episode] = []
    gui = seg.gui.filter(pl.col("kind") == "slot")
    gui_tick = gui["tick"].to_numpy().astype(np.int64)
    gui_menu = gui["menu"].to_list()

    def close(i_end: int) -> None:
        i0, kind = cur
        i1 = max(i0, i_end)
        if kind == "other":
            # A screen class we don't know (often a mod's): its slot clicks may name the menu.
            lo = np.searchsorted(gui_tick, seg.tick[i0], "left")
            hi = np.searchsorted(gui_tick, seg.tick[i1], "right")
            menus = [m for m in gui_menu[lo:hi] if m]
            if menus:
                kind = menu_kind(Counter(menus).most_common(1)[0][0])
        out.append(Episode(i0, i1, kind))

    for kind, tick, screen, menu in ev.select("kind", "tick", "screen", "menu").iter_rows():
        i = seg.index_of(int(tick))
        if kind == "screen_opened":
            if cur is not None:
                close(i - 1)
            cur = (i, menu_kind(menu, screen))
        elif cur is not None:
            close(i - 1)
            cur = None
    if cur is not None:
        close(n - 1)
    return out


def _context(seg: Seg) -> _Context:
    n = len(seg)
    ev = seg.events
    episodes = _screen_episodes(seg)
    screen_kind = np.full(n, None, dtype=object)
    for e in episodes:
        screen_kind[e.i0 : e.i1 + 1] = e.kind

    gui = seg.gui.filter(pl.col("kind") == "slot")
    kinds = [
        menu_kind(m) if m else (screen_kind[seg.index_of(int(t))] or "other")
        for m, t in gui.select("menu", "tick").iter_rows()
    ]
    gui = gui.with_columns(menu_kind=pl.Series(kinds, dtype=pl.String))

    results = ev.filter(pl.col("kind") == "result_taken")
    for tick, rk in results.select("tick", "result_kind").iter_rows():
        i = seg.index_of(int(tick))
        for e in episodes:
            if e.i0 <= i <= e.i1 + 1:
                e.results[_result_kind(rk)] += 1
    for tick in gui["tick"]:
        i = seg.index_of(int(tick))
        for e in episodes:
            if e.i0 <= i <= e.i1:
                e.clicks += 1
    for e in episodes:
        # Crafting in the inventory's 2x2 grid makes that screen crafting, not sorting.
        if e.results["craft"] and e.kind in ("inventory", "container"):
            e.kind = "craft"
        elif e.results["smelt"]:
            e.kind = "smelt"
        elif e.results["trade"]:
            e.kind = "merchant"
        screen_kind[e.i0 : e.i1 + 1] = e.kind

    breaks = ev.filter(pl.col("kind") == "block_broken", pl.col("x").is_not_null())
    if "open_sides" not in breaks.columns:
        breaks = breaks.with_columns(open_sides=pl.lit(None, pl.Int8))
    places = ev.filter(pl.col("kind") == "block_placed", pl.col("x").is_not_null())
    breaks, places = _annotate_blocks(breaks, places)

    inter = ev.filter(pl.col("kind") == "interaction")
    dealt = ev.filter(pl.col("kind") == "damage_dealt")
    taken = ev.filter(pl.col("kind") == "damage_taken")
    misc = ev.filter(pl.col("kind").is_in(["item_picked_up", "item_dropped", "death"]))
    return _Context(
        seg=seg,
        episodes=episodes,
        screen_kind=screen_kind,
        in_screen=np.array([k is not None for k in screen_kind], dtype=bool),
        hostile_near=_hostile_near(seg),
        breaks=breaks,
        places=places,
        brk=_Events(
            breaks,
            (
                "x",
                "y",
                "z",
                "block",
                "player_x",
                "player_y",
                "player_z",
                "refill",
                "rebroken",
                "underfoot",
                "open_sides",
            ),
        ),
        plc=_Events(
            places,
            ("x", "y", "z", "block", "player_x", "player_y", "player_z", "removed", "scaffold"),
        ),
        inter=_Events(inter, ("interaction", "block", "item_id", "entity_type")),
        dealt=_Events(dealt, ("entity_type", "amount")),
        taken=_Events(taken, ("entity_type", "amount", "damage_source")),
        results=_Events(results, ("result_kind",)),
        clicks=_Events(gui, ("menu_kind", "click_type")),
        misc=_Events(misc, ("kind",)),
        coverage=_coverage(seg),
    )


def _annotate_blocks(breaks: pl.DataFrame, places: pl.DataFrame) -> tuple[pl.DataFrame, ...]:
    """Breaks of a block placed earlier in the segment (rebroken) or under the player's feet
    (underfoot: digging down); placements broken again later (removed) or standing in for a floor
    under the player's feet (scaffold: pillars, bridges)."""
    placed_at: dict[tuple, list[int]] = {}
    for x, y, z, t in places.select("x", "y", "z", "tick").iter_rows():
        placed_at.setdefault((x, y, z), []).append(t)
    broken_at: dict[tuple, list[int]] = {}
    for x, y, z, t in breaks.select("x", "y", "z", "tick").iter_rows():
        broken_at.setdefault((x, y, z), []).append(t)
    rebroken = [
        any(p < t for p in placed_at.get((x, y, z), ()))
        for x, y, z, t in breaks.select("x", "y", "z", "tick").iter_rows()
    ]
    underfoot = [
        px is not None
        and py is not None
        and pz is not None
        and abs(x + 0.5 - px) < 0.9
        and abs(z + 0.5 - pz) < 0.9
        and y + 0.5 < py  # below the feet, or the block they are already sinking into
        for x, y, z, px, py, pz in breaks.select(
            "x", "y", "z", "player_x", "player_y", "player_z"
        ).iter_rows()
    ]
    removed, scaffold = [], []
    for x, y, z, t, px, py, pz in places.select(
        "x", "y", "z", "tick", "player_x", "player_y", "player_z"
    ).iter_rows():
        removed.append(any(b > t for b in broken_at.get((x, y, z), ())))
        if px is None or py is None or pz is None:
            scaffold.append(False)
            continue
        dy = y - math.floor(py + 1e-3)
        dh = max(abs(x + 0.5 - px), abs(z + 0.5 - pz))
        scaffold.append(dy <= -1 and dh < 1.3)
    return (
        breaks.with_columns(
            rebroken=pl.Series(rebroken, dtype=pl.Boolean),
            underfoot=pl.Series(underfoot, dtype=pl.Boolean),
        ),
        places.with_columns(
            removed=pl.Series(removed, dtype=pl.Boolean),
            scaffold=pl.Series(scaffold, dtype=pl.Boolean),
        ),
    )


def _hostile_near(seg: Seg) -> np.ndarray:
    n = len(seg)
    out = np.zeros(n, dtype=bool)
    if not n:
        return out
    ents = (
        seg.ticks.select("entities")
        .with_row_index("row")
        .explode("entities")
        .drop_nulls("entities")
        .unnest("entities")
    )
    if ents.is_empty():
        return out
    ents = ents.filter(
        pl.col("type").str.strip_prefix("minecraft:").is_in(list(HOSTILE)),
        (pl.col("dx") ** 2 + pl.col("dy") ** 2 + pl.col("dz") ** 2) <= HOSTILE_RANGE**2,
    )
    out[ents["row"].to_numpy()] = True
    return out


def _coverage(seg: Seg) -> dict[str, np.ndarray]:
    n = len(seg)
    out = {}
    for key in (*FAMILIES, *PRIMITIVES):
        mask = np.zeros(n, dtype=bool)
        for s in seg.labeled(key):
            mask[seg.index_of(s.start_tick) : seg.index_of(s.end_tick) + 1] = True
        out[key] = np.concatenate(([0], np.cumsum(mask)))
    return out


# ---------------------------------------------------------------- break geometry


def _legs(cols: np.ndarray, axis: int) -> list[tuple[int, int, int]]:
    """Straight runs of columns along one axis (0 = x, 1 = z): (fixed coordinate, lo, hi) of
    each run at least LEG_MIN long. A one-column gap doesn't split a run."""
    out = []
    other = 1 - axis
    for f in np.unique(cols[:, other]):
        a = np.sort(cols[cols[:, other] == f, axis])
        for run in np.split(a, np.flatnonzero(np.diff(a) > 2) + 1):
            if run[-1] - run[0] + 1 >= LEG_MIN:
                out.append((int(f), int(run[0]), int(run[-1])))
    return out


@dataclass
class _Corridor:
    """Legs on adjacent lines along one axis (0 = x, 1 = z), with similar extents."""

    axis: int
    f_lo: int
    f_hi: int
    lo: int
    hi: int
    legs: list[tuple[int, int, int]]

    @property
    def width(self) -> int:
        return self.f_hi - self.f_lo + 1

    @property
    def length(self) -> int:
        return self.hi - self.lo + 1

    def covers(self, cols: np.ndarray) -> np.ndarray:
        """Which columns lie on this corridor's legs."""
        out = np.zeros(len(cols), dtype=bool)
        for f, lo, hi in self.legs:
            a = cols[:, self.axis]
            out |= (cols[:, 1 - self.axis] == f) & (a >= lo) & (a <= hi)
        return out


def _corridors(legs: list[tuple[int, int, int]], axis: int) -> list[_Corridor]:
    """A leg joins a corridor on the neighbouring line when they share at least half the longer
    extent, so a long tunnel beside a small cleared patch stays a tunnel."""
    out: list[_Corridor] = []
    for f, lo, hi in sorted(legs):
        for c in out:
            shared = min(hi, c.hi) - max(lo, c.lo) + 1
            if c.f_hi >= f - 1 and shared >= 0.5 * max(hi - lo + 1, c.length):
                c.f_hi, c.lo, c.hi = max(c.f_hi, f), min(c.lo, lo), max(c.hi, hi)
                c.legs.append((f, lo, hi))
                break
        else:
            out.append(_Corridor(axis, f, f, lo, hi, [(f, lo, hi)]))
    return out


def _teeth(corridors: list[_Corridor]) -> int:
    """Longest chain of parallel narrow corridors spaced 3-8 apart (2-7 blocks of wall between)
    whose shared length is at least max(5, twice the spacing): the branches of a strip mine, not
    the far sides of a room."""
    cs = sorted((c for c in corridors if c.width <= NARROW), key=lambda c: c.f_lo)
    best = min(len(cs), 1)
    chain = [1] * len(cs)
    for j in range(len(cs)):
        for i in range(j):
            gap = cs[j].f_lo - cs[i].f_hi - 1
            shared = min(cs[i].hi, cs[j].hi) - max(cs[i].lo, cs[j].lo) + 1
            if 2 <= gap <= 7 and shared >= max(5, 2 * (gap + 1)):
                chain[j] = max(chain[j], chain[i] + 1)
        best = max(best, chain[j])
    return best


def _ring(cols: np.ndarray, legs: dict[int, list[tuple[int, int, int]]]) -> tuple[int, int, float]:
    """The best rectangle the breaks outline: (sides, interior, interior_dug). Two parallel legs
    5+ long and 5+ apart, sharing 4+ of their length, are two sides. Broken columns within 1 of
    either end of their joint extent, covering 70% of the span between them, close a third or
    fourth (a gap or two in a side is fine). A 1-wide leg standing alone between the two (a strip
    mine's tooth) rules the pair out; wide passes there are the inside being cleared. interior is
    the smaller inner side (4+ for a ring), interior_dug the share of inner columns broken."""
    best = (0, 0, 0.0)
    dug = {(int(a), int(b)) for a, b in cols}
    for a in (0, 1):
        par = [leg for leg in legs[a] if leg[2] - leg[1] + 1 >= 5]

        def col(f: int, along: int, a=a) -> tuple[int, int]:
            return (along, f) if a == 0 else (f, along)

        def alone(g: int, lo: int, hi: int, par=par) -> bool:
            return not any(abs(g2 - g) == 1 and lo2 <= hi and hi2 >= lo for g2, lo2, hi2 in par)

        for f1, lo1, hi1 in par:
            for f2, lo2, hi2 in par:
                if f2 - f1 < 5 or min(hi1, hi2) - max(lo1, lo2) + 1 < 4:
                    continue
                lo, hi = min(lo1, lo2), max(hi1, hi2)
                if hi - lo + 1 < 6:
                    continue
                if any(
                    f1 < g < f2 and l2 <= hi and h2 >= lo and alone(g, l2, h2) for g, l2, h2 in par
                ):
                    continue
                span = range(f1 + 1, f2)
                ends = sum(
                    sum(any(col(u, e + d) in dug for d in (-1, 0, 1)) for u in span)
                    >= 0.7 * len(span)
                    for e in (lo, hi)
                )
                inside = [col(u, v) for u in span for v in range(lo + 1, hi)]
                dug_inside = sum(c in dug for c in inside) / len(inside)
                best = max(best, (2 + ends, min(f2 - f1 - 1, hi - lo - 1), dug_inside))
    return best


def _shape(f: dict[str, float], x, y, z, order: np.ndarray) -> None:
    """Break geometry features into ``f``; x, y, z in event order."""
    pos = np.stack([x, y, z], axis=1).astype(np.int64)
    upos = np.unique(pos, axis=0)
    cols, inv = np.unique(pos[:, [0, 2]], axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    nc = len(cols)
    f["break_cols"] = nc
    span_x = int(np.ptp(cols[:, 0])) + 1
    span_z = int(np.ptp(cols[:, 1])) + 1
    f["bbox_long"], f["bbox_short"] = max(span_x, span_z), min(span_x, span_z)
    f["break_height"] = int(np.ptp(pos[:, 1])) + 1
    f["break_y_std"] = float(np.std(pos[:, 1]))
    floors = np.array([pos[inv == k, 1].min() for k in range(nc)])
    heights = np.array([np.ptp(pos[inv == k, 1]) + 1 for k in range(nc)])
    f["break_floor_std"] = float(np.std(floors))
    f["floor_share"] = float(np.mean(np.abs(floors - np.median(floors)) <= 1))
    f["col_height"] = float(np.median(heights))
    f["col_height_std"] = float(np.std(heights))
    f["break_fill_xz"] = nc / (span_x * span_z)
    lo, hi = np.percentile(pos[:, 1], [5, 95])
    inner = upos[(upos[:, 1] >= np.floor(lo)) & (upos[:, 1] <= np.ceil(hi))]
    f["break_fill_xyz"] = len(inner) / (span_x * span_z * (np.ceil(hi) - np.floor(lo) + 1))
    if nc >= 2:
        c = cols - cols.mean(axis=0)
        ev, vec = np.linalg.eigh(c.T @ c / nc)
        f["break_lin"] = float(ev[1] / ev.sum()) if ev.sum() > 0 else 1.0
        u = (pos[:, [0, 2]] - cols.mean(axis=0)) @ vec[:, 1]
        if np.std(u) > 0 and len(u) >= 3:
            f["break_advance"] = float(abs(np.corrcoef(order, u)[0, 1]))
    legs = {a: _legs(cols, a) for a in (0, 1)}
    f["legs"] = len(legs[0]) + len(legs[1])
    cors = {a: _corridors(legs[a], a) for a in (0, 1)}
    narrow = [c for a in (0, 1) for c in cors[a] if c.width <= NARROW]
    in_narrow = np.zeros(nc, dtype=bool)
    in_wide = np.zeros(nc, dtype=bool)
    for a in (0, 1):
        for c in cors[a]:
            if c.width <= NARROW:
                in_narrow |= c.covers(cols)
            else:
                in_wide |= c.covers(cols)
    f["corridor_cover"] = float(in_narrow.mean())
    f["wide_cover"] = float(in_wide.mean())
    f["teeth"] = max(_teeth(cors[0]), _teeth(cors[1]))
    f["ring_sides"], f["ring_interior"], f["ring_interior_dug"] = _ring(cols, legs)
    if narrow:
        main = max(narrow, key=lambda c: (c.length, c.width))
        f["corridor_main"] = main.length
        f["corridor_width"] = main.width
        f["corridor_main_share"] = float(main.covers(cols).mean())
        branches = turns = 0
        for c in cors[1 - main.axis]:
            touches = c.lo <= main.f_hi + 1 and c.hi >= main.f_lo - 1
            beyond = max(c.hi - main.f_hi, main.f_lo - c.lo) >= 2
            if c.width > NARROW or not (touches and beyond):
                continue
            if main.lo + 1 < c.f_lo and c.f_hi < main.hi - 1:
                branches += 1
            else:
                turns += 1
        f["branches"], f["turns"] = branches, turns
    if f["bbox_short"] >= 5:
        edge = (
            (cols[:, 0] == cols[:, 0].min())
            | (cols[:, 0] == cols[:, 0].max())
            | (cols[:, 1] == cols[:, 1].min())
            | (cols[:, 1] == cols[:, 1].max())
        )
        f["perim_share"] = float(edge.mean())


# ---------------------------------------------------------------- features


def window_features(seg: Seg, i0: int, i1: int) -> dict[str, float]:
    """Evidence for rows i0..i1 (inclusive) of ``seg``; keys are FEATURE_KEYS, values floats."""
    return _features(_context(seg), i0, i1)


def _share(mask: np.ndarray) -> float:
    return float(mask.mean()) if mask.size else 0.0


def _isin(values: np.ndarray, allowed) -> np.ndarray:
    return np.array([v in allowed for v in values], dtype=bool)


def _flags(values, test) -> np.ndarray:
    """test(block id) for each id, as a bool array (nulls become "")."""
    return np.array([test(v or "") for v in values], dtype=bool)


def _crop_break(block: str) -> bool:
    return blocks.is_crop(block) and block != "farmland"


def _log_or_leaves(block: str) -> bool:
    return blocks.is_log(block) or blocks.is_leaves(block)


def _features(ctx: _Context, i0: int, i1: int) -> dict[str, float]:
    seg = ctx.seg
    f: dict[str, float] = dict.fromkeys(FEATURE_KEYS, 0.0)
    f["dur_s"] = (i1 - i0 + 1) / 20.0
    t0, t1 = int(seg.tick[i0]), int(seg.tick[i1])
    rows = slice(i0, i1 + 1)
    it = ctx.inter.window(t0, t1)
    _break_features(f, ctx.brk.window(t0, t1))
    _place_features(f, ctx.plc.window(t0, t1))
    _motion_features(f, ctx, rows)
    _combat_features(f, ctx, it, t0, t1, rows)
    _screen_features(f, ctx, it, i0, i1)
    f["farm_uses"] = int(_farm_uses(it).sum())
    f["farm_ops"] = f["farm_breaks"] + f["farm_uses"] + f["place_crop"] + f["dmg_dealt_livestock"]
    n = i1 - i0 + 1
    for key in (*FAMILIES, *PRIMITIVES):
        cum = ctx.coverage[key]
        name = f"p_{key[:-1]}" if key.endswith(".") else f"p_{key.replace('.', '_')}"
        f[name] = float(cum[i1 + 1] - cum[i0]) / n
    return {k: float(v) for k, v in f.items()}


def _break_features(f: dict[str, float], b: dict[str, np.ndarray]) -> None:
    nb = len(b["x"])
    f["breaks"] = nb
    f["break_rate"] = nb / f["dur_s"]
    if not nb:
        return
    block = b["block"]
    logs = _flags(block, blocks.is_log)
    f["log_breaks"] = int(logs.sum())
    f["log_share"] = _share(logs)
    f["leaves_share"] = _share(_flags(block, blocks.is_leaves))
    f["ore_share"] = _share(_flags(block, blocks.is_ore))
    f["stone_share"] = _share(_isin(block, STONE))
    f["soil_share"] = _share(_isin(block, SOIL))
    crops = _flags(block, _crop_break)
    f["crop_share"] = _share(crops)
    f["farm_breaks"] = int(crops.sum())
    f["rebroken_share"] = _share(b["rebroken"].astype(bool))
    f["refills"] = int(sum(r is not None for r in b["refill"]))
    x, y, z = (b[c].astype(np.int64) for c in ("x", "y", "z"))
    if logs.any():
        lc, li = np.unique(np.stack([x[logs], z[logs]], 1), axis=0, return_inverse=True)
        li = li.reshape(-1)
        f["logs_per_col"] = logs.sum() / len(lc)
        f["log_height"] = max(np.ptp(y[logs][li == k]) + 1 for k in range(len(lc)))
    _shape(f, x, y, z, np.arange(nb, dtype=float))
    o = b["open_sides"].astype(float)
    natural = ~(
        b["rebroken"].astype(bool)
        | b["underfoot"].astype(bool)
        | _flags(block, _log_or_leaves)
        | _flags(block, blocks.passable)
        | np.isnan(o)
    )
    if natural.any():
        o = o[natural]
        f["open_n"] = int(natural.sum())
        f["open_mean"] = float(o.mean())
        f["open3_share"] = _share(o >= 3)
        f["open1_share"] = _share(o <= 1)
    px, py, pz = (b[c].astype(float) for c in ("player_x", "player_y", "player_z"))
    ok = ~np.isnan(px)
    if ok.any():
        x, y, z, px, py, pz = x[ok], y[ok], z[ok], px[ok], py[ok], pz[ok]
        eye = py + 1.5
        reach = np.sqrt((x + 0.5 - px) ** 2 + (y + 0.5 - eye) ** 2 + (z + 0.5 - pz) ** 2)
        f["break_reach"] = float(np.median(reach))
        feet = np.floor(py + 1e-3)
        same_col = (np.abs(x + 0.5 - px) < 0.9) & (np.abs(z + 0.5 - pz) < 0.9)
        f["break_down_share"] = _share(same_col & (y < feet))
        f["break_up_share"] = _share(y >= feet + 2)


def _place_features(f: dict[str, float], p: dict[str, np.ndarray]) -> None:
    npl = len(p["x"])
    f["places"] = npl
    f["place_rate"] = npl / f["dur_s"]
    if not npl:
        return
    light = _flags(p["block"], blocks.is_light)
    crop = _flags(p["block"], blocks.is_crop)
    scaffold = p["scaffold"].astype(bool)
    removed = p["removed"].astype(bool)
    f["place_light"] = int(light.sum())
    f["place_crop"] = int(crop.sum())
    f["place_scaffold"] = int(scaffold.sum())
    f["place_removed"] = int(removed.sum())
    keep = ~(light | crop | scaffold | removed)
    f["build_places"] = int(keep.sum())
    f["build_rate"] = keep.sum() / f["dur_s"]
    if keep.any():
        bx, by, bz = (p[c][keep].astype(np.int64) for c in ("x", "y", "z"))
        f["build_cols"] = len(set(zip(bx, bz, strict=True)))
        f["build_extent"] = float(math.dist((bx.min(), bz.min()), (bx.max(), bz.max())) + 1)
        f["build_height"] = int(np.ptp(by)) + 1


def _motion_features(f: dict[str, float], ctx: _Context, rows: slice) -> None:
    seg = ctx.seg
    x, y, z = seg.col("x")[rows], seg.col("y")[rows], seg.col("z")[rows]
    speed = seg.col("speed")[rows]
    path = float(np.nansum(np.hypot(np.diff(x), np.diff(z))))
    f["path_h"] = path
    f["disp_h"] = float(np.hypot(x[-1] - x[0], z[-1] - z[0]))
    f["straight"] = f["disp_h"] / path if path >= 1 else 0.0
    f["straight_10s"] = _local_straightness(x, z, 200)
    f["speed_mean"] = path / f["dur_s"]
    moving = speed > MOVING
    f["frac_moving"] = _share(moving)
    f["frac_sprint"] = _share(seg.col("sprinting")[rows])
    f["frac_crouch"] = _share(seg.col("crouching")[rows])
    f["frac_swim"] = _share(seg.col("in_water")[rows] | seg.col("swimming")[rows])
    f["stops"] = len(runs(speed < 0.5, min_len=20))
    f["rg_h"] = float(np.sqrt(np.nanmean((x - np.nanmean(x)) ** 2 + (z - np.nanmean(z)) ** 2)))
    f["cells_4"] = len(set(zip(np.floor(x / 4), np.floor(z / 4), strict=True)))
    f["y_range"] = float(np.nanmax(y) - np.nanmin(y))
    f["dy_net"] = float(y[-1] - y[0])
    yaw_rate, pitch_rate = np.abs(seg.col("yaw_rate")[rows]), np.abs(seg.col("pitch_rate")[rows])
    f["yaw_speed"] = float(np.nanmean(yaw_rate))
    f["pitch_speed"] = float(np.nanmean(pitch_rate))
    if moving.any():
        vx, vz = seg.col("vx")[rows][moving], seg.col("vz")[rows][moving]
        heading = np.degrees(np.arctan2(-vx, vz))  # Minecraft yaw: 0 = +z, 90 = -x
        off = np.abs((seg.col("yaw")[rows][moving] - heading + 180.0) % 360.0 - 180.0)
        f["look_off_heading"] = _share(off > 60)
    still = (speed < 0.5) & (yaw_rate + pitch_rate > 30) & ~ctx.in_screen[rows]
    f["frac_look_still"] = _share(still)
    f["hotbar_changes"] = int(np.count_nonzero(np.diff(seg.col("slot")[rows])))


def _combat_features(
    f: dict[str, float], ctx: _Context, it: dict[str, np.ndarray], t0: int, t1: int, rows: slice
) -> None:
    kind = it["interaction"]
    atk = kind == "attack_entity"
    f["attacks"] = int(atk.sum())
    f["attacks_hostile"] = int((atk & _isin(it["entity_type"], HOSTILE)).sum())
    f["shots"] = int(((kind == "use_item") & _isin(it["item_id"], RANGED)).sum())
    d = ctx.dealt.window(t0, t1)
    f["dmg_dealt_n"] = len(d["amount"])
    f["dmg_dealt_hostile"] = int(_isin(d["entity_type"], HOSTILE).sum())
    f["dmg_dealt_livestock"] = int(_isin(d["entity_type"], LIVESTOCK).sum())
    f["dmg_dealt"] = float(np.nansum(d["amount"].astype(float)))
    d = ctx.taken.window(t0, t1)
    f["dmg_taken_n"] = len(d["amount"])
    f["dmg_taken_mob"] = int(_isin(d["entity_type"], HOSTILE).sum())
    f["dmg_taken"] = float(np.nansum(d["amount"].astype(float)))
    f["hostile_frac"] = _share(ctx.hostile_near[rows])
    m = ctx.misc.window(t0, t1)["kind"]
    f["deaths"] = int((m == "death").sum())
    f["pickups"] = int((m == "item_picked_up").sum())
    f["drops"] = int((m == "item_dropped").sum())


def _screen_features(
    f: dict[str, float], ctx: _Context, it: dict[str, np.ndarray], i0: int, i1: int
) -> None:
    t0, t1 = int(ctx.seg.tick[i0]), int(ctx.seg.tick[i1])
    for e in ctx.episodes:
        overlap = min(e.i1, i1) - max(e.i0, i0) + 1
        if overlap > 0:
            f[f"screen_s_{e.kind}"] += overlap / 20.0
            f["screen_s"] += overlap / 20.0
    c = ctx.clicks.window(t0, t1)
    f["clicks"] = len(c["menu_kind"])
    for k in c["menu_kind"]:
        f[f"clicks_{k}"] += 1
    f["clicks_throw"] = int((c["click_type"] == "throw").sum())
    for k in ctx.results.window(t0, t1)["result_kind"]:
        f[f"results_{_result_kind(k)}"] += 1
    kind = it["interaction"]
    f["uses_container"] = int(((kind == "use_block") & _isin(it["block"], CONTAINERS)).sum())
    villager = (kind == "use_entity") & _isin(it["entity_type"], ("villager", "wandering_trader"))
    f["uses_villager"] = int(villager.sum())


def _farm_uses(it: dict[str, np.ndarray]) -> np.ndarray:
    """Tilling, planting, bone meal, and feeding, shearing or milking farm animals."""
    out = []
    for kind, block, item, ent in zip(
        it["interaction"], it["block"], it["item_id"], it["entity_type"], strict=True
    ):
        block, item = block or "", item or ""
        if kind == "use_block":
            till = item.endswith("_hoe") and block in TILLABLE
            plant = item in PLANTING and (blocks.is_crop(block) or block in TILLABLE)
            out.append(till or plant or block == "farmland")
        elif kind == "use_entity":
            out.append(ent in FARM_ANIMALS and item in FEED)
        else:
            out.append(False)
    return np.array(out, dtype=bool)


def _local_straightness(x: np.ndarray, z: np.ndarray, chunk: int) -> float:
    """Path-weighted mean straightness of consecutive ``chunk``-tick pieces (0 if barely moving):
    a winding road is locally straight, wandering is not."""
    num = den = 0.0
    for a in range(0, len(x) - 1, chunk):
        b = min(a + chunk, len(x) - 1)
        if b - a < chunk // 2 and a > 0:
            break
        path = float(np.nansum(np.hypot(np.diff(x[a : b + 1]), np.diff(z[a : b + 1]))))
        if path >= 1:
            num += float(np.hypot(x[b] - x[a], z[b] - z[a]))
            den += path
    return num / den if den else 0.0


# ---------------------------------------------------------------- rules


def _not_mining_other_things(f: dict[str, float]) -> bool:
    return (
        f["log_share"] + f["leaves_share"] <= 0.2
        and f["crop_share"] <= 0.2
        and f["rebroken_share"] <= 0.5
    )


def score_strip_mine(f: dict[str, float]) -> float:
    """At least 20 breaks at 0.2/s or more, mostly in narrow corridors (corridor_cover >= 0.7,
    wide_cover <= 0.25) on one floor level (85% of column floors within 1 of the median, columns
    <= 3 high), with side branches: three or more parallel corridors spaced 3-8 apart (teeth) or
    two or more corridors leaving the main one between its ends (branches). Two teeth alone are
    also the two sides of a room dug perimeter-first, so they don't count, and neither does a
    window outlining a ring (ring_sides >= 3: area clearing)."""
    if f["breaks"] < 20 or f["break_rate"] < 0.2 or not _not_mining_other_things(f):
        return 0.0
    if f["corridor_cover"] < 0.7 or f["wide_cover"] > 0.25:
        return 0.0
    if f["floor_share"] < 0.85 or f["col_height"] > 3:
        return 0.0
    arms = max(f["teeth"] - 1, f["branches"])
    if arms < 2 or f["ring_sides"] >= 3:
        return 0.0
    return min(0.95, 0.7 + 0.08 * (arms - 2) + (0.1 if f["ore_share"] > 0 else 0.0))


def score_tunnel(f: dict[str, float]) -> float:
    """At least 15 breaks at 0.2/s or more, three quarters of the columns in one straight corridor
    at most 3 wide and 8+ long, a consistent cross-section (column height std <= 0.75), breaks
    advancing along it (|corr(order, position)| >= 0.7), no strip-mine branches, and not part of
    a ring (ring_sides < 3)."""
    if f["breaks"] < 15 or f["break_rate"] < 0.2 or not _not_mining_other_things(f):
        return 0.0
    if f["corridor_main"] < 8 or f["corridor_main_share"] < 0.75 or f["corridor_width"] > NARROW:
        return 0.0
    if f["col_height_std"] > 0.75 or f["break_advance"] < 0.7:
        return 0.0
    if f["teeth"] >= 2 or f["branches"] >= 2 or f["ring_sides"] >= 3:
        return 0.0
    return 0.7 + 0.2 * min(1.0, (f["corridor_main"] - 8) / 16)


def score_area_clear(f: dict[str, float]) -> float:
    """The best of three ways a room gets cleared (filled, ring, hollowed), each on top of the
    usual mining checks: not logs, crops or blocks placed this segment."""
    if not _not_mining_other_things(f):
        return 0.0
    return max(_filled(f), _ring_dug(f), _hollowed(f))


def _filled(f: dict[str, float]) -> float:
    """At least 20 breaks over 12+ columns filling a bounded region: half the columns in
    corridors 4+ wide (wide_cover), both footprint sides >= 4 and at most 4:1, 35% of the
    bounding box broken (break_fill_xyz), on one floor level (80% of column floors within 1 of
    the median). Parallel 1-wide passes, a 3-wide tunnel, or digging up or down through a small
    patch are not filling."""
    if f["breaks"] < 20 or f["break_rate"] < 0.2:
        return 0.0
    if f["break_cols"] < 12 or f["bbox_short"] < 4 or f["bbox_long"] > 4 * f["bbox_short"]:
        return 0.0
    if f["wide_cover"] < 0.5 or f["break_fill_xyz"] < 0.35 or f["floor_share"] < 0.8:
        return 0.0
    return 0.65 + 0.25 * min(1.0, (f["wide_cover"] - 0.5) / 0.4)


def _ring_dug(f: dict[str, float]) -> float:
    """At least 20 breaks at 0.2/s outlining three or four sides of a rectangle around an
    interior 4x4 or larger (ring_sides, ring_interior), on one floor level (80% of column floors
    within 1 of the median): the loop dug before the inside is cleared."""
    if f["breaks"] < 20 or f["break_rate"] < 0.2:
        return 0.0
    if f["ring_sides"] < 3 or f["ring_interior"] < 4 or f["floor_share"] < 0.8:
        return 0.0
    return 0.75 if f["ring_sides"] == 3 else 0.85


def _hollowed(f: dict[str, float]) -> float:
    """Hollowing out an existing space: 10+ breaks counted for open sides (open_n) at 0.2/s, half
    of them with 3+ neighbours already open and a mean of 2.3+, over 4+ columns in a bounded
    footprint (<= 32 blocks) on one floor level (75% within 1 of the median), columns at most 4
    high, mostly not ore, and not a straight corridor (three quarters of the columns in one 8+
    long: that stays act.tunnel). Boring a tunnel or a ring opens 1-2 sides per block."""
    if f["open_n"] < 10 or f["break_rate"] < 0.2:
        return 0.0
    if f["open3_share"] < 0.5 or f["open_mean"] < 2.3:
        return 0.0
    if f["break_cols"] < 4 or f["bbox_long"] > 32 or f["floor_share"] < 0.75:
        return 0.0
    if f["col_height"] > 4 or f["ore_share"] > 0.5:
        return 0.0
    if f["corridor_main"] >= 8 and f["corridor_main_share"] >= 0.75:
        return 0.0
    return 0.65 + 0.25 * min(1.0, (f["open3_share"] - 0.5) / 0.4)


def score_tree_farm(f: dict[str, float]) -> float:
    """Four or more log breaks, logs and leaves half the breaks or more, in vertical clusters
    (2+ logs per log column, a column 3+ high)."""
    if f["log_breaks"] < 4 or f["log_share"] + f["leaves_share"] < 0.5:
        return 0.0
    if f["logs_per_col"] < 2 or f["log_height"] < 3:
        return 0.0
    return 0.7 + 0.2 * min(1.0, (f["log_breaks"] - 4) / 12)


def score_build(f: dict[str, float]) -> float:
    """Kept placements (not scaffolding under the feet, not lights or crops, not broken again in
    the segment): at least 12 and one per 3 s, over 5+ columns, and at least as many as the
    breaks. Patching a few holes while mining or sealing a shaft is not building."""
    kept = f["build_places"]
    if kept < max(12.0, f["dur_s"] / 3) or f["build_cols"] < 5 or kept < f["breaks"]:
        return 0.0
    return 0.65 + 0.25 * min(1.0, (kept - 12) / 36)


def score_farm(f: dict[str, float]) -> float:
    """Four or more farm actions (crop harvests, planting, tilling, bone meal, feeding or shearing
    farm animals, hits on livestock) making up half the breaks, placements, farm uses and
    livestock hits or more."""
    ops = f["farm_ops"]
    work = f["breaks"] + f["places"] + f["farm_uses"] + f["dmg_dealt_livestock"]
    if ops < 4 or ops < 0.5 * work:
        return 0.0
    return 0.7 + 0.2 * min(1.0, (ops - 4) / 12)


def _settled(f: dict[str, float]) -> bool:
    """No other work in the window: few breaks (clearing the path only), placements or screens."""
    return (
        f["breaks"] <= max(3.0, 0.1 * f["path_h"])
        and f["build_places"] <= 3
        and f["screen_s"] <= 0.15 * f["dur_s"]
        and f["farm_ops"] < 2
    )


def score_travel(f: dict[str, float]) -> float:
    """Directed movement: 20+ m of horizontal path over 8+ s, moving 60%+ of the time, at most 3
    stops (1 s+) per minute, and straight (net displacement / path >= 0.7, or >= 0.4 when each
    10 s piece is >= 0.85 straight: a winding road), with no other work (_settled)."""
    if f["path_h"] < 20 or f["dur_s"] < 8 or f["frac_moving"] < 0.6 or not _settled(f):
        return 0.0
    if f["stops"] > 3 * f["dur_s"] / 60 + 0.5:
        return 0.0
    if f["straight"] >= 0.7:
        return 0.65 + 0.25 * min(1.0, (f["straight"] - 0.7) / 0.25)
    if f["straight"] >= 0.4 and f["straight_10s"] >= 0.85:
        return 0.65
    return 0.0


def score_explore(f: dict[str, float]) -> float:
    """Wandering: 30+ m of path over 20+ s that doesn't go anywhere (straightness <= 0.5 and 10 s
    pieces <= 0.8), spread out (radius of gyration >= 5 m, 8+ distinct 4x4 cells), with
    looking around (camera off the heading 30%+ of moving time, look.idle_scan 10%+ of the window,
    or turning the camera while standing 15%+), no screens, containers or other work. "New
    terrain" can't be checked inside one segment, so spread stands in for it."""
    if f["path_h"] < 30 or f["dur_s"] < 20 or not _settled(f):
        return 0.0
    if f["straight"] > 0.5 or f["straight_10s"] > 0.8:
        return 0.0
    if f["rg_h"] < 5 or f["cells_4"] < 8 or f["clicks"] or f["uses_container"] or f["screen_s"]:
        return 0.0
    looks = (
        f["look_off_heading"] >= 0.3 or f["p_look_idle_scan"] >= 0.1 or f["frac_look_still"] >= 0.15
    )
    if not looks:
        return 0.0
    return 0.65 + (0.1 if f["p_look_idle_scan"] >= 0.1 else 0.0)


def score_fight(f: dict[str, float]) -> float:
    """Two or more hits on hostile mobs (or players), plus a third hit, damage from a mob, or a
    hostile within 8 blocks for 20%+ of the window; or two ranged shots while a hostile is near
    and hurting you."""
    hits = max(f["attacks_hostile"], f["dmg_dealt_hostile"])
    if hits >= 2 and (hits >= 3 or f["dmg_taken_mob"] >= 1 or f["hostile_frac"] >= 0.2):
        return min(0.95, 0.7 + 0.1 * (f["dmg_taken_mob"] >= 1) + 0.1 * (hits >= 5))
    if f["shots"] >= 2 and f["dmg_taken_mob"] >= 1 and f["hostile_frac"] >= 0.2:
        return 0.65
    return 0.0


def score_manage_inventory(f: dict[str, float]) -> float:
    """Two or more slot clicks in the player inventory or a container (chest, barrel, shulker,
    hopper). Screens where something was crafted, smelted or traded don't count (their kind
    changes), so mixed windows keep only the sorting screens as evidence."""
    clicks = f["clicks_inventory"] + f["clicks_container"]
    if clicks < 2:
        return 0.0
    return 0.7 + 0.2 * min(1.0, (clicks - 2) / 10)


def score_smelt_craft(f: dict[str, float]) -> float:
    """A craft or smelt result taken, or two or more slot clicks in a furnace, blast furnace,
    smoker or crafting table."""
    if f["results_craft"] + f["results_smelt"] >= 1:
        return 0.9
    if f["clicks_smelt"] + f["clicks_craft"] >= 2:
        return 0.75
    return 0.0


RULES = {
    "act.strip_mine": score_strip_mine,
    "act.tunnel": score_tunnel,
    "act.area_clear": score_area_clear,
    "act.tree_farm": score_tree_farm,
    "act.build": score_build,
    "act.farm": score_farm,
    "act.travel": score_travel,
    "act.explore": score_explore,
    "act.fight": score_fight,
    "act.manage_inventory": score_manage_inventory,
    "act.smelt_craft": score_smelt_craft,
}


_MODEL = None  # hbpipe.label.activity_model.ActivityModel, once one is trained


def use_model(model) -> None:
    """Lets a trained classifier choose each window's primary activity (None: rules only)."""
    global _MODEL
    _MODEL = model


def decide(f: dict[str, float]) -> dict[str, float]:
    """The labels a window claims, with their scores: at most one primary, any overlays."""
    scores = {name: rule(f) for name, rule in RULES.items()}
    out = {k: scores[k] for k in OVERLAY if scores[k] >= FIRE}
    if _MODEL is not None:
        label, p = _MODEL.predict(f | {f"rule_{k}": scores[k] for k in PRIMARY})
        if label in PRIMARY and p >= FIRE:
            out[label] = p
        return out
    primary = sorted(((scores[k], k) for k in PRIMARY if scores[k] >= FIRE), reverse=True)
    if primary and (len(primary) == 1 or primary[0][0] - primary[1][0] >= MARGIN):
        out[primary[0][1]] = primary[0][0]
    return out


# ---------------------------------------------------------------- windows and spans


def windows(n: int) -> list[tuple[int, int, int, int]]:
    """(i0, i1, core0, core1) row indices, inclusive, of each window over ``n`` rows."""
    if n <= WINDOW_TICKS:
        return [(0, n - 1, 0, n - 1)] if n else []
    starts = list(range(0, n - WINDOW_TICKS + 1, STEP_TICKS))
    if starts[-1] + WINDOW_TICKS < n:
        starts.append(n - WINDOW_TICKS)
    centers = [a + WINDOW_TICKS // 2 for a in starts]
    out = []
    for k, a in enumerate(starts):
        c0 = 0 if k == 0 else (centers[k - 1] + centers[k]) // 2
        c1 = n - 1 if k == len(starts) - 1 else (centers[k] + centers[k + 1]) // 2 - 1
        out.append((a, a + WINDOW_TICKS - 1, c0, c1))
    return out


def _dilate(n: int, rows: np.ndarray, pad: int) -> np.ndarray:
    mask = np.zeros(n, dtype=bool)
    for r in rows:
        mask[max(0, r - pad) : r + pad + 1] = True
    return mask


def _close(mask: np.ndarray, gap: int) -> np.ndarray:
    out = np.zeros_like(mask)
    for a, b in runs(mask, max_gap=gap):
        out[a : b + 1] = True
    return out


def _rows(seg: Seg, ticks: np.ndarray) -> np.ndarray:
    return np.clip(np.searchsorted(seg.tick, ticks), 0, len(seg) - 1)


# (dilation, gap closed, minimum span) in ticks per label's evidence, and for event evidence the
# fewest events a stretch needs, so one stray break or hit can't make or stretch a span
EVIDENCE_TICKS = {
    "mine": (60, 100, 100),
    "act.tree_farm": (100, 200, 100),
    "act.build": (60, 200, 100),
    "act.farm": (60, 200, 100),
    "act.travel": (10, 60, 100),
    "act.explore": (10, 100, 200),
    "act.fight": (60, 200, 40),
    "screen": (10, 100, 40),
}
MIN_EVENTS = {"mine": 3, "act.tree_farm": 3, "act.build": 3, "act.farm": 3, "act.fight": 2}


def _evidence(ctx: _Context) -> dict[str, np.ndarray]:
    """Per-label tick masks of where the label's own evidence lies (already dilated and closed)."""
    seg = ctx.seg
    n = len(seg)

    def from_ticks(ticks: np.ndarray, key: str) -> np.ndarray:
        pad, gap, _ = EVIDENCE_TICKS[key]
        rows = np.sort(_rows(seg, ticks))
        mask = _close(_dilate(n, rows, pad), gap)
        for a, b in runs(mask):
            inside = np.searchsorted(rows, b, "right") - np.searchsorted(rows, a, "left")
            if inside < MIN_EVENTS[key]:
                mask[a : b + 1] = False
        return mask

    b, p, it = ctx.breaks, ctx.places, ctx.inter.cols
    btick = b["tick"].to_numpy().astype(np.int64)
    ptick = p["tick"].to_numpy().astype(np.int64)
    is_log = _flags(b["block"], _log_or_leaves)
    down = b["underfoot"].to_numpy().astype(bool)
    keep = ~(
        p["scaffold"].to_numpy().astype(bool)
        | p["removed"].to_numpy().astype(bool)
        | _flags(p["block"], blocks.is_light)
        | _flags(p["block"], blocks.is_crop)
    )
    farm_ticks = np.concatenate(
        [
            ctx.inter.tick[_farm_uses(it)],
            btick[_flags(b["block"], _crop_break)],
            ptick[_flags(p["block"], blocks.is_crop)],
            ctx.dealt.tick[_isin(ctx.dealt.cols["entity_type"], LIVESTOCK)],
        ]
    )
    hostile_hits = np.concatenate(
        [
            ctx.inter.tick[
                (it["interaction"] == "attack_entity") & _isin(it["entity_type"], HOSTILE)
            ],
            ctx.dealt.tick[_isin(ctx.dealt.cols["entity_type"], HOSTILE)],
            ctx.taken.tick[_isin(ctx.taken.cols["entity_type"], HOSTILE)],
        ]
    )
    speed = seg.col("speed")
    turning = np.abs(seg.col("yaw_rate")) + np.abs(seg.col("pitch_rate")) > 20

    def moving_mask(base: np.ndarray, key: str) -> np.ndarray:
        pad, gap, _ = EVIDENCE_TICKS[key]
        return _close(_dilate(n, np.flatnonzero(base), pad), gap)

    def screens(kinds: tuple[str, ...], need_action: bool) -> np.ndarray:
        pad, gap, _ = EVIDENCE_TICKS["screen"]
        mask = np.zeros(n, dtype=bool)
        for e in ctx.episodes:
            if e.kind in kinds and (not need_action or e.clicks or sum(e.results.values())):
                mask[max(0, e.i0 - pad) : e.i1 + pad + 1] = True
        return _close(mask, gap)

    mining = from_ticks(btick[~is_log & ~down], "mine")
    return {
        "act.strip_mine": mining,
        "act.tunnel": mining,
        "act.area_clear": mining,
        "act.tree_farm": from_ticks(btick[is_log], "act.tree_farm"),
        "act.build": from_ticks(ptick[keep], "act.build"),
        "act.farm": from_ticks(farm_ticks, "act.farm"),
        "act.travel": moving_mask(speed > MOVING, "act.travel"),
        "act.explore": moving_mask((speed > 0.5) | turning, "act.explore"),
        "act.fight": from_ticks(hostile_hits, "act.fight"),
        "act.manage_inventory": screens(("inventory", "container"), need_action=True),
        "act.smelt_craft": screens(("smelt", "craft"), need_action=True),
    }


def _min_len(label: str) -> int:
    if label in ("act.strip_mine", "act.tunnel", "act.area_clear"):
        return EVIDENCE_TICKS["mine"][2]
    if label in ("act.manage_inventory", "act.smelt_craft"):
        return EVIDENCE_TICKS["screen"][2]
    return EVIDENCE_TICKS[label][2]


def label_activities(seg: Seg) -> dict[str, list[Span]]:
    """Every activity span in the segment, by label."""
    n = len(seg)
    out: dict[str, list[Span]] = {name: [] for name in RULES}
    if not n:
        return out
    ctx = _context(seg)
    score = {name: np.zeros(n) for name in RULES}
    for i0, i1, c0, c1 in windows(n):
        for name, s in decide(_features(ctx, i0, i1)).items():
            score[name][c0 : c1 + 1] = s
    evidence = _evidence(ctx)
    for name in RULES:
        mask = (score[name] > 0) & evidence[name]
        for a, b in runs(mask, min_len=_min_len(name)):
            out[name].append(seg.span(a, b, name, float(score[name][a : b + 1].mean())))
    return out


def _activities(seg: Seg) -> dict[str, list[Span]]:
    """label_activities, computed once per segment (every act.* detector reads it)."""
    return seg.memo("act.labels", label_activities)


@detector("act.travel", level="activity")
def travel(seg: Seg) -> list[Span]:
    return _activities(seg)["act.travel"]


@detector("act.strip_mine", level="activity")
def strip_mine(seg: Seg) -> list[Span]:
    return _activities(seg)["act.strip_mine"]


@detector("act.area_clear", level="activity")
def area_clear(seg: Seg) -> list[Span]:
    return _activities(seg)["act.area_clear"]


@detector("act.tunnel", level="activity")
def tunnel(seg: Seg) -> list[Span]:
    return _activities(seg)["act.tunnel"]


@detector("act.tree_farm", level="activity")
def tree_farm(seg: Seg) -> list[Span]:
    return _activities(seg)["act.tree_farm"]


@detector("act.build", level="activity")
def build(seg: Seg) -> list[Span]:
    return _activities(seg)["act.build"]


@detector("act.fight", level="activity")
def fight(seg: Seg) -> list[Span]:
    return _activities(seg)["act.fight"]


@detector("act.explore", level="activity")
def explore(seg: Seg) -> list[Span]:
    return _activities(seg)["act.explore"]


@detector("act.farm", level="activity")
def farm(seg: Seg) -> list[Span]:
    return _activities(seg)["act.farm"]


@detector("act.manage_inventory", level="activity")
def manage_inventory(seg: Seg) -> list[Span]:
    return _activities(seg)["act.manage_inventory"]


@detector("act.smelt_craft", level="activity")
def smelt_craft(seg: Seg) -> list[Span]:
    return _activities(seg)["act.smelt_craft"]
