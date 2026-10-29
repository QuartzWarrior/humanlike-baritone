"""Synthetic world records (cubes and deltas) for tests."""

import polars as pl

from hbpipe.convert.tables import SCHEMAS

TICK_NS = 50_000_000


def cube(origin=(-4, 60, -4), fill=None, tick=0) -> dict:
    """A 9x9x9 cube of air with stone wherever ``fill(x, y, z)`` is true."""
    ox, oy, oz = origin
    indices = []
    for dy in range(9):
        for dz in range(9):
            for dx in range(9):
                indices.append(1 if fill and fill(ox + dx, oy + dy, oz + dz) else 0)
    return {
        "session_id": "s",
        "segment_id": "seg-0001",
        "tick": tick,
        "t_ns": tick * TICK_NS,
        "kind": "cube",
        "origin_x": ox,
        "origin_y": oy,
        "origin_z": oz,
        "size": 9,
        "palette": ["minecraft:air", "minecraft:stone"],
        "indices": indices,
    }


def delta(tick: int, *changes: tuple[int, int, int, str]) -> dict:
    return {
        "session_id": "s",
        "segment_id": "seg-0001",
        "tick": tick,
        "t_ns": tick * TICK_NS,
        "kind": "delta",
        "changes": [{"x": x, "y": y, "z": z, "state": s} for x, y, z, s in changes],
    }


def world(*rows: dict) -> pl.DataFrame:
    return pl.from_dicts(list(rows), schema=SCHEMAS["world"])
