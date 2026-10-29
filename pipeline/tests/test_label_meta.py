from labelkit import detect, ev, row, seg, spans_of, t

from hbpipe.label.base import Span


def act(start: int, end: int, label: str = "act.tunnel", confidence: float = 0.9) -> Span:
    return Span(t(start), t(end), label, confidence)


def press(tick: int, button: int) -> dict:
    return row(tick, kind="button", button=button, down=True, in_gui=False)


def click(tick: int, button: int, held: int = 1) -> list[dict]:
    return [press(tick, button), row(tick + held, kind="button", button=button, down=False)]


def wheel(*ticks: int) -> list[dict]:
    return [row(k, kind="scroll", scroll_dx=0.0, scroll_dy=-1.0) for k in ticks]


def sneak_key(tick: int, down: bool) -> dict:
    return row(tick, key=340, scancode=42, down=down, binding="key.sneak")


def test_hesitate_is_a_short_input_gap_in_active_play():
    walk = [True] * 30 + [False] * 20 + [True] * 50
    s = seg(100, k_forward=walk, labels=[act(0, 99)])
    assert spans_of("meta.hesitate", s) == [(30, 49)]
    assert detect("meta.hesitate", s)[0].confidence == 0.8

    # Outside a recognized activity it's still a hesitation, with less confidence.
    outside = seg(100, k_forward=walk, labels=[act(0, 39)])
    assert spans_of("meta.hesitate", outside) == [(30, 49)]
    assert detect("meta.hesitate", outside)[0].confidence == 0.7
    long = [True] * 10 + [False] * 80 + [True] * 10
    assert spans_of("meta.hesitate", seg(100, k_forward=long, labels=[act(0, 99)])) == []
    short = [True] * 30 + [False] * 4 + [True] * 66
    assert spans_of("meta.hesitate", seg(100, k_forward=short, labels=[act(0, 99)])) == []
    edge = [False] * 20 + [True] * 80  # the gap may have begun before the segment
    assert spans_of("meta.hesitate", seg(100, k_forward=edge, labels=[act(0, 99)])) == []


def test_hesitate_counts_mouse_and_key_changes_but_not_key_repeat():
    walk = [True] * 30 + [False] * 20 + [True] * 50
    nudge = seg(100, k_forward=walk, labels=[act(0, 99)], mouse=[row(40, dx=3.0, dy=0.0)])
    assert spans_of("meta.hesitate", nudge) == [(30, 39), (41, 49)]
    tremor = seg(100, k_forward=walk, labels=[act(0, 99)], mouse=[row(40, dx=1.0, dy=0.0)])
    assert spans_of("meta.hesitate", tremor) == [(30, 49)]

    repeat = [sneak_key(10, True), sneak_key(35, True), sneak_key(40, True)]
    s = seg(100, k_forward=walk, labels=[act(0, 99)], keys=repeat)
    assert spans_of("meta.hesitate", s) == [(30, 49)]
    released = repeat + [sneak_key(42, False)]
    s = seg(100, k_forward=walk, labels=[act(0, 99)], keys=released)
    assert spans_of("meta.hesitate", s) == [(30, 41), (43, 49)]


def test_fidget_crouch_spam_without_world_effect():
    crouch = [False] * 10 + ([True] * 4 + [False] * 4) * 4 + [False] * 18
    s = seg(60, crouching=crouch)
    assert spans_of("meta.fidget", s) == [(10, 38)]
    assert detect("meta.fidget", s)[0].confidence == 0.85

    bridging = seg(60, crouching=crouch, events=[ev(20, "block_placed", x=0, y=63, z=1)])
    assert spans_of("meta.fidget", bridging) == []
    slow = [False] * 10 + ([True] * 4 + [False] * 26) * 3 + [False] * 20
    assert spans_of("meta.fidget", seg(120, crouching=slow)) == []


def test_fidget_aimless_hotbar_scrolling():
    there_and_back = [0] * 10 + [1] * 3 + [2] * 3 + [1] * 3 + [0] * 21
    s = seg(40, slot=there_and_back)
    assert spans_of("meta.fidget", s) == [(10, 19)]
    assert detect("meta.fidget", s)[0].confidence == 0.75

    onward = [0] * 10 + [1] * 3 + [2] * 3 + [3] * 3 + [4] * 21
    assert detect("meta.fidget", seg(40, slot=onward))[0].confidence == 0.6
    assert spans_of("meta.fidget", seg(40, slot=onward, buttons=[press(25, 1)])) == []
    few = [0] * 10 + [1] * 3 + [2] * 27
    assert spans_of("meta.fidget", seg(40, slot=few)) == []


