import polars as pl
import pytest
from labelkit import seg
from worldkit import cube, delta, world

from hbpipe.convert.tables import SCHEMAS
from hbpipe.label.base import Span, runs
from hbpipe.label.clean import clean, enrich_events, fill_gaps
from hbpipe.label.run import UNLABELED, label_id, unlabeled
from hbpipe.label.world import World
from hbpipe.schema import pb

TICK_NS = 50_000_000


def ticks(rows: list[dict]) -> pl.DataFrame:
    base = {"session_id": "s", "segment_id": "seg-0001", "flags": 0, "keys_down": 0}
    base |= {
        "x": 0.5,
        "y": 64.0,
        "z": 0.5,
        "vx": 0.0,
        "vy": 0.0,
        "vz": 0.0,
        "yaw": 0.0,
        "pitch": 0.0,
    }
    full = [base | {"t_ns": r["tick"] * TICK_NS} | r for r in rows]
    return pl.from_dicts(full, schema=SCHEMAS["ticks"])


HEADER = pb.SessionHeader(key_order=["key.attack", "key.forward", "key.sprint"])


def test_world_replays_cubes_and_deltas():
    w = World(
        world(
            cube(fill=lambda x, y, z: y < 64),
            delta(5, (0, 63, 0, "minecraft:air[waterlogged=false]")),
        )
    )
    w.advance_to(4)
    assert w.get(0, 63, 0) == "stone" and w.get(0, 64, 0) == "air"
    assert w.get(100, 0, 0) is None  # never seen
    w.advance_to(5)
    assert w.get(0, 63, 0) == "air"
    assert w.changed == [((0, 63, 0), "stone", "air")]


def test_fill_gaps_copies_the_previous_tick_and_interpolates_time():
    df = fill_gaps(ticks([{"tick": 1, "x": 1.0}, {"tick": 4, "x": 4.0}]))
    assert df["tick"].to_list() == [1, 2, 3, 4]
    assert df["x"].to_list() == [1.0, 1.0, 1.0, 4.0]
    assert df["filled"].to_list() == [False, True, True, False]
    assert df["t_ns"].to_list() == [50_000_000, 100_000_000, 150_000_000, 200_000_000]


def test_fill_gaps_keeps_real_nulls():
    rows = [
        {"tick": 1, "screen": "InventoryScreen", "held_item": "minecraft:dirt"},
        {"tick": 2, "screen": None, "held_item": None},  # closed, hand emptied
        {"tick": 4, "screen": None, "held_item": None},
    ]
    df = fill_gaps(ticks(rows))
    assert df["screen"].to_list() == ["InventoryScreen", None, None, None]
    assert df["held_item"].to_list() == ["minecraft:dirt", None, None, None]
    assert df["filled"].to_list() == [False, False, True, False]


def test_fill_gaps_never_bridges_segments():
    a = ticks([{"tick": 1}, {"tick": 2}])
    b = ticks([{"tick": 40}]).with_columns(segment_id=pl.lit("seg-0002"))
    assert fill_gaps(pl.concat([a, b])).height == 3


def test_clean_decodes_flags_keys_and_kinematics():
    rows = [
        {
            "tick": 1,
            "yaw": 179.0,
            "vx": 0.2,
            "flags": pb.TICK_FLAG_ON_GROUND | pb.TICK_FLAG_SPRINTING,
            "keys_down": 0b110,
        },
        {"tick": 2, "yaw": -179.0, "vx": 0.3, "flags": pb.TICK_FLAG_SPRINTING},
        {"tick": 3, "yaw": -178.0, "vx": 0.3, "flags": 0},
        {"tick": 4, "yaw": -178.0, "vx": 0.0, "flags": pb.TICK_FLAG_ON_GROUND},
    ]
    df = clean(ticks(rows), world(), HEADER)
    assert df["on_ground"].to_list() == [True, False, False, True]
    assert df["sprinting"].to_list() == [True, True, False, False]
    assert df["k_forward"].to_list() == [True, False, False, False]
    assert df["k_sprint"][0] and not df["k_attack"][0] and not df["k_jump"][0]
    assert df["yaw_rate"].to_list() == pytest.approx([0.0, 40.0, 20.0, 0.0])  # unwrapped
    assert df["speed"].to_list() == pytest.approx([4.0, 6.0, 6.0, 0.0])
    assert df["accel"].to_list() == pytest.approx([0.0, 40.0, 0.0, -120.0])
    assert df["air_ticks"].to_list() == [0, 1, 2, 0]
    assert df["ticks_since_landing"].to_list() == [None, None, None, 0]
    assert df["sprint_method"].to_list() == ["key", "key", None, None]


