"""Hand-built statistics per clip, for the feature model (spec, Component D, model 1).

Every feature describes behaviour; nothing that names the source is used (session ids, times,
settings, FPS, ping, coordinates, biome, light, items, health). Features come in groups:

    motion      camera and body: yaw/pitch rates and jerk, snaps, wobble, heading error, speed,
                acceleration, ground/sprint/crouch shares, jump timing after landing
    keys        tick-level key states: hold shares, key changes, forward held while not moving
    events      breaking and placing: rate, break time, re-acquisition delay, aborted breaks
    mouse       raw per-frame mouse: frames per tick, size, zero-turn frames

Plain Baritone moves the player through its own input override: its sessions carry those held
inputs as keys (hb-executor's BotRecorder) but no raw mouse frames, so the mouse group would
separate it trivially. DEFAULT_GROUPS leaves mouse out until the bot sends real input (Phase 4).
"""

from __future__ import annotations

import numpy as np
import polars as pl

from hbpipe.discrim.eval_set import EvalSet
from hbpipe.store import Store

KEYS = (
    "k_forward",
    "k_back",
    "k_left",
    "k_right",
    "k_jump",
    "k_sneak",
    "k_sprint",
    "k_attack",
    "k_use",
)
MOVING = 1.0  # m/s
SNAP = 300.0  # deg/s in one tick
REACQUIRE_MAX = 100  # ticks; a longer gap between breaks is a new start, not a re-acquisition

DEFAULT_GROUPS = ("motion", "keys", "events")


def _abs_stats(col: str, name: str, qs=(0.5, 0.9, 0.99)) -> list[pl.Expr]:
    a = pl.col(col).abs()
    out = [
        a.mean().alias(f"{name}_mean"),
        a.std().alias(f"{name}_std"),
        a.max().alias(f"{name}_max"),
    ]
    out += [a.quantile(q).alias(f"{name}_q{int(q * 100)}") for q in qs]
    return out


MOTION = [
    *_abs_stats("yaw_rate", "yaw_rate"),
    *_abs_stats("pitch_rate", "pitch_rate"),
    pl.col("yaw_jerk").abs().quantile(0.5).alias("yaw_jerk_q50"),
    pl.col("yaw_jerk").abs().quantile(0.9).alias("yaw_jerk_q90"),
    pl.col("pitch_jerk").abs().quantile(0.5).alias("pitch_jerk_q50"),
    pl.col("pitch_jerk").abs().quantile(0.9).alias("pitch_jerk_q90"),
    (pl.col("yaw_rate").abs() < 1e-3).mean().alias("yaw_still_share"),
    (pl.col("pitch_rate").abs() < 1e-3).mean().alias("pitch_still_share"),
    (pl.col("yaw_rate").abs() > SNAP).mean().alias("yaw_snap_share"),
    (pl.col("yaw_rate").abs().max() / (pl.col("yaw_rate").abs().sum() + 1e-9)).alias(
        "yaw_peak_share"
    ),
    pl.corr(pl.col("yaw_rate"), pl.col("yaw_rate").shift(1)).alias("yaw_rate_autocorr"),
    (pl.col("yaw_rate").sign().diff().fill_null(0) != 0).mean().alias("yaw_reversal_share"),
    pl.col("pitch").mean().alias("pitch_mean"),
    pl.col("pitch").std().alias("pitch_std"),
    pl.col("heading_err").abs().mean().alias("heading_err_mean"),
    pl.col("heading_err").std().alias("heading_err_std"),
    pl.col("speed").mean().alias("speed_mean"),
    pl.col("speed").std().alias("speed_std"),
    pl.col("speed").quantile(0.9).alias("speed_q90"),
    pl.col("accel").abs().quantile(0.5).alias("accel_q50"),
    pl.col("accel").abs().quantile(0.9).alias("accel_q90"),
    pl.col("vspeed").std().alias("vspeed_std"),
    pl.col("on_ground").mean().alias("ground_share"),
    pl.col("sprinting").mean().alias("sprint_share"),
    pl.col("crouching").mean().alias("crouch_share"),
    pl.col("takeoff_delay").median().alias("jump_after_landing_q50"),
    pl.col("takeoff_delay").std().alias("jump_after_landing_std"),
]

