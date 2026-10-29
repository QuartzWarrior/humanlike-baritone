"""Combat primitives (spec: Action taxonomy, combat).

HOSTILE and is_hostile come from hbpipe.label.entities (``is_hostile`` also takes the "minecraft:"
form that ticks.entities keeps). Neutral mobs (enderman, zombified piglin, wolf, iron golem, bee,
...) and players count as threats too, but only once they have hurt the player in the segment.
"""

from __future__ import annotations

import numpy as np
import polars as pl

from hbpipe.label.base import Seg, Span, clusters, detector, merge
from hbpipe.label.detectors.inventory import FOOD, used_item
from hbpipe.label.entities import HOSTILE, is_hostile

NOT_TARGETS = frozenset(
    """
    item experience_orb falling_block tnt arrow spectral_arrow trident snowball egg ender_pearl
    eye_of_ender potion experience_bottle fireball small_fireball dragon_fireball wither_skull
    shulker_bullet llama_spit firework_rocket fishing_bobber wind_charge breeze_wind_charge
    area_effect_cloud evoker_fangs lightning_bolt marker interaction item_display block_display
    text_display painting item_frame glow_item_frame leash_knot armor_stand end_crystal
    """.split()  # noqa: SIM905
)
"""Entities a hit on isn't a fight: items, projectiles, decorations (boats and minecarts too)."""
RANGED = frozenset({"bow", "crossbow", "trident"})
ENVIRONMENT = frozenset(
    "minecraft:" + s
    for s in """
    fall in_wall drown starve cactus lava in_fire on_fire hot_floor freeze sweet_berry_bush
    stalagmite falling_stalactite fly_into_wall out_of_world outside_border generic
    """.split()  # noqa: SIM905
)
"""Damage sources with no one to retreat from."""

MELEE_GAP = 60  # ticks between hits on one target that still make one fight
MELEE_PAD = 10  # ticks before the first hit (approach, crit jump) and after the last
MIN_DRAW = 3  # ticks of use held before a bow, crossbow or trident counts as drawn
SHIELD_RADIUS = 12.0  # blocks: a threat this close makes a raised shield blocking
RETREAT_WINDOW = 100  # ticks after damage to look for a retreat
RETREAT_SPEED = 1.0  # m/s away from the nearest threat
RETREAT_NEAR = 8.0  # blocks: eating this close to a threat is part of a retreat


def _attackers(seg: Seg) -> set[int]:
    ids = seg.events_of("damage_taken")["entity_id"].drop_nulls().to_list()
    return {i for i in ids if i > 0}


def threats(seg: Seg) -> pl.DataFrame:
    """One row per (tick row, threat) from ticks.entities: hostile mobs plus anything that hurt
    the player in this segment. Columns i (row), id, type (short), dx, dy, dz, dist."""
    df = (
        seg.ticks.select(pl.int_range(pl.len()).alias("i"), "entities")
        .explode("entities")
        .drop_nulls("entities")
        .unnest("entities")
        .with_columns(type=pl.col("type").str.strip_prefix("minecraft:"))
    )
    df = df.filter(pl.col("type").is_in(list(HOSTILE)) | pl.col("id").is_in(list(_attackers(seg))))
    return df.select(
        "i",
        "id",
        "type",
        "dx",
        "dy",
        "dz",
        dist=(pl.col("dx") ** 2 + pl.col("dy") ** 2 + pl.col("dz") ** 2).sqrt(),
    )


def nearest_threat(seg: Seg) -> np.ndarray:
    """Distance to the nearest threat per tick row (inf when none is within 16 blocks)."""
    out = np.full(len(seg), np.inf)
    near = threats(seg).group_by("i").agg(pl.col("dist").min())
    out[near["i"].to_numpy()] = near["dist"].to_numpy()
    return out


def wish_direction(seg: Seg) -> tuple[np.ndarray, np.ndarray]:
    """Horizontal (x, z) direction the held movement keys push toward, from yaw (yaw 0 faces +z,
    left of that is +x). Zero with no keys or opposing keys."""
    yaw = np.radians(seg.col("yaw"))
    fwd = seg.col("k_forward").astype(float) - seg.col("k_back")
    left = seg.col("k_left").astype(float) - seg.col("k_right")
    sin, cos = np.sin(yaw), np.cos(yaw)
    return fwd * -sin + left * cos, fwd * cos + left * sin


@detector("combat.melee")
def melee(seg: Seg) -> list[Span]:
    """Hits on living entities (damage_dealt and attack_entity events, NOT_TARGETS excluded).
    Hits on one target at most 3 s apart are one fight, spanned from 10 ticks before the first
    hit (approach, crit jump) to 10 after the last; fights that overlap merge. Hostile targets
    get 0.9, others (animals, villagers, players) 0.75."""
    target = pl.col("entity_type")
    thing = (
        target.is_in(list(NOT_TARGETS))
        | target.str.ends_with("_boat")
        | target.str.ends_with("_raft")
        | target.str.contains("minecart")
    )
    ev = seg.events.filter(
        ((pl.col("kind") == "damage_dealt") | (pl.col("interaction") == "attack_entity"))
        & ~thing.fill_null(False)
    )
    out = []
    for (_,), hits in ev.group_by("entity_id"):
        conf = 0.9 if any(is_hostile(t) for t in hits["entity_type"].to_list()) else 0.75
        ticks = hits["tick"].to_numpy()
        out += clusters(
            seg, ticks, "combat.melee", conf, gap=MELEE_GAP, before=MELEE_PAD, after=MELEE_PAD
        )
    return merge(out)


