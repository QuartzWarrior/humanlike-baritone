from labelkit import detect, ev, row, seg, spans_of

from hbpipe.label.detectors.camera import screen_shown


def look(yaw: list[float], pitch: list[float] | None = None) -> dict:
    """yaw/pitch per tick plus the derived rates (deg/s) the detectors read."""
    pitch = pitch if pitch is not None else [0.0] * len(yaw)
    rate = [0.0] + [(b - a) * 20.0 for a, b in zip(yaw, yaw[1:], strict=False)]
    prate = [0.0] + [(b - a) * 20.0 for a, b in zip(pitch, pitch[1:], strict=False)]
    return {"yaw": yaw, "pitch": pitch, "yaw_rate": rate, "pitch_rate": prate}


def ramp(n: int, start: int, ticks: int, total: float, base: float = 0.0) -> list[float]:
    """An angle that holds ``base``, turns by ``total`` over ``ticks`` ticks from row ``start``,
    then holds."""
    out = []
    for i in range(n):
        k = min(max(i - start + 1, 0), ticks)
        out.append(base + total * k / ticks)
    return out


def blocks_hit(*parts: tuple[int, tuple[int, int, int] | None]) -> dict:
    """Crosshair columns from (ticks, block pos or None for a miss) parts."""
    cols = {"hit_type": [], "hit_x": [], "hit_y": [], "hit_z": []}
    for n, pos in parts:
        cols["hit_type"] += ["block" if pos else "miss"] * n
        for k, axis in enumerate("xyz"):
            cols[f"hit_{axis}"] += [pos[k] if pos else None] * n
    return cols


A, B, C = (1, 64, 3), (2, 64, 3), (3, 64, 3)


def test_acquire_is_a_motion_ending_on_a_new_held_target():
    yaw = look(ramp(30, 10, 5, 10.0))  # turning on rows 10-14
    s = seg(30, **yaw, **blocks_hit((12, A), (18, B)))
    assert spans_of("look.acquire", s) == [(10, 14)]
    assert detect("look.acquire", s)[0].confidence == 0.6

    dug = seg(
        30,
        **yaw,
        **blocks_hit((12, A), (18, B)),
        events=[ev(16, "destroy_started", x=2, y=64, z=3)],
    )
    assert detect("look.acquire", dug)[0].confidence == 0.9


def test_acquire_needs_a_new_target_held_three_ticks():
    yaw = look(ramp(30, 10, 5, 10.0))
    same = seg(30, **yaw, **blocks_hit((30, A)))
    assert spans_of("look.acquire", same) == []
    brief = seg(30, **yaw, **blocks_hit((12, A), (2, B), (16, None)))
    assert spans_of("look.acquire", brief) == []
    nothing = seg(30, **yaw, **blocks_hit((12, A), (18, None)))
    assert spans_of("look.acquire", nothing) == []


def test_acquire_mid_sweep_needs_an_action_on_the_target():
    yaw = look(ramp(40, 10, 15, 15.0))  # a slow sweep, rows 10-24
    path = blocks_hit((12, A), (6, B), (22, C))  # B on rows 12-17, C from row 18
    dig_b = [ev(13, "destroy_started", x=2, y=64, z=3)]
    s = seg(40, **yaw, **path, events=dig_b)
    assert spans_of("look.acquire", s) == [(10, 17), (18, 24)]
    assert [sp.confidence for sp in detect("look.acquire", s)] == [0.8, 0.6]

    assert spans_of("look.acquire", seg(40, **yaw, **path)) == [(10, 24)]  # passed over B
    broke = dig_b + [ev(17, "block_broken", x=2, y=64, z=3)]
    assert spans_of("look.acquire", seg(40, **yaw, **path, events=broke)) == [(10, 17)]


def test_acquire_entity():
    yaw = look(ramp(30, 10, 5, 10.0))
    hit = {"hit_type": ["miss"] * 12 + ["entity"] * 18, "hit_entity_id": [None] * 12 + [7] * 18}
    s = seg(30, **yaw, **hit)
    assert detect("look.acquire", s)[0].confidence == 0.7
    hitting = seg(30, **yaw, **hit, events=[ev(17, "damage_dealt", entity_id=7)])
    assert detect("look.acquire", hitting)[0].confidence == 0.9


def test_travel_is_turning_while_moving_without_acquiring():
    yaw = look(ramp(40, 5, 11, 22.0))  # rows 5-15
    s = seg(40, **yaw, speed=4.3, k_forward=True)
    assert spans_of("look.travel", s) == [(5, 15)]
    assert detect("look.travel", s)[0].confidence == 0.85
    assert spans_of("look.travel", seg(40, **yaw)) == []  # standing
    assert spans_of("look.travel", seg(40, speed=4.3, k_forward=True)) == []  # no turning

    onto = seg(40, **yaw, speed=4.3, k_forward=True, **blocks_hit((10, A), (30, B)))
    assert spans_of("look.travel", onto) == []  # that motion is an acquisition