KEY_FEATURES = [
    *[pl.col(k).mean().alias(f"{k}_share") for k in KEYS],
    (pl.col("key_changes").sum() / pl.len() * 20).alias("key_changes_per_s"),
    (pl.col("k_forward") & (pl.col("speed") < 0.5)).mean().alias("forward_still_share"),
    (pl.col("k_left") | pl.col("k_right")).mean().alias("strafe_share"),
]

EVENT_FEATURES = [
    (pl.col("n_break").sum() / pl.len() * 20).alias("breaks_per_s"),
    (pl.col("n_place").sum() / pl.len() * 20).alias("places_per_s"),
    (pl.col("n_abort").sum() / (pl.col("n_start").sum() + 1)).alias("abort_per_start"),
    pl.col("break_ticks").median().alias("break_ticks_q50"),
    pl.col("reacquire").median().alias("reacquire_q50"),
    pl.col("reacquire").std().alias("reacquire_std"),
]

MOUSE_FEATURES = [
    (pl.col("frames").sum() / pl.len()).alias("mouse_frames_per_tick"),
    (pl.col("mouse_abs").sum() / (pl.col("frames").sum() + 1)).alias("mouse_mean_size"),
    (pl.col("zero_turn").sum() / (pl.col("frames").sum() + 1)).alias("mouse_zero_turn_share"),
]

GROUPS: dict[str, list[pl.Expr]] = {
    "motion": MOTION,
    "keys": KEY_FEATURES,
    "events": EVENT_FEATURES,
    "mouse": MOUSE_FEATURES,
}


def names(groups=DEFAULT_GROUPS) -> list[str]:
    return [e.meta.output_name() for g in groups for e in GROUPS[g]]


def _per_tick(store: Store, session_id: str) -> pl.DataFrame:
    """The session's cleaned ticks with the per-tick columns the statistics read."""
    t = store.read("ticks_clean", session_id).sort("segment_id", "tick")
    seg = "segment_id"
    yaw = np.radians(t["yaw"].to_numpy().astype(float))
    heading = np.arctan2(-t["vx"].to_numpy().astype(float), t["vz"].to_numpy().astype(float))
    err = (np.degrees(yaw - heading) + 180.0) % 360.0 - 180.0
    moving = t["speed"].fill_null(0).to_numpy() > MOVING
    t = t.with_columns(
        heading_err=pl.Series(np.where(moving, err, np.nan)).fill_nan(None),
        takeoff_delay=pl.when(
            pl.col("on_ground").shift(1).over(seg)
            & ~pl.col("on_ground")
            & ((pl.col("vspeed") > 3.0) | pl.col("k_jump"))
        ).then(pl.col("ticks_since_landing").shift(1).over(seg)),
        key_changes=pl.sum_horizontal(
            [
                (pl.col(k) != pl.col(k).shift(1).over(seg)).fill_null(False).cast(pl.Int32)
                for k in KEYS
            ]
        ),
    )
    events = store.read("events", session_id).filter(~pl.col("while_paused"))
    per_tick = _event_counts(events)
    mouse = _mouse_counts(store, session_id)
    keep = [
        "segment_id",
        "tick",
        "yaw_rate",
        "pitch_rate",
        "yaw_jerk",
        "pitch_jerk",
        "pitch",
        "speed",
    ]
    keep += [
        "accel",
        "vspeed",
        "on_ground",
        "sprinting",
        "crouching",
        "heading_err",
        "takeoff_delay",
    ]
    keep += ["key_changes", *KEYS]
    return (
        t.select(keep)
        .join(per_tick, on=["segment_id", "tick"], how="left")
        .join(mouse, on=["segment_id", "tick"], how="left")
        .with_columns(
            pl.col(
                "n_break", "n_place", "n_start", "n_abort", "frames", "mouse_abs", "zero_turn"
            ).fill_null(0)
        )
    )


