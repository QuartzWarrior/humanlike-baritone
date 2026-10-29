"""Inventory and GUI primitives (spec: Action taxonomy, inventory and GUI).

Also the item, screen and span helpers the combat and interact families share:

    used_item(seg)      per tick, the item a held use acts on
    screens(seg)        open-screen stretches with their menu type
    clusters(...)       event ticks grouped into padded spans
    merge(spans)        overlapping same-label spans joined

Screen stretches come from screen_opened and screen_closed events. The cleaned ``ticks.screen``
column only adds what the events miss: a screen already open when the segment starts (opened while
recording was paused), opens without an event, and closes once the column reads null again.
(It currently never does after a close: clean.fill_gaps forward-fills real nulls.) A stretch's
menu is its screen_opened event's menu, else the most common menu among its slot clicks, else a
guess from the screen's class name. Screens without a menu (chat, pause, mod menus) pause
recording or aren't game screens, and are dropped.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
import polars as pl

from hbpipe.label.base import Seg, Span, clusters, detector, groups, merge

INVENTORY = "minecraft:inventory"
CRAFT_MENUS = frozenset({"minecraft:crafting", "minecraft:crafter_3x3"})
MERCHANT = "minecraft:merchant"
WORKSTATION_MENUS = frozenset(
    {
        "minecraft:enchantment",
        "minecraft:anvil",
        "minecraft:brewing_stand",
        "minecraft:grindstone",
        "minecraft:smithing",
        "minecraft:stonecutter",
        "minecraft:loom",
        "minecraft:cartography_table",
        "minecraft:beacon",
        "minecraft:lectern",
    }
)
"""Menus with their own set (use.misc), so not inv.container."""

_SCREEN_MENUS = {
    "InventoryScreen": INVENTORY,
    "CreativeModeInventoryScreen": INVENTORY,
    "ContainerScreen": "minecraft:generic_9x3",
    "ShulkerBoxScreen": "minecraft:shulker_box",
    "FurnaceScreen": "minecraft:furnace",
    "BlastFurnaceScreen": "minecraft:blast_furnace",
    "SmokerScreen": "minecraft:smoker",
    "HopperScreen": "minecraft:hopper",
    "DispenserScreen": "minecraft:generic_3x3",
    "HorseInventoryScreen": "minecraft:horse",
    "CraftingScreen": "minecraft:crafting",
    "CrafterScreen": "minecraft:crafter_3x3",
    "MerchantScreen": MERCHANT,
    "EnchantmentScreen": "minecraft:enchantment",
    "AnvilScreen": "minecraft:anvil",
    "BrewingStandScreen": "minecraft:brewing_stand",
    "GrindstoneScreen": "minecraft:grindstone",
    "SmithingScreen": "minecraft:smithing",
    "StonecutterScreen": "minecraft:stonecutter",
    "LoomScreen": "minecraft:loom",
    "CartographyTableScreen": "minecraft:cartography_table",
    "BeaconScreen": "minecraft:beacon",
    "LecternScreen": "minecraft:lectern",
}

FOOD = frozenset(
    """
    apple baked_potato beef beetroot beetroot_soup bread carrot chicken chorus_fruit cod
    cooked_beef cooked_chicken cooked_cod cooked_mutton cooked_porkchop cooked_rabbit
    cooked_salmon cookie dried_kelp enchanted_golden_apple glow_berries golden_apple
    golden_carrot honey_bottle melon_slice mushroom_stew mutton poisonous_potato porkchop potato
    pufferfish pumpkin_pie rabbit rabbit_stew rotten_flesh salmon spider_eye suspicious_stew
    sweet_berries tropical_fish
    """.split()  # noqa: SIM905
)
DRINKS = frozenset({"potion", "milk_bucket", "ominous_bottle"})
HOLD_USE = (
    FOOD | DRINKS | {"bow", "crossbow", "trident", "shield", "spyglass", "goat_horn", "brush"}
)
"""Items whose use is held (TickFlag using_item while the use key is down)."""

SCREEN_GAP = 2  # ticks between an event and the ticks.screen change it explains


def short(item_id: str | None) -> str:
    """'minecraft:bow' -> 'bow'; '' for None."""
    return item_id.removeprefix("minecraft:") if item_id else ""


def hand_ids(seg: Seg, hand: str) -> np.ndarray:
    """Short item ids in ``main_hand`` or ``off_hand`` per tick ('' when empty)."""
    s = seg.ticks[hand].struct.field("id").str.strip_prefix("minecraft:").fill_null("")
    return s.to_numpy().astype(object)


def used_item(seg: Seg) -> np.ndarray:
    """Per tick, the item a held use acts on: the main hand's if it has a held use (HOLD_USE),
    else the off hand's. The game tries the main hand first and falls back to the off hand."""
    main = np.array([h or "" for h in seg.col("held_item")], dtype=object)
    off = hand_ids(seg, "off_hand")
    return np.where(np.isin(main, list(HOLD_USE)), main, off)


