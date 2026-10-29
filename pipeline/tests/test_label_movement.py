from labelkit import concat, seg, spans_of


def jumps(n_jumps: int, *, period: int = 12, air: int = 10, head_hit: bool = False) -> dict:
    """Per-tick columns for sprint-jumping: each jump is a takeoff tick then ``air`` ticks up
    and down, then ground until the next."""
    cols = {"on_ground": [], "vspeed": [], "head_hit": [], "y": []}
    for _ in range(n_jumps):
        for k in range(period):
            airborne = 1 <= k <= air
            cols["on_ground"].append(not airborne)
            cols["vspeed"].append(8.4 if k == 1 else (2.0 if airborne and k < air // 2 else -2.0))
            cols["head_hit"].append(head_hit and k == 3)
            cols["y"].append(64.0 + (0.6 if airborne else 0.0))
    return cols


def test_walk_needs_a_move_key_and_walking_speed():
    s = seg(
        40,
        k_forward=[True] * 30 + [False] * 10,
        speed=[4.3] * 30 + [0.0] * 10,
    )
    assert spans_of("move.walk", s) == [(0, 29)]
    assert spans_of("move.walk", seg(40, k_forward=True, speed=4.3, sprinting=True)) == []
    assert spans_of("move.walk", seg(40, k_forward=True, speed=0.5)) == []  # pushing a wall
    assert spans_of("move.walk", seg(3, k_forward=True, speed=4.3)) == []  # too short
    assert spans_of("move.walk", seg(6, k_forward=True, speed=2.5)) == [(0, 5)]  # a step


def test_walk_bridges_short_dips():
    speed = [4.3] * 15 + [1.0] * 3 + [4.3] * 15
    assert spans_of("move.walk", seg(33, k_forward=True, speed=speed)) == [(0, 32)]


def test_sprint_is_on_the_ground():
    s = seg(30, sprinting=True, on_ground=[True] * 20 + [False] * 10, speed=5.6)
    assert spans_of("move.sprint", s) == [(0, 19)]
    assert spans_of("move.sprint", seg(30, sprinting=True, in_water=True)) == []


def test_sprint_jump_in_the_open_vs_head_hitter_under_a_ceiling():
    open_air = seg(48, sprinting=True, headroom=3, **jumps(4))
    assert spans_of("move.sprint_jump", open_air) == [(0, 47)]
    assert spans_of("move.head_hitter", open_air) == []

    tunnel = seg(48, sprinting=True, headroom=0, **jumps(4, head_hit=True))
    assert spans_of("move.head_hitter", tunnel) == [(0, 47)]
    assert spans_of("move.sprint_jump", tunnel) == []


def test_one_jump_is_not_sprint_jumping():
    flat = {"on_ground": [True] * 12, "vspeed": [0.0] * 12, "head_hit": [False] * 12}
    single = seg(24, sprinting=True, **concat(jumps(1), flat | {"y": [64.0] * 12}))
    assert spans_of("move.sprint_jump", single) == []
    assert spans_of("move.sprint_jump", seg(24, sprinting=True, **jumps(2))) == [(0, 23)]


def test_strafe_is_one_side_key_while_moving():
    s = seg(20, k_left=[True] * 10 + [False] * 10, speed=[3.0] * 20)
    assert spans_of("move.strafe", s) == [(0, 9)]
    both = seg(20, k_left=True, k_right=True, speed=3.0)
    assert spans_of("move.strafe", both) == []


def test_sneak_needs_movement():
    s = seg(20, crouching=True, speed=[1.3] * 12 + [0.0] * 8)
    assert spans_of("move.sneak", s) == [(0, 11)]


def test_step_up_onto_a_block_but_not_a_jump_in_place():
    # Walk east, hop up one block over 6 ticks, keep walking.
    n = 30
    x = [0.5 + 0.2 * i for i in range(n)]
    y = [64.0] * 10 + [64.4, 64.8, 65.1, 65.2, 65.1] + [65.0] * 15
    ground = [True] * 10 + [False] * 5 + [True] * 15
    s = seg(n, x=x, y=y, on_ground=ground, speed=4.0)
    assert spans_of("move.step_up", s) == [(9, 15)]

    in_place = seg(n, y=y, on_ground=ground)
    assert spans_of("move.step_up", in_place) == []


def test_stairs_merge_into_one_step_up():
    n = 40
    x = [0.5 + 0.25 * i for i in range(n)]
    y = [64.0 + 0.5 * (i // 5) for i in range(n)]  # half a block every 5 ticks
    s = seg(n, x=x, y=y, speed=5.0)
    assert spans_of("move.step_up", s) == [(4, 35)]


def test_parkour_needs_a_real_gap():
    n = 20
    x = [0.5 + 0.3 * i for i in range(n)]
    ground = [True] * 5 + [False] * 9 + [True] * 6
    vs = [0.0] * 5 + [8.4] + [2.0] * 4 + [-3.0] * 4 + [0.0] * 6
    # A jump off a floor at y 63 that rises 1.2 blocks and comes back down.
    y = [64.0] * 5 + [64.4, 64.8, 65.1, 65.2, 65.2, 65.1, 64.8, 64.4, 64.1] + [64.0] * 6
    flat = [0] * 5 + [int(h - 64) + 1 for h in y[5:14]] + [0] * 6  # air down to the floor
    gap = [0] * 5 + [d + 2 for d in flat[5:14]] + [0] * 6  # and two more blocks of air below
    common = {"x": x, "y": y, "on_ground": ground, "vspeed": vs, "sprinting": True}
    assert spans_of("move.parkour", seg(n, ground_depth=gap, **common)) == [(4, 14)]
    # Mid-air over flat ground the blocks under the feet are air too; that's no gap.
    assert spans_of("move.parkour", seg(n, ground_depth=flat, **common)) == []
    # A hop down a block over a dip two below the top but one below the bottom: a bumpy slope.
    down = {**common, "y": y[:14] + [63.0] * 6}
    assert spans_of("move.parkour", seg(n, ground_depth=gap, **down)) == []


def test_climb_swim_glide_ride_follow_their_flags():
    s = seg(20, on_climbable=[True] * 10 + [False] * 10, vspeed=2.4)
    assert spans_of("move.climb", s) == [(0, 9)]
    assert spans_of("move.swim", seg(20, in_water=[False] * 5 + [True] * 15)) == [(5, 19)]
    assert spans_of("move.glide", seg(20, gliding=True)) == [(0, 19)]
    assert spans_of("move.ride", seg(20, passenger=[True] * 4 + [False] * 16)) == []


def test_fall_covers_the_drop_through_landing():
    n = 30
    ground = [True] * 5 + [False] * 15 + [True] * 10
    fd = [0.0] * 5 + [0.4 * i for i in range(15)] + [0.0] * 10
    assert spans_of("move.fall", seg(n, on_ground=ground, fall_distance=fd)) == [(5, 20)]
    short = [0.0] * 5 + [0.2 * i for i in range(15)] + [0.0] * 10  # 2.8 blocks
    assert spans_of("move.fall", seg(n, on_ground=ground, fall_distance=short)) == []


def test_head_hitter_with_bonks_on_the_takeoff_tick():
    # A 2-high tunnel: the ceiling stops each jump at once, so no tick shows upward speed;
    # only the held jump key and the bonk mark the jumps.
    ground = ([True] * 2 + [False] * 2) * 10
    s = seg(
        40,
        sprinting=True,
        headroom=0,
        on_ground=ground,
        vspeed=0.0,
        k_jump=[not g for g in ground],
        head_hit=[not g for g in ground],
    )
    assert spans_of("move.head_hitter", s) == [(1, 39)]
    assert spans_of("move.sprint_jump", s) == []
