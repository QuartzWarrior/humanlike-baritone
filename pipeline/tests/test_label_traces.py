import polars as pl
from worldkit import cube, delta, world

from hbpipe.convert.tables import SCHEMAS
from hbpipe.label.run import LABELS_SCHEMA
from hbpipe.label.traces import MARGIN, extract

TICK_NS = 50_000_000


def events(rows: list[dict]) -> pl.DataFrame:
    extra = {"player_x": pl.Float64, "player_y": pl.Float64, "player_z": pl.Float64}
    full = [
        {"session_id": "s", "segment_id": "seg-0001", "t_ns": r["tick"] * TICK_NS} | r for r in rows
    ]
    return pl.from_dicts(
        full, schema=SCHEMAS["events"] | extra | {"below": pl.String, "refill": pl.String}
    )


def label(start: int, end: int, name: str = "mine.tunnel") -> pl.DataFrame:
    return pl.from_dicts(
        [
            {
                "label_id": "x",
                "session_id": "s",
                "segment_id": "seg-0001",
                "start_tick": start,
                "end_tick": end,
                "label": name,
                "level": "primitive",
                "confidence": 0.9,
                "detector_version": 1,
            }
        ],
        schema=LABELS_SCHEMA,
    )


def ticks(n: int) -> pl.DataFrame:
    return pl.DataFrame(
        {"tick": list(range(n)), "yaw": [float(i) for i in range(n)], "pitch": [10.0] * n},
        schema={"tick": pl.UInt64, "yaw": pl.Float32, "pitch": pl.Float32},
    )


def tunnel_events() -> pl.DataFrame:
    """Two breaks heading east: start mining (2,64,0) at tick 10, break at 16; walk one block;
    start (3,64,0) at 20, break at 25."""
    rows = []
    for x, start, end, px in ((2, 10, 16, 0.5), (3, 20, 25, 1.5)):
        stand = {"player_x": px, "player_y": 64.0, "player_z": 0.5}
        rows.append({"tick": start, "kind": "destroy_started", "x": x, "y": 64, "z": 0} | stand)
        rows.append(
            {
                "tick": end,
                "kind": "block_broken",
                "x": x,
                "y": 64,
                "z": 0,
                "block": "deepslate",
                "tool": "diamond_pickaxe",
                "ticks_taken": end - start,
            }
            | stand
        )
    return events(rows)


def test_trace_steps_record_order_stance_and_decision_time():
    w = world(cube(fill=lambda x, y, z: y >= 64 and x >= 2, tick=0))
    traces, volumes = extract("s", label(5, 30), tunnel_events(), ticks(40), w)
    assert traces["step"].to_list() == [0, 1]
    assert traces["decision_tick"].to_list() == [10, 20]
    assert traces["tick"].to_list() == [16, 25]
    assert traces["x"].to_list() == [2, 3]
    assert traces["yaw"].to_list() == [10.0, 20.0]  # look at the decision, not the break
    assert traces["moved"].to_list() == [False, True]
    assert traces["ticks_since_prev"].to_list() == [None, 9]
    assert traces["tool"].to_list() == ["diamond_pickaxe"] * 2
    assert traces["trace_id"].n_unique() == 1


def test_volume_is_the_world_at_job_start_around_the_breaks():
    # The cube saw x -4..4 at the start; x 5 shows up mid-job.
    w = world(
        cube(fill=lambda x, y, z: y >= 64 and x >= 2, tick=0),
        delta(22, (5, 64, 0, "minecraft:diamond_ore")),
    )
    _, volumes = extract("s", label(5, 30), tunnel_events(), ticks(40), w)
    side = 2 + 2 * MARGIN  # breaks span x 2..3, one block in y and z
    assert volumes.height <= side * (1 + 2 * MARGIN) ** 2
    v = {(r["x"], r["y"], r["z"]): (r["block"], r["seen_at_start"]) for r in volumes.to_dicts()}
    assert v[(2, 64, 0)] == ("deepslate", True)  # broken blocks take the event's id
    assert v[(1, 64, 0)] == ("air", True)
    assert v[(5, 64, 0)] == ("diamond_ore", False)  # first seen during the job


def test_only_mining_job_labels_make_traces():
    w = world(cube(tick=0))
    traces, volumes = extract("s", label(5, 30, "move.walk"), tunnel_events(), ticks(40), w)
    assert traces.is_empty() and volumes.is_empty()
