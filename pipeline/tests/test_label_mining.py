from labelkit import detect, ev, seg, spans_of, t


def dig(cells, *, t0=10, period=9, taken=3, block="stone", feet=None, refill=None, open_sides=None):
    """A destroy_started and a block_broken for each (x, y, z), one break every ``period`` ticks
    starting at ``t0``. ``feet(x, y, z)`` places the player at each break (default: standing one
    block west of it, on y 64); ``block``, ``refill`` and ``open_sides`` may be functions of the
    position."""
    out = []
    for k, (x, y, z) in enumerate(cells):
        tick = t0 + k * period
        px, py, pz = feet(x, y, z) if feet else (x - 0.5, 64.0, z + 0.5)
        b = block(x, y, z) if callable(block) else block
        r = refill(x, y, z) if callable(refill) else refill
        o = open_sides(x, y, z) if callable(open_sides) else open_sides
        out.append(ev(tick - taken, "destroy_started", x=x, y=y, z=z, block=b))
        out.append(
            ev(
                tick,
                "block_broken",
                x=x,
                y=y,
                z=z,
                block=b,
                ticks_taken=taken,
                player_x=px,
                player_y=py,
                player_z=pz,
                refill=r,
                open_sides=o,
            )
        )
    return out


def mined(cells, **kw):
    """A segment long enough to hold ``dig(cells, **kw)``."""
    period = kw.get("period", 9)
    return seg(kw.get("t0", 10) + len(cells) * period + 30, events=dig(cells, **kw))


def tunnel_cells(length, *, width=1, height=2, axis="x"):
    """A tunnel dug column by column from the origin, top block first."""
    cells = []
    for a in range(1, length + 1):
        for w in range(width):
            for y in range(64 + height - 1, 63, -1):
                cells.append((a, y, w) if axis == "x" else (w, y, a))
    return cells


def test_block_spans_destroy_start_to_break():
    events = [
        ev(10, "destroy_started", x=1, y=64, z=0, block="stone"),
        ev(13, "block_broken", x=1, y=64, z=0, block="stone", ticks_taken=3),
        ev(20, "block_broken", x=2, y=64, z=0, block="stone", ticks_taken=4),  # start not seen
        ev(30, "block_broken", x=3, y=64, z=0, block="short_grass"),  # instant
    ]
    s = seg(40, events=events)
    assert spans_of("mine.block", s) == [(10, 13), (16, 20), (30, 30)]
    assert [sp.confidence for sp in detect("mine.block", s)] == [0.95, 0.85, 0.8]


def test_aborted_attempt_is_not_a_block_span():
    events = [
        ev(5, "destroy_started", x=1, y=64, z=0, block="stone"),
        ev(8, "destroy_aborted", x=1, y=64, z=0, progress=0.4),
        ev(20, "destroy_started", x=1, y=64, z=0, block="stone"),
        ev(24, "block_broken", x=1, y=64, z=0, block="stone", ticks_taken=4),
        ev(30, "destroy_started", x=2, y=64, z=0, block="stone"),
        ev(31, "destroy_aborted", x=2, y=64, z=0, progress=0.0),
    ]
    assert spans_of("mine.block", seg(40, events=events)) == [(20, 24)]


def test_held_attack_through_a_refill_starts_after_the_last_break():
    events = [
        ev(10, "destroy_started", x=1, y=64, z=0, block="gravel"),
        ev(25, "block_broken", x=1, y=64, z=0, block="gravel", ticks_taken=15, refill="gravel"),
        ev(36, "block_broken", x=1, y=64, z=0, block="gravel", ticks_taken=26),
    ]
    assert spans_of("mine.block", seg(40, events=events)) == [(10, 25), (26, 36)]


def test_tunnel_is_a_straight_run_of_consistent_cross_section():
    cells = tunnel_cells(8)
    s = mined(cells)
    assert spans_of("mine.tunnel", s) == [(7, 10 + 9 * 15)]
    assert detect("mine.tunnel", s)[0].confidence >= 0.8
    assert spans_of("mine.area_clear", s) == []
    assert spans_of("mine.strip", s) == []

    wide = mined(tunnel_cells(6, width=3, height=3, axis="z"))
    assert len(spans_of("mine.tunnel", wide)) == 1
    assert spans_of("mine.area_clear", wide) == []