def test_idle_scan_is_looking_around_in_place_with_nothing_after():
    yaw = look(ramp(60, 10, 10, 20.0))  # rows 10-19, 20 degrees
    assert spans_of("look.idle_scan", seg(60, **yaw)) == [(10, 19)]

    click = [row(30, kind="button", button=0, down=True, in_gui=False)]
    assert spans_of("look.idle_scan", seg(60, **yaw, buttons=click)) == []
    assert spans_of("look.idle_scan", seg(60, **yaw, k_attack=[False] * 25 + [True] * 35)) == []
    assert spans_of("look.idle_scan", seg(60, **yaw, speed=4.3)) == []
    small = look(ramp(60, 10, 5, 5.0))
    assert spans_of("look.idle_scan", seg(60, **small)) == []


def zombie_at(n: int, dx: float, dz: float, start: int = 0, kind: str = "zombie") -> list[list]:
    mob = {"id": 7, "type": f"minecraft:{kind}", "dx": dx, "dy": 0.0, "dz": dz}
    return [[mob] if i >= start else [] for i in range(n)]


def test_react_to_damage_turns_toward_the_attacker():
    n = 40
    yaw = look(ramp(n, 14, 6, 90.0))  # rows 14-19 at 300 deg/s, to face west
    hurt = [ev(10, "damage_taken", damage_source="minecraft:mob_attack", entity_id=7)]
    s = seg(n, **yaw, entities=zombie_at(n, -5.0, 0.0), events=hurt)
    assert spans_of("look.react", s) == [(14, 19)]
    assert detect("look.react", s)[0].confidence == 0.9

    away = seg(n, **yaw, entities=zombie_at(n, 5.0, 0.0), events=hurt)  # attacker to the east
    assert detect("look.react", away)[0].confidence == 0.5


def test_react_needs_a_fast_large_turn_soon_after():
    n = 40
    hurt = [ev(10, "damage_taken", damage_source="minecraft:mob_attack", entity_id=7)]
    late = look(ramp(n, 22, 6, 90.0))
    assert spans_of("look.react", seg(n, **late, events=hurt)) == []
    slow = look(ramp(n, 12, 20, 40.0))  # 40 deg/s
    assert spans_of("look.react", seg(n, **slow, events=hurt)) == []
    small = look(ramp(n, 12, 2, 20.0))
    assert spans_of("look.react", seg(n, **small, events=hurt)) == []
    snap = look(ramp(n, 14, 6, 90.0))
    assert spans_of("look.react", seg(n, **snap)) == []
    fall = [ev(10, "damage_taken", damage_source="minecraft:fall")]
    assert spans_of("look.react", seg(n, **snap, events=fall)) == []


def test_react_to_sounds_but_not_the_players_own():
    n = 40
    snap = look(ramp(n, 14, 6, 90.0))
    growl = [ev(12, "sound_heard", category="hostile", dx=-8.0, dy=-1.0, dz=0.5)]
    s = seg(n, **snap, events=growl)
    assert spans_of("look.react", s) == [(14, 19)]
    assert detect("look.react", s)[0].confidence == 0.9

    own = [ev(12, "sound_heard", category="player", dx=0.05, dy=-1.62, dz=-0.3)]
    assert spans_of("look.react", seg(n, **snap, events=own)) == []
    far_cow = [ev(12, "sound_heard", category="neutral", dx=-9.0, dy=0.0, dz=0.0)]
    assert spans_of("look.react", seg(n, **snap, events=far_cow)) == []
    near_cow = [ev(12, "sound_heard", category="neutral", dx=-2.0, dy=0.0, dz=0.0)]
    assert spans_of("look.react", seg(n, **snap, events=near_cow)) == [(14, 19)]


def test_react_to_a_hostile_mob_entering_view():
    n = 40
    # Facing south; the mob appears 40 degrees to the right (west of south) on row 10.
    snap = look(ramp(n, 13, 3, 40.0))
    s = seg(n, **snap, entities=zombie_at(n, -3.2, 3.8, start=10))
    assert spans_of("look.react", s) == [(13, 15)]
    assert detect("look.react", s)[0].confidence == 0.9

    villager = seg(n, **snap, entities=zombie_at(n, -3.2, 3.8, start=10, kind="villager"))
    assert spans_of("look.react", villager) == []
    behind = seg(n, **snap, entities=zombie_at(n, 3.2, -3.8, start=10))
    assert spans_of("look.react", behind) == []
    always = seg(n, **snap, entities=zombie_at(n, -3.2, 3.8))  # in view all along
    assert spans_of("look.react", always) == []


def test_screen_shown_ends_at_screen_closed():
    inv = "net.minecraft.client.gui.screens.inventory.InventoryScreen"
    chest = "net.minecraft.client.gui.screens.inventory.ContainerScreen"
    # The cleaned screen column keeps its last value; the events say when it really closed.
    screens = [None] * 5 + [inv] * 15 + [chest] * 10
    events = [ev(5, "screen_opened", screen=inv), ev(10, "screen_closed", screen=inv)]
    shown = screen_shown(seg(30, screen=screens, events=events))
    assert shown.tolist() == [False] * 5 + [True] * 5 + [False] * 10 + [True] * 10

    yaw = look(ramp(60, 30, 10, 20.0))
    stale = seg(60, **yaw, screen=[inv] * 60, events=[ev(5, "screen_closed", screen=inv)])
    assert spans_of("look.idle_scan", stale) == [(30, 39)]
    assert spans_of("look.idle_scan", seg(60, **yaw, screen=[inv] * 60)) == []
