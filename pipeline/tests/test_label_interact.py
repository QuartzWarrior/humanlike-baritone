from labelkit import detect, ev, row, seg, spans_of

from hbpipe.label import detectors  # noqa: F401  (registers every family)
from hbpipe.label.base import REGISTRY


def item(item_id: str) -> dict:
    return {"id": f"minecraft:{item_id}", "count": 1, "damage": 0, "max_damage": 0}


def use(
    tick: int,
    kind: str,
    *,
    item_id: str | None = None,
    block: str | None = None,
    entity: str | None = None,
    hand: str = "main",
) -> dict:
    return ev(
        tick,
        "interaction",
        interaction=kind,
        item_id=item_id,
        block=block,
        entity_type=entity,
        hand=hand,
    )


def test_mechanism_is_a_use_on_a_door_or_lever():
    s = seg(
        60, events=[use(20, "use_block", block="oak_door"), use(45, "use_block", block="lever")]
    )
    assert spans_of("use.mechanism", s) == [(18, 23), (43, 48)]
    assert spans_of("use.mechanism", seg(60, events=[use(20, "use_block", block="stone")])) == []


def test_mechanism_skips_placements_and_sneak_bypass():
    placed = seg(
        40,
        held_item="cobblestone",
        events=[
            use(20, "use_block", item_id="cobblestone", block="oak_trapdoor"),
            ev(20, "block_placed", block="cobblestone", x=0, y=64, z=1),
        ],
    )
    assert spans_of("use.mechanism", placed) == []
    sneaking = seg(
        40, crouching=True, held_item="torch", events=[use(20, "use_block", block="oak_door")]
    )
    assert spans_of("use.mechanism", sneaking) == []
    empty_handed = seg(40, crouching=True, events=[use(20, "use_block", block="oak_door")])
    assert spans_of("use.mechanism", empty_handed) == [(18, 23)]


def test_mechanism_pressure_plate_is_unsure():
    s = seg(40, events=[use(20, "use_block", block="stone_pressure_plate")])
    assert [sp.confidence for sp in detect("use.mechanism", s)] == [0.5]


def test_bucket_scoop_and_pour():
    held = ["bucket"] * 21 + ["water_bucket"] * 19
    s = seg(
        40,
        held_item=held,
        events=[
            use(20, "use_block", item_id="bucket", block="water"),
            use(20, "use_item", item_id="bucket"),
        ],
    )
    assert spans_of("use.bucket", s) == [(17, 25)]
    assert detect("use.bucket", s)[0].confidence == 0.9


def test_bucket_that_did_nothing_and_milk():
    dud = seg(40, held_item="bucket", events=[use(20, "use_item", item_id="bucket")])
    assert [sp.confidence for sp in detect("use.bucket", dud)] == [0.6]
    milk = seg(40, held_item="milk_bucket", events=[use(20, "use_item", item_id="milk_bucket")])
    assert spans_of("use.bucket", milk) == []


def test_farm_till_plant_harvest_breed():
    s = seg(
        300,
        events=[
            use(10, "use_block", item_id="iron_hoe", block="grass_block"),
            ev(20, "block_placed", block="wheat", x=0, y=64, z=1),
            ev(100, "block_broken", block="carrots", x=0, y=64, z=2),
            use(200, "use_entity", item_id="wheat", entity="cow"),
            ev(250, "damage_dealt", entity_type="sheep", amount=7.0),
            ev(280, "damage_dealt", entity_type="zombie", amount=7.0),  # a fight, not farming
        ],
    )
    assert spans_of("use.farm", s) == [(7, 25), (97, 105), (197, 205), (247, 255)]


def test_farm_ignores_other_uses_and_breaks():
    s = seg(
        100,
        events=[
            use(10, "use_entity", item_id="stick", entity="cow"),
            use(20, "use_block", item_id="iron_hoe", block="stone"),
            ev(40, "block_broken", block="farmland", x=0, y=63, z=1),
            ev(60, "block_broken", block="stone", x=0, y=64, z=1),
        ],
    )
    assert spans_of("use.farm", s) == []


def test_farm_path_crops_are_less_sure():
    s = seg(40, events=[ev(20, "block_broken", block="sugar_cane", x=0, y=64, z=1)])
    assert [sp.confidence for sp in detect("use.farm", s)] == [0.75]


def test_trade_is_a_merchant_screen_from_the_click():
    trader = "net.minecraft.client.gui.screens.inventory.MerchantScreen"
    s = seg(
        100,
        events=[
            use(15, "use_entity", item_id="emerald", entity="villager"),
            ev(18, "screen_opened", screen=trader, menu="minecraft:merchant"),
            ev(80, "screen_closed", screen=trader),
        ],
        gui=[row(40, kind="slot", slot_id=2, click_type="pickup", menu="minecraft:merchant")],
    )
    assert spans_of("use.trade", s) == [(15, 79)]
    assert detect("use.trade", s)[0].confidence == 0.95
    chest = "net.minecraft.client.gui.screens.inventory.ContainerScreen"
    other = seg(
        100,
        events=[
            ev(18, "screen_opened", screen=chest, menu="minecraft:generic_9x3"),
            ev(80, "screen_closed", screen=chest),
        ],
    )
    assert spans_of("use.trade", other) == []


def test_misc_beds_fire_and_workstations():
    anvil = "net.minecraft.client.gui.screens.inventory.AnvilScreen"
    s = seg(
        200,
        events=[
            use(10, "use_block", block="red_bed"),
            use(50, "use_block", item_id="flint_and_steel", block="obsidian"),
            ev(100, "screen_opened", screen=anvil, menu="minecraft:anvil"),
            ev(150, "screen_closed", screen=anvil),
        ],
    )
    assert spans_of("use.misc", s) == [(8, 13), (48, 53), (100, 149)]
    assert spans_of("inv.container", s) == []


def test_misc_fishing_is_cast_to_reel():
    rod = [use(t, "use_item", item_id="fishing_rod") for t in (10, 300, 900)]
    s = seg(1000, held_item="fishing_rod", events=rod)
    assert spans_of("use.misc", s) == [(7, 910)]


def test_misc_ignores_plain_blocks_and_sneaking_into_beds():
    plain = seg(40, events=[use(20, "use_block", item_id="stick", block="stone")])
    assert spans_of("use.misc", plain) == []
    sneaking = seg(
        40, crouching=True, off_hand=item("shield"), events=[use(20, "use_block", block="red_bed")]
    )
    assert spans_of("use.misc", sneaking) == []


def test_every_interact_detector_handles_a_bare_segment():
    names = [n for n in REGISTRY if n.startswith("use.")]
    assert names
    for name in names:
        assert detect(name, seg(1)) == []
        assert detect(name, seg(30)) == []
