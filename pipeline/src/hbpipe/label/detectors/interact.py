"""World-interaction primitives (spec: Action taxonomy, world interactions).

Uses come from `interaction` events (use_block, use_item, use_entity), which fire for every
attempt, plus block_placed and block_broken for farming. A use_block reached the block itself
only if no block was placed on that tick (the click placed one against it instead) and the player
wasn't sneaking with anything in either hand (the game then skips the block's own use).

use.misc covers, by block (use_block): beds, respawn anchors, enchanting tables, anvils, brewing
stands, grindstones, smithing tables, stonecutters, looms, cartography tables, lecterns,
beacons, end portal frames, bells, jukeboxes, note blocks, cauldrons, cakes and lodestones; by
item (use_item or use_block): flint and steel and fire charges (lighting fires and portals),
fishing rods and eyes of ender; the workstation screens (enchanting, anvil, brewing, grindstone,
smithing, stonecutter, loom, cartography, beacon, lectern); and dimension changes (portal
travel).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import polars as pl

from hbpipe.label.base import Seg, Span, clusters, detector, merge
from hbpipe.label.blocks import is_crop, is_mechanism
from hbpipe.label.detectors.inventory import (
    MERCHANT,
    WORKSTATION_MENUS,
    hand_ids,
    screens,
    slot_clicks,
)
from hbpipe.label.entities import LIVESTOCK

BUCKETS = frozenset({"bucket", "water_bucket", "lava_bucket", "powder_snow_bucket"})
MOB_BUCKETS = frozenset(
    {
        "cod_bucket",
        "salmon_bucket",
        "pufferfish_bucket",
        "tropical_fish_bucket",
        "axolotl_bucket",
        "tadpole_bucket",
    }
)

TILLABLE = frozenset({"dirt", "grass_block", "dirt_path", "coarse_dirt", "rooted_dirt"})
PLANTED = frozenset(
    """
    wheat carrots potatoes beetroots melon_stem pumpkin_stem nether_wart sweet_berry_bush cocoa
    torchflower_crop pitcher_crop sugar_cane cactus bamboo_sapling
    """.split()  # noqa: SIM905
)
PICKED = frozenset({"sweet_berry_bush", "cave_vines", "cave_vines_plant"})
"""Harvested by using them (berries)."""
CLEARED_TOO = frozenset({"sugar_cane", "cactus", "bamboo", "melon", "pumpkin"})
"""Crops that are as often broken to clear a path as to harvest."""
_SEEDS = {"wheat_seeds", "melon_seeds", "pumpkin_seeds", "beetroot_seeds", "torchflower_seeds"}
_MEAT = {
    "beef",
    "cooked_beef",
    "chicken",
    "cooked_chicken",
    "porkchop",
    "cooked_porkchop",
    "mutton",
    "cooked_mutton",
    "rabbit",
    "cooked_rabbit",
    "rotten_flesh",
}
BREED_FOOD: dict[str, set[str]] = {
    "cow": {"wheat"},
    "mooshroom": {"wheat"},
    "sheep": {"wheat"},
    "goat": {"wheat"},
    "pig": {"carrot", "potato", "beetroot"},
    "chicken": _SEEDS | {"pitcher_pod"},
    "horse": {"golden_apple", "enchanted_golden_apple", "golden_carrot"},
    "donkey": {"golden_apple", "enchanted_golden_apple", "golden_carrot"},
    "llama": {"hay_block"},
    "trader_llama": {"hay_block"},
    "rabbit": {"dandelion", "carrot", "golden_carrot"},
    "wolf": _MEAT,
    "cat": {"cod", "salmon"},
    "ocelot": {"cod", "salmon"},
    "turtle": {"seagrass"},
    "panda": {"bamboo"},
    "fox": {"sweet_berries", "glow_berries"},
    "hoglin": {"crimson_fungus"},
    "strider": {"warped_fungus"},
    "axolotl": {"tropical_fish_bucket"},
    "frog": {"slime_ball"},
    "camel": {"cactus"},
    "sniffer": {"torchflower_seeds"},
    "armadillo": {"spider_eye"},
}
"""Animal -> items that breed (or feed) it on use_entity."""
HUSBANDRY = {
    "shears": {"sheep", "mooshroom"},
    "bucket": {"cow", "goat", "mooshroom"},
    "bowl": {"mooshroom"},
}
"""Item -> animals it shears or milks."""

MISC_BLOCKS = frozenset(
    """
    respawn_anchor enchanting_table anvil chipped_anvil damaged_anvil brewing_stand grindstone
    smithing_table stonecutter loom cartography_table lectern beacon end_portal_frame bell
    jukebox note_block cauldron water_cauldron lava_cauldron powder_snow_cauldron cake lodestone
    """.split()  # noqa: SIM905
)
MISC_ITEMS = frozenset({"flint_and_steel", "fire_charge", "ender_eye"})
FISHING_GAP = 1200  # ticks between rod uses that are still one fishing stretch (cast to reel)


def _is_misc_block(block: str) -> bool:
    return block in MISC_BLOCKS or block.endswith(("_bed", "candle_cake"))


def _uses(seg: Seg, *kinds: str) -> pl.DataFrame:
    return seg.events.filter(pl.col("interaction").is_in(kinds))


def _used_blocks(seg: Seg, match: Callable[[str], bool]) -> pl.DataFrame:
    """use_block events on blocks ``match`` accepts that reached the block (module docstring)."""
    placed = seg.events_of("block_placed")["tick"]
    main = np.array([h or "" for h in seg.col("held_item")], dtype=object)
    holding = (main != "") | (hand_ids(seg, "off_hand") != "")
    bypass = seg.col("crouching") & holding
    ev = _uses(seg, "use_block").drop_nulls("block").filter(~pl.col("tick").is_in(placed.to_list()))
    keep = [
        match(b) and not bypass[seg.index_of(t)] for t, b in ev.select("tick", "block").iter_rows()
    ]
    return ev.filter(pl.Series(keep, dtype=pl.Boolean))


@detector("use.mechanism")
def mechanism(seg: Seg) -> list[Span]:
    """use_block that reached a door, trapdoor, fence gate, button, lever or other redstone part
    (blocks.is_mechanism). Each use spans 2 ticks before to 3 after; uses at most 10 ticks apart
    join. Pressure plates do nothing when used: 0.5."""
    ev = _used_blocks(seg, is_mechanism)
    conf = [0.5 if b.endswith("_pressure_plate") else 0.9 for b in ev["block"].to_list()]
    return clusters(seg, ev["tick"].to_numpy(), "use.mechanism", conf, gap=10, before=2, after=3)


@detector("use.bucket")
def bucket(seg: Seg) -> list[Span]:
    """use_item or use_block with an empty, water, lava or powder snow bucket (fish buckets at
    0.8), uses at most 5 ticks apart joined, spanned 3 ticks before to 5 after. A use that didn't
    change the bucket in that hand within 5 ticks (nothing to scoop, nowhere to pour) gets 0.6."""
    ev = _uses(seg, "use_item", "use_block").filter(
        pl.col("item_id").is_in(list(BUCKETS | MOB_BUCKETS))
    )
    main = np.array([h or "" for h in seg.col("held_item")], dtype=object)
    off = hand_ids(seg, "off_hand")
    ticks, conf = [], []
    for t, item, hand in ev.select("tick", "item_id", "hand").iter_rows():
        ids = off if hand == "off" else main
        i = seg.index_of(t)
        after = ids[i + 1 : i + 6]
        changed = bool(((after != item) & np.char.endswith(after.astype(str), "bucket")).any())
        ticks.append(t)
        conf.append((0.9 if item in BUCKETS else 0.8) if changed else 0.6)
    return clusters(seg, ticks, "use.bucket", conf, gap=5, before=3, after=5)


def _farm_events(seg: Seg) -> tuple[list[int], list[float]]:
    ticks: list[int] = []
    conf: list[float] = []

    def add(t: int, c: float) -> None:
        ticks.append(int(t))
        conf.append(c)

    uses = _uses(seg, "use_block").select("tick", "item_id", "block").iter_rows()
    for t, item, block in uses:
        item, block = item or "", block or ""
        if item == "bone_meal" or item.endswith("_hoe") and block in TILLABLE:
            add(t, 0.9)
    for t in _used_blocks(seg, lambda b: b in PICKED)["tick"].to_list():
        add(t, 0.85)
    for t in _used_blocks(seg, lambda b: b == "composter")["tick"].to_list():
        add(t, 0.75)
    for t, block in seg.events_of("block_placed").select("tick", "block").iter_rows():
        if block in PLANTED:
            add(t, 0.9)
        elif block and block.endswith(("_sapling", "_propagule")):
            add(t, 0.75)
    for t, block in seg.events_of("block_broken").select("tick", "block").iter_rows():
        if block and is_crop(block) and block != "farmland":
            add(t, 0.75 if block in CLEARED_TOO else 0.85)
    animals = _uses(seg, "use_entity").select("tick", "item_id", "entity_type").iter_rows()
    for t, item, animal in animals:
        if item in BREED_FOOD.get(animal or "", ()):
            add(t, 0.9)
        elif animal in HUSBANDRY.get(item or "", ()):
            add(t, 0.8)
    for t, animal in seg.events_of("damage_dealt").select("tick", "entity_type").iter_rows():
        if animal in LIVESTOCK:
            add(t, 0.8)
    return ticks, conf


@detector("use.farm")
def farm(seg: Seg) -> list[Span]:
    """Tilling (a hoe on dirt or grass), planting (crops, cane, cactus, cocoa; saplings 0.75),
    harvesting (breaking crops; cane, cactus, bamboo, melons and pumpkins 0.75 as they're often
    just in the way; picking berries), bone meal, composting (0.75), and breeding, shearing or
    milking animals (use_entity with the right item), and killing livestock for food or drops
    (damage_dealt on entities.LIVESTOCK). Events at most 2 s apart join, spanned 3 ticks before to
    5 after."""
    ticks, conf = _farm_events(seg)
    return clusters(seg, ticks, "use.farm", conf, gap=40, before=3, after=5)


@detector("use.trade")
def trade(seg: Seg) -> list[Span]:
    """A merchant screen open, from the use_entity click that opened it (if within 10 ticks
    before) to its close. 0.95 with slot clicks or a trade taken inside, else 0.85."""
    clicks = slot_clicks(seg)["tick"].to_numpy().astype(np.int64)
    trades = seg.events_of("result_taken")["tick"].to_numpy().astype(np.int64)
    acted = np.concatenate([clicks, trades])
    talks = _uses(seg, "use_entity")["tick"].to_numpy().astype(np.int64)
    out = []
    for s in screens(seg):
        if s.menu != MERCHANT:
            continue
        t0, t1 = int(seg.tick[s.i0]), int(seg.tick[s.i1])
        busy = bool(((acted >= t0) & (acted <= t1)).any())
        lead = talks[(talks >= t0 - 10) & (talks <= t0)]
        start = int(lead.min()) if lead.size else t0
        out.append(seg.tick_span(start, t1, "use.trade", 0.95 if busy else 0.85))
    return merge(out)


@detector("use.misc")
def misc(seg: Seg) -> list[Span]:
    """The uses listed in the module docstring. Block and item uses span 2 ticks before to 3
    after, joined within 10 ticks; fishing-rod uses at most 60 s apart are one fishing stretch
    (cast to reel); workstation screens span while open; a dimension change spans the second
    before it."""
    blocks = _used_blocks(seg, _is_misc_block)["tick"].to_list()
    items = _uses(seg, "use_item", "use_block").filter(pl.col("item_id").is_in(list(MISC_ITEMS)))
    rods = _uses(seg, "use_item").filter(pl.col("item_id") == "fishing_rod")["tick"].to_numpy()
    out = clusters(
        seg, blocks + items["tick"].to_list(), "use.misc", 0.85, gap=10, before=2, after=3
    )
    out += clusters(seg, rods, "use.misc", 0.85, gap=FISHING_GAP, before=3, after=10)
    for t in seg.events_of("dimension_change")["tick"].to_list():
        out.append(seg.tick_span(t - 20, t, "use.misc", 0.7))
    for s in screens(seg):
        if s.menu in WORKSTATION_MENUS:
            out.append(seg.span(s.i0, s.i1, "use.misc", 0.9))
    return merge(out)
