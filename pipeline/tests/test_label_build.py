from labelkit import detect, ev, seg, spans_of


def placed(tick, x, y, z, *, feet=(0.5, 64.0, 0.5), below="stone", block="cobblestone", **kw):
    """A block_placed at (x, y, z), clicked on the block under it, with the player's feet at
    ``feet``."""
    fields = dict(
        x=x,
        y=y,
        z=z,
        clicked_x=x,
        clicked_y=y - 1,
        clicked_z=z,
        face="up",
        block=block,
        hand="main",
        player_x=feet[0],
        player_y=feet[1],
        player_z=feet[2],
        below=below,
    )
    return ev(tick, "block_placed", **(fields | kw))


def use_block(tick, x, y, z):
    return ev(tick, "interaction", interaction="use_block", x=x, y=y, z=z, hand="main")


def test_place_spans_from_the_click():
    k_use = [False] * 17 + [True] * 5 + [False] * 58
    events = [
        use_block(20, 1, 63, 0),
        placed(20, 1, 64, 0),
        use_block(40, 2, 63, 0),
        placed(40, 2, 64, 0),
        placed(60, 3, 64, 0),
    ]
    s = seg(80, k_use=k_use, events=events)
    assert spans_of("build.place", s) == [(17, 20), (40, 40), (60, 60)]
    assert [sp.confidence for sp in detect("build.place", s)] == [0.95, 0.95, 0.85]


def test_held_use_key_repeats_without_a_new_click():
    events = [placed(30, 1, 64, 0), placed(34, 2, 64, 0)]
    s = seg(50, k_use=True, events=events)
    assert spans_of("build.place", s) == [(30, 30), (34, 34)]


ARC = [0.4, 0.75, 1.0, 1.15, 1.2, 1.15, 1.1, 1.0]  # feet above the takeoff, airborne rows


def pillar_seg(steps, *, period=12, place_x=0, jump=True, again=False):
    """``steps`` jump-places on the spot from y 64: each takeoff row, 8 airborne rows, then
    ground on the new block; the block goes in at the top of each jump (``again``: twice)."""
    n = 4 + steps * period + 8
    y, ground, vs, k_jump = [64.0] * 4, [True] * 4, [0.0] * 4, [False] * 4
    events = []
    for s in range(steps):
        b = 4 + s * period
        y0 = 64.0 + s
        for r in range(period):
            airborne = jump and 1 <= r <= 8
            y.append(y0 + ARC[r - 1] if airborne else (y0 if r == 0 or not jump else y0 + 1))
            ground.append(not airborne)
            vs.append(8.4 if airborne and r == 1 else 0.0)
            k_jump.append(jump and r <= 2)
        feet = (0.5, y[b + 5], 0.5)
        events.append(placed(b + 5, place_x, int(y0), 0, feet=feet, below="stone"))
        if again:
            again_feet = (0.5, y[b + 6], 0.5)
            events.append(placed(b + 6, place_x, int(y0), 0, feet=again_feet, below="stone"))
    last = y[-1]
    y += [last] * 8
    ground += [True] * 8
    vs += [0.0] * 8
    k_jump += [False] * 8
    return seg(n, y=y, on_ground=ground, vspeed=vs, k_jump=k_jump, events=events)


def test_pillar_is_jump_placing_under_the_feet():
    s = pillar_seg(3)
    assert spans_of("build.pillar", s) == [(4, 37)]
    assert detect("build.pillar", s)[0].confidence == 0.9
    single = pillar_seg(1)
    assert spans_of("build.pillar", single) == [(4, 13)]
    assert detect("build.pillar", single)[0].confidence == 0.7


def test_one_jump_placing_the_same_block_twice_is_one_pillar_span():
    # bedwars: a block placed, broken and placed again in one jump (one label id twice crashed it)
    assert spans_of("build.pillar", pillar_seg(1, again=True)) == [(4, 13)]


def test_pillar_needs_the_own_column_and_a_jump():
    assert spans_of("build.pillar", pillar_seg(3, place_x=2)) == []
    standing = seg(40, events=[placed(10, 0, 63, 0, feet=(0.5, 64.0, 0.5)), placed(20, 0, 63, 0)])
    assert spans_of("build.pillar", standing) == []