def slot_clicks(seg: Seg) -> pl.DataFrame:
    return seg.gui.filter(pl.col("kind") == "slot")


def key_presses(seg: Seg, *bindings: str, prefix: str | None = None) -> np.ndarray:
    """Ticks where one of ``bindings`` (or any binding starting with ``prefix``) went down."""
    k = seg.keys.filter(pl.col("down"))
    hit = pl.col("binding").is_in(bindings)
    if prefix:
        hit = hit | pl.col("binding").str.starts_with(prefix)
    return k.filter(hit)["tick"].to_numpy().astype(np.int64)


def menu_of_screen(screen: str | None) -> str | None:
    """A menu type guessed from a screen class name; None for screens without a menu."""
    if not screen:
        return None
    name = screen.rsplit(".", 1)[-1]
    if name in _SCREEN_MENUS:
        return _SCREEN_MENUS[name]
    if "Merchant" in name:
        return MERCHANT
    return None


@dataclass(frozen=True)
class Screen:
    i0: int  # first row it is open
    i1: int  # last row it is open
    screen: str | None
    menu: str | None
    opened: bool  # it opened inside the segment
    closed: bool  # it closed inside the segment


def screens(seg: Seg) -> list[Screen]:
    """Open-screen stretches that have a menu (see the module docstring)."""
    col = seg.col("screen")
    ev = seg.events_of("screen_opened", "screen_closed")
    # (row, order within the row, opens, screen, menu, opened inside the segment)
    trans: list[tuple[int, int, bool, str | None, str | None, bool]] = [
        (seg.index_of(t), int(t_ns), kind == "screen_opened", screen, menu, True)
        for kind, t, t_ns, screen, menu in ev.select(
            "kind", "tick", "t_ns", "screen", "menu"
        ).iter_rows()
    ]
    open_rows = np.array([r[0] for r in trans if r[2]], dtype=np.int64)
    close_rows = np.array([r[0] for r in trans if not r[2]], dtype=np.int64)

    def near(rows: np.ndarray, i: int) -> bool:
        return bool((np.abs(rows - i) <= SCREEN_GAP).any())

    if col[0] is not None and not near(open_rows, 0):
        trans.append((0, -1, True, col[0], None, False))
    for i in (np.flatnonzero(col[1:] != col[:-1]) + 1).tolist():
        if col[i] is None and not near(close_rows, i):
            trans.append((i, -1, False, None, None, True))
        elif col[i] is not None and not near(open_rows, i):
            trans.append((i, -1, True, col[i], None, True))
    trans.sort(key=lambda r: (r[0], r[1]))

    found: list[Screen] = []
    cur: Screen | None = None
    for i, _, opens, screen, menu, inside in trans:
        if cur is not None and (not opens or i > cur.i0):
            found.append(Screen(cur.i0, max(cur.i0, i - 1), cur.screen, cur.menu, cur.opened, True))
            cur = None
        if opens:
            cur = Screen(i, i, screen, menu, inside, False)
    if cur is not None:
        found.append(Screen(cur.i0, len(seg) - 1, cur.screen, cur.menu, cur.opened, False))

    clicks = slot_clicks(seg)
    out = []
    for s in found:
        menu = s.menu
        if menu is None:
            menus = [m for m in _clicks_in(clicks, seg, s)["menu"].to_list() if m]
            menu = Counter(menus).most_common(1)[0][0] if menus else menu_of_screen(s.screen)
        if menu is not None:
            out.append(Screen(s.i0, s.i1, s.screen, menu, s.opened, s.closed))
    return out


