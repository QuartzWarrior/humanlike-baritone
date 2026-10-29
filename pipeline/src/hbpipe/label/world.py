"""Replays the recorded world: a full 9x9x9 cube at each segment start, then per-tick changes
(including blocks that come into view as the cube follows the player).

Only blocks that have been near the player are known. Positions that left the cube keep their
last seen state.
"""

from __future__ import annotations

from collections.abc import Iterator

import polars as pl

from hbpipe.label.blocks import block_id

Pos = tuple[int, int, int]


class World:
    """Known block ids, advanced in record order with :meth:`advance_to`."""

    def __init__(self, world: pl.DataFrame):
        self._rows = world.sort("tick", "t_ns", maintain_order=True).iter_rows(named=True)
        self._next: dict | None = next(self._rows, None)
        self.blocks: dict[Pos, str] = {}
        self.tick = -1
        self.changed: list[tuple[Pos, str, str]] = []  # (pos, before, after) of the last advance

    def advance_to(self, tick: int) -> None:
        """Applies every world record up to and including ``tick`` (a tick's block changes are
        recorded before its tick state, so after this the world matches that tick)."""
        self.changed = []
        while self._next is not None and self._next["tick"] <= tick:
            self._apply(self._next)
            self._next = next(self._rows, None)
        self.tick = tick

    def _apply(self, row: dict) -> None:
        if row["kind"] == "cube":
            size = row["size"]
            palette = [block_id(s) for s in row["palette"]]
            ox, oy, oz = row["origin_x"], row["origin_y"], row["origin_z"]
            i = 0
            # Cube indices run x fastest, then z, then y (McWorldCube.index).
            for dy in range(size):
                for dz in range(size):
                    for dx in range(size):
                        self._set((ox + dx, oy + dy, oz + dz), palette[row["indices"][i]])
                        i += 1
        else:
            for c in row["changes"]:
                self._set((c["x"], c["y"], c["z"]), block_id(c["state"]))

    def _set(self, pos: Pos, block: str) -> None:
        before = self.blocks.get(pos)
        if before != block:
            self.blocks[pos] = block
            if before is not None:
                self.changed.append((pos, before, block))

    def get(self, x: int, y: int, z: int) -> str | None:
        """Block id at a position, or None if never seen."""
        return self.blocks.get((x, y, z))


def replay(world: pl.DataFrame, ticks: Iterator[int]) -> Iterator[tuple[int, World]]:
    """Yields the world at each of ``ticks`` (ascending)."""
    w = World(world)
    for tick in ticks:
        w.advance_to(tick)
        yield tick, w