def _event_counts(events: pl.DataFrame) -> pl.DataFrame:
    """Per (segment, tick): breaks, places, destroy starts and aborts, break time, and for each
    destroy start the ticks since the last break before it (re-acquisition delay)."""
    ev = events.select("segment_id", "tick", "kind", "ticks_taken").with_columns(
        pl.col("tick").cast(pl.UInt64)
    )
    breaks = ev.filter(pl.col("kind") == "block_broken").sort("tick")
    starts = ev.filter(pl.col("kind") == "destroy_started").sort("tick")
    reacq = starts.join_asof(
        breaks.select("segment_id", "tick", prev_break=pl.col("tick")),
        on="tick",
        by="segment_id",
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,  # sorted by tick, so by tick within each segment too
    ).with_columns(reacquire=(pl.col("tick") - pl.col("prev_break")).cast(pl.Float64))
    reacq = (
        reacq.with_columns(pl.when(pl.col("reacquire") <= REACQUIRE_MAX).then(pl.col("reacquire")))
        .group_by("segment_id", "tick")
        .agg(pl.col("reacquire").mean())
    )
    counts = ev.group_by("segment_id", "tick").agg(
        n_break=(pl.col("kind") == "block_broken").sum(),
        n_place=(pl.col("kind") == "block_placed").sum(),
        n_start=(pl.col("kind") == "destroy_started").sum(),
        n_abort=(pl.col("kind") == "destroy_aborted").sum(),
        break_ticks=pl.col("ticks_taken")
        .filter(pl.col("kind") == "block_broken")
        .mean()
        .cast(pl.Float64),
    )
    return counts.join(reacq, on=["segment_id", "tick"], how="left")


def _mouse_counts(store: Store, session_id: str) -> pl.DataFrame:
    path = store.table_path("mouse", session_id)
    schema = {
        "segment_id": pl.String,
        "tick": pl.UInt64,
        "frames": pl.UInt32,
        "mouse_abs": pl.Float64,
        "zero_turn": pl.UInt32,
    }
    if not path.exists():
        return pl.DataFrame(schema=schema)
    m = pl.read_parquet(path)
    if m.is_empty():
        return pl.DataFrame(schema=schema)
    return m.group_by("segment_id", "tick").agg(
        frames=pl.len(),
        mouse_abs=(pl.col("dx").abs() + pl.col("dy").abs()).sum(),
        zero_turn=((pl.col("dx") != 0) & (pl.col("d_yaw") == 0)).sum(),
    )


def clip_features(store: Store, ev: EvalSet, groups=tuple(GROUPS)) -> pl.DataFrame:
    """clip_id plus every feature in ``groups``, one row per clip."""
    exprs = [e for g in groups for e in GROUPS[g]]
    out = []
    for sid, clips in ev.clips.group_by("session_id", maintain_order=True):
        ticks = _per_tick(store, sid[0])
        long = pl.DataFrame(
            {
                "clip_id": np.repeat(
                    clips["clip_id"].to_list(), [len(ev.ticks[c]) for c in clips["clip_id"]]
                ),
                "segment_id": np.repeat(
                    clips["segment_id"].to_list(), [len(ev.ticks[c]) for c in clips["clip_id"]]
                ),
                "tick": np.concatenate([ev.ticks[c] for c in clips["clip_id"]]).astype(np.uint64),
            }
        )
        joined = long.join(ticks, on=["segment_id", "tick"], how="left", maintain_order="left")
        out.append(joined.group_by("clip_id", maintain_order=True).agg(exprs))
    if not out:
        return pl.DataFrame(schema={"clip_id": pl.String})
    return pl.concat(out, how="vertical_relaxed").with_columns(
        pl.exclude("clip_id").cast(pl.Float64)
    )
