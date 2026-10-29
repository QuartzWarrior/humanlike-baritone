import math

import numpy as np
from labelkit import detect, ev, row, seg, spans_of, t

from hbpipe.label.base import Span
from hbpipe.label.detectors import activities as act

CONTAINER = "net.minecraft.client.gui.screens.inventory.ContainerScreen"
INVENTORY = "net.minecraft.client.gui.screens.inventory.InventoryScreen"
FURNACE = "net.minecraft.client.gui.screens.inventory.FurnaceScreen"
MERCHANT = "net.minecraft.client.gui.screens.inventory.MerchantScreen"
MINING = ("act.strip_mine", "act.tunnel", "act.area_clear")


def dig(
    cols, *, y0=-50, height=2, every=10, start=0, block="deepslate", open_sides=None
) -> list[dict]:
    """Break events: each (x, z) column dug ``height`` blocks up from y0, one break every
    ``every`` ticks, the player standing just behind the block. ``open_sides`` is one value for
    every break, or a list cycled through (one per break)."""
    out = []
    tick = start
    for x, z in cols:
        for dy in range(height):
            e = ev(
                tick,
                "block_broken",
                x=x,
                y=y0 + dy,
                z=z,
                block=block,
                player_x=x + 0.5,
                player_y=float(y0),
                player_z=z - 0.5,
            )
            if open_sides is not None:
                cycle = open_sides if isinstance(open_sides, list) else [open_sides]
                e["open_sides"] = cycle[len(out) % len(cycle)]
            out.append(e)
            tick += every
    return out


def dig_down(n: int, *, x=0, z=0, y0=-51, start=0, every=10) -> list[dict]:
    """Digging straight down: each block under the feet, with its sides already open (an old
    pillar's shaft)."""
    return [
        ev(
            start + every * i,
            "block_broken",
            x=x,
            y=y0 - i,
            z=z,
            block="cobbled_deepslate",
            player_x=x + 0.5,
            player_y=float(y0 + 1 - i),
            player_z=z + 0.5,
            open_sides=3,
        )
        for i in range(n)
    ]


def comb() -> list[tuple[int, int]]:
    """A strip mine: a main tunnel along x with a branch every 4 blocks, dug as you would."""
    cols = []
    for x in range(21):
        cols.append((x, 0))
        if x in (4, 8, 12, 16):
            cols += [(x, z) for z in range(1, 7)]
    return cols


def room(w: int, d: int) -> list[tuple[int, int]]:
    return [(x if z % 2 == 0 else w - 1 - x, z) for z in range(d) for x in range(w)]


def ring(n: int) -> list[tuple[int, int]]:
    """The perimeter of an n x n square, dug as one loop."""
    side = range(n - 1)
    return (
        [(x, 0) for x in side]
        + [(n - 1, z) for z in side]
        + [(n - 1 - x, n - 1) for x in side]
        + [(0, n - 1 - z) for z in side]
    )


def labels(s) -> dict[str, list[tuple[int, int]]]:
    return {name: spans_of(name, s) for name in act.RULES if spans_of(name, s)}


# ---------------------------------------------------------------- framework


def test_windows_tile_the_segment():
    for n in (1, 400, 1200, 1201, 1500, 3333):
        ws = act.windows(n)
        cores = [(c0, c1) for _, _, c0, c1 in ws]
        assert cores[0][0] == 0 and cores[-1][1] == n - 1
        assert all(b + 1 == c for (_, b), (c, _) in zip(cores, cores[1:], strict=False))
        assert all(i1 - i0 + 1 == min(n, act.WINDOW_TICKS) for i0, i1, _, _ in ws)
        assert all(i0 <= c0 <= c1 <= i1 for i0, i1, c0, c1 in ws)
    assert act.windows(800) == [(0, 799, 0, 799)]  # shorter than a window: one covering it all


