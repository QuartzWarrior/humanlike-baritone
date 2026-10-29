from labelkit import detect, ev, row, seg, spans_of

from hbpipe.label.base import REGISTRY
from hbpipe.label.detectors.inventory import screens

INV_SCREEN = "net.minecraft.client.gui.screens.inventory.InventoryScreen"
CHEST_SCREEN = "net.minecraft.client.gui.screens.inventory.ContainerScreen"


def item(item_id: str, damage: int = 0, max_damage: int = 0) -> dict:
    return {"id": f"minecraft:{item_id}", "count": 1, "damage": damage, "max_damage": max_damage}


def opened(tick: int, screen: str, menu: str) -> dict:
    return ev(tick, "screen_opened", screen=screen, menu=menu)


def closed(tick: int, screen: str) -> dict:
    return ev(tick, "screen_closed", screen=screen)


def click(tick: int, slot: int, menu: str = "minecraft:inventory", kind: str = "pickup") -> dict:
    return row(tick, kind="slot", slot_id=slot, button=0, click_type=kind, menu=menu)


def test_screens_come_from_events_not_the_stale_screen_column():
    # The cleaned column stays set after the close; the close event ends the stretch.
    s = seg(
        60,
        screen=[None] * 10 + [CHEST_SCREEN] * 50,
        events=[opened(10, CHEST_SCREEN, "minecraft:generic_9x3"), closed(30, CHEST_SCREEN)],
    )
    [sc] = screens(s)
    assert (sc.i0, sc.i1, sc.menu, sc.opened, sc.closed) == (
        10,
        29,
        "minecraft:generic_9x3",
        True,
        True,
    )


def test_screen_open_at_segment_start_takes_its_menu_from_clicks_or_class():
    s = seg(40, screen=CHEST_SCREEN, events=[closed(20, CHEST_SCREEN)])
    [sc] = screens(s)
    assert (sc.i0, sc.i1, sc.menu, sc.opened) == (0, 19, "minecraft:generic_9x3", False)
    clicked = seg(
        40,
        screen=CHEST_SCREEN,
        events=[closed(20, CHEST_SCREEN)],
        gui=[click(5, 12, "minecraft:generic_9x6", "quick_move")],
    )
    assert screens(clicked)[0].menu == "minecraft:generic_9x6"


def test_chat_and_pause_screens_are_not_stretches():
    chat = "net.minecraft.client.gui.screens.ChatScreen"
    assert screens(seg(20, events=[ev(19, "screen_opened", screen=chat)])) == []


def test_hotbar_spans_each_switch():
    s = seg(
        40,
        slot=[0] * 10 + [1] * 20 + [4] * 10,
        keys=[row(10, binding="key.hotbar.2", down=True)],
    )
    assert spans_of("inv.hotbar", s) == [(8, 12), (28, 32)]
    assert [sp.confidence for sp in detect("inv.hotbar", s)] == [0.95, 0.75]


def test_hotbar_scrolling_past_slots_is_one_switch():
    s = seg(
        30,
        slot=[0] * 10 + [1, 2] + [3] * 18,
        buttons=[row(t, kind="scroll", scroll_dy=-1.0) for t in (10, 11, 12)],
    )
    assert spans_of("inv.hotbar", s) == [(8, 14)]
    assert spans_of("inv.hotbar", seg(30, slot=2)) == []


def test_organize_groups_inventory_clicks():
    s = seg(120, gui=[click(20, 9), click(30, 36), click(45, -999), click(100, 12)])
    assert spans_of("inv.organize", s) == [(16, 49), (96, 104)]
    assert [sp.confidence for sp in detect("inv.organize", s)] == [0.9, 0.75]


def test_organize_ignores_containers_and_the_craft_result():
    s = seg(60, gui=[click(20, 9, "minecraft:generic_9x3"), click(40, 0)])
    assert spans_of("inv.organize", s) == []


def test_craft_takes_the_grid_clicks_before_a_result():
    s = seg(
        80,
        gui=[click(20, 1), click(30, 2), click(40, 0)],
        events=[ev(40, "result_taken", result_kind="craft", menu="minecraft:inventory")],
    )
    assert spans_of("inv.craft", s) == [(16, 44)]
    no_result = seg(80, gui=[click(20, 1), click(30, 2)])
    assert spans_of("inv.craft", no_result) == []


