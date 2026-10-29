"""Entity categories the detectors share, by short id ("zombie", not "minecraft:zombie")."""

from __future__ import annotations

# Mobs that attack on sight: what a player reacts to when it comes into view.
AGGRESSIVE = frozenset(
    """
    zombie zombie_villager husk drowned skeleton stray bogged wither_skeleton creeper spider
    cave_spider witch slime magma_cube phantom blaze ghast hoglin zoglin piglin_brute vindicator
    pillager evoker illusioner ravager vex guardian elder_guardian shulker silverfish endermite
    warden breeze creaking wither ender_dragon
    """.split()  # noqa: SIM905
)

# Mobs a fight can be with: the aggressive ones plus those that turn hostile when provoked or
# without gold armor.
HOSTILE = AGGRESSIVE | frozenset({"piglin", "enderman", "zombified_piglin"})

# Animals killed for food or drops. Hunting them is farming, not fighting (the user's call).
LIVESTOCK = frozenset({"cow", "mooshroom", "sheep", "pig", "chicken", "rabbit", "goat"})


def short_type(entity_type: str | None) -> str:
    return (entity_type or "").removeprefix("minecraft:")


def is_hostile(entity_type: str | None) -> bool:
    """True for HOSTILE mobs, with or without the "minecraft:" prefix."""
    return short_type(entity_type) in HOSTILE