def test_window_features_are_stable_and_numeric():
    s = seg(
        400,
        events=dig(comb()[:20], every=8)
        + [
            ev(300, "block_placed", x=3, y=64, z=3, block="oak_planks", player_x=0.5),
            ev(310, "damage_dealt", entity_type="zombie", amount=4.0),
        ],
        labels=[Span(t(0), t(199), "mine.block", 0.9), Span(t(100), t(299), "look.idle_scan", 0.8)],
    )
    f = act.window_features(s, 0, 399)
    assert tuple(f) == act.FEATURE_KEYS
    assert all(isinstance(v, float) and math.isfinite(v) for v in f.values())
    assert f["breaks"] == 40 and f["dur_s"] == 20.0
    assert f["p_mine"] == 0.5 and f["p_look_idle_scan"] == 0.5 and f["p_move"] == 0.0
    assert f["dmg_dealt_hostile"] == 1
    assert act.FEATURE_KEYS[-7:] == (  # later additions go at the end
        "open_n",
        "open_mean",
        "open3_share",
        "open1_share",
        "ring_sides",
        "ring_interior",
        "ring_interior_dug",
    )


def test_break_shape_features():
    f = act.window_features(seg(700, events=dig([(x, 0) for x in range(30)])), 0, 699)
    assert f["break_cols"] == 30 and f["corridor_main"] == 30 and f["corridor_width"] == 1
    assert f["corridor_main_share"] == 1.0 and f["break_advance"] > 0.95 and f["col_height"] == 2
    f = act.window_features(seg(1200, events=dig(comb(), every=12)), 0, 1199)
    assert f["branches"] == 4 and f["wide_cover"] == 0.0 and f["floor_share"] == 1.0
    f = act.window_features(seg(1200, events=dig(room(8, 8), height=3, every=6)), 0, 1199)
    assert f["wide_cover"] == 1.0 and f["break_fill_xyz"] == 1.0 and f["corridor_cover"] == 0.0


# ---------------------------------------------------------------- mining shapes


def test_strip_mine_is_a_corridor_with_side_branches():
    s = seg(1200, events=dig(comb(), every=12))
    assert labels(s) == {"act.strip_mine": [(0, 1128)]}  # last break + 3 s


def test_tunnel_is_one_straight_corridor_advancing():
    s = seg(1000, events=dig([(x, 0) for x in range(31)], every=15))
    assert labels(s) == {"act.tunnel": [(0, 975)]}
    short = seg(400, events=dig([(x, 0) for x in range(6)], every=15))  # 6 columns: too short
    assert labels(short) == {}


def test_area_clear_fills_a_bounded_volume():
    s = seg(1200, events=dig(room(8, 8), height=3, every=6))
    assert labels(s) == {"act.area_clear": [(0, 1199)]}


def test_ring_dug_edges_first_is_area_clear():
    # Rooms get cleared edges-first: a loop of 1-wide corridors around the inside, then the
    # inside (the real session's iron golem farm site).
    loop = seg(1200, events=dig(ring(12), height=3, every=9))
    assert labels(loop) == {"act.area_clear": [(0, 1199)]}
    f = act.window_features(loop, 0, 1199)
    assert (f["ring_sides"], f["ring_interior"], f["ring_interior_dug"]) == (4, 10, 0.0)

    three_sides = seg(1200, events=dig(ring(12)[:33], height=3, every=10))
    assert labels(three_sides) == {"act.area_clear": [(0, 1040)]}
    assert act.window_features(three_sides, 0, 1199)["ring_sides"] == 3

    gappy = [c for c in ring(12) if c not in ((5, 11), (6, 11))]  # two blocks were air already
    assert spans_of("act.area_clear", seg(1200, events=dig(gappy, height=3, every=9)))


def test_corridor_turning_once_is_not_a_ring():
    ell = [(x, 0) for x in range(12)] + [(11, z) for z in range(1, 12)]
    assert act.window_features(seg(800, events=dig(ell, every=15)), 0, 799)["ring_sides"] == 0
    assert labels(seg(800, events=dig(ell, every=15))) == {}


def test_straight_corridor_stays_tunnel():
    # Boring opens one side of the top block and two of the one below; even a corridor
    # through ground that is open around it stays a tunnel, not a hollowed room.
    for sides in ([1, 2], 3):
        s = seg(1000, events=dig([(x, 0) for x in range(31)], every=15, open_sides=sides))
        assert labels(s) == {"act.tunnel": [(0, 975)]}


