"""Labeling pass 1: clean the tick stream and add derived features.

Paused spans never reach the tables (the recorder doesn't write them), so cleaning here means:
fill tick gaps inside a segment, decode flag and key bitfields into booleans, and compute
the derived features the detectors share.

Derived columns, all per tick:
    filled              row was missing and forward-filled
    on_ground ... eyes_in_water     TickFlag bits as booleans
    k_forward ... k_use             held keys (from SessionHeader.key_order)
    speed               horizontal speed, m/s
    vspeed              vertical speed, m/s
    accel               change in horizontal speed, m/s^2
    yaw_rate, pitch_rate            deg/s (yaw unwrapped)
    yaw_jerk, pitch_jerk            deg/s^3
    ticks_since_landing             0 on the landing tick; null before the first landing
    air_ticks           consecutive ticks off the ground (0 on the ground)
    headroom            passable blocks above the head block, 0-3 (0 = a 2-high ceiling)
    underfoot           block id under the feet
    ground_depth        passable blocks from the feet down to the first solid one, 0 on the
                        ground, at most MAX_DEPTH (MAX_DEPTH = that many or more)
    surface             ice | soul_sand | honey | slime | path | normal
    sprint_method       while sprinting: key (sprint key held as it began), toggle, or double_tap
    screen_open         a screen (inventory, chest, ...) is showing
World-derived columns are null where the block was never seen.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl

from hbpipe.label import blocks
from hbpipe.label.world import World
from hbpipe.schema import pb

FLAG_COLUMNS = {
    "on_ground": pb.TICK_FLAG_ON_GROUND,
    "sprinting": pb.TICK_FLAG_SPRINTING,
    "crouching": pb.TICK_FLAG_CROUCHING,
    "swimming": pb.TICK_FLAG_SWIMMING,
    "gliding": pb.TICK_FLAG_GLIDING,
    "h_collision": pb.TICK_FLAG_H_COLLISION,
    "v_collision": pb.TICK_FLAG_V_COLLISION,
    "head_hit": pb.TICK_FLAG_V_COLLISION_UP,
    "in_water": pb.TICK_FLAG_IN_WATER,
    "in_lava": pb.TICK_FLAG_IN_LAVA,
    "on_climbable": pb.TICK_FLAG_ON_CLIMBABLE,
    "passenger": pb.TICK_FLAG_PASSENGER,
    "using_item": pb.TICK_FLAG_USING_ITEM,
    "blocking": pb.TICK_FLAG_BLOCKING,
    "eyes_in_water": pb.TICK_FLAG_EYES_IN_WATER,
}

KEY_COLUMNS = {
    "k_forward": "key.forward",
    "k_back": "key.back",
    "k_left": "key.left",
    "k_right": "key.right",
    "k_jump": "key.jump",
    "k_sneak": "key.sneak",
    "k_sprint": "key.sprint",
    "k_attack": "key.attack",
    "k_use": "key.use",
}

MAX_DEPTH = 6  # enough to see a 2-deep gap under a jump that rose 2 blocks above takeoff

DERIVED_SCHEMA: dict[str, pl.DataType] = {
    "filled": pl.Boolean,
    **{c: pl.Boolean for c in FLAG_COLUMNS},
    **{c: pl.Boolean for c in KEY_COLUMNS},
    "speed": pl.Float64,
    "vspeed": pl.Float64,
    "accel": pl.Float64,
    "yaw_rate": pl.Float64,
    "pitch_rate": pl.Float64,
    "yaw_jerk": pl.Float64,
    "pitch_jerk": pl.Float64,
    "ticks_since_landing": pl.Int64,
    "air_ticks": pl.Int64,
    "headroom": pl.Int8,
    "underfoot": pl.String,
    "ground_depth": pl.Int8,
    "surface": pl.String,
    "sprint_method": pl.String,
    "screen_open": pl.Boolean,
}


def fill_gaps(ticks: pl.DataFrame) -> pl.DataFrame:
    """One row per tick inside each segment; missing rows copy the previous tick."""
    if ticks.is_empty():
        return ticks.with_columns(filled=pl.lit(False))
    ticks = ticks.unique(["segment_id", "tick"], keep="first", maintain_order=True)
    full = (
        ticks.group_by("segment_id", maintain_order=True)
        .agg(pl.int_ranges(pl.col("tick").min(), pl.col("tick").max() + 1).first().alias("tick"))
        .explode("tick")
        .with_columns(pl.col("tick").cast(pl.UInt64))
    )
    out = full.join(ticks.with_columns(filled=pl.lit(False)), on=["segment_id", "tick"], how="left")
    # A missing row copies the whole previous real row, nulls included: forward-filling column by
    # column would also fill genuine nulls (a closed screen, an emptied hand) in real rows.
    out = out.with_row_index("_row").with_columns(
        _src=pl.when(pl.col("filled").is_not_null()).then(pl.col("_row")).forward_fill()
    )
    copy = [c for c in ticks.columns if c not in ("segment_id", "tick", "t_ns")]
    return out.with_columns(
        pl.col(copy).gather(pl.col("_src")),
        pl.col("t_ns").cast(pl.Float64).interpolate().over("segment_id").cast(pl.UInt64),
        pl.col("filled").fill_null(True),
    ).select(ticks.columns + ["filled"])


def _segment_kinematics(df: pl.DataFrame) -> pl.DataFrame:
    seg = pl.col("segment_id")
    dyaw = pl.col("yaw").cast(pl.Float64).diff().over(seg)
    dyaw = ((dyaw + 180.0) % 360.0) - 180.0
    return df.with_columns(
        speed=(pl.col("vx").cast(pl.Float64) ** 2 + pl.col("vz").cast(pl.Float64) ** 2).sqrt()
        * 20.0,
        vspeed=pl.col("vy").cast(pl.Float64) * 20.0,
        yaw_rate=(dyaw * 20.0).fill_null(0.0),
        pitch_rate=(pl.col("pitch").cast(pl.Float64).diff().over(seg) * 20.0).fill_null(0.0),
    ).with_columns(
        accel=(pl.col("speed").diff().over(seg) * 20.0).fill_null(0.0),
        yaw_jerk=(pl.col("yaw_rate").diff().diff().over(seg) * 400.0).fill_null(0.0),
        pitch_jerk=(pl.col("pitch_rate").diff().diff().over(seg) * 400.0).fill_null(0.0),
    )


def _landing_counters(on_ground: np.ndarray, segment: np.ndarray) -> tuple[list, list]:
    since: list[int | None] = []
    air: list[int] = []
    last_landing = None
    air_run = 0
    prev_seg = None
    prev_ground = True
    for i, (g, s) in enumerate(zip(on_ground, segment, strict=True)):
        if s != prev_seg:
            last_landing, air_run, prev_seg, prev_ground = None, 0, s, bool(g)
        if g and not prev_ground:
            last_landing = i
        air_run = 0 if g else air_run + 1
        since.append(None if last_landing is None else i - last_landing)
        air.append(air_run)
        prev_ground = bool(g)
    return since, air


def _sprint_methods(
    sprinting: np.ndarray, k_sprint: np.ndarray, segment: np.ndarray, toggle: bool
) -> list[str | None]:
    out: list[str | None] = []
    method = None
    prev = False
    prev_seg = None
    for sp, key, s in zip(sprinting, k_sprint, segment, strict=True):
        if s != prev_seg:
            prev, prev_seg = False, s
        if sp and not prev:
            method = "key" if key else ("toggle" if toggle else "double_tap")
        out.append(method if sp else None)
        prev = bool(sp)
    return out


def _world_features(ticks: pl.DataFrame, world: pl.DataFrame) -> dict[str, list]:
    out: dict[str, list] = {"headroom": [], "underfoot": [], "ground_depth": []}
    w = World(world)
    for tick, x, y, z in ticks.select("tick", "x", "y", "z").iter_rows():
        w.advance_to(tick)
        bx, by, bz = math.floor(x), math.floor(y), math.floor(z)
        foot = math.floor(y - 0.01)
        out["underfoot"].append(w.get(bx, foot, bz))
        out["ground_depth"].append(_ground_depth(w, bx, foot, bz))
        room = 0
        for dy in (2, 3, 4):
            b = w.get(bx, by + dy, bz)
            if b is None:
                room = None
                break
            if blocks.solid(b):
                break
            room += 1
        out["headroom"].append(room)
    return out


def _ground_depth(w: World, x: int, y: int, z: int) -> int | None:
    """Passable blocks from (x, y, z) down to the first solid one, at most MAX_DEPTH; None if a
    block on the way was never seen."""
    for d in range(MAX_DEPTH):
        b = w.get(x, y - d, z)
        if b is None:
            return None
        if blocks.solid(b):
            return d
    return MAX_DEPTH


NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def short_id(column: str) -> pl.Expr:
    """'minecraft:oak_stairs[facing=north]' -> 'oak_stairs', like hbpipe.label.blocks.block_id."""
    return pl.col(column).str.replace(r"\[.*", "").str.strip_prefix("minecraft:")


def clean(ticks: pl.DataFrame, world: pl.DataFrame, header: pb.SessionHeader) -> pl.DataFrame:
    """Cleaned ticks for one session, sorted by segment then tick. Block, item and entity ids
    lose their "minecraft:" prefix."""
    df = fill_gaps(ticks.sort("tick", "t_ns"))
    df = df.with_columns(short_id(c) for c in ("held_item", "hit_block", "hit_entity_type"))
    flags = pl.col("flags")
    df = df.with_columns([((flags & bit) != 0).alias(name) for name, bit in FLAG_COLUMNS.items()])
    order = list(header.key_order)
    keys = pl.col("keys_down")
    df = df.with_columns(
        [
            ((keys & pl.lit(1 << order.index(binding), pl.UInt64)) != 0).alias(name)
            if binding in order
            else pl.lit(False).alias(name)
            for name, binding in KEY_COLUMNS.items()
        ]
    )
    df = _segment_kinematics(df)
    since, air = _landing_counters(df["on_ground"].to_numpy(), df["segment_id"].to_numpy())
    feats = _world_features(df, world)
    df = df.with_columns(
        pl.Series("ticks_since_landing", since, dtype=pl.Int64),
        pl.Series("air_ticks", air, dtype=pl.Int64),
        pl.Series("headroom", feats["headroom"], dtype=pl.Int8),
        pl.Series("underfoot", feats["underfoot"], dtype=pl.String),
        pl.Series("ground_depth", feats["ground_depth"], dtype=pl.Int8),
        pl.Series(
            "sprint_method",
            _sprint_methods(
                df["sprinting"].to_numpy(),
                df["k_sprint"].to_numpy(),
                df["segment_id"].to_numpy(),
                header.toggle_sprint,
            ),
            dtype=pl.String,
        ),
        screen_open=pl.col("screen").is_not_null(),
    ).with_columns(
        surface=pl.col("underfoot").map_elements(blocks.surface, return_dtype=pl.String),
    )
    return df


def enrich_events(events: pl.DataFrame, ticks: pl.DataFrame, world: pl.DataFrame) -> pl.DataFrame:
    """Adds the player's position at each event, and world context for placements and breaks:

        player_x, player_y, player_z    feet position at the event's tick
        below       block under a placed block when it was placed
        refill      a gravity block that fell into a broken block's place within 2 s
        open_sides  block_broken: how many of its 6 neighbours were already passable just
                    before the break (from everything seen so far, earlier segments included;
                    unseen neighbours count as closed). Hollowing out an existing room breaks
                    blocks with open sides; boring into rock breaks blocks with one.

    Ids lose their "minecraft:" prefix (and block states their properties), as in the ticks.
    """
    if events.is_empty():
        return events.with_columns(
            player_x=pl.lit(None, pl.Float64),
            player_y=pl.lit(None, pl.Float64),
            player_z=pl.lit(None, pl.Float64),
            below=pl.lit(None, pl.String),
            refill=pl.lit(None, pl.String),
            open_sides=pl.lit(None, pl.Int8),
        )
    events = events.sort("tick", "t_ns").with_columns(
        short_id(c) for c in ("block", "tool", "item_id", "entity_type")
    )
    pos = ticks.select("tick", player_x="x", player_y="y", player_z="z").sort("tick")
    events = events.join_asof(pos, on="tick", strategy="backward")

    # Block history: every change seen while replaying, per position.
    history: dict[tuple[int, int, int], list[tuple[int, str]]] = {}
    w = World(world)
    for tick in world["tick"].unique().sort():
        w.advance_to(tick)
        for p, _, after in w.changed:
            history.setdefault(p, []).append((tick, after))

    below: list[str | None] = []
    refill: list[str | None] = []
    open_sides: list[int | None] = []
    w = World(world)
    for kind, tick, x, y, z in events.select("kind", "tick", "x", "y", "z").iter_rows():
        b = r = o = None
        if kind == "block_placed" and x is not None:
            w.advance_to(tick - 1)
            b = w.get(x, y - 1, z)
        elif kind == "block_broken" and x is not None:
            w.advance_to(tick - 1)
            o = sum(
                1
                for dx, dy, dz in NEIGHBOURS
                if (n := w.get(x + dx, y + dy, z + dz)) is not None and blocks.passable(n)
            )
            for t, after in history.get((x, y, z), ()):
                if tick < t <= tick + 40 and blocks.falls(after):
                    r = after
                    break
        below.append(b)
        refill.append(r)
        open_sides.append(o)
    return events.with_columns(
        pl.Series("below", below, dtype=pl.String),
        pl.Series("refill", refill, dtype=pl.String),
        pl.Series("open_sides", open_sides, dtype=pl.Int8),
    )