def test_clean_reads_headroom_and_underfoot_from_the_world():
    # Stone floor at y 63, a ceiling at y 66 over x >= 2: 2-high gap there, open sky elsewhere.
    def solid(x, y, z):
        return y == 63 or (y == 66 and x >= 2)

    rows = [{"tick": 1, "x": 0.5, "y": 64.0, "z": 0.5}, {"tick": 2, "x": 2.5, "y": 64.0, "z": 0.5}]
    df = clean(ticks(rows), world(cube(fill=solid, tick=1)), HEADER)
    assert df["underfoot"].to_list() == ["stone", "stone"]
    assert df["ground_depth"].to_list() == [0, 0]
    assert df["headroom"].to_list() == [3, 0]
    # Mid-jump, 1.2 blocks up: the two blocks under the feet are air, then the floor.
    airborne = [{"tick": 1, "x": 0.5, "y": 65.2, "z": 0.5}]
    df = clean(ticks(airborne), world(cube(fill=solid, tick=1)), HEADER)
    assert df["underfoot"].to_list() == ["air"]
    assert df["ground_depth"].to_list() == [2]
    # A cube spanning y 56-64 never saw the blocks above the head: unknown, not open.
    df = clean(ticks(rows[:1]), world(cube(origin=(-4, 56, -4), fill=solid, tick=1)), HEADER)
    assert df["headroom"].to_list() == [None]


def test_enrich_events_adds_position_below_and_refill():
    t = clean(ticks([{"tick": 1, "x": 0.5, "y": 64.0, "z": 0.5}, {"tick": 9}]), world(), HEADER)
    w = world(
        cube(fill=lambda x, y, z: y < 64, tick=0),
        delta(3, (1, 64, 1, "minecraft:dirt")),
        delta(5, (2, 63, 0, "minecraft:air")),
        delta(8, (2, 63, 0, "minecraft:gravel")),
    )
    ev = pl.from_dicts(
        [
            {"tick": 3, "kind": "block_placed", "x": 1, "y": 64, "z": 1, "block": "minecraft:dirt"},
            {
                "tick": 5,
                "kind": "block_broken",
                "x": 2,
                "y": 63,
                "z": 0,
                "block": "minecraft:gravel",
            },
        ],
        schema=SCHEMAS["events"],
    ).with_columns(t_ns=pl.col("tick") * TICK_NS, session_id=pl.lit("s"))
    out = enrich_events(ev, t, w)
    assert out["below"].to_list() == ["stone", None]
    assert out["refill"].to_list() == [None, "gravel"]
    assert out["open_sides"].to_list() == [None, 1]  # stone below y 64: only the top is open
    assert out["block"].to_list() == ["dirt", "gravel"]
    assert out["player_x"].to_list() == [0.5, 0.5]


def test_runs_merges_gaps_then_drops_short_runs():
    m = [True, True, False, True, False, False, False, True]
    assert runs(m) == [(0, 1), (3, 3), (7, 7)]
    assert runs(m, max_gap=1) == [(0, 3), (7, 7)]
    assert runs(m, max_gap=1, min_len=2) == [(0, 3)]
    assert runs([False, False]) == []


def test_unlabeled_fills_what_no_primitive_covers():
    s = seg(10)
    spans = [Span(1001, 1003, "move.walk", 0.9), Span(1003, 1004, "move.strafe", 0.8)]
    gaps = unlabeled(s, spans)
    assert [(g.start_tick, g.end_tick) for g in gaps] == [(1000, 1000), (1005, 1009)]
    assert {g.label for g in gaps} == {UNLABELED}


def test_label_ids_are_stable_and_distinct():
    a = Span(1, 5, "move.walk", 0.9)
    assert label_id("s", "seg-0001", a) == label_id("s", "seg-0001", Span(1, 5, "move.walk", 0.5))
    assert label_id("s", "seg-0001", a) != label_id("s", "seg-0001", Span(1, 6, "move.walk", 0.9))