def test_hollowing_an_existing_room_is_area_clear():
    # A 6x3 patch inside a room already opened up: on its own that's neither a filled volume
    # (3 wide) nor a tunnel (6 long), but every block broken had 3+ open neighbours.
    patch = [(x, z) for z in range(3) for x in range(6)]
    hollow = seg(600, events=dig(patch, every=15, open_sides=[3, 4]))
    assert labels(hollow) == {"act.area_clear": [(0, 585)]}
    f = act.window_features(hollow, 0, 599)
    assert (f["open_n"], f["open_mean"], f["open3_share"]) == (36, 3.5, 1.0)

    boring = seg(600, events=dig(patch, every=15, open_sides=[1, 2]))
    assert labels(boring) == {}  # the same shape bored out of solid rock


def test_digging_down_is_not_hollowing():
    # Hollowing for 27 s, then digging down through an old pillar (sides open, under the feet):
    # the clearing label stops 3 s after the last clearing break.
    patch = [(x, z) for z in range(3) for x in range(6)]
    events = dig(patch, every=15, open_sides=3) + dig_down(15, start=560)
    s = seg(1000, events=events)
    assert labels(s) == {"act.area_clear": [(0, 585)]}
    assert labels(seg(400, events=dig_down(30))) == {}
    assert act.window_features(seg(400, events=dig_down(30)), 0, 399)["open_n"] == 0


def test_shaft_down_is_not_mining_activity():
    shaft = [
        ev(
            i * 15,
            "block_broken",
            x=0,
            y=-30 - i,
            z=0,
            block="deepslate",
            player_x=0.5,
            player_y=-29.0 - i,
            player_z=0.5,
        )
        for i in range(40)
    ]
    assert labels(seg(700, events=shaft)) == {}


def test_mining_span_ends_with_the_breaks():
    # 20 s of tunnel, then 40 s of standing around: the label stops 3 s after the last break.
    s = seg(1200, events=dig([(x, 0) for x in range(20)], every=10))
    assert spans_of("act.tunnel", s) == [(0, 450)]


# ---------------------------------------------------------------- trees, building, farming


def test_tree_farm_is_logs_in_vertical_clusters():
    trees = []
    tick = 0
    for tx, tz, h in ((0, 0, 6), (6, 3, 5), (12, 1, 6)):
        for dy in range(h):
            trees.append(ev(tick, "block_broken", x=tx, y=64 + dy, z=tz, block="oak_log"))
            tick += 20
        trees.append(ev(tick, "block_broken", x=tx + 1, y=64 + h, z=tz, block="oak_leaves"))
        tick += 20
    assert labels(seg(1000, events=trees)) == {"act.tree_farm": [(0, 480)]}

    floor = [
        ev(20 * i, "block_broken", x=i, y=64, z=0, block="oak_planks" if i % 2 else "oak_log")
        for i in range(20)
    ]
    assert spans_of("act.tree_farm", seg(600, events=floor)) == []  # logs in a line: no tree