def test_short_flat_or_scattered_breaks_are_not_tunnels():
    assert spans_of("mine.tunnel", mined(tunnel_cells(4))) == []  # too short
    assert spans_of("mine.tunnel", mined(tunnel_cells(10, height=1))) == []  # floor pass
    scatter = [(0, 64, 0), (5, 66, 2), (1, 63, 5), (6, 64, 6), (2, 65, 1), (4, 64, 3)]
    assert spans_of("mine.tunnel", mined(scatter)) == []


def test_tunnel_dug_out_of_order_is_not_a_tunnel():
    order = [8, 1, 6, 3, 7, 2, 5, 4]
    cells = [(a, y, 0) for a in order for y in (65, 64)]
    assert spans_of("mine.tunnel", mined(cells)) == []


def test_area_clear_fills_a_room_and_suppresses_tunnels():
    cells = []
    for z in range(5):
        xs = range(5) if z % 2 == 0 else range(4, -1, -1)
        cells += [(x, y, z) for x in xs for y in (65, 64)]
    s = mined(cells, feet=lambda x, y, z: (x + 0.5, 64.0, z - 0.5))
    assert spans_of("mine.area_clear", s) == [(7, 10 + 9 * 49)]
    assert spans_of("mine.tunnel", s) == []
    assert spans_of("mine.strip", s) == []


def test_tunnel_into_a_room_keeps_its_own_span():
    approach = [(x, y, 2) for x in range(-8, 0) for y in (65, 64)]
    room = []
    for z in range(5):
        xs = range(5) if z % 2 == 0 else range(4, -1, -1)
        room += [(x, y, z) for x in xs for y in (65, 64)]
    s = mined(approach + room)
    assert spans_of("mine.tunnel", s) == [(7, 10 + 9 * 15)]
    assert len(spans_of("mine.area_clear", s)) == 1
    assert spans_of("mine.area_clear", s)[0][0] > 10 + 9 * 15


def strip_cells(branch_at=(4, 8, 12), length=16, branch_len=5):
    cells, x = [], 0
    for b in [*branch_at, length - 1]:
        cells += [(a, y, 0) for a in range(x, b + 1) for y in (65, 64)]
        if b in branch_at:
            cells += [(b, y, z) for z in range(1, branch_len + 1) for y in (65, 64)]
        x = b + 1
    return cells


def test_strip_is_a_main_tunnel_with_side_branches():
    cells = strip_cells()
    s = mined(cells)
    assert spans_of("mine.strip", s) == [(7, 10 + 9 * (len(cells) - 1))]
    assert spans_of("mine.tunnel", s) == []
    assert spans_of("mine.area_clear", s) == []

    one_branch = mined(strip_cells(branch_at=(8,)))
    assert spans_of("mine.strip", one_branch) == []
    assert len(spans_of("mine.tunnel", one_branch)) == 2  # main and branch


def ring_cells(size, *, sides=4):
    """A 1x2 perimeter dug around a ``size`` x ``size`` square from the origin: east along
    z 0, south along the east edge, west along the far edge, north along x 0."""
    n = size - 1
    path = [(x, 0) for x in range(size)] + [(n, z) for z in range(1, size)]
    if sides >= 3:
        path += [(x, n) for x in range(n - 1, -1, -1)]
    if sides >= 4:
        path += [(0, z) for z in range(n - 1, 0, -1)]
    return [(x, y, z) for x, z in path for y in (65, 64)]


def test_a_ring_around_undug_ground_is_area_clearing():
    for sides in (4, 3):
        cells = ring_cells(8, sides=sides)
        s = mined(cells)
        assert spans_of("mine.area_clear", s) == [(7, 10 + 9 * (len(cells) - 1))]
        assert spans_of("mine.tunnel", s) == []
        assert spans_of("mine.strip", s) == []
    assert detect("mine.area_clear", mined(ring_cells(8)))[0].confidence == 0.85


def test_a_ring_needs_room_inside():
    small = mined(ring_cells(5))  # 3 x 3 inside
    assert spans_of("mine.area_clear", small) == []
    assert len(spans_of("mine.tunnel", small)) == 4


def test_l_shaped_and_straight_corridors_stay_tunnels():
    l_shape = mined(ring_cells(8, sides=2))
    assert spans_of("mine.area_clear", l_shape) == []
    assert len(spans_of("mine.tunnel", l_shape)) == 2
    straight = mined(tunnel_cells(12), open_sides=2)
    assert spans_of("mine.area_clear", straight) == []
    assert len(spans_of("mine.tunnel", straight)) == 1