def is_container(menu: str | None) -> bool:
    return (
        menu is not None
        and menu != INVENTORY
        and menu != MERCHANT
        and menu not in CRAFT_MENUS
        and menu not in WORKSTATION_MENUS
    )


def _screen_span(seg: Seg, s: Screen, label: str, confidence: float) -> Span:
    return seg.span(s.i0, s.i1, label, confidence)


def _clicks_in(clicks: pl.DataFrame, seg: Seg, s: Screen) -> pl.DataFrame:
    return clicks.filter(pl.col("tick").is_between(seg.tick[s.i0], seg.tick[s.i1]))


@detector("inv.hotbar")
def hotbar(seg: Seg) -> list[Span]:
    """Selected-slot changes. Changes at most 4 ticks apart (scrolling past several slots) are one
    switch, spanned from 2 ticks before the first change to 2 after the last. Confidence is lower
    when no number key or scroll came in the 3 ticks before (the server or a pick moved it)."""
    slot = seg.col("slot")
    changes = np.flatnonzero(slot[1:] != slot[:-1]) + 1
    scrolls = seg.buttons.filter(pl.col("kind") == "scroll")["tick"].to_numpy().astype(np.int64)
    inputs = np.concatenate([key_presses(seg, prefix="key.hotbar."), scrolls])
    out = []
    for g in groups(changes, gap=4):
        t0, t1 = int(seg.tick[g[0]]), int(seg.tick[g[-1]])
        prompted = bool(((inputs >= t0 - 3) & (inputs <= t1)).any())
        out.append(seg.tick_span(t0 - 2, t1 + 2, "inv.hotbar", 0.95 if prompted else 0.75))
    return out


@detector("inv.organize")
def organize(seg: Seg) -> list[Span]:
    """Slot clicks in the player's own inventory screen (menu minecraft:inventory) other than on
    the crafting result (slot 0): clicks at most 2 s apart form one span, padded 4 ticks."""
    clicks = slot_clicks(seg).filter((pl.col("menu") == INVENTORY) & (pl.col("slot_id") != 0))
    out = []
    for g in groups(clicks["tick"].to_numpy(), gap=40):
        conf = 0.9 if g.size >= 2 else 0.75
        out.append(seg.tick_span(int(g[0]) - 4, int(g[-1]) + 4, "inv.organize", conf))
    return out


@detector("inv.craft")
def craft(seg: Seg) -> list[Span]:
    """Crafting-table (or crafter) screens, whole; and outside them, craft results taken together
    with the grid clicks leading up to them (inventory slots 0-4, at most 3 s apart)."""
    taken = seg.events_of("result_taken").filter(pl.col("result_kind") == "craft")
    taken_ticks = taken["tick"].to_numpy().astype(np.int64)
    grid = slot_clicks(seg).filter(
        (pl.col("menu") == INVENTORY) & pl.col("slot_id").is_between(0, 4)
        | pl.col("menu").is_in(list(CRAFT_MENUS))
    )
    grid_ticks = grid["tick"].to_numpy().astype(np.int64)
    out = []
    for g in groups(np.concatenate([taken_ticks, grid_ticks]), gap=60):
        if np.isin(g, taken_ticks).any():
            out.append(seg.tick_span(int(g[0]) - 4, int(g[-1]) + 4, "inv.craft", 0.9))
    for s in screens(seg):
        if s.menu in CRAFT_MENUS:
            t0, t1 = seg.tick[s.i0], seg.tick[s.i1]
            made = bool(((taken_ticks >= t0) & (taken_ticks <= t1)).any())
            out.append(_screen_span(seg, s, "inv.craft", 0.9 if made else 0.6))
    return merge(out)


@detector("inv.container")
def container(seg: Seg) -> list[Span]:
    """A container screen open (chest, barrel, shulker box, furnace, hopper, dispenser, horse...):
    any screen whose menu isn't the inventory, crafting, merchant or a workstation. Confidence is
    lower when the stretch is cut by the segment edge and has no clicks."""
    clicks = slot_clicks(seg)
    out = []
    for s in screens(seg):
        if not is_container(s.menu):
            continue
        sure = (s.opened and s.closed) or not _clicks_in(clicks, seg, s).is_empty()
        out.append(_screen_span(seg, s, "inv.container", 0.9 if sure else 0.75))
    return out


