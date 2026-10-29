"""Review tool: check labels for the Phase 2 gate, browse and fix spans, name discovery clusters.

    uv run hbpipe review <hb-data>

Check   one span at a time from a set, in a fixed random order: correct, wrong (optionally with
        what it really was) or skip; keys 1, 2 and 3 do the same. Or a grid of 12 at a time,
        all correct unless ticked wrong. 200 checked spans per set feed the gate's precision.
Browse  every span of a set or session; fix one, or add a span the detectors missed.
Discover  the clusters of unlabeled play; name one to make it a new set.
Report  the gate numbers.

Verdicts go to the corrections table in index.sqlite and win over detector output.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

import altair as alt
import numpy as np
import polars as pl
import streamlit as st

from hbpipe.label.definitions import DEFINITIONS
from hbpipe.label.discovery import discover, name_cluster
from hbpipe.label.discovery import load as load_discovery
from hbpipe.label.report import check_target, set_report, unlabeled_share
from hbpipe.label.run import UNLABELED
from hbpipe.store import Store

PAD_TICKS = 100  # context shown around a span, each side
KEYS = (
    ("k_forward", "forward"),
    ("k_back", "back"),
    ("k_left", "left"),
    ("k_right", "right"),
    ("k_jump", "jump"),
    ("k_sneak", "sneak"),
    ("k_sprint", "sprint"),
    ("k_attack", "attack"),
    ("k_use", "use"),
)
EVENT_KINDS = (
    "block_broken",
    "block_placed",
    "destroy_started",
    "interaction",
    "damage_dealt",
    "damage_taken",
    "item_picked_up",
    "screen_opened",
    "screen_closed",
    "result_taken",
)
SPAN_COLOR = "#d9480f"
CONTEXT_COLOR = "#adb5bd"


def hb_root() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.environ.get("HBPIPE_REVIEW_ROOT"))
    args, _ = parser.parse_known_args(sys.argv[1:])
    if not args.root:
        st.error("Start the tool with: uv run hbpipe review <hb-data>")
        st.stop()
    return Path(args.root)


ROOT = hb_root()


def store() -> Store:
    # One per script run: sqlite connections can't cross Streamlit's threads.
    return Store(ROOT)


@st.cache_data(show_spinner=False)
def table(name: str, session_id: str, mtime: float) -> pl.DataFrame:
    return pl.read_parquet(ROOT / "tables" / name / f"{session_id}.parquet")


def session_table(name: str, session_id: str) -> pl.DataFrame:
    path = ROOT / "tables" / name / f"{session_id}.parquet"
    return table(name, session_id, path.stat().st_mtime)


def short(session_id: str) -> str:
    return session_id[:8]


# --- Span view ----------------------------------------------------------------------------------


def span_view(db: Store, span: dict, *, compact: bool = False) -> None:
    sid, seg_id = span["session_id"], span["segment_id"]
    start, end = int(span["start_tick"]), int(span["end_tick"])
    lo, hi = start - PAD_TICKS, end + PAD_TICKS
    ticks = session_table("ticks_clean", sid).filter(
        (pl.col("segment_id") == seg_id) & pl.col("tick").is_between(lo, hi)
    )
    if ticks.is_empty():
        st.warning("No ticks for this span (relabel the session?)")
        return
    ticks = ticks.with_columns(
        t=(pl.col("tick").cast(pl.Int64) - start) / 20.0,
        part=pl.when(pl.col("tick").is_between(start, end))
        .then(pl.lit("span"))
        .otherwise(pl.lit("context")),
    )
    events = session_table("events", sid).filter(
        (pl.col("segment_id") == seg_id)
        & pl.col("tick").is_between(lo, hi)
        & ~pl.col("while_paused")
        & pl.col("kind").is_in(EVENT_KINDS)
    )
    events = events.with_columns(
        t=(pl.col("tick").cast(pl.Int64) - start) / 20.0,
        what=pl.coalesce(
            pl.col("block"), pl.col("entity_type"), pl.col("interaction"), pl.col("screen")
        ).str.replace(r".*[.:]", ""),
    )

    secs = (end - start + 1) / 20
    held = ticks.filter(pl.col("part") == "span")["held_item"].drop_nulls().value_counts(sort=True)
    held_text = ", ".join(held["held_item"].head(3).to_list()) or "nothing"
    screens = ticks.filter(pl.col("part") == "span")["screen"].drop_nulls().unique().to_list()
    st.markdown(
        f"**{span['label']}** · {secs:.1f} s · confidence {span.get('confidence', 1):.2f} · "
        f"session `{short(sid)}` {seg_id} · ticks {start}-{end}  \n"
        f"holding {held_text}"
        + (f" · screens: {', '.join(s.rsplit('.', 1)[-1] for s in screens)}" if screens else "")
    )

    span_band = (
        alt.Chart(pl.DataFrame({"a": [0.0], "b": [secs]}).to_pandas())
        .mark_rect(opacity=0.12, color=SPAN_COLOR)
        .encode(x="a:Q", x2="b:Q")
    )
    x_axis = alt.X("t:Q", title="seconds from span start")
    color = alt.Color(
        "part:N",
        scale=alt.Scale(domain=["span", "context"], range=[SPAN_COLOR, CONTEXT_COLOR]),
        legend=None,
    )
    tp = ticks.select(
        "t", "x", "y", "z", "yaw", "pitch", "speed", "part", *[k for k, _ in KEYS]
    ).to_pandas()

    # Top-down path with breaks and placements (north is up).
    xs = np.concatenate([tp["x"], events["x"].drop_nulls().to_numpy() + 0.5])
    zs = np.concatenate([tp["z"], events["z"].drop_nulls().to_numpy() + 0.5])
    cx, cz = (xs.min() + xs.max()) / 2, (zs.min() + zs.max()) / 2
    half = max(xs.max() - xs.min(), zs.max() - zs.min(), 6) / 2 + 1
    path = (
        alt.Chart(tp)
        .mark_line(point=alt.OverlayMarkDef(size=6))
        .encode(
            x=alt.X("x:Q", scale=alt.Scale(domain=[cx - half, cx + half]), title="x (east)"),
            y=alt.Y("z:Q", scale=alt.Scale(domain=[cz + half, cz - half]), title="z (south)"),
            color=color,
            order="t:Q",
        )
    )
    blocks = events.filter(pl.col("kind").is_in(["block_broken", "block_placed"])).with_columns(
        bx=pl.col("x") + 0.5, bz=pl.col("z") + 0.5
    )
    block_marks = (
        alt.Chart(blocks.select("bx", "bz", "y", "kind", "what", "t").to_pandas())
        .mark_point(filled=True, size=60)
        .encode(
            x="bx:Q",
            y="bz:Q",
            shape=alt.Shape(
                "kind:N",
                scale=alt.Scale(
                    domain=["block_broken", "block_placed"], range=["square", "triangle-up"]
                ),
            ),
            color=alt.Color("t:Q", scale=alt.Scale(scheme="viridis"), title="when (s)"),
            tooltip=["kind", "what", "y", "t"],
        )
    )
    top_down = (path + block_marks).properties(title="path from above", height=320)

    height = alt.Chart(tp).mark_line().encode(x=x_axis, y=alt.Y("y:Q", scale=alt.Scale(zero=False)))
    height = (span_band + height).properties(title="height", height=120)
    if compact:
        c1, c2 = st.columns([1, 1])
        c1.altair_chart(top_down, use_container_width=True)
        c2.altair_chart(_inputs_chart(tp, span_band, x_axis), use_container_width=True)
        return

    tp["yaw_unwrapped"] = np.rad2deg(np.unwrap(np.deg2rad(tp["yaw"].to_numpy(dtype=float))))
    camera = (
        alt.Chart(tp)
        .transform_fold(["yaw_unwrapped", "pitch"], as_=["axis", "degrees"])
        .mark_line()
        .encode(x=x_axis, y=alt.Y("degrees:Q", scale=alt.Scale(zero=False)), color="axis:N")
    )
    camera = (span_band + camera).properties(title="camera", height=140)
    speed = alt.Chart(tp).mark_line().encode(x=x_axis, y=alt.Y("speed:Q", title="m/s"))
    speed = (span_band + speed).properties(title="speed", height=100)
    ev_chart = (
        alt.Chart(events.select("t", "kind", "what").to_pandas())
        .mark_point(filled=True, size=50)
        .encode(x=x_axis, y=alt.Y("kind:N", title=None), tooltip=["kind", "what", "t"])
    )
    ev_chart = (span_band + ev_chart).properties(title="events", height=150)

    others = db.labels(session_id=sid, segment_id=seg_id).filter(
        (pl.col("end_tick") >= lo) & (pl.col("start_tick") <= hi)
    )
    others = others.with_columns(
        a=(pl.col("start_tick").clip(lo, hi) - start) / 20.0,
        b=(pl.col("end_tick").clip(lo, hi) - start + 1) / 20.0,
        this=(pl.col("start_tick") == start) & (pl.col("end_tick") == end),
    )
    gantt = (
        alt.Chart(others.select("label", "a", "b", "this", "confidence").to_pandas())
        .mark_bar()
        .encode(
            x=alt.X("a:Q", title="seconds from span start"),
            x2="b:Q",
            y=alt.Y("label:N", title=None),
            color=alt.condition("datum.this", alt.value(SPAN_COLOR), alt.value(CONTEXT_COLOR)),
            tooltip=["label", "a", "b", "confidence"],
        )
        .properties(title="labels here", height=max(120, 16 * others["label"].n_unique()))
    )

    left, right = st.columns([1, 1])
    with left:
        st.markdown("  \n".join(describe(db, span)))
    with right:
        st.altair_chart(top_down, use_container_width=True)
        st.caption(
            "Seen from above, north up. Orange: where you went during this span; gray: the 5 s "
            "before and after. Squares: blocks broken, triangles: blocks placed."
        )
    with st.expander("Charts: height, keys, camera, speed, events, labels nearby"):
        st.caption(
            "Time runs left to right; the shaded band is this span. Camera: yaw is turning left "
            "and right; pitch goes up when you look down."
        )
        c1, c2 = st.columns([1, 1])
        with c1:
            st.altair_chart(height, use_container_width=True)
            st.altair_chart(camera, use_container_width=True)
            st.altair_chart(speed, use_container_width=True)
        with c2:
            st.altair_chart(_inputs_chart(tp, span_band, x_axis), use_container_width=True)
            st.altair_chart(ev_chart, use_container_width=True)
        st.altair_chart(gantt, use_container_width=True)


def _inputs_chart(tp, span_band, x_axis) -> alt.LayerChart:
    held = tp.melt(
        id_vars=["t"], value_vars=[k for k, _ in KEYS], var_name="key", value_name="down"
    )
    held = held[held["down"]]
    names = dict(KEYS)
    held["key"] = held["key"].map(names)
    chart = (
        alt.Chart(held)
        .mark_tick(thickness=3)
        .encode(x=x_axis, y=alt.Y("key:N", sort=[n for _, n in KEYS], title=None))
    )
    return (span_band + chart).properties(title="keys held", height=170)


# --- Modes --------------------------------------------------------------------------------------


def verdict_buttons(db: Store, span: dict, all_sets: list[str], key: str) -> bool:
    """Correct / wrong / skip for a span. Returns True when a verdict was saved."""
    c1, c2, c3, c4 = st.columns([1, 1, 2, 1])
    actual = c3.selectbox(
        "If wrong, it was",
        ["(not sure)", *all_sets],
        key=f"actual-{key}",
        label_visibility="collapsed",
    )
    common = {
        "label_id": span.get("label_id"),
        "session_id": span["session_id"],
        "segment_id": span["segment_id"],
        "start_tick": int(span["start_tick"]),
        "end_tick": int(span["end_tick"]),
        "label": span["label"],
    }
    if c1.button("✓ Correct", key=f"ok-{key}", type="primary", use_container_width=True):
        db.add_correction(verdict="correct", **common)
        return True
    if c2.button("✗ Wrong", key=f"bad-{key}", use_container_width=True):
        new = None if actual == "(not sure)" else actual
        db.add_correction(verdict="wrong", new_label=new, **common)
        return True
    if c4.button("Skip", key=f"skip-{key}", use_container_width=True):
        st.session_state.setdefault("skipped", set()).add(span.get("label_id"))
        return True
    return False


def check_mode(db: Store, labels: pl.DataFrame, all_sets: list[str]) -> None:
    st.subheader("Check labels")
    st.caption(
        "Is this span really what the label says? 200 checked spans per set (30 for sets read "
        "straight from an event or flag, like eating or trading) measure "
        "its precision for the gate. Spans come in a fixed random order."
    )
    judged = db.corrections().filter(pl.col("verdict").is_in(["correct", "wrong"]))
    live = judged.join(labels.select("label_id"), on="label_id", how="semi")
    counts = (
        labels.filter(pl.col("label") != UNLABELED)
        .group_by("label")
        .agg(spans=pl.len())
        .join(live.group_by("label").agg(checked=pl.len()), on="label", how="left")
        .with_columns(pl.col("checked").fill_null(0))
        .sort("label")
    )
    if counts.is_empty():
        st.info("No labels yet. Run: uv run hbpipe label <hb-data>")
        return
    options = counts["label"].to_list()
    info = {r["label"]: r for r in counts.iter_rows(named=True)}
    chosen = st.selectbox(
        "Set",
        options,
        format_func=lambda s: (
            f"{s}  ({info[s]['checked']}/{min(check_target(s), info[s]['spans'])}"
            f" checked of {info[s]['spans']})"
        ),
    )
    if chosen in DEFINITIONS:
        covers, signal = DEFINITIONS[chosen]
        st.info(f"**{chosen}** means: {covers}.  \nThe labeler looks for: {signal}.")
    with st.expander("How to judge (you don't need to remember the moment)"):
        st.markdown(
            "Each span is a few seconds the labeler tagged with this set. Read **What happened**, "
            "built from the recording, and ask: *does the label's meaning fit that?*\n\n"
            "- **Correct**: it fits, even roughly (a walk that drifts sideways is still a walk).\n"
            "- **Wrong**: it clearly doesn't, e.g. *move.walk* while standing still, or "
            "*mine.tree* breaking stone. Pick what it really was if you know; otherwise leave "
            "it as not sure.\n"
            "- **Skip**: you can't tell from what's shown.\n\n"
            "Keys 1, 2 and 3 do the same. Grid mode is faster for sets you can judge from the "
            "path and one line of text."
        )
    spans = labels.filter(pl.col("label") == chosen)
    order = sorted(spans["label_id"].to_list())
    random.Random(chosen).shuffle(order)
    done = set(live["label_id"].to_list()) | st.session_state.get("skipped", set())
    remaining = [i for i in order if i not in done]
    set_verdicts = live.filter(pl.col("label") == chosen)
    n, good = set_verdicts.height, int((set_verdicts["verdict"] == "correct").sum())
    target = min(check_target(chosen), spans.height)
    st.progress(min(1.0, n / target) if target else 1.0, text=f"{n}/{target} checked")
    if n:
        st.caption(f"Precision so far: {good / n:.0%} ({good} of {n})")
    if not remaining:
        st.success("Every span of this set has a verdict (or was skipped).")
        return
    if st.toggle(
        f"Grid: {GRID} at a time", key="grid", help="Faster for sets you can judge at a glance"
    ):
        check_grid(db, spans, remaining[:GRID])
        return
    span = spans.filter(pl.col("label_id") == remaining[0]).row(0, named=True)
    if verdict_buttons(db, span, all_sets, key=span["label_id"]):
        st.rerun()
    keyboard_shortcuts()
    span_view(db, span)


GRID = 12


def check_grid(db: Store, spans: pl.DataFrame, ids: list[str]) -> None:
    """A page of spans, all correct unless ticked wrong, saved together."""
    st.caption("Every span counts as correct unless you tick it wrong. Then save the page.")
    page = spans.filter(pl.col("label_id").is_in(ids))
    page = page.with_columns(order=pl.col("label_id").replace_strict(ids, range(len(ids))))
    rows = page.sort("order").to_dicts()
    wrong = {}
    for start in range(0, len(rows), 3):
        cols = st.columns(3)
        for col, span in zip(cols, rows[start : start + 3], strict=False):
            with col, st.container(border=True):
                st.altair_chart(thumbnail(span), use_container_width=True)
                said = describe(db, span)  # moving, keys held, did
                st.caption("  \n".join(said[i] for i in (1, 2, 4) if i < len(said)))
                wrong[span["label_id"]] = st.checkbox("✗ wrong", key=f"g-{span['label_id']}")
    n_wrong = sum(wrong.values())
    if st.button(f"Save page: {len(rows) - n_wrong} correct, {n_wrong} wrong", type="primary"):
        for span in rows:
            db.add_correction(
                verdict="wrong" if wrong[span["label_id"]] else "correct",
                label_id=span["label_id"],
                session_id=span["session_id"],
                segment_id=span["segment_id"],
                start_tick=int(span["start_tick"]),
                end_tick=int(span["end_tick"]),
                label=span["label"],
            )
        st.rerun()


def _span_ticks(span: dict, pad: int) -> pl.DataFrame:
    start, end = int(span["start_tick"]), int(span["end_tick"])
    return session_table("ticks_clean", span["session_id"]).filter(
        (pl.col("segment_id") == span["segment_id"])
        & pl.col("tick").is_between(start - pad, end + pad)
    )


def thumbnail(span: dict) -> alt.LayerChart:
    """Top-down path (the span in color, two seconds either side in gray) with the blocks
    broken and placed during the span."""
    start, end = int(span["start_tick"]), int(span["end_tick"])
    ticks = _span_ticks(span, 40).with_columns(
        part=pl.when(pl.col("tick").is_between(start, end))
        .then(pl.lit("span"))
        .otherwise(pl.lit("context"))
    )
    events = session_table("events", span["session_id"]).filter(
        (pl.col("segment_id") == span["segment_id"])
        & pl.col("tick").is_between(start, end)
        & pl.col("kind").is_in(["block_broken", "block_placed"])
    )
    xs = np.concatenate([ticks["x"].to_numpy(), events["x"].drop_nulls().to_numpy() + 0.5])
    zs = np.concatenate([ticks["z"].to_numpy(), events["z"].drop_nulls().to_numpy() + 0.5])
    cx, cz = (xs.min() + xs.max()) / 2, (zs.min() + zs.max()) / 2
    half = max(xs.max() - xs.min(), zs.max() - zs.min(), 4) / 2 + 1
    sx = alt.Scale(domain=[cx - half, cx + half])
    sz = alt.Scale(domain=[cz + half, cz - half])
    path = (
        alt.Chart(ticks.select("tick", "x", "z", "part").to_pandas())
        .mark_line(point=alt.OverlayMarkDef(size=4))
        .encode(
            x=alt.X("x:Q", scale=sx, axis=None),
            y=alt.Y("z:Q", scale=sz, axis=None),
            color=alt.Color(
                "part:N",
                scale=alt.Scale(domain=["span", "context"], range=[SPAN_COLOR, CONTEXT_COLOR]),
                legend=None,
            ),
            order="tick:Q",
        )
    )
    marks = (
        alt.Chart(
            events.with_columns(bx=pl.col("x") + 0.5, bz=pl.col("z") + 0.5)
            .select("bx", "bz", "kind")
            .to_pandas()
        )
        .mark_point(filled=True, size=30, color="#1971c2")
        .encode(
            x=alt.X("bx:Q", scale=sx),
            y=alt.Y("bz:Q", scale=sz),
            shape=alt.Shape(
                "kind:N",
                scale=alt.Scale(
                    domain=["block_broken", "block_placed"], range=["square", "triangle-up"]
                ),
                legend=None,
            ),
        )
    )
    return (path + marks).properties(height=150)


ACTIVITY_WORDS = {
    "act.travel": "travelling",
    "act.strip_mine": "strip mining",
    "act.area_clear": "clearing an area",
    "act.tunnel": "tunneling",
    "act.tree_farm": "tree farming",
    "act.build": "building",
    "act.fight": "fighting",
    "act.explore": "exploring",
    "act.farm": "farming",
    "act.manage_inventory": "sorting the inventory",
    "act.smelt_craft": "smelting or crafting",
}
KEY_WORDS = {
    "k_forward": "forward",
    "k_back": "back",
    "k_left": "left",
    "k_right": "right",
    "k_jump": "jump",
    "k_sneak": "sneak",
    "k_sprint": "sprint",
    "k_attack": "attack (left click)",
    "k_use": "use (right click)",
}
DIRECTIONS = (
    "north",
    "north-east",
    "east",
    "south-east",
    "south",
    "south-west",
    "west",
    "north-west",
)


def _pace(speed: float) -> str:
    if speed < 0.3:
        return "standing still"
    if speed < 2.0:
        return "slow, about sneaking pace"
    if speed < 4.9:
        return "walking pace"
    if speed < 6.5:
        return "sprinting pace"
    return "faster than sprinting (falling, flying or riding)"


def _compass(dx: float, dz: float) -> str:
    """Minecraft directions: x is east, z is south."""
    angle = (np.degrees(np.arctan2(dx, -dz)) + 360) % 360  # 0 = north, clockwise
    return DIRECTIONS[int((angle + 22.5) // 45) % 8]


def _look(pitch: float) -> str:
    if pitch > 60:
        return "straight down"
    if pitch > 20:
        return "down"
    if pitch < -60:
        return "straight up"
    if pitch < -20:
        return "up"
    return "ahead"


def _plural(n: float, word: str) -> str:
    return f"{n:.0f} {word}{'' if round(n) == 1 else 's'}"


def _items(df: pl.DataFrame, col: str) -> str:
    counts = df[col].drop_nulls().str.replace(r".*[.:]", "").value_counts(sort=True)
    return ", ".join(f"{n} {name}" if n > 1 else name for name, n in counts.head(3).iter_rows())


def describe(db: Store, span: dict) -> list[str]:
    """What happened during a span, in plain words, from the recording alone: when, where to,
    how fast, keys, camera, what was broken, placed or hit, and what was held. Enough to judge a
    label without remembering the moment."""
    import datetime as dt

    sid, seg_id = span["session_id"], span["segment_id"]
    start, end = int(span["start_tick"]), int(span["end_tick"])
    t = _span_ticks(span, 0)
    if t.is_empty():
        return ["No ticks for this span (relabel the session?)"]
    out = []

    acts = db.labels(session_id=sid, segment_id=seg_id).filter(
        pl.col("label").str.starts_with("act.")
        & (pl.col("start_tick") <= end)
        & (pl.col("end_tick") >= start)
    )
    doing = sorted({ACTIVITY_WORDS.get(a, a) for a in acts["label"].to_list()})
    session = db.session(sid)
    when = "unknown"
    if session is not None:
        into = int(t["t_ns"][0]) / 1e9
        at = dt.datetime.fromtimestamp(session["started_unix_ms"] / 1000 + into)
        when = f"{at:%a %b %d, %I:%M %p}, {into / 60:.0f} min into that session"
    out.append(f"**When:** {when}" + (f"; you were {' and '.join(doing)}" if doing else ""))

    secs = (end - start + 1) / 20
    dx, dz = float(t["x"][-1] - t["x"][0]), float(t["z"][-1] - t["z"][0])
    dist, rise = float(np.hypot(dx, dz)), float(t["y"][-1] - t["y"][0])
    moving = t.filter(pl.col("speed") > 0.3)
    speed = float(moving["speed"].mean()) if moving.height else 0.0
    if dist >= 0.7:
        move = f"went {_plural(dist, 'block')} {_compass(dx, dz)}"
    else:
        move = "stayed in about the same spot"
    move += f" over {secs:.1f} s, at {_pace(speed)}"
    if abs(rise) >= 0.6:
        move += f", {'up' if rise > 0 else 'down'} {_plural(abs(rise), 'block')}"
    states = [
        name
        for col, name in (
            ("sprinting", "sprinting"),
            ("crouching", "sneaking"),
            ("in_water", "in water"),
            ("on_climbable", "on a ladder or vines"),
            ("passenger", "riding something"),
            ("gliding", "gliding"),
        )
        if t[col].mean() >= 0.3
    ]
    if t["on_ground"].mean() < 0.5:
        states.append("mostly in the air")
    ground = t["on_ground"]
    jumps = int((ground.shift(1).fill_null(False) & ~ground & (t["vspeed"] > 3)).sum())
    if jumps:
        states.append(f"jumped {_plural(jumps, 'time')}")
    out.append("**Moving:** " + move + (f" ({', '.join(states)})" if states else ""))

    keys = [word for col, word in KEY_WORDS.items() if t[col].mean() >= 0.3]
    out.append("**Keys held:** " + (", ".join(keys) if keys else "none"))

    yaw = np.rad2deg(np.unwrap(np.deg2rad(t["yaw"].to_numpy().astype(float))))
    turn, tilt = float(yaw[-1] - yaw[0]), float(t["pitch"][-1] - t["pitch"][0])
    total = float(np.abs(np.diff(yaw)).sum())
    if total < 10 and abs(tilt) < 5:
        cam = "held nearly still"
    else:
        if abs(turn) >= 5:
            cam = f"turned {abs(turn):.0f} degrees {'right' if turn > 0 else 'left'}"
        else:
            cam = "kept facing the same way"
        if abs(tilt) >= 5:
            cam += f", looked {abs(tilt):.0f} degrees {'down' if tilt > 0 else 'up'}"
        if total > abs(turn) + 45:
            cam += f" (swung around {total:.0f} degrees in all)"
    out.append(f"**Camera:** {cam}; at the end looking {_look(float(t['pitch'][-1]))}")

    ev = session_table("events", sid).filter(
        (pl.col("segment_id") == seg_id)
        & pl.col("tick").is_between(start, end)
        & ~pl.col("while_paused")
    )
    did = []
    for kind, verb, col in (
        ("block_broken", "broke", "block"),
        ("block_placed", "placed", "block"),
        ("damage_dealt", "hit", "entity_type"),
        ("damage_taken", "got hurt by", "entity_type"),
        ("screen_opened", "opened", "screen"),
    ):
        part = ev.filter(pl.col("kind") == kind)
        if part.height:
            did.append(f"{verb} {_items(part, col) or _plural(part.height, 'time')}")
    pickups = ev.filter(pl.col("kind") == "item_picked_up").height
    if pickups:
        did.append(f"picked up {_plural(pickups, 'item')}")
    out.append("**Did:** " + ("; ".join(did) if did else "didn't break, place or hit anything"))
    held = t["held_item"].drop_nulls().value_counts(sort=True)
    out.append("**Holding:** " + (", ".join(held["held_item"].head(2).to_list()) or "nothing"))
    return out


def keyboard_shortcuts() -> None:
    """1 = correct, 2 = wrong, 3 = skip, by clicking the matching button (ignored while
    typing in a field)."""
    import streamlit.components.v1 as components

    components.html(
        """
        <script>
        const doc = window.parent.document;
        if (!doc.hbShortcuts) {
          doc.hbShortcuts = true;
          const labels = {"1": "✓ Correct", "2": "✗ Wrong", "3": "Skip"};
          doc.addEventListener("keydown", (e) => {
            const tag = (e.target.tagName || "").toLowerCase();
            if (tag === "input" || tag === "textarea" || !(e.key in labels)) return;
            const button = [...doc.querySelectorAll("button")]
              .find((b) => b.innerText.trim() === labels[e.key]);
            if (button) { e.preventDefault(); button.click(); }
          });
        }
        </script>
        """,
        height=0,
    )


def browse_mode(db: Store, labels: pl.DataFrame, all_sets: list[str]) -> None:
    st.subheader("Browse spans")
    c1, c2, c3 = st.columns([2, 2, 1])
    sets = sorted(labels["label"].unique().to_list())
    chosen = c1.selectbox("Set", ["(all)", *sets])
    sessions = sorted(labels["session_id"].unique().to_list())
    session = c2.selectbox(
        "Session", ["(all)", *sessions], format_func=lambda s: s[:8] if s != "(all)" else s
    )
    min_conf = c3.slider("Min confidence", 0.0, 1.0, 0.0, 0.05)
    shown = labels.filter(pl.col("confidence") >= min_conf)
    if chosen != "(all)":
        shown = shown.filter(pl.col("label") == chosen)
    if session != "(all)":
        shown = shown.filter(pl.col("session_id") == session)
    verdicts = (
        db.corrections().sort("id").unique("label_id", keep="last").select("label_id", "verdict")
    )
    shown = shown.join(verdicts, on="label_id", how="left").sort("session_id", "start_tick")
    view = shown.select(
        "label",
        session=pl.col("session_id").str.slice(0, 8),
        segment="segment_id",
        start="start_tick",
        seconds=((pl.col("end_tick") - pl.col("start_tick") + 1) / 20).round(1),
        confidence="confidence",
        verdict="verdict",
    )
    st.caption(f"{shown.height} spans")
    picked = st.dataframe(
        view, on_select="rerun", selection_mode="single-row", hide_index=True, height=260
    )
    rows = picked.selection.rows if picked else []
    if not rows:
        st.info("Pick a row to see it.")
        return
    span = shown.row(rows[0], named=True)
    if verdict_buttons(db, span, all_sets, key=f"b-{span['label_id']}"):
        st.rerun()
    with st.expander("Add a label the detectors missed, inside this window"):
        a1, a2, a3, a4 = st.columns([2, 1, 1, 1])
        new = a1.selectbox("Label", all_sets, key="add-label")
        s0 = a2.number_input("from (s)", value=0.0, step=0.5, key="add-from")
        s1 = a3.number_input(
            "to (s)", value=(span["end_tick"] - span["start_tick"] + 1) / 20, step=0.5, key="add-to"
        )
        if a4.button("Add", key="add-go"):
            db.add_correction(
                session_id=span["session_id"],
                segment_id=span["segment_id"],
                start_tick=int(span["start_tick"] + s0 * 20),
                end_tick=int(span["start_tick"] + s1 * 20),
                label=new,
                verdict="added",
            )
            st.success(f"Added {new}")
    span_view(db, span)


def discover_mode(db: Store, labels: pl.DataFrame) -> None:
    st.subheader("Discovery")
    st.caption(
        "Unlabeled play, clustered by motor statistics. Name a cluster that is one recurring "
        "behavior: on the next relabel (hbpipe label --force) it becomes its own set."
    )
    if st.button("Run discovery now"):
        with st.spinner("Embedding and clustering unlabeled stretches..."):
            discover(db)
        st.rerun()
    result = load_discovery(db)
    if result is None:
        st.info("No discovery run yet.")
        return
    st.dataframe(result.summary, hide_index=True)
    if result.summary.is_empty():
        st.info("No clusters found; record more play.")
        return
    cid = st.selectbox("Cluster", result.summary["cluster"].to_list())
    members = result.windows.filter(pl.col("cluster") == cid)
    n1, n2 = st.columns([3, 1])
    name = n1.text_input("Name it (family.name, e.g. move.boat_drift)", key="cluster-name")
    if n2.button("Save name") and name:
        try:
            name_cluster(db.db, result, cid, name)
            st.success(f"Saved {name}. Run hbpipe label --force to apply it.")
        except ValueError as e:
            st.error(str(e))
    for r in members.sample(min(4, members.height), seed=cid).iter_rows(named=True):
        span_view(db, r | {"label": f"cluster {cid}", "confidence": 1.0}, compact=True)
        st.divider()


def report_mode(db: Store) -> None:
    st.subheader("Phase 2 gate")
    share = unlabeled_share(db)
    st.metric("other.unlabeled share of active play", f"{share:.1%}", help="Goal: under 15%")
    report = set_report(db)
    if report.is_empty():
        st.info("No labels yet.")
        return
    st.dataframe(
        report.select(
            "label",
            "level",
            "spans",
            "seconds",
            "checked",
            "correct",
            pl.col("precision").round(3),
            pl.col("precision_low").round(3).alias("precision (95% low)"),
            "enough_data",
            "passes",
            "stale",
        ),
        hide_index=True,
        height=600,
    )


def discrim_mode() -> None:
    """The newest discriminator run (hbpipe discrim): the Phase 3 gate, per-set scores, what gives
    the bot away, and per-feature distances between human and bot."""
    from hbpipe.discrim.report import latest

    report = latest(ROOT)
    st.subheader("Discriminator")
    if report is None:
        st.info("No runs yet. Run: uv run hbpipe discrim <hb-data>")
        return
    gate = report["gate"]
    acc = gate["accuracy"]
    st.metric(
        f"Phase {gate.get('phase', 3)} gate: " + gate["goal"],
        "-" if acc is None else f"{acc:.1%}",
        help="pass" if gate["pass"] else "not yet",
    )
    st.caption(f"{report['dir']}  -  feature groups: {', '.join(report['feature_groups'])}")
    rows = []
    for label, e in report["sets"].items():
        row = {"set": label, "human clips": e["clips"]["human"], "bot clips": e["clips"]["bot"]}
        for model in ("feature", "sequence"):
            m = e.get(model, {})
            row[f"{model} accuracy"] = m.get("accuracy")
            row[f"{model} AUC"] = m.get("auc")
        f = e.get("feature", {})
        lo, hi = f.get("accuracy_ci") or (None, None)
        row["feature 95% CI"] = "" if lo is None else f"{lo:.0%} - {hi:.0%}"
        row["gives the bot away"] = ", ".join(t["feature"] for t in f.get("top_features", []))
        rows.append(row)
    st.dataframe(pl.from_dicts(rows), hide_index=True)
    if "decision" in report:
        d = report["decision"]
        st.write(
            "Decision model (mining jobs): "
            + (
                f"accuracy {d['accuracy']:.0%}, AUC {d['auc']:.2f}"
                if "accuracy" in d
                else d.get("status", "")
            )
        )
    pick = st.selectbox("Set", list(report["sets"]))
    dist = report["sets"][pick].get("distances", [])
    if dist:
        st.caption(
            "Human vs bot per feature: Wasserstein distance in pooled standard deviations, KS test"
        )
        st.dataframe(pl.from_dicts(dist), hide_index=True)


def main() -> None:
    st.set_page_config(page_title="hbpipe review", layout="wide")
    db = store()
    labels = db.labels(source="human")  # bot sessions are labeled for the discriminator only
    known = sorted(set(labels["label"].to_list()) - {UNLABELED}) if not labels.is_empty() else []
    from hbpipe.label import detectors  # noqa: F401
    from hbpipe.label.base import REGISTRY

    all_sets = sorted(set(REGISTRY) | set(known))
    with st.sidebar:
        st.title("hbpipe review")
        st.caption(str(ROOT))
        mode = st.radio("Mode", ["Check", "Browse", "Discover", "Report", "Discriminator"])
    if mode == "Discriminator":
        discrim_mode()
        return
    if labels.is_empty() and mode != "Report":
        st.info("No labels yet. Run: uv run hbpipe label <hb-data>")
        return
    if mode == "Check":
        check_mode(db, labels, all_sets)
    elif mode == "Browse":
        browse_mode(db, labels, all_sets)
    elif mode == "Discover":
        discover_mode(db, labels)
    else:
        report_mode(db)


main()
