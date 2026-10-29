"""Synthetic segments for detector tests.

    s = seg(60, speed=4.3, k_forward=True)                 # 60 ticks of walking
    s = seg(40, y=[64.0] * 20 + [65.0] * 20)               # per-tick lists or one value for all
    s = seg(100, events=[ev(10, "block_broken", x=1, y=63, z=0, block="stone")])

Columns not given take a standing-still default: on the ground at (0.5, 64, 0.5), facing south,
no keys, nothing in hand, no screen. Derived columns (speed, headroom, ...) are not recomputed
from position; set the ones a detector reads. Events, mouse, keys, buttons and gui rows only need
the fields a test cares about.
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from hbpipe.convert.tables import SCHEMAS
from hbpipe.label.base import Seg, Span, run_detector
from hbpipe.label.clean import DERIVED_SCHEMA, FLAG_COLUMNS, KEY_COLUMNS
from hbpipe.schema import pb

TICK_NS = 50_000_000
START_TICK = 1000

TICK_DEFAULTS = {
    "x": 0.5,
    "y": 64.0,
    "z": 0.5,
    "vx": 0.0,
    "vy": 0.0,
    "vz": 0.0,
    "yaw": 0.0,
    "pitch": 0.0,
    "flags": 0,
    "keys_down": 0,
    "slot": 0,
    "hit_type": "miss",
    "destroy_progress": 0.0,
    "attack_cooldown": 1.0,
    "health": 20.0,
    "food": 20,
    "saturation": 5.0,
    "air": 300,
    "fall_distance": 0.0,
    "fps": 144.0,
    "armor": [],
    "effects": [],
    "entities": [],
    "dimension": "minecraft:overworld",
    "block_light": 0,
    "sky_light": 15,
    "ping_ms": 0,
    # derived
    "filled": False,
    **{c: False for c in FLAG_COLUMNS},
    **{c: False for c in KEY_COLUMNS},
    "on_ground": True,
    "speed": 0.0,
    "vspeed": 0.0,
    "accel": 0.0,
    "yaw_rate": 0.0,
    "pitch_rate": 0.0,
    "yaw_jerk": 0.0,
    "pitch_jerk": 0.0,
    "ticks_since_landing": 100,
    "air_ticks": 0,
    "headroom": 3,
    "underfoot": "stone",
    "ground_depth": 0,
    "surface": "normal",
    "screen_open": False,
}

EVENT_EXTRA = {
    "player_x": pl.Float64,
    "player_y": pl.Float64,
    "player_z": pl.Float64,
    "below": pl.String,
    "refill": pl.String,
    "open_sides": pl.Int8,
}

KEY_ORDER = [
    "key.attack",
    "key.use",
    "key.forward",
    "key.left",
    "key.back",
    "key.right",
    "key.jump",
    "key.sneak",
    "key.sprint",
    "key.drop",
    "key.inventory",
] + [f"key.hotbar.{i}" for i in range(1, 10)]


def ev(tick: int, kind: str, **fields) -> dict:
    """An event row at a segment-relative tick (0 = the segment's first tick)."""
    return {"tick": tick, "kind": kind, **fields}


def row(tick: int, **fields) -> dict:
    """A mouse, key, button or gui row at a segment-relative tick."""
    return {"tick": tick, **fields}


def _frame(rows: Sequence[dict], schema: dict, segment_id: str) -> pl.DataFrame:
    full = []
    for r in rows:
        r = dict(r)
        tick = START_TICK + r.pop("tick")
        sub = r.pop("t_offset_ns", 0)
        full.append(
            {
                "session_id": "test-session",
                "segment_id": segment_id,
                "tick": tick,
                "t_ns": tick * TICK_NS + sub,
                **r,
            }
        )
    if not full:
        return pl.DataFrame(schema=schema)
    return pl.from_dicts(full, schema=schema).sort("tick", "t_ns")


def seg(
    n: int,
    *,
    events: Sequence[dict] = (),
    mouse: Sequence[dict] = (),
    keys: Sequence[dict] = (),
    buttons: Sequence[dict] = (),
    gui: Sequence[dict] = (),
    labels: Sequence[Span] = (),
    header: pb.SessionHeader | None = None,
    segment_id: str = "seg-0001",
    **columns,
) -> Seg:
    """A segment of ``n`` ticks. Ticks are numbered from START_TICK; use ``t(i)`` for the tick
    number of row i in assertions."""
    data = {}
    schema = SCHEMAS["ticks"] | DERIVED_SCHEMA
    for name, dtype in schema.items():
        if name in ("session_id", "segment_id", "tick", "t_ns"):
            continue
        value = columns.pop(name, TICK_DEFAULTS.get(name))
        if isinstance(dtype, pl.List):
            # One list for every tick, or a list of n per-tick lists.
            per_tick = len(value) == n and n > 0 and all(isinstance(v, list) for v in value)
            data[name] = list(value) if per_tick else [list(value)] * n
        elif isinstance(value, list | tuple):
            if len(value) != n:
                raise ValueError(f"{name}: {len(value)} values for {n} ticks")
            data[name] = list(value)
        else:
            data[name] = [value] * n
    if columns:
        raise TypeError(f"unknown tick columns: {sorted(columns)}")
    ticks = pl.DataFrame(
        {
            "session_id": ["test-session"] * n,
            "segment_id": [segment_id] * n,
            "tick": [START_TICK + i for i in range(n)],
            "t_ns": [(START_TICK + i) * TICK_NS for i in range(n)],
            **data,
        },
        schema=schema,
    )
    if header is None:
        header = pb.SessionHeader(session_id="test-session", key_order=KEY_ORDER)
    return Seg(
        session_id="test-session",
        segment_id=segment_id,
        header=header,
        ticks=ticks,
        events=_frame(events, SCHEMAS["events"] | EVENT_EXTRA, segment_id),
        mouse=_frame(mouse, SCHEMAS["mouse"], segment_id),
        keys=_frame(keys, SCHEMAS["keys"], segment_id),
        buttons=_frame(buttons, SCHEMAS["buttons"], segment_id),
        gui=_frame(gui, SCHEMAS["gui"], segment_id),
        labels=list(labels),
    )


def t(i: int) -> int:
    """Tick number of segment row ``i``."""
    return START_TICK + i


def detect(name: str, s: Seg) -> list[Span]:
    """Runs one registered detector (importing all detector modules first)."""
    from hbpipe.label import detectors  # noqa: F401  (registers everything)
    from hbpipe.label.base import REGISTRY

    return run_detector(REGISTRY[name], s)


def spans_of(name: str, s: Seg) -> list[tuple[int, int]]:
    """(start_row, end_row) of each span, segment-relative, for compact assertions."""
    return [(sp.start_tick - START_TICK, sp.end_tick - START_TICK) for sp in detect(name, s)]


def concat(*parts: dict) -> dict:
    """Joins per-part column dicts into per-tick lists: concat({"speed": [4.3] * 10},
    {"speed": [0.0] * 5}) == {"speed": [4.3] * 10 + [0.0] * 5}. Every part needs the same keys."""
    out: dict[str, list] = {}
    for p in parts:
        for k, v in p.items():
            out.setdefault(k, []).extend(v)
    return out