def test_parallel_branches_of_a_strip_mine_are_not_a_ring():
    s = mined(strip_cells(branch_at=(3, 9), length=14, branch_len=6))
    assert len(spans_of("mine.strip", s)) == 1
    assert spans_of("mine.area_clear", s) == []


def test_hollowing_out_dug_space_is_area_clearing():
    cells = tunnel_cells(8, width=3)
    hollow = mined(cells, open_sides=lambda x, y, z: 3 + (x + z) % 2)
    assert spans_of("mine.area_clear", hollow) == [(7, 10 + 9 * (len(cells) - 1))]
    assert spans_of("mine.tunnel", hollow) == []

    fresh = mined(cells, open_sides=lambda x, y, z: 1 + (y == 64) + (z != 1))
    assert spans_of("mine.area_clear", fresh) == []
    assert len(spans_of("mine.tunnel", fresh)) == 1


def test_a_shaft_through_open_space_is_not_hollowing():
    cells = [(0, 63 - k, 0) for k in range(20)]
    s = mined(cells, feet=lambda x, y, z: (0.5, y + 1.0, 0.5), open_sides=3)
    assert spans_of("mine.area_clear", s) == []
    assert len(spans_of("mine.dig_down", s)) == 1


def test_vein_follows_adjacent_breaks_of_one_ore():
    ore = {(0, 64, 0), (1, 64, 0), (1, 65, 1)}
    cells = [(0, 64, 0), (1, 64, 0), (2, 64, 0), (1, 65, 1)]
    s = mined(cells, block=lambda *p: "deepslate_diamond_ore" if p in ore else "stone")
    assert spans_of("mine.vein", s) == [(7, 10 + 27)]
    assert detect("mine.vein", s)[0].confidence == 0.9

    mixed = mined(
        [(0, 64, 0), (1, 64, 0)],
        block=lambda x, y, z: "deepslate_diamond_ore" if x == 0 else "diamond_ore",
    )
    assert spans_of("mine.vein", mixed) == [(7, 19)]
    apart = mined([(0, 64, 0), (3, 64, 0)], block="diamond_ore")
    assert spans_of("mine.vein", apart) == []
    two_ores = mined([(0, 64, 0), (1, 64, 0)], block=lambda x, y, z: ["iron_ore", "gold_ore"][x])
    assert spans_of("mine.vein", two_ores) == []


def test_staircase_steps_one_block_per_slice():
    cells = [(k, 64 - k + dy, 0) for k in range(1, 7) for dy in (2, 1, 0)]
    s = mined(cells, feet=lambda x, y, z: (x - 0.5, 65.0 - x, 0.5))
    assert spans_of("mine.staircase", s) == [(7, 10 + 9 * 17)]
    assert spans_of("mine.tunnel", s) == []

    flat = mined(tunnel_cells(8, height=3))
    assert spans_of("mine.staircase", flat) == []


def test_staircase_needs_the_player_to_follow():
    cells = [(k, 64 - k + dy, 0) for k in range(1, 7) for dy in (2, 1, 0)]
    s = mined(cells, feet=lambda x, y, z: (0.5, 64.0, 0.5))
    assert spans_of("mine.staircase", s) == []


def test_dig_down_breaks_under_the_feet():
    cells = [(0, 63 - k, 0) for k in range(4)]
    s = mined(cells, feet=lambda x, y, z: (0.5, y + 1.0, 0.5))
    assert spans_of("mine.dig_down", s) == [(7, 10 + 27)]

    beside = mined(cells, feet=lambda x, y, z: (2.5, y + 1.0, 0.5))
    assert spans_of("mine.dig_down", beside) == []
    two = mined(cells[:2], feet=lambda x, y, z: (0.5, y + 1.0, 0.5))
    assert spans_of("mine.dig_down", two) == []


def test_dig_up_breaks_above_the_head():
    cells = [(0, 66 + k, 0) for k in range(4)]
    climb = [64.0] * 50 + [66.0] * 30
    s = seg(80, y=climb, events=dig(cells, feet=lambda x, y, z: (0.5, 64.0, 0.5)))
    assert spans_of("mine.dig_up", s) == [(7, 10 + 27)]
    assert detect("mine.dig_up", s)[0].confidence == 0.9
    assert spans_of("mine.dig_down", s) == []

    no_climb = mined(cells, feet=lambda x, y, z: (0.5, 64.0, 0.5))
    assert detect("mine.dig_up", no_climb)[0].confidence == 0.75


