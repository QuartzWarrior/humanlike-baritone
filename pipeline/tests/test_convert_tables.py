import polars as pl

from hbpipe.convert.run import convert_all, play_day
from hbpipe.convert.tables import SCHEMAS, convert_envelopes
from hbpipe.schema import pb
from hbpipe.store import Store

S = 1_000_000_000


def env(tick: int, seg: str = "seg-0001", **body) -> pb.Envelope:
    return pb.Envelope(schema=1, segment_id=seg, tick=tick, t_ns=tick * S // 20, **body)


def recording(tick, seg="seg-0001"):
    return env(tick, seg, state=pb.StateChange(state=pb.StateChange.RECORDING))


def tick_state(tick, seg="seg-0001", **fields):
    return env(tick, seg, tick_state=pb.Tick(**fields))


def stream() -> list[pb.Envelope]:
    cube = pb.WorldCube(
        origin=pb.BlockPos(x=-4, y=60, z=-4),
        size=9,
        palette=["minecraft:air", "minecraft:stone"],
        indices=[1] * 81 + [0] * (729 - 81),
    )
    return [
        recording(10),
        env(10, cube=cube),
        tick_state(
            10,
            x=0.5,
            y=64.0,
            z=0.5,
            flags=pb.TICK_FLAG_ON_GROUND,
            held_item="minecraft:iron_pickaxe",
            main_hand=pb.ItemStack(id="minecraft:iron_pickaxe", count=1, damage=3, max_damage=250),
            hit=pb.HitResult(
                type=pb.HitResult.BLOCK,
                block_pos=pb.BlockPos(x=1, y=63, z=0),
                face=pb.DIRECTION_UP,
                block="minecraft:stone",
            ),
            entities=[pb.NearbyEntity(id=7, type="minecraft:cow", dx=2.0)],
            context=pb.TickContext(dimension="minecraft:overworld", screen=""),
        ),
        env(10, mouse=pb.MouseMove(dx=3.0, dy=-1.0, d_yaw=0.45, d_pitch=-0.15)),
        env(11, key=pb.Key(key=87, scancode=17, down=True, binding="key.forward")),
        env(11, scroll=pb.Scroll(dy=-1.0)),
        env(11, button=pb.Button(button=0, down=True)),
        env(
            12,
            event=pb.GameEvent(
                block_broken=pb.BlockBroken(
                    pos=pb.BlockPos(x=1, y=63, z=0),
                    block="minecraft:stone",
                    tool="minecraft:iron_pickaxe",
                    ticks_taken=8,
                    face=pb.DIRECTION_UP,
                )
            ),
        ),
        env(
            12,
            delta=pb.WorldDelta(
                origin=pb.BlockPos(x=-4, y=60, z=-4),
                changes=[pb.BlockChange(pos=pb.BlockPos(x=1, y=63, z=0), state="minecraft:air")],
            ),
        ),
        tick_state(11),
        tick_state(12),
        env(12, flag=pb.SegmentFlag(kind=pb.SegmentFlag.DEGRADED, reason="fps under 20")),
        env(
            13,
            state=pb.StateChange(
                state=pb.StateChange.PAUSED, reason_code=pb.PAUSE_REASON_IDLE, reason="idle 10 s"
            ),
        ),
        env(14, event=pb.GameEvent(chat_sent=pb.ChatSent(is_command=True))),  # while paused
        recording(40, "seg-0002"),
        tick_state(40, "seg-0002"),
        env(40, "seg-0002", cursor=pb.GuiCursor(x=100.0, y=50.0)),
        env(
            41,
            "seg-0002",
            slot=pb.SlotClick(
                slot_id=36,
                click_type=pb.CLICK_TYPE_QUICK_MOVE,
                slot_item=pb.ItemStack(id="minecraft:dirt", count=64),
                menu="minecraft:inventory",
            ),
        ),
        tick_state(41, "seg-0002"),
    ]


def test_every_table_gets_its_rows_and_keys():
    t = convert_envelopes(stream(), session_id="s-1", source="human", complete=False)
    assert set(t) == set(SCHEMAS)
    for name, df in t.items():
        assert df.schema == pl.Schema(SCHEMAS[name]), name
    assert t["ticks"].height == 5
    assert t["mouse"].height == 1 and t["keys"].height == 1
    assert t["buttons"]["kind"].to_list() == ["scroll", "button"]
    assert t["gui"]["kind"].to_list() == ["cursor", "slot"]
    assert t["world"]["kind"].to_list() == ["cube", "delta"]
    first = t["ticks"].row(0, named=True)
    assert first["session_id"] == "s-1" and first["segment_id"] == "seg-0001"
    assert first["tick"] == 10 and first["t_ns"] == 500_000_000
    assert first["hit_type"] == "block" and first["hit_face"] == "up"
    assert (first["hit_x"], first["hit_y"], first["hit_z"]) == (1, 63, 0)
    assert first["main_hand"]["max_damage"] == 250
    assert first["entities"][0]["type"] == "minecraft:cow"
    assert first["screen"] is None  # empty context strings become null


def test_events_flatten_into_shared_columns():
    t = convert_envelopes(stream(), session_id="s-1", source="human")
    ev = t["events"]
    broken = ev.filter(pl.col("kind") == "block_broken").row(0, named=True)
    assert (broken["x"], broken["y"], broken["z"]) == (1, 63, 0)
    assert broken["tool"] == "minecraft:iron_pickaxe" and broken["ticks_taken"] == 8
    assert broken["while_paused"] is False
    chat = ev.filter(pl.col("kind") == "chat_sent").row(0, named=True)
    assert chat["is_command"] is True and chat["while_paused"] is True


def test_segments_record_how_they_ended():
    t = convert_envelopes(stream(), session_id="s-1", source="human", complete=False)
    segs = t["segments"].to_dicts()
    assert [s["segment_id"] for s in segs] == ["seg-0001", "seg-0002"]
    assert segs[0]["end_state"] == "paused" and segs[0]["end_reason"] == "idle"
    assert segs[0]["ticks"] == 3 and segs[0]["degraded"] and not segs[0]["suspect_afk"]
    assert segs[0]["flags"] == [{"kind": "degraded", "reason": "fps under 20"}]
    assert segs[1]["end_state"] == "crashed"  # no pause record, session not closed


def test_discards_are_applied_before_conversion():
    from hbpipe.convert.session import drop_discarded

    envs = stream()
    envs.append(env(41, "seg-0002", discard=pb.Discard(from_t_ns=0, to_t_ns=10**18)))
    t = convert_envelopes(drop_discarded(envs), session_id="s-1", source="human")
    assert all(df.height == 0 for df in t.values())


def test_convert_all_writes_tables_and_index_and_skips_done_sessions(tmp_path):
    import zstandard
    from google.protobuf import json_format

    from hbpipe.convert import hbr

    d = tmp_path / "sessions" / "s-1"
    d.mkdir(parents=True)
    header = pb.SessionHeader(
        schema=1, session_id="s-1", source=pb.SOURCE_HUMAN, started_unix_ms=1_790_000_000_000
    )
    (d / "header.json").write_text(json_format.MessageToJson(header), encoding="utf-8")
    (d / "chunk-0000.hbr.zst").write_bytes(
        zstandard.ZstdCompressor().compress(hbr.encode(stream()))
    )
    (d / "stats.json").write_text('{"duration_s": 2.0, "records": 20}', encoding="utf-8")

    first = convert_all(tmp_path)
    assert [(r.session_id, r.skipped, r.ticks, r.segments) for r in first] == [("s-1", False, 5, 2)]
    assert convert_all(tmp_path)[0].skipped
    store = Store(tmp_path)
    assert store.read("ticks", "s-1").height == 5
    row = store.session("s-1")
    assert row["source"] == "human" and row["complete"] == 1
    assert row["play_day"] == play_day(1_790_000_000_000)
    segs = store.db.execute("SELECT segment_id, end_reason FROM segments").fetchall()
    assert [tuple(s) for s in segs] == [("seg-0001", "idle"), ("seg-0002", None)]
    flags = store.db.execute("SELECT kind, reason FROM flags").fetchall()
    assert [tuple(f) for f in flags] == [("degraded", "fps under 20")]
    store.close()


def test_play_days_roll_over_at_4am():
    import datetime as dt

    def ms(h):
        return int(dt.datetime(2026, 10, 26, h, 30).timestamp() * 1000)

    assert play_day(ms(3)) == "2026-10-25"
    assert play_day(ms(5)) == "2026-10-26"