def test_craft_covers_a_crafting_table_screen():
    table = "net.minecraft.client.gui.screens.inventory.CraftingScreen"
    s = seg(60, events=[opened(10, table, "minecraft:crafting"), closed(50, table)])
    assert spans_of("inv.craft", s) == [(10, 49)]
    assert detect("inv.craft", s)[0].confidence == 0.6
    assert spans_of("inv.container", s) == []


def test_container_is_any_non_inventory_screen():
    furnace = "net.minecraft.client.gui.screens.inventory.FurnaceScreen"
    s = seg(
        100,
        events=[
            opened(10, CHEST_SCREEN, "minecraft:generic_9x3"),
            closed(30, CHEST_SCREEN),
            opened(50, furnace, "minecraft:furnace"),
            closed(70, furnace),
        ],
    )
    assert spans_of("inv.container", s) == [(10, 29), (50, 69)]
    inv = seg(60, events=[opened(10, INV_SCREEN, "minecraft:inventory"), closed(30, INV_SCREEN)])
    assert spans_of("inv.container", inv) == []
    trader = "cc.cassian.trading.BetterGuiMerchant"
    merchant = seg(60, events=[opened(10, trader, "minecraft:merchant"), closed(30, trader)])
    assert spans_of("inv.container", merchant) == []


def test_container_cut_by_the_segment_without_clicks_is_less_sure():
    s = seg(40, screen=CHEST_SCREEN, events=[closed(20, CHEST_SCREEN)])
    assert [sp.confidence for sp in detect("inv.container", s)] == [0.75]


def test_eat_is_use_held_with_food():
    using = [False] * 10 + [True] * 32 + [False] * 8
    s = seg(50, using_item=using, held_item="golden_carrot")
    assert spans_of("inv.eat", s) == [(10, 41)]
    assert detect("inv.eat", s)[0].confidence == 0.95
    off = seg(50, using_item=using, held_item="diamond_pickaxe", off_hand=item("bread"))
    assert spans_of("inv.eat", off) == [(10, 41)]
    assert spans_of("inv.eat", seg(50, using_item=using, held_item="bow")) == []


def test_eat_splits_items_eaten_back_to_back():
    using = [True] * 32 + [False] + [True] * 32
    assert spans_of("inv.eat", seg(65, using_item=using, held_item="bread")) == [(0, 31), (33, 64)]


def test_equip_armor_change():
    boots = [None, None, None, None]
    worn = [item("iron_boots", 0, 195), None, None, None]
    s = seg(40, armor=[boots] * 20 + [worn] * 20)
    assert spans_of("inv.equip", s) == [(17, 23)]


def test_equip_ignores_armor_that_breaks():
    worn = [item("iron_boots", 194, 195), None, None, None]
    s = seg(40, armor=[worn] * 20 + [[None, None, None, None]] * 20)
    assert spans_of("inv.equip", s) == []


def test_equip_off_hand_and_swap_key():
    shield = seg(40, off_hand=[None] * 20 + [item("shield")] * 20)
    assert spans_of("inv.equip", shield) == [(17, 23)]
    used_up = seg(40, off_hand=[item("torch")] * 20 + [None] * 20)
    assert spans_of("inv.equip", used_up) == []

    swap = seg(
        40,
        held_item=["diamond_pickaxe"] * 21 + ["torch"] * 19,
        off_hand=[item("torch")] * 21 + [item("diamond_pickaxe")] * 19,
        keys=[row(20, binding="key.swapOffhand", down=True)],
    )
    assert spans_of("inv.equip", swap) == [(17, 24)]
    assert detect("inv.equip", swap)[0].confidence == 0.95


def test_glance_is_an_inventory_with_no_clicks():
    events = [opened(10, INV_SCREEN, "minecraft:inventory"), closed(40, INV_SCREEN)]
    s = seg(60, events=events)
    assert spans_of("inv.glance", s) == [(10, 39)]
    assert detect("inv.glance", s)[0].confidence == 0.85
    assert spans_of("inv.glance", seg(60, events=events, gui=[click(20, 9)])) == []
    chest = [opened(10, CHEST_SCREEN, "minecraft:generic_9x3"), closed(40, CHEST_SCREEN)]
    assert spans_of("inv.glance", seg(60, events=chest)) == []


def test_every_inventory_detector_handles_a_bare_segment():
    names = [n for n in REGISTRY if n.startswith("inv.")]
    assert names
    for name in names:
        assert detect(name, seg(1)) == []
        assert detect(name, seg(30)) == []