def test_build_needs_kept_placements_off_the_feet():
    wall = [
        ev(
            10 + 15 * i,
            "block_placed",
            x=i % 10,
            y=64 + i // 10,
            z=5,
            block="stone_bricks",
            player_x=4.5,
            player_y=64.0,
            player_z=2.5,
        )
        for i in range(30)
    ]
    assert labels(seg(600, events=wall)) == {"act.build": [(0, 505)]}

    pillar = [
        ev(
            10 + 15 * i,
            "block_placed",
            x=0,
            y=64 + i,
            z=0,
            block="cobblestone",
            player_x=0.5,
            player_y=65.2 + i,
            player_z=0.5,
        )
        for i in range(30)
    ]
    assert labels(seg(600, events=pillar)) == {}  # scaffolding under the feet

    taken_down = wall + [
        ev(500 + 3 * i, "block_broken", x=i % 10, y=64 + i // 10, z=5, block="stone_bricks")
        for i in range(30)
    ]
    assert spans_of("act.build", seg(600, events=taken_down)) == []


def test_farm_is_harvesting_and_planting():
    work = []
    for i in range(8):
        work.append(ev(30 * i, "block_broken", x=i, y=64, z=0, block="wheat"))
        work.append(
            ev(
                30 * i + 10,
                "interaction",
                interaction="use_block",
                block="farmland",
                item_id="wheat_seeds",
                x=i,
                y=63,
                z=0,
            )
        )
        work.append(ev(30 * i + 11, "block_placed", x=i, y=64, z=0, block="wheat"))
    assert labels(seg(400, events=work)) == {"act.farm": [(0, 281)]}
    assert labels(seg(400, events=work[:3])) == {}  # one crop is not farming


# ---------------------------------------------------------------- travel vs explore


def walk_straight(n: int, v: float = 4.0) -> dict:
    return {"x": [0.5 + v / 20 * i for i in range(n)], "speed": v, "vx": v / 20, "yaw": -90.0}


def circles(n: int, loops: float, r: float = 10.0, look_out: bool = False) -> dict:
    """Walking ``loops`` circles of radius r over n ticks; the camera ahead or facing outward."""
    th = np.linspace(0, 2 * np.pi * loops, n)
    vx, vz = -np.sin(th) * r * (th[1] - th[0]), np.cos(th) * r * (th[1] - th[0])
    heading = np.degrees(np.arctan2(-vx, vz))
    yaw = heading + (90.0 if look_out else 0.0)
    return {
        "x": list(r * np.cos(th)),
        "z": list(r * np.sin(th)),
        "vx": list(vx),
        "vz": list(vz),
        "speed": list(np.hypot(vx, vz) * 20),
        "yaw": list(yaw),
    }


def test_travel_is_directed_movement():
    s = seg(400, k_forward=True, **walk_straight(400))
    assert labels(s) == {"act.travel": [(0, 399)]}
    assert labels(seg(100, **walk_straight(100))) == {}  # 20 m path takes more than 5 s


def test_explore_wanders_while_looking_around():
    s = seg(600, **circles(600, loops=2, look_out=True))
    assert labels(s) == {"act.explore": [(0, 599)]}

    ahead = seg(600, **circles(600, loops=2))
    assert labels(ahead) == {}  # wandering with the eyes on the path: neither

    scanning = seg(600, labels=[Span(t(0), t(599), "look.idle_scan", 0.8)], **circles(600, 2))
    assert labels(scanning) == {"act.explore": [(0, 599)]}


def test_travel_with_clutter_is_not_travel():
    digging = dig([(x, 3) for x in range(10)], every=30)
    assert spans_of("act.travel", seg(400, events=digging, **walk_straight(400))) == []


# ---------------------------------------------------------------- overlays


def test_fight_is_hits_on_hostiles_and_overlaps_travel():
    combat = [
        ev(200, "damage_taken", entity_type="zombie", amount=2.0, damage_source="mob_attack"),
        ev(210, "damage_dealt", entity_type="zombie", amount=4.0),
        ev(230, "damage_dealt", entity_type="zombie", amount=4.0),
        ev(250, "damage_dealt", entity_type="zombie", amount=4.0),
    ]
    s = seg(600, events=combat, k_forward=True, **walk_straight(600))
    assert labels(s) == {"act.fight": [(140, 310)], "act.travel": [(0, 599)]}

    # Killing livestock is farming, not a fight.
    cows = [ev(200 + 20 * i, "damage_dealt", entity_type="cow", amount=4.0) for i in range(4)]
    assert labels(seg(600, events=cows)) == {"act.farm": [(140, 320)]}


def chest_visit(open_at: int, close_at: int, clicks: int, menu="minecraft:generic_9x3"):
    screen = CONTAINER if menu.startswith("minecraft:generic") else INVENTORY
    events = [
        ev(open_at, "screen_opened", screen=screen, menu=menu),
        ev(close_at, "screen_closed", screen=screen),
    ]
    gui = [
        row(open_at + 10 + 5 * i, kind="slot", slot_id=i, click_type="quick_move", menu=menu)
        for i in range(clicks)
    ]
    return events, gui


def test_manage_inventory_is_sorting_clicks_in_a_container():
    events, gui = chest_visit(100, 200, clicks=6)
    s = seg(400, events=events, gui=gui)
    assert labels(s) == {"act.manage_inventory": [(90, 209)]}

    events, gui = chest_visit(100, 200, clicks=0)
    assert labels(seg(400, events=events, gui=gui)) == {}  # a glance is not managing


def test_manage_inventory_with_a_screen_open_at_segment_start():
    # The segment resumes with a chest already open; the cleaned screen column would keep it
    # open forever, the close event ends it.
    gui = [
        row(5 + 5 * i, kind="slot", click_type="quick_move", menu="minecraft:generic_9x6")
        for i in range(4)
    ]
    s = seg(300, screen=CONTAINER, events=[ev(40, "screen_closed", screen=CONTAINER)], gui=gui)
    assert labels(s) == {"act.manage_inventory": [(0, 49)]}


def test_trading_and_crafting_are_not_managing_inventory():
    trade = [
        ev(100, "screen_opened", screen=MERCHANT, menu="minecraft:merchant"),
        ev(160, "result_taken", result_kind="trade", menu="minecraft:merchant"),
        ev(200, "screen_closed", screen=MERCHANT),
    ]
    clicks = [
        row(110 + 5 * i, kind="slot", click_type="pickup", menu="minecraft:merchant")
        for i in range(6)
    ]
    assert labels(seg(400, events=trade, gui=clicks)) == {}

    events, gui = chest_visit(100, 200, clicks=4, menu="minecraft:inventory")
    events.append(ev(150, "result_taken", result_kind="craft", menu="minecraft:inventory"))
    assert labels(seg(400, events=events, gui=gui)) == {"act.smelt_craft": [(90, 209)]}


def test_smelt_craft_at_a_furnace():
    events = [
        ev(100, "screen_opened", screen=FURNACE, menu="minecraft:furnace"),
        ev(170, "result_taken", result_kind="smelt", menu="minecraft:furnace"),
        ev(220, "screen_closed", screen=FURNACE),
    ]
    gui = [
        row(110 + 10 * i, kind="slot", click_type="pickup", menu="minecraft:furnace")
        for i in range(3)
    ]
    assert labels(seg(400, events=events, gui=gui)) == {"act.smelt_craft": [(90, 229)]}
    looking = [events[0], events[2]]  # opened to check on it, nothing taken or clicked
    assert labels(seg(400, events=looking)) == {}


def test_manage_inventory_during_strip_mine():
    events, gui = chest_visit(500, 600, clicks=5, menu="minecraft:inventory")
    s = seg(1200, events=dig(comb(), every=12) + events, gui=gui)
    assert labels(s) == {"act.strip_mine": [(0, 1128)], "act.manage_inventory": [(490, 609)]}


# ---------------------------------------------------------------- decisions


def test_two_primaries_too_close_leave_the_window_unlabeled():
    f = dict.fromkeys(act.FEATURE_KEYS, 0.0)
    f |= {"dur_s": 60.0, "breaks": 60.0, "break_rate": 1.0, "break_cols": 30.0}
    f |= {"bbox_long": 6.0, "bbox_short": 6.0, "wide_cover": 0.9, "break_fill_xyz": 0.8}
    f |= {"floor_share": 1.0}
    assert set(act.decide(f)) == {"act.area_clear"}
    f |= {"build_places": 60.0, "build_cols": 20.0}  # also building, about as strongly
    assert act.score_build(f) >= act.FIRE
    assert act.decide(f) == {}
    f |= {"clicks_inventory": 5.0}  # overlays still apply
    assert set(act.decide(f)) == {"act.manage_inventory"}


def test_detectors_return_their_own_labels_with_confidence():
    s = seg(1200, events=dig(comb(), every=12))
    (span,) = detect("act.strip_mine", s)
    assert span.label == "act.strip_mine" and 0.6 <= span.confidence <= 1.0