def bridge_seg(*, keys="back", below="air", walk=True, xs=(-1, -2, -3, -4)):
    """Sneaking backward (west) at 1 m/s from x 0.5 on blocks at y 63, placing the next block
    behind every 20 ticks."""
    n = 100
    x = [0.5 - (0.05 * i if walk else 0.0) for i in range(n)]
    events = []
    for k, bx in enumerate(xs):
        t = 10 + 20 * k
        events.append(placed(t, bx, 63, 0, feet=(x[t], 64.0, 0.5), below=below, face="west"))
    cols = {"k_back": keys == "back", "k_left": keys == "left", "k_forward": keys == "forward"}
    return seg(n, x=x, crouching=True, speed=1.0, events=events, **cols)


def test_bridge_places_under_the_feet_over_air_moving_back():
    s = bridge_seg()
    assert spans_of("build.bridge", s) == [(10, 70)]
    assert detect("build.bridge", s)[0].confidence == 0.9
    assert spans_of("build.fill", s) == []


def test_bridge_needs_air_below_back_or_side_keys_and_walking_on_it():
    assert spans_of("build.bridge", bridge_seg(keys="forward")) == []
    assert spans_of("build.bridge", bridge_seg(below="stone")) == []
    beside = bridge_seg(keys="left", walk=False, xs=(1, 1))
    assert spans_of("build.bridge", beside) == []
    assert spans_of("build.bridge", bridge_seg(xs=(-1,))) == []


def test_fill_covers_a_plane_of_placements():
    wall = [(5, y, z) for y in range(64, 67) for z in range(3)]
    events = [placed(10 + 10 * k, *p, feet=(3.5, 64.0, 1.5)) for k, p in enumerate(wall)]
    s = seg(120, events=events)
    assert spans_of("build.fill", s) == [(10, 90)]
    assert detect("build.fill", s)[0].confidence == 0.95


def test_lines_and_scattered_placements_are_not_fills():
    line = [placed(10 + 10 * k, 5, 64, k, feet=(3.5, 64.0, 1.5)) for k in range(5)]
    assert spans_of("build.fill", seg(80, events=line)) == []
    slow = [placed(10 + 150 * k, 5, 64 + k // 2, k % 2) for k in range(4)]
    assert spans_of("build.fill", seg(700, events=slow)) == []
    assert spans_of("build.fill", pillar_seg(5)) == []


def test_light_spans_the_torch_routine():
    held = ["stone_pickaxe"] * 20 + ["torch"] * 15 + ["stone_pickaxe"] * 45
    events = [
        placed(28, 3, 65, 0, block="wall_torch"),
        placed(50, 4, 65, 0, block="torch", hand="off"),
        placed(60, 5, 65, 0, block="cobblestone"),
    ]
    s = seg(80, held_item=held, events=events)
    assert spans_of("build.light", s) == [(20, 34), (50, 50)]


def test_undo_breaks_a_fresh_placement():
    events = [
        placed(10, 1, 64, 0, block="cobblestone"),
        ev(40, "block_broken", x=1, y=64, z=0, block="cobblestone", ticks_taken=5),
        placed(50, 2, 64, 0, block="cobblestone"),
        ev(200, "block_broken", x=2, y=64, z=0, block="cobblestone", ticks_taken=5),
        ev(210, "block_broken", x=3, y=64, z=0, block="stone", ticks_taken=5),
    ]
    s = seg(220, events=events)
    assert spans_of("build.undo", s) == [(10, 40)]
    assert detect("build.undo", s)[0].confidence == 0.9


def test_undo_needs_the_same_position_and_no_break_between():
    events = [
        placed(10, 1, 64, 0),
        ev(20, "block_broken", x=1, y=64, z=1, block="cobblestone"),
        ev(25, "block_broken", x=1, y=64, z=0, block="cobblestone"),
        ev(30, "block_broken", x=1, y=64, z=0, block="cobblestone"),
    ]
    assert spans_of("build.undo", seg(40, events=events)) == [(10, 25)]