@detector("combat.ranged")
def ranged(seg: Seg) -> list[Span]:
    """Use held (using_item) with a bow, crossbow or trident in the hand being used, for at least
    MIN_DRAW ticks, then released. Draws cut by the segment end get 0.6; draws without the use key
    held for most of them (a modded binding) 0.75. A loaded crossbow fires on a click without a
    draw: a crossbow use_item with no draw starting within 3 ticks is a 6-tick shot span."""
    item = used_item(seg)
    using = seg.col("using_item")
    k_use = seg.col("k_use")
    drawn = using & np.isin(item, list(RANGED))

    def confidence(i0: int, i1: int) -> float:
        if i1 == len(seg) - 1:
            return 0.6
        return 0.9 if k_use[i0 : i1 + 1].mean() >= 0.5 else 0.75

    out = seg.mask_spans(drawn, "combat.ranged", min_len=MIN_DRAW, confidence=confidence)
    shots = seg.events.filter(
        (pl.col("interaction") == "use_item") & (pl.col("item_id") == "crossbow")
    )["tick"].to_list()
    for t in shots:
        i = seg.index_of(t)
        if not drawn[i : i + 4].any():
            out.append(seg.tick_span(t - 2, t + 3, "combat.ranged", 0.75))
    return merge(out)


@detector("combat.shield")
def shield(seg: Seg) -> list[Span]:
    """A raised shield (the blocking flag, or use held with a shield in the hand being used) for
    3+ ticks while a threat is within SHIELD_RADIUS blocks on some tick of it. The game only
    blocks after 5 ticks raised, so runs without the blocking flag get 0.8."""
    raised = seg.col("blocking") | (seg.col("using_item") & (used_item(seg) == "shield"))
    if not raised.any():
        return []
    near = nearest_threat(seg) <= SHIELD_RADIUS
    blocking = seg.col("blocking")
    out = []
    for s in seg.mask_spans(raised, "combat.shield", min_len=3, max_gap=2):
        i0, i1 = seg.index_of(s.start_tick), seg.index_of(s.end_tick)
        if near[i0 : i1 + 1].any():
            out.append(
                seg.span(i0, i1, "combat.shield", 0.9 if blocking[i0 : i1 + 1].any() else 0.8)
            )
    return out


def _hurt_by_someone(entity_id: int | None, source: str | None) -> bool:
    """Damage with an attacker, or from a source that isn't the environment."""
    return bool(entity_id and entity_id > 0) or bool(source and source not in ENVIRONMENT)


@detector("combat.retreat")
def retreat(seg: Seg) -> list[Span]:
    """Within RETREAT_WINDOW ticks of damage from an attacker (not falls, walls, lava...), ticks
    spent getting away from the nearest threat: moving away from it faster than RETREAT_SPEED
    with the movement keys pointing away (so not just knockback), eating with it within
    RETREAT_NEAR blocks, or pillaring (a block placed under the feet) while it is in view. 5+
    tick runs, gaps up to 4 bridged; 0.85 with movement away, 0.7 for eating or pillaring
    alone."""
    n = len(seg)
    window = np.zeros(n, dtype=bool)
    hits = seg.events_of("damage_taken").select("tick", "entity_id", "damage_source")
    for tick, entity_id, source in hits.iter_rows():
        if _hurt_by_someone(entity_id, source):
            i = seg.index_of(tick)
            window[i : i + RETREAT_WINDOW + 1] = True
    if not window.any():
        return []

    nearest = threats(seg).sort("dist").unique("i", keep="first")
    idx = nearest["i"].to_numpy()
    dx, dz = np.full(n, np.nan), np.full(n, np.nan)
    dx[idx], dz[idx] = nearest["dx"].to_numpy(), nearest["dz"].to_numpy()
    d = np.hypot(dx, dz)
    vx, vz = seg.col("vx") * 20.0, seg.col("vz") * 20.0
    wx, wz = wish_direction(seg)
    with np.errstate(invalid="ignore", divide="ignore"):
        ux, uz = -dx / d, -dz / d
        keys_away = (wx * ux + wz * uz) / np.hypot(wx, wz)
    seen = d > 0.1
    away = seen & (vx * ux + vz * uz > RETREAT_SPEED) & (keys_away > 0.5)
    eating = seg.col("using_item") & np.isin(used_item(seg), list(FOOD)) & (d <= RETREAT_NEAR)
    other = eating | (_pillar_rows(seg) & seen)

    def confidence(i0: int, i1: int) -> float:
        return 0.85 if away[i0 : i1 + 1].any() else 0.7

    mask = window & (away | other)
    return seg.mask_spans(mask, "combat.retreat", min_len=5, max_gap=4, confidence=confidence)


def _pillar_rows(seg: Seg) -> np.ndarray:
    """Rows within 5 ticks before to 2 after a block placed in the column under the player's
    feet (1-2 blocks below them), as in jump-placing."""
    mask = np.zeros(len(seg), dtype=bool)
    placed = seg.events_of("block_placed").drop_nulls(["x", "player_x"])
    for t, x, y, z, px, py, pz in placed.select(
        "tick", "x", "y", "z", "player_x", "player_y", "player_z"
    ).iter_rows():
        if x == np.floor(px) and z == np.floor(pz) and 0.5 <= py - y <= 2.5:
            i = seg.index_of(t)
            mask[max(i - 5, 0) : i + 3] = True
    return mask