def test_dig_up_follows_a_wide_shaft_column_by_column():
    cells = [(x, y, z) for x, z in [(0, 0), (1, 0), (1, 1), (0, 1)] for y in (66, 67, 68)]
    s = mined(cells, feet=lambda x, y, z: (x + 0.5, 64.0, z + 0.5))
    assert spans_of("mine.dig_up", s) == [(7, 10 + 9 * 11)]


def test_dig_up_needs_breaks_over_the_head_that_climb():
    cells = [(0, 66 + k, 0) for k in range(4)]
    beside = mined(cells, feet=lambda x, y, z: (2.5, 64.0, 0.5))
    assert spans_of("mine.dig_up", beside) == []
    head = mined(cells, feet=lambda x, y, z: (0.5, y - 1.0, 0.5))  # head level, climbing
    assert spans_of("mine.dig_up", head) == []
    ceiling = mined([(x, 66, 0) for x in range(4)], feet=lambda x, y, z: (x + 0.5, 64.0, 0.5))
    assert spans_of("mine.dig_up", ceiling) == []
    two = mined(cells[:2], feet=lambda x, y, z: (0.5, 64.0, 0.5))
    assert spans_of("mine.dig_up", two) == []
    slow = mined(cells, period=80, feet=lambda x, y, z: (0.5, 64.0, 0.5))
    assert spans_of("mine.dig_up", slow) == []


def test_tree_is_a_vertical_cluster_of_logs():
    cells = [(5, 64 + k, 5) for k in range(5)] + [(5, 69, 6)]
    names = ["oak_log"] * 5 + ["oak_leaves"]
    s = seg(120, events=dig(cells, block=lambda x, y, z: names[cells.index((x, y, z))]))
    assert spans_of("mine.tree", s) == [(7, 10 + 36)]
    assert detect("mine.tree", s)[0].confidence == 0.95

    two_logs = mined(cells[:2], block="oak_log")
    assert spans_of("mine.tree", two_logs) == []
    spread = mined([(0, 64, 0), (9, 65, 0), (0, 66, 9)], block="birch_log")
    assert spans_of("mine.tree", spread) == []
    flat = mined([(0, 64, 0), (1, 64, 0), (2, 64, 0)], block="oak_log")
    assert spans_of("mine.tree", flat) == []


def test_falling_follows_refills_of_the_same_block():
    p = (0, 65, 1)
    events = dig([p, p, p], period=15, block="gravel")
    broken = [e for e in events if e["kind"] == "block_broken"]
    for e, r in zip(broken, ["gravel", "gravel", None], strict=True):
        e["refill"] = r
    events += dig([(9, 65, 9)], t0=100, block="sand", refill="sand")
    s = seg(140, events=events)
    assert spans_of("mine.falling", s) == [(7, 40), (97, 110)]
    assert [sp.confidence for sp in detect("mine.falling", s)] == [0.9, 0.75]
    assert spans_of("mine.falling", mined([p, p], block="gravel")) == []


def walking(n, events, *, moving=True):
    x = [0.5 + (0.2 * i if moving else 0.0) for i in range(n)]
    return seg(n, x=x, speed=4.0 if moving else 0.0, events=events)


def test_clear_path_is_an_isolated_break_while_traveling():
    one = dig([(41, 64, 0)], t0=200)
    assert spans_of("mine.clear_path", walking(400, one)) == [(197, 200)]
    assert spans_of("mine.clear_path", walking(400, one, moving=False)) == []
    near_edge = dig([(5, 64, 0)], t0=20)
    assert spans_of("mine.clear_path", walking(400, near_edge)) == []


def test_clear_path_ignores_mining_and_plants():
    busy = dig([(41, 64, 0)], t0=200) + dig([(42, 64, 0)], t0=260)
    assert spans_of("mine.clear_path", walking(400, busy)) == []
    many = dig([(41, 64, 0), (41, 65, 0), (42, 64, 0), (42, 65, 0)], t0=200)
    assert spans_of("mine.clear_path", walking(400, many)) == []
    grass = dig([(41, 64, 0)], t0=200, block="short_grass")
    assert spans_of("mine.clear_path", walking(400, grass)) == []


def test_spans_are_clamped_to_the_segment():
    events = [ev(0, "block_broken", x=1, y=64, z=0, block="stone", ticks_taken=5)]
    assert detect("mine.block", seg(10, events=events))[0].start_tick == t(0)
