"""Session records to training tables.

Every table keys on session_id, segment_id, tick and t_ns, so any model can join motor data to
game state. F9 discards are applied before anything is converted, and so is the detached-camera
filter (hbpipe.convert.camera) for sessions recorded before the recorder caught freecam.

    ticks     one row per client tick while recording: position, motion, look, held keys, hands,
              crosshair target, vitals, nearby entities, context
    mouse     raw mouse turns, one row per frame with motion
    keys      key presses and releases (bound or GUI keys only; the recorder writes no others)
    buttons   mouse buttons and the scroll wheel (kind = button | scroll)
    gui       cursor motion and slot clicks inside screens (kind = cursor | slot)
    events    discrete game events in one shared column set (kind = block_broken, ...)
    world     the 9x9x9 block cube at each segment start and the per-tick changes after it
              (kind = cube | delta)
    segments  one row per recording segment: span, how it ended, flags
The labeler (hbpipe.label) adds a labels table.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import polars as pl

from hbpipe.convert.camera import drop_detached_camera
from hbpipe.convert.session import Session
from hbpipe.schema import pb

FLUSH_EVERY = 20_000  # records
CONVERTER_VERSION = 2  # 2: drops detached-camera play in older sessions

KEY = {
    "session_id": pl.String,
    "segment_id": pl.String,
    "tick": pl.UInt64,
    "t_ns": pl.UInt64,
}

ENCHANTMENT = pl.Struct({"id": pl.String, "level": pl.Int32})
ITEM = pl.Struct(
    {
        "id": pl.String,
        "count": pl.Int32,
        "damage": pl.Int32,
        "max_damage": pl.Int32,
        "enchantments": pl.List(ENCHANTMENT),
    }
)
ENTITY = pl.Struct(
    {
        "id": pl.Int32,
        "type": pl.String,
        "dx": pl.Float32,
        "dy": pl.Float32,
        "dz": pl.Float32,
        "vx": pl.Float32,
        "vy": pl.Float32,
        "vz": pl.Float32,
        "health": pl.Float32,
        "yaw": pl.Float32,
    }
)
EFFECT = pl.Struct({"id": pl.String, "amplifier": pl.Int32, "duration_ticks": pl.Int32})
BLOCK_CHANGE = pl.Struct({"x": pl.Int32, "y": pl.Int32, "z": pl.Int32, "state": pl.String})

SCHEMAS: dict[str, dict[str, pl.DataType]] = {
    "ticks": {
        **KEY,
        "x": pl.Float64,
        "y": pl.Float64,
        "z": pl.Float64,
        "vx": pl.Float32,
        "vy": pl.Float32,
        "vz": pl.Float32,
        "yaw": pl.Float32,
        "pitch": pl.Float32,
        "flags": pl.UInt32,
        "keys_down": pl.UInt64,
        "slot": pl.Int32,
        "held_item": pl.String,
        "hit_type": pl.String,
        "hit_x": pl.Int32,
        "hit_y": pl.Int32,
        "hit_z": pl.Int32,
        "hit_face": pl.String,
        "hit_block": pl.String,
        "hit_entity_id": pl.Int32,
        "hit_entity_type": pl.String,
        "hit_loc_x": pl.Float64,
        "hit_loc_y": pl.Float64,
        "hit_loc_z": pl.Float64,
        "destroy_progress": pl.Float32,
        "attack_cooldown": pl.Float32,
        "health": pl.Float32,
        "food": pl.Int32,
        "saturation": pl.Float32,
        "air": pl.Int32,
        "fall_distance": pl.Float32,
        "fps": pl.Float32,
        "main_hand": ITEM,
        "off_hand": ITEM,
        "armor": pl.List(ITEM),
        "effects": pl.List(EFFECT),
        "entities": pl.List(ENTITY),
        "breaking_x": pl.Int32,
        "breaking_y": pl.Int32,
        "breaking_z": pl.Int32,
        "dimension": pl.String,
        "biome": pl.String,
        "block_light": pl.Int32,
        "sky_light": pl.Int32,
        "day_time": pl.Int64,
        "screen": pl.String,
        "ping_ms": pl.Int32,
    },
    "mouse": {
        **KEY,
        "dx": pl.Float64,
        "dy": pl.Float64,
        "d_yaw": pl.Float32,
        "d_pitch": pl.Float32,
    },
    "keys": {
        **KEY,
        "key": pl.Int32,
        "scancode": pl.Int32,
        "down": pl.Boolean,
        "binding": pl.String,
        "mods": pl.Int32,
    },
    "buttons": {
        **KEY,
        "kind": pl.String,
        "button": pl.Int32,
        "down": pl.Boolean,
        "mods": pl.Int32,
        "in_gui": pl.Boolean,
        "scroll_dx": pl.Float64,
        "scroll_dy": pl.Float64,
    },
    "gui": {
        **KEY,
        "kind": pl.String,
        "x": pl.Float64,
        "y": pl.Float64,
        "slot_id": pl.Int32,
        "button": pl.Int32,
        "click_type": pl.String,
        "slot_item": ITEM,
        "carried": ITEM,
        "menu": pl.String,
        "container_id": pl.Int32,
    },
    "events": {
        **KEY,
        "kind": pl.String,
        "while_paused": pl.Boolean,
        "x": pl.Int32,
        "y": pl.Int32,
        "z": pl.Int32,
        "face": pl.String,
        "block": pl.String,
        "tool": pl.String,
        "ticks_taken": pl.UInt32,
        "clicked_x": pl.Int32,
        "clicked_y": pl.Int32,
        "clicked_z": pl.Int32,
        "hand": pl.String,
        "hit_x": pl.Float64,
        "hit_y": pl.Float64,
        "hit_z": pl.Float64,
        "item": ITEM,
        "item_id": pl.String,
        "whole_stack": pl.Boolean,
        "result_kind": pl.String,
        "menu": pl.String,
        "quick_move": pl.Boolean,
        "entity_id": pl.Int32,
        "entity_type": pl.String,
        "amount": pl.Float32,
        "critical": pl.Boolean,
        "sprinting": pl.Boolean,
        "attack_cooldown": pl.Float32,
        "damage_source": pl.String,
        "from_dimension": pl.String,
        "to_dimension": pl.String,
        "screen": pl.String,
        "gui_width": pl.Int32,
        "gui_height": pl.Int32,
        "is_command": pl.Boolean,
        "progress": pl.Float32,
        "before_x": pl.Float64,
        "before_y": pl.Float64,
        "before_z": pl.Float64,
        "after_x": pl.Float64,
        "after_y": pl.Float64,
        "after_z": pl.Float64,
        "sound": pl.String,
        "category": pl.String,
        "dx": pl.Float32,
        "dy": pl.Float32,
        "dz": pl.Float32,
        "volume": pl.Float32,
        "interaction": pl.String,
    },
    "world": {
        **KEY,
        "kind": pl.String,
        "origin_x": pl.Int32,
        "origin_y": pl.Int32,
        "origin_z": pl.Int32,
        "size": pl.UInt32,
        "palette": pl.List(pl.String),
        "indices": pl.List(pl.UInt32),
        "changes": pl.List(BLOCK_CHANGE),
    },
    "segments": {
        "session_id": pl.String,
        "segment_id": pl.String,
        "source": pl.String,
        "start_tick": pl.UInt64,
        "start_t_ns": pl.UInt64,
        "end_tick": pl.UInt64,
        "end_t_ns": pl.UInt64,
        "end_state": pl.String,
        "end_reason": pl.String,
        "end_detail": pl.String,
        "ticks": pl.UInt32,
        "degraded": pl.Boolean,
        "suspect_afk": pl.Boolean,
        "flags": pl.List(pl.Struct({"kind": pl.String, "reason": pl.String})),
    },
}
TABLES = tuple(SCHEMAS)


def _enum_name(value: int, enum, prefix: str) -> str | None:
    """'DIRECTION_NORTH' -> 'north'; None for the unspecified zero value."""
    if not value:
        return None
    return enum.Name(value).removeprefix(prefix).lower()


def _direction(value: int) -> str | None:
    return _enum_name(value, pb.Direction, "DIRECTION_")


def _hand(value: int) -> str | None:
    return _enum_name(value, pb.Hand, "HAND_")


def _item(stack: pb.ItemStack) -> dict | None:
    if not stack.id:
        return None
    return {
        "id": stack.id,
        "count": stack.count,
        "damage": stack.damage,
        "max_damage": stack.max_damage,
        "enchantments": [{"id": e.id, "level": e.level} for e in stack.enchantments],
    }


def _tick_row(t: pb.Tick) -> dict:
    row = {
        "x": t.x,
        "y": t.y,
        "z": t.z,
        "vx": t.vx,
        "vy": t.vy,
        "vz": t.vz,
        "yaw": t.yaw,
        "pitch": t.pitch,
        "flags": t.flags,
        "keys_down": t.keys_down,
        "slot": t.slot,
        "held_item": t.held_item or None,
        "destroy_progress": t.destroy_progress,
        "attack_cooldown": t.attack_cooldown,
        "health": t.health,
        "food": t.food,
        "saturation": t.saturation,
        "air": t.air,
        "fall_distance": t.fall_distance,
        "fps": t.fps,
        "main_hand": _item(t.main_hand),
        "off_hand": _item(t.off_hand),
        "armor": [_item(a) for a in t.armor],
        "effects": [
            {"id": e.id, "amplifier": e.amplifier, "duration_ticks": e.duration_ticks}
            for e in t.effects
        ],
        "entities": [
            {
                "id": e.id,
                "type": e.type,
                "dx": e.dx,
                "dy": e.dy,
                "dz": e.dz,
                "vx": e.vx,
                "vy": e.vy,
                "vz": e.vz,
                "health": e.health,
                "yaw": e.yaw,
            }
            for e in t.entities
        ],
    }
    hit = t.hit
    row["hit_type"] = pb.HitResult.Type.Name(hit.type).lower()
    if hit.type == pb.HitResult.BLOCK:
        row |= {
            "hit_x": hit.block_pos.x,
            "hit_y": hit.block_pos.y,
            "hit_z": hit.block_pos.z,
            "hit_face": _direction(hit.face),
            "hit_block": hit.block or None,
        }
    elif hit.type == pb.HitResult.ENTITY:
        row |= {"hit_entity_id": hit.entity_id, "hit_entity_type": hit.entity_type or None}
    if hit.HasField("location"):
        row |= {
            "hit_loc_x": hit.location.x,
            "hit_loc_y": hit.location.y,
            "hit_loc_z": hit.location.z,
        }
    if t.HasField("breaking_pos"):
        row |= {
            "breaking_x": t.breaking_pos.x,
            "breaking_y": t.breaking_pos.y,
            "breaking_z": t.breaking_pos.z,
        }
    c = t.context
    row |= {
        "dimension": c.dimension or None,
        "biome": c.biome or None,
        "block_light": c.block_light,
        "sky_light": c.sky_light,
        "day_time": c.day_time,
        "screen": c.screen or None,
        "ping_ms": c.ping_ms,
    }
    return row


def _pos(prefix: str, p: pb.BlockPos) -> dict:
    return {f"{prefix}x": p.x, f"{prefix}y": p.y, f"{prefix}z": p.z}


def _event_row(e: pb.GameEvent) -> dict:
    kind = e.WhichOneof("kind")
    v = getattr(e, kind)
    row: dict = {"kind": kind}
    match kind:
        case "block_broken":
            row |= _pos("", v.pos) | {
                "block": v.block,
                "tool": v.tool or None,
                "ticks_taken": v.ticks_taken,
                "face": _direction(v.face),
            }
        case "block_placed":
            row |= _pos("", v.pos) | _pos("clicked_", v.clicked_pos)
            row |= {
                "face": _direction(v.clicked_face),
                "block": v.block,
                "hand": _hand(v.hand),
                "hit_x": v.hit.x,
                "hit_y": v.hit.y,
                "hit_z": v.hit.z,
            }
        case "item_picked_up":
            row["item"] = _item(v.item)
        case "item_dropped":
            row |= {"item": _item(v.item), "whole_stack": v.whole_stack}
        case "result_taken":
            row |= {
                "result_kind": pb.ResultTaken.Kind.Name(v.kind).lower(),
                "item": _item(v.result),
                "menu": v.menu or None,
                "quick_move": v.quick_move,
            }
        case "damage_dealt":
            row |= {
                "entity_id": v.entity_id,
                "entity_type": v.entity_type or None,
                "amount": v.amount,
                "critical": v.critical,
                "sprinting": v.sprinting,
                "attack_cooldown": v.attack_cooldown,
            }
        case "damage_taken":
            row |= {
                "amount": v.amount,
                "damage_source": v.source or None,
                "entity_id": v.attacker_id or None,
                "entity_type": v.attacker_type or None,
            }
        case "death":
            row["damage_source"] = v.cause or None
        case "dimension_change":
            row |= {"from_dimension": v.from_dimension, "to_dimension": v.to_dimension}
        case "screen_opened":
            row |= {
                "screen": v.screen,
                "menu": v.menu or None,
                "gui_width": v.gui_width,
                "gui_height": v.gui_height,
            }
        case "screen_closed":
            row["screen"] = v.screen
        case "chat_sent":
            row["is_command"] = v.is_command
        case "destroy_started":
            row |= _pos("", v.pos) | {
                "face": _direction(v.face),
                "block": v.block,
                "tool": v.tool or None,
            }
        case "destroy_aborted":
            row |= _pos("", v.pos) | {"progress": v.progress}
        case "position_corrected":
            row |= {f"before_{a}": getattr(v.before, a) for a in "xyz"}
            row |= {f"after_{a}": getattr(v.after, a) for a in "xyz"}
        case "sound_heard":
            row |= {
                "sound": v.sound,
                "category": v.category,
                "dx": v.dx,
                "dy": v.dy,
                "dz": v.dz,
                "volume": v.volume,
            }
        case "interaction":
            row["interaction"] = pb.Interaction.Kind.Name(v.kind).lower()
            row |= {"hand": _hand(v.hand), "face": _direction(v.face)}
            row |= {"item_id": v.item or None, "block": v.block or None}
            if v.HasField("pos"):
                row |= _pos("", v.pos)
            if v.entity_id or v.entity_type:
                row |= {"entity_id": v.entity_id, "entity_type": v.entity_type or None}
    return row


@dataclass
class _Segment:
    segment_id: str
    start_tick: int
    start_t_ns: int
    ticks: int = 0
    flags: list[tuple[str, str]] = field(default_factory=list)


def convert(session: Session) -> dict[str, pl.DataFrame]:
    """All tables for one session."""
    return convert_envelopes(
        drop_detached_camera(session.envelopes(apply_discards=True)),
        session_id=session.session_id,
        source=pb.Source.Name(session.header.source).removeprefix("SOURCE_").lower(),
        complete=session.complete,
    )


def convert_envelopes(
    envelopes: Iterable[pb.Envelope], *, session_id: str, source: str, complete: bool = True
) -> dict[str, pl.DataFrame]:
    rows: dict[str, list[dict]] = {name: [] for name in TABLES}
    # Rows become columnar frames every FLUSH_EVERY records, so memory holds Python dicts for one
    # batch at a time rather than for a whole session (a tick's nearby-entity list is large).
    frames: dict[str, list[pl.DataFrame]] = {name: [] for name in TABLES}
    current: _Segment | None = None
    last_tick, last_t = 0, 0

    def flush() -> None:
        for name, table_rows in rows.items():
            if table_rows:
                frames[name].append(
                    pl.from_dicts(table_rows, schema=SCHEMAS[name], infer_schema_length=None)
                )
                rows[name] = []

    def close(
        seg: _Segment, env: pb.Envelope | None, state: str, reason: str | None, detail: str | None
    ) -> None:
        flags = [kind for kind, _ in seg.flags]
        rows["segments"].append(
            {
                "session_id": session_id,
                "segment_id": seg.segment_id,
                "source": source,
                "start_tick": seg.start_tick,
                "start_t_ns": seg.start_t_ns,
                "end_tick": env.tick if env else last_tick,
                "end_t_ns": env.t_ns if env else last_t,
                "end_state": state,
                "end_reason": reason,
                "end_detail": detail,
                "ticks": seg.ticks,
                "degraded": "degraded" in flags,
                "suspect_afk": "suspect_afk" in flags,
                "flags": [{"kind": k, "reason": r} for k, r in seg.flags],
            }
        )

    for n, env in enumerate(envelopes, 1):
        if n % FLUSH_EVERY == 0:
            flush()
        body = env.WhichOneof("body")
        key = {
            "session_id": session_id,
            "segment_id": env.segment_id,
            "tick": env.tick,
            "t_ns": env.t_ns,
        }
        last_tick, last_t = env.tick, env.t_ns
        match body:
            case "state":
                s = env.state
                if s.state == pb.StateChange.RECORDING:
                    if current is not None:
                        close(current, env, "paused", None, "resumed without a pause record")
                    current = _Segment(env.segment_id, env.tick, env.t_ns)
                elif current is not None:
                    state = "stopped" if s.state == pb.StateChange.STOPPED else "paused"
                    reason = _enum_name(s.reason_code, pb.PauseReason, "PAUSE_REASON_")
                    close(current, env, state, reason, s.reason or None)
                    current = None
            case "flag":
                if current is not None:
                    kind = pb.SegmentFlag.Kind.Name(env.flag.kind).lower()
                    current.flags.append((kind, env.flag.reason))
            case "tick_state":
                if current is not None:
                    current.ticks += 1
                rows["ticks"].append(key | _tick_row(env.tick_state))
            case "mouse":
                m = env.mouse
                rows["mouse"].append(
                    key | {"dx": m.dx, "dy": m.dy, "d_yaw": m.d_yaw, "d_pitch": m.d_pitch}
                )
            case "key":
                k = env.key
                rows["keys"].append(
                    key
                    | {
                        "key": k.key,
                        "scancode": k.scancode,
                        "down": k.down,
                        "binding": k.binding or None,
                        "mods": k.mods,
                    }
                )
            case "button":
                b = env.button
                rows["buttons"].append(
                    key
                    | {
                        "kind": "button",
                        "button": b.button,
                        "down": b.down,
                        "mods": b.mods,
                        "in_gui": b.in_gui,
                    }
                )
            case "scroll":
                rows["buttons"].append(
                    key | {"kind": "scroll", "scroll_dx": env.scroll.dx, "scroll_dy": env.scroll.dy}
                )
            case "cursor":
                rows["gui"].append(key | {"kind": "cursor", "x": env.cursor.x, "y": env.cursor.y})
            case "slot":
                s = env.slot
                rows["gui"].append(
                    key
                    | {
                        "kind": "slot",
                        "slot_id": s.slot_id,
                        "button": s.button,
                        "click_type": _enum_name(s.click_type, pb.ClickType, "CLICK_TYPE_"),
                        "slot_item": _item(s.slot_item),
                        "carried": _item(s.carried),
                        "menu": s.menu or None,
                        "container_id": s.container_id,
                    }
                )
            case "event":
                row = key | _event_row(env.event)
                row["while_paused"] = current is None
                rows["events"].append(row)
            case "cube":
                c = env.cube
                rows["world"].append(
                    key
                    | {
                        "kind": "cube",
                        "origin_x": c.origin.x,
                        "origin_y": c.origin.y,
                        "origin_z": c.origin.z,
                        "size": c.size,
                        "palette": list(c.palette),
                        "indices": list(c.indices),
                    }
                )
            case "delta":
                d = env.delta
                rows["world"].append(
                    key
                    | {
                        "kind": "delta",
                        "origin_x": d.origin.x,
                        "origin_y": d.origin.y,
                        "origin_z": d.origin.z,
                        "changes": [
                            {"x": c.pos.x, "y": c.pos.y, "z": c.pos.z, "state": c.state}
                            for c in d.changes
                        ],
                    }
                )
    if current is not None:
        # A crash: the last segment never got its pause record.
        close(current, None, "stopped" if complete else "crashed", None, None)
    flush()
    return {
        name: pl.concat(parts, rechunk=True) if parts else pl.DataFrame(schema=SCHEMAS[name])
        for name, parts in frames.items()
    }