@detector("inv.eat")
def eat(seg: Seg) -> list[Span]:
    """Use held with food (FOOD) in the hand being used, 4+ ticks, one span per item (eating
    several in a row drops the flag for a tick between them). Drinking (potions, milk) counts at
    lower confidence. A full eat is 32 ticks (16 for dried kelp); shorter ones were cut off."""
    item = used_item(seg)
    using = seg.col("using_item")
    food = using & np.isin(item, list(FOOD))
    drink = using & np.isin(item, list(DRINKS))

    def confidence(i0: int, i1: int) -> float:
        return 0.95 if i1 - i0 + 1 >= 12 else 0.8

    return merge(
        seg.mask_spans(food, "inv.eat", min_len=4, confidence=confidence)
        + seg.mask_spans(drink, "inv.eat", min_len=4, confidence=0.7)
    )


def _armor_broke(seg: Seg, i: int, slots: list[int]) -> bool:
    """True if every changed armor slot emptied with its piece on its last durability point."""
    armor = seg.ticks["armor"]
    before = armor[i - 1].to_list() if armor[i - 1] is not None else []
    after = armor[i].to_list() if armor[i] is not None else []
    for k in slots:
        was = before[k] if k < len(before) else None
        now = after[k] if k < len(after) else None
        if now is not None or was is None or not was["max_damage"]:
            return False
        if was["max_damage"] - was["damage"] > 1:
            return False
    return True


@detector("inv.equip")
def equip(seg: Seg) -> list[Span]:
    """Equipment changes, each spanned 3 ticks either side, merged within 10 ticks: armor pieces
    put on, taken off or swapped (a piece vanishing on its last durability point broke instead);
    a new off-hand item (an emptied off hand counts only with a screen open or the swap key, else
    it was used up); and the swap-hands key when it changed either hand."""
    armor = [
        tuple(short(a) for a in row or ())
        for row in seg.ticks["armor"].list.eval(pl.element().struct.field("id")).to_list()
    ]
    off = hand_ids(seg, "off_hand")
    main = np.array([h or "" for h in seg.col("held_item")], dtype=object)
    swaps = key_presses(seg, "key.swapOffhand")
    in_screen = np.zeros(len(seg), dtype=bool)
    for s in screens(seg):
        in_screen[s.i0 : s.i1 + 1] = True

    def swapped(t: int) -> bool:
        return bool(((swaps >= t - 3) & (swaps <= t)).any())

    ticks: list[int] = []
    conf: list[float] = []
    for i in range(1, len(seg)):
        a, b = armor[i - 1], armor[i]
        if a == b:
            continue
        width = max(len(a), len(b))
        a, b = a + ("",) * (width - len(a)), b + ("",) * (width - len(b))
        slots = [k for k in range(width) if a[k] != b[k]]
        if slots and not _armor_broke(seg, i, slots):
            ticks.append(int(seg.tick[i]))
            conf.append(0.9)
    for i in (np.flatnonzero(off[1:] != off[:-1]) + 1).tolist():
        t = int(seg.tick[i])
        if off[i] or in_screen[i] or swapped(t):
            ticks.append(t)
            conf.append(0.95 if swapped(t) else 0.9)
    for t in swaps.tolist():
        i = seg.index_of(t)
        prev, j = max(i - 1, 0), min(i + 3, len(seg) - 1)
        if (main[i : j + 1] != main[prev]).any() or (off[i : j + 1] != off[prev]).any():
            ticks.append(t)
            conf.append(0.95)
    return clusters(seg, ticks, "inv.equip", conf, gap=10, before=3, after=3)


@detector("inv.glance")
def glance(seg: Seg) -> list[Span]:
    """The inventory opened and closed with no slot clicks in between. Confidence is lower when
    the segment edge cuts the stretch, so the open or close wasn't seen."""
    clicks = slot_clicks(seg)
    out = []
    for s in screens(seg):
        if s.menu != INVENTORY or not _clicks_in(clicks, seg, s).is_empty():
            continue
        out.append(_screen_span(seg, s, "inv.glance", 0.85 if s.opened and s.closed else 0.6))
    return out
