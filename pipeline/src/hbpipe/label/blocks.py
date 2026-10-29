"""Block categories the detectors need, from block ids alone.

The recorder stores block-state strings ("minecraft:oak_stairs[facing=north,...]"). The game's
real collision shapes aren't recorded, so solidity is a name-based approximation: good for
"is there a ceiling" or "is this a gap", not for exact hitboxes.
"""

from __future__ import annotations

from functools import cache


def block_id(state: str | None) -> str:
    """'minecraft:oak_stairs[facing=north]' -> 'oak_stairs'."""
    if not state:
        return ""
    return state.split("[", 1)[0].removeprefix("minecraft:")


_PASSABLE = frozenset(
    """
    air cave_air void_air water lava bubble_column light structure_void
    short_grass tall_grass fern large_fern dead_bush seagrass tall_seagrass kelp kelp_plant
    sugar_cane vine glow_lichen sculk_vein cave_vines cave_vines_plant weeping_vines
    weeping_vines_plant twisting_vines twisting_vines_plant hanging_roots spore_blossom
    torch wall_torch soul_torch soul_wall_torch redstone_torch redstone_wall_torch
    copper_torch copper_wall_torch
    redstone_wire tripwire tripwire_hook lever rail powered_rail detector_rail activator_rail
    cobweb fire soul_fire nether_portal end_portal end_gateway
    wheat carrots potatoes beetroots melon_stem attached_melon_stem pumpkin_stem
    attached_pumpkin_stem sweet_berry_bush nether_wart torchflower_crop pitcher_crop
    brown_mushroom red_mushroom crimson_fungus warped_fungus crimson_roots warped_roots
    nether_sprouts dandelion poppy blue_orchid allium azure_bluet red_tulip orange_tulip
    white_tulip pink_tulip oxeye_daisy cornflower lily_of_the_valley wither_rose sunflower
    lilac rose_bush peony torchflower pitcher_plant pink_petals wildflowers leaf_litter
    firefly_bush short_dry_grass tall_dry_grass bush cactus_flower closed_eyeblossom
    open_eyeblossom snow
    """.split()  # noqa: SIM905 (a word list reads better than 100 quoted strings)
)
_PASSABLE_SUFFIXES = (
    "_sapling",
    "_button",
    "_pressure_plate",
    "_sign",
    "_wall_sign",
    "_hanging_sign",
    "_banner",
    "_wall_banner",
    "_carpet",
    "_coral",
    "_coral_fan",
    "_coral_wall_fan",
)


@cache
def passable(block: str) -> bool:
    """True if the player can move through it (by id; see the module note)."""
    return block in _PASSABLE or block.endswith(_PASSABLE_SUFFIXES) or block == ""


def solid(block: str) -> bool:
    return not passable(block)


def is_air(block: str) -> bool:
    return block in ("air", "cave_air", "void_air", "")


def is_liquid(block: str) -> bool:
    return block in ("water", "lava", "bubble_column")


@cache
def is_ore(block: str) -> bool:
    return block.endswith("_ore") or block in ("ancient_debris", "raw_iron_block")


@cache
def is_log(block: str) -> bool:
    return (
        block.endswith(("_log", "_stem", "_wood", "_hyphae"))
        and not block.startswith(("stripped_", "attached_", "melon", "pumpkin"))
        or block in ("mangrove_roots",)
    )


@cache
def is_leaves(block: str) -> bool:
    return block.endswith("_leaves") or block in ("nether_wart_block", "warped_wart_block")


def falls(block: str) -> bool:
    """Gravity blocks that refill a hole from above."""
    return block in (
        "sand",
        "red_sand",
        "gravel",
        "suspicious_sand",
        "suspicious_gravel",
    ) or block.endswith(("concrete_powder", "anvil"))


@cache
def is_crop(block: str) -> bool:
    return block in (
        "wheat",
        "carrots",
        "potatoes",
        "beetroots",
        "melon",
        "pumpkin",
        "melon_stem",
        "pumpkin_stem",
        "attached_melon_stem",
        "attached_pumpkin_stem",
        "sweet_berry_bush",
        "nether_wart",
        "cocoa",
        "sugar_cane",
        "cactus",
        "bamboo",
        "torchflower_crop",
        "pitcher_crop",
        "farmland",
    )


@cache
def is_mechanism(block: str) -> bool:
    return block.endswith(("_door", "_trapdoor", "_fence_gate", "_button", "_pressure_plate")) or (
        block
        in ("lever", "iron_door", "iron_trapdoor", "repeater", "comparator", "daylight_detector")
    )


@cache
def is_light(block: str) -> bool:
    return block.endswith(("torch", "lantern", "candle", "_froglight", "campfire")) or block in (
        "glowstone",
        "sea_lantern",
        "shroomlight",
        "jack_o_lantern",
        "end_rod",
        "redstone_lamp",
        "beacon",
        "copper_bulb",
    )


@cache
def is_climbable(block: str) -> bool:
    return block in (
        "ladder",
        "vine",
        "scaffolding",
        "twisting_vines",
        "twisting_vines_plant",
        "weeping_vines",
        "weeping_vines_plant",
        "cave_vines",
        "cave_vines_plant",
    )


SURFACES = {
    "ice": "ice",
    "packed_ice": "ice",
    "blue_ice": "ice",
    "frosted_ice": "ice",
    "soul_sand": "soul_sand",
    "soul_soil": "soul_sand",
    "honey_block": "honey",
    "slime_block": "slime",
    "dirt_path": "path",
}


def surface(block: str) -> str:
    """Context flag for movement labels: ice, soul_sand, honey, slime, path, or normal."""
    return SURFACES.get(block, "normal")
