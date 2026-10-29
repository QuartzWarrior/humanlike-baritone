from labelkit import detect, ev, seg, spans_of

from hbpipe.label.base import REGISTRY
from hbpipe.label.detectors.combat import is_hostile


def mob(entity_id: int, kind: str, *, dx: float = 0.0, dz: float = 0.0) -> dict:
    return {
        "id": entity_id,
        "type": f"minecraft:{kind}",
        "dx": dx,
        "dy": 0.0,
        "dz": dz,
        "vx": 0.0,
        "vy": 0.0,
        "vz": 0.0,
        "health": 20.0,
        "yaw": 0.0,
    }


def item(item_id: str) -> dict:
    return {"id": f"minecraft:{item_id}", "count": 1, "damage": 0, "max_damage": 0}


def hit(tick: int, entity_id: int = 7, kind: str = "zombie") -> dict:
    return ev(tick, "damage_dealt", entity_id=entity_id, entity_type=kind, amount=4.0)


def test_hostile_set_takes_both_id_forms():
    assert is_hostile("zombie") and is_hostile("minecraft:creeper")
    assert not is_hostile("minecraft:cow") and not is_hostile(None)


def test_melee_groups_hits_on_one_target():
    s = seg(100, events=[hit(20), hit(35), hit(50)])
    assert spans_of("combat.melee", s) == [(10, 60)]
    assert detect("combat.melee", s)[0].confidence == 0.9


def test_melee_splits_slow_hits_and_merges_overlapping_targets():
    slow = seg(200, events=[hit(20), hit(120)])
    assert spans_of("combat.melee", slow) == [(10, 30), (110, 130)]
    two = seg(100, events=[hit(20), hit(30, entity_id=8, kind="skeleton")])
    assert spans_of("combat.melee", two) == [(10, 40)]


def test_melee_counts_attack_entity_and_passive_targets_lower():
    s = seg(
        60,
        events=[ev(20, "interaction", interaction="attack_entity", entity_id=3, entity_type="cow")],
    )
    assert spans_of("combat.melee", s) == [(10, 30)]
    assert detect("combat.melee", s)[0].confidence == 0.75


def test_melee_ignores_hits_on_things():
    s = seg(60, events=[hit(20, 9, "falling_block"), hit(30, 10, "item"), hit(40, 11, "oak_boat")])
    assert spans_of("combat.melee", s) == []


def test_ranged_is_a_held_draw_with_a_bow():
    using = [False] * 10 + [True] * 20 + [False] * 10
    bow = seg(40, using_item=using, k_use=using, held_item="bow", main_hand=item("bow"))
    assert spans_of("combat.ranged", bow) == [(10, 29)]
    assert detect("combat.ranged", bow)[0].confidence == 0.9

    off = seg(40, using_item=using, held_item="diamond_pickaxe", off_hand=item("crossbow"))
    assert spans_of("combat.ranged", off) == [(10, 29)]

    food = seg(40, using_item=using, held_item="bread", off_hand=item("bow"))
    assert spans_of("combat.ranged", food) == []  # eating from the main hand
    tap = seg(40, using_item=[False] * 10 + [True] * 2 + [False] * 28, held_item="bow")
    assert spans_of("combat.ranged", tap) == []


def test_ranged_draw_cut_by_the_segment_is_less_sure():
    s = seg(30, using_item=[False] * 10 + [True] * 20, k_use=True, held_item="trident")
    assert [sp.confidence for sp in detect("combat.ranged", s)] == [0.6]


def test_loaded_crossbow_shot_without_a_draw():
    s = seg(
        40,
        held_item="crossbow",
        events=[ev(20, "interaction", interaction="use_item", item_id="crossbow", hand="main")],
    )
    assert spans_of("combat.ranged", s) == [(18, 23)]


def test_shield_needs_a_threat_near():
    blocking = [False] * 10 + [True] * 20 + [False] * 10
    near = seg(40, blocking=blocking, entities=[mob(7, "zombie", dz=4.0)])
    assert spans_of("combat.shield", near) == [(10, 29)]
    assert spans_of("combat.shield", seg(40, blocking=blocking)) == []
    far = seg(40, blocking=blocking, entities=[mob(7, "zombie", dz=14.0)])
    assert spans_of("combat.shield", far) == []
    cow = seg(40, blocking=blocking, entities=[mob(7, "cow", dz=3.0)])
    assert spans_of("combat.shield", cow) == []


def test_shield_raised_counts_and_a_neutral_attacker_is_a_threat():
    using = [False] * 10 + [True] * 4 + [False] * 26
    wolf = seg(
        40,
        using_item=using,
        held_item="iron_sword",
        off_hand=item("shield"),
        entities=[mob(5, "wolf", dx=3.0)],
        events=[ev(5, "damage_taken", entity_id=5, entity_type="wolf", amount=2.0)],
    )
    assert spans_of("combat.shield", wolf) == [(10, 13)]
    assert detect("combat.shield", wolf)[0].confidence == 0.8


def backing_off(n: int, start: int, *, keys: bool = True) -> dict:
    """Facing a zombie 2 blocks south (+z) and backing north from row ``start``."""
    moving = [False] * start + [True] * (n - start)
    return {
        "k_back": moving if keys else [False] * n,
        "vz": [-0.2 if m else 0.0 for m in moving],
        "entities": [mob(7, "zombie", dz=2.0)],
    }


def test_retreat_backs_away_after_damage():
    hurt = [ev(10, "damage_taken", entity_id=7, entity_type="zombie", amount=3.0)]
    s = seg(40, events=hurt, **backing_off(40, 12))
    assert spans_of("combat.retreat", s) == [(12, 39)]
    assert detect("combat.retreat", s)[0].confidence == 0.85


def test_retreat_needs_damage_from_someone_and_keys():
    assert spans_of("combat.retreat", seg(40, **backing_off(40, 12))) == []
    fall = [ev(10, "damage_taken", entity_id=-1, damage_source="minecraft:fall", amount=3.0)]
    assert spans_of("combat.retreat", seg(40, events=fall, **backing_off(40, 12))) == []
    hurt = [ev(10, "damage_taken", entity_id=7, entity_type="zombie", amount=3.0)]
    knockback = seg(40, events=hurt, **backing_off(40, 12, keys=False))
    assert spans_of("combat.retreat", knockback) == []


def test_retreat_window_ends():
    hurt = [ev(10, "damage_taken", entity_id=7, entity_type="zombie", amount=3.0)]
    s = seg(200, events=hurt, **backing_off(200, 150))
    assert spans_of("combat.retreat", s) == []


def test_retreat_includes_eating_near_the_attacker():
    hurt = [ev(5, "damage_taken", entity_id=7, entity_type="zombie", amount=3.0)]
    using = [False] * 10 + [True] * 32 + [False] * 8
    s = seg(
        50,
        events=hurt,
        using_item=using,
        held_item="cooked_beef",
        entities=[mob(7, "zombie", dz=5.0)],
    )
    assert spans_of("combat.retreat", s) == [(10, 41)]
    assert detect("combat.retreat", s)[0].confidence == 0.7


def test_every_combat_detector_handles_a_bare_segment():
    names = [n for n in REGISTRY if n.startswith("combat.")]
    assert names
    for name in names:
        assert detect(name, seg(1)) == []
        assert detect(name, seg(30)) == []