def wiggle_yaw(n: int, start: int, swings: int, per: int = 3, step: float = 2.0) -> list[float]:
    yaw, a = [], 0.0
    for i in range(n):
        k = i - start
        if 0 <= k < swings * per:
            a += step if (k // per) % 2 == 0 else -step
        yaw.append(a)
    return yaw


def look(yaw: list[float]) -> dict:
    rate = [0.0] + [(b - a) * 20.0 for a, b in zip(yaw, yaw[1:], strict=False)]
    return {"yaw": yaw, "yaw_rate": rate}


def test_fidget_camera_wiggle():
    yaw = look(wiggle_yaw(50, 10, 6))  # rows 10-27, reversing every 3 ticks
    s = seg(50, **yaw)
    assert spans_of("meta.fidget", s) == [(10, 27)]
    assert spans_of("meta.fidget", seg(50, **yaw, k_attack=True)) == []
    one_way = look([0.0] * 10 + [2.0 * i for i in range(1, 19)] + [36.0] * 22)
    assert spans_of("meta.fidget", seg(50, **one_way)) == []
    glances = look(wiggle_yaw(50, 10, 4, per=4, step=10.0))  # 40-degree swings
    assert spans_of("meta.fidget", seg(50, **glances)) == []


ON_BLOCK = {"hit_type": "block", "hit_x": 1, "hit_y": 64, "hit_z": 1, "hit_block": "stone"}


def test_mistake_wrong_slot_then_switch_before_use():
    picks = [0] * 10 + [3] * 8 + [5] * 22
    s = seg(40, slot=picks, buttons=[press(22, 1)], **ON_BLOCK)
    assert spans_of("meta.mistake", s) == [(10, 18)]
    assert detect("meta.mistake", s)[0].confidence == 0.75

    assert spans_of("meta.mistake", seg(40, slot=picks, **ON_BLOCK)) == []  # nothing used
    used = seg(40, slot=picks, buttons=[press(12, 1), press(22, 1)], **ON_BLOCK)
    assert spans_of("meta.mistake", used) == []
    onward = [0] * 10 + [1] * 6 + [2] * 24
    scrolled = seg(40, slot=onward, buttons=[press(20, 1), *wheel(10, 16)], **ON_BLOCK)
    assert spans_of("meta.mistake", scrolled) == []  # scrolled on past slot 1
    keyed = seg(40, slot=onward, buttons=[press(20, 1)], **ON_BLOCK)
    assert spans_of("meta.mistake", keyed) == [(10, 16)]  # pressed 2, then 3
    back = [0] * 10 + [1] * 2 + [2] * 6 + [1] * 22
    overshot = seg(40, slot=back, buttons=[press(24, 1), *wheel(10, 12, 18)], **ON_BLOCK)
    assert spans_of("meta.mistake", overshot) == [(12, 18)]
    assert detect("meta.mistake", overshot)[0].confidence == 0.75


def test_mistake_missed_attack():
    s = seg(30, buttons=click(10, 0))
    assert spans_of("meta.mistake", s) == [(10, 11)]
    assert detect("meta.mistake", s)[0].confidence == 0.7
    on_block = seg(30, buttons=click(10, 0), hit_type=["block"] * 30, hit_x=1, hit_y=64, hit_z=1)
    assert spans_of("meta.mistake", on_block) == []
    # Held from the air onto a block, which it starts digging.
    swept = {"hit_type": ["miss"] * 14 + ["block"] * 16, "hit_x": 1, "hit_y": 64, "hit_z": 1}
    dig = [ev(14, "interaction", interaction="attack_block", x=1, y=64, z=1)]
    assert spans_of("meta.mistake", seg(30, buttons=click(10, 0, 8), events=dig, **swept)) == []


def test_mistake_use_with_no_effect():
    s = seg(30, buttons=click(10, 1))
    assert spans_of("meta.mistake", s) == [(10, 11)]
    eating = seg(30, buttons=click(10, 1), using_item=[False] * 11 + [True] * 19)
    assert spans_of("meta.mistake", eating) == []

    stone = {"hit_type": "block", "hit_x": 1, "hit_y": 64, "hit_z": 1, "held_item": "cobblestone"}
    earlier = [ev(2, "block_placed", x=0, y=64, z=2, block="cobblestone")]
    failed = seg(30, buttons=click(10, 1), events=earlier, hit_block="stone", **stone)
    assert spans_of("meta.mistake", failed) == [(10, 11)]
    placed = earlier + [ev(11, "block_placed", x=1, y=65, z=1, block="cobblestone")]
    ok = seg(30, buttons=click(10, 1), events=placed, hit_block="stone", **stone)
    assert spans_of("meta.mistake", ok) == []
    door = seg(30, buttons=click(10, 1), events=earlier, hit_block="oak_door", **stone)
    assert spans_of("meta.mistake", door) == []
    # Use held from the ground before a pillar jump; the block goes down 10 ticks later.
    jump = earlier + [ev(20, "block_placed", x=1, y=65, z=1, block="cobblestone")]
    held = seg(30, buttons=click(10, 1, 12), events=jump, hit_block="stone", **stone)
    assert spans_of("meta.mistake", held) == []
    # The same hold recorded as a press every 4 ticks (the game's use repeat).
    train = [r for k in (10, 14, 18, 22) for r in click(k, 1, 2)]
    repeat = seg(30, buttons=train, events=jump, hit_block="stone", **stone)
    assert spans_of("meta.mistake", repeat) == []
    no_jump = seg(30, buttons=train, events=earlier, hit_block="stone", **stone)
    assert spans_of("meta.mistake", no_jump) == [(10, 24)]


def walk_back(back_ticks: int) -> dict:
    """East for 20 ticks at 4.3 m/s, stop for 5, then west for ``back_ticks``."""
    n = 50
    step = 0.215
    x, speed, fwd, back = [], [], [], []
    pos = 0.5
    for i in range(n):
        moving_east = i < 20
        moving_west = 25 <= i < 25 + back_ticks
        pos += step if moving_east else (-step if moving_west else 0.0)
        x.append(pos)
        speed.append(4.3 if moving_east or moving_west else 0.0)
        fwd.append(moving_east)
        back.append(moving_west)
    return {"x": x, "speed": speed, "k_forward": fwd, "k_back": back}


def test_mistake_overshoot_walked_back():
    s = seg(50, **walk_back(5))
    assert spans_of("meta.mistake", s) == [(9, 29)]
    placed = seg(50, **walk_back(5), events=[ev(22, "block_placed", x=5, y=64, z=0)])
    assert spans_of("meta.mistake", placed) == []
    assert spans_of("meta.mistake", seg(50, **walk_back(15))) == []  # walked 3 blocks back


def test_mistake_placed_block_broken_again():
    pos = {"x": 1, "y": 64, "z": 0}
    s = seg(100, events=[ev(5, "block_placed", **pos), ev(30, "block_broken", **pos)])
    assert spans_of("meta.mistake", s) == [(5, 30)]
    late = seg(100, events=[ev(5, "block_placed", **pos), ev(80, "block_broken", **pos)])
    assert spans_of("meta.mistake", late) == []
    other = seg(100, events=[ev(5, "block_placed", **pos), ev(30, "block_broken", x=2, y=64, z=0)])
    assert spans_of("meta.mistake", other) == []


def test_switch_is_the_boundary_between_activities():
    two = [act(0, 99, "act.travel", 0.9), act(100, 299, "act.strip_mine", 0.8)]
    s = seg(300, labels=two)
    assert spans_of("meta.switch", s) == [(80, 119)]
    assert detect("meta.switch", s)[0].confidence == 0.8

    same = [act(0, 99, "act.travel"), act(100, 299, "act.travel")]
    assert spans_of("meta.switch", seg(300, labels=same)) == []
    assert spans_of("meta.switch", seg(300, labels=two[:1])) == []

    gap = [act(0, 99, "act.travel", 0.9), act(150, 299, "act.strip_mine", 0.8)]
    s = seg(300, labels=gap)
    assert spans_of("meta.switch", s) == [(80, 169)]
    assert detect("meta.switch", s)[0].confidence == 0.68
    far = [act(0, 99, "act.travel"), act(400, 599, "act.strip_mine")]
    assert spans_of("meta.switch", seg(600, labels=far)) == []


def test_switch_with_overlapping_activities_follows_the_more_confident():
    overlap = [act(0, 120, "act.travel", 0.6), act(100, 299, "act.strip_mine", 0.9)]
    s = seg(300, labels=overlap)
    assert spans_of("meta.switch", s) == [(80, 119)]
    assert detect("meta.switch", s)[0].confidence == 0.6
