"""Stage 1 movement model on synthetic play generated with known parameters.

Each session has four segments, one per kind of measurement, so they don't mix:
    seg-0001    walking straight with the camera wandering about the path (an Ornstein-Uhlenbeck
                process), every key pressed for log-normal durations, forward regripped
    seg-0002    sprint-jumping with known waits after landing, sprinting without jumps in the
                open and under a low ceiling
    seg-0003    sprint starts by toggle, double tap and an auto-sprint
    seg-0004    walking a loop of tight left turns with the camera aimed a log-normal distance
                ahead along the path (pure pursuit)
"""

import json
import math
from collections import Counter

import numpy as np
import polars as pl
import pytest
from google.protobuf import json_format

from hbpipe.label.run import LABELS_SCHEMA
from hbpipe.models.motor import movement
from hbpipe.schema import pb
from hbpipe.store import Store

TICK_MS = 50.0
WALK = 4.3 / 20  # blocks a tick

HUMAN = {
    "hold": {
        "forward": (30.0, 0.6),  # median ticks, sigma
        "strafe": (6.0, 0.5),
        "back": (4.0, 0.5),
        "jump": (3.0, 0.7),
        "sneak": (25.0, 0.8),
    },
    "regrip": (5.0, 0.4),
    "starts": {"toggle": 30, "double_tap": 10, "auto": 15},
    "waits": (0.5, 0.3, 0.2),  # chance of 0, 1, 2 ticks on the ground after landing
    "heading": (2.0, 3.0, 1.5),  # theta per s, sd deg, mean deg
    "look": (2.0, 0.4),  # median blocks, sigma
    "pitch": (20.0, 8.0),
    "toggle_sprint": True,
    "sneak": (60.0, 0.5, 30.0, 0.5),  # mining: sneaking stretch median, sigma; gap median, sigma
    "steps": (20.0, 6.0, 0.4, (0.5, 0.1, 0.2, 0.2)),  # a minute; median ticks, sigma; key shares
}
BOT = {
    "hold": {k: (80.0, 0.1) for k in HUMAN["hold"]},
    "regrip": (15.0, 0.1),
    "starts": {"toggle": 40, "double_tap": 0, "auto": 0},
    "waits": (0.0, 0.0, 1.0),
    "heading": (8.0, 0.5, -6.0),
    "look": (6.0, 0.1),
    "pitch": (0.0, 1.0),
    "toggle_sprint": False,
    "sneak": (10.0, 0.1, 200.0, 0.1),
    "steps": (2.0, 30.0, 0.1, (0.0, 1.0, 0.0, 0.0)),
}

SCHEMA = {
    "schema": 0,
    "kind": "",
    "created": 0,
    "fitted_on": {"sessions": 0, "minutes": 0.0},
    "hold_ticks": {
        "forward": {"median": 0.0, "sigma": 0.0},
        "strafe": {"median": 0.0, "sigma": 0.0},
        "back": {"median": 0.0, "sigma": 0.0},
        "jump": {"median": 0.0, "sigma": 0.0},
        "sneak": {"median": 0.0, "sigma": 0.0},
    },
    "regrip_gap_ticks": {"median": 0.0, "sigma": 0.0},
    "sprint_method": {"key": 0.0, "double_tap": 0.0, "toggle": 0.0},
    "jump_after_landing": [0.0] * 11,
    "sprint_jump_share": 0.0,
    "heading": {"theta_per_s": 0.0, "sigma_deg": 0.0, "mean_deg": 0.0},
    "look_ahead_blocks": {"median": 0.0, "sigma": 0.0},
    "walk_pitch": {"mean": 0.0, "sd": 0.0},
    "mining_sneak": {
        "share": 0.0,
        "bout_ticks": {"median": 0.0, "sigma": 0.0},
        "gap_ticks": {"median": 0.0, "sigma": 0.0},
    },
    "mining_steps": {
        "per_min": 0.0,
        "ticks": {"median": 0.0, "sigma": 0.0},
        "keys": {"forward": 0.0, "back": 0.0, "left": 0.0, "right": 0.0},
    },
    "mining_reach": {"median": 0.0, "q90": 0.0},
}


def lognormal(rng, median, sigma, size=None):
    return median * np.exp(sigma * rng.standard_normal(size))


def ticks_frame(sid: str, segment_id: str, first: int, n: int, **cols) -> pl.DataFrame:
    """Cleaned ticks for one segment: standing still on open ground unless ``cols`` say
    otherwise (one value or one per tick)."""
    tick = np.arange(first, first + n)
    base = {
        "filled": False,
        "x": 0.5,
        "y": 64.0,
        "z": 0.5,
        "yaw": 0.0,
        "pitch": 0.0,
        "on_ground": True,
        "sprinting": False,
        "in_water": False,
        "swimming": False,
        "on_climbable": False,
        "gliding": False,
        "passenger": False,
        "k_forward": False,
        "k_back": False,
        "k_left": False,
        "k_right": False,
        "k_jump": False,
        "k_sprint": False,
        "speed": 0.0,
        "vspeed": 0.0,
        "headroom": 3,
        "ticks_since_landing": 100,
        "screen_open": False,
        "crouching": False,
    }
    data = {"session_id": [sid] * n, "segment_id": [segment_id] * n, "tick": tick}
    data["t_ns"] = (tick * TICK_MS * 1e6).astype(np.uint64)
    for name, default in base.items():
        value = cols.pop(name, default)
        data[name] = list(value) if isinstance(value, list | np.ndarray) else [value] * n
    assert not cols, cols
    return pl.DataFrame(data).with_columns(
        pl.col("tick").cast(pl.UInt64),
        pl.col("headroom").cast(pl.Int8),
        pl.col("ticks_since_landing").cast(pl.Int64),
        pl.col("x", "y", "z", "yaw", "pitch").cast(pl.Float64),
    )


def label(sid: str, segment_id: str, name: str, start: int, end: int) -> dict:
    return {
        "label_id": f"{sid}-{segment_id}-{name}-{start}",
        "session_id": sid,
        "segment_id": segment_id,
        "start_tick": start,
        "end_tick": end,
        "start_t_ns": int(start * TICK_MS * 1e6),
        "end_t_ns": int(end * TICK_MS * 1e6),
        "label": name,
        "level": "primitive",
        "confidence": 0.9,
        "detector_version": 1,
    }


def ou(rng, n: int, theta: float, sd: float, mean: float) -> np.ndarray:
    phi = math.exp(-theta / 20)
    e = np.empty(n)
    e[0] = mean + sd * rng.standard_normal()
    noise = sd * math.sqrt(1 - phi**2) * rng.standard_normal(n)
    for t in range(1, n):
        e[t] = mean + (e[t - 1] - mean) * phi + noise[t]
    return e


def straight_walk(rng, sid, p, first, n, labels) -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    """seg-0001: a straight walk, the camera wandering about the path; key presses."""
    seg = "seg-0001"
    bearing = math.radians(30.0)
    t = np.arange(n)
    ticks = ticks_frame(
        sid,
        seg,
        first,
        n,
        x=0.5 - np.sin(bearing) * WALK * t,
        z=0.5 + np.cos(bearing) * WALK * t,
        yaw=30.0 + ou(rng, n, *p["heading"]),
        pitch=rng.normal(*p["pitch"], n),
        k_forward=True,
        speed=4.3,
    )
    for k in range(0, n, 300):  # ten walk spans end to end
        labels.append(label(sid, seg, "move.walk", first + k, first + min(k + 299, n - 1)))

    keys = []
    truth: dict = {"holds": {name: [] for name in movement.KEYS}, "regrips": []}
    end_ms = (first + n - 5) * TICK_MS
    for name, bindings in movement.KEYS.items():
        for binding in bindings:
            t_ms = (first + 2) * TICK_MS + rng.uniform(0, 500)
            while True:
                hold = lognormal(rng, *p["hold"][name]) * TICK_MS
                if t_ms + hold > end_ms:
                    break
                down, up = t_ms, t_ms + hold
                keys.append((down, binding, True))
                if binding == "key.forward":  # OS key repeat while held
                    keys += [(r, binding, True) for r in np.arange(down + 500, up, 33)]
                keys.append((up, binding, False))
                truth["holds"][name].append(hold / TICK_MS)
                if binding == "key.forward":
                    gap = lognormal(rng, *p["regrip"]) * TICK_MS
                    truth["regrips"].append(gap / TICK_MS)
                else:
                    gap = rng.uniform(5, 30) * TICK_MS
                t_ms = up + gap
            if binding == "key.forward":
                truth["regrips"].pop()  # the last press has no next one
    keys.sort()
    k = pl.DataFrame(
        {
            "session_id": sid,
            "segment_id": seg,
            "tick": [int(t // TICK_MS) for t, _, _ in keys],
            "t_ns": [int(t * 1e6) for t, _, _ in keys],
            "key": 0,
            "scancode": 0,
            "down": [d for _, _, d in keys],
            "binding": [b for _, b, _ in keys],
            "mods": 0,
        }
    ).with_columns(pl.col("tick", "t_ns").cast(pl.UInt64))
    return ticks, k, truth


def landing_counters(ground: list[bool]) -> list[int | None]:
    since, last, prev = [], None, True
    for i, g in enumerate(ground):
        if g and not prev:
            last = i
        since.append(None if last is None else i - last)
        prev = g
    return since


def sprint_jumps(rng, sid, p, first, labels) -> tuple[pl.DataFrame, dict]:
    """seg-0002: spans of sprint-jumping, sprinting in the open and under a low ceiling."""
    seg = "seg-0002"
    ground, vspeed, jump, headroom = [], [], [], []
    truth = {"waits": [], "sj_ticks": 0, "open_ticks": 0, "spans": []}

    def add(g: bool, v: float = 0.0, j: bool = False, room: int = 3) -> int:
        ground.append(g)
        vspeed.append(v)
        jump.append(j)
        headroom.append(room)
        return len(ground) - 1

    for span in range(15):
        a = len(ground)
        for _ in range(20):
            add(True)
        labels.append(label(sid, seg, "move.sprint", first + a, first + len(ground) - 1))
        truth["open_ticks"] += 20
        a = len(ground)
        for _ in range(15):
            add(True, room=0)
        labels.append(label(sid, seg, "move.sprint", first + a, first + len(ground) - 1))
        a = len(ground)
        add(True)
        add(True)
        for k in range(10):
            add(False, 8.4, True)  # takeoff
            for air in range(10):
                add(False, 4.0 - air)
            add(True)  # landing
            if k == 9:
                break
            if span % 5 == 0 and k == 4:
                wait = 14  # sprinting on before the next jump: no re-jump
            else:
                wait = int(rng.choice(3, p=p["waits"]))
                truth["waits"].append(wait)
            for _ in range(wait):
                add(True)
        labels.append(label(sid, seg, "move.sprint_jump", first + a, first + len(ground) - 1))
        truth["sj_ticks"] += len(ground) - a
        truth["spans"].append((first + a, first + len(ground) - 1))
    n = len(ground)
    ticks = ticks_frame(
        sid,
        seg,
        first,
        n,
        on_ground=ground,
        vspeed=vspeed,
        k_jump=jump,
        headroom=headroom,
        ticks_since_landing=landing_counters(ground),
        sprinting=True,
        speed=5.6,
        pitch=rng.normal(*p["pitch"], n),
    )
    return ticks, truth


def sprint_starts(rng, sid, p, first) -> pl.DataFrame:
    """seg-0003: bursts of sprinting, each started one way."""
    seg = "seg-0003"
    kinds = [k for k, count in p["starts"].items() for _ in range(count)]
    rng.shuffle(kinds)
    forward, sprinting, key = [], [], []

    def add(f: bool, s: bool, k: bool = False) -> None:
        forward.append(f)
        sprinting.append(s)
        key.append(k)

    for kind in kinds:
        for _ in range(10):
            add(False, False)
        if kind == "double_tap":
            add(True, False)
            add(False, False)
            add(False, False)
        else:
            add(True, False, kind == "toggle")
        for _ in range(20):
            add(True, True, kind == "toggle")
    return ticks_frame(
        sid,
        seg,
        first,
        len(forward),
        k_forward=forward,
        sprinting=sprinting,
        k_sprint=key,
        speed=[5.6 if s else 0.0 for s in sprinting],
    )


def loop_walk(rng, sid, p, first, labels) -> tuple[pl.DataFrame, list[float]]:
    """seg-0004: a loop of 4-block straights and quarter turns left of radius 2, walked with the
    camera aimed a look-ahead L ahead along the path; each turn has its own L."""
    seg = "seg-0004"
    turns, straight, radius = 30, 4.0, 2.0
    ds = 0.002
    pieces, looks, ends = [], [], []
    s = 0.0
    for _ in range(turns):
        pieces.append(np.zeros(int(straight / ds)))
        pieces.append(np.full(int(math.pi / 2 * radius / ds), ds / radius))
        s += straight + math.pi / 2 * radius
        ends.append(s)
        looks.append(float(lognormal(rng, *p["look"])))
    pieces.append(np.zeros(int(15.0 / ds)))
    turn_rate = np.concatenate(pieces)
    heading = np.concatenate([[0.0], np.cumsum(turn_rate)])  # radians, turning left
    # Minecraft yaw grows turning right; the direction of yaw h is (-sin h, cos h).
    px = np.concatenate([[0.0], np.cumsum(-np.sin(-heading[:-1]) * ds)])
    pz = np.concatenate([[0.0], np.cumsum(np.cos(-heading[:-1]) * ds)])
    total = (len(px) - 1) * ds
    n = int((total - 8.0) / WALK)
    at = np.arange(n) * WALK
    upcoming = np.minimum(np.searchsorted(ends, at, side="right"), turns - 1)
    ahead = np.minimum(at + np.array(looks)[upcoming], total)
    x, z = np.interp(at, np.arange(len(px)) * ds, px), np.interp(at, np.arange(len(pz)) * ds, pz)
    tx = np.interp(ahead, np.arange(len(px)) * ds, px)
    tz = np.interp(ahead, np.arange(len(pz)) * ds, pz)
    yaw = np.degrees(np.arctan2(-(tx - x), tz - z))
    ticks = ticks_frame(
        sid,
        seg,
        first,
        n,
        x=x,
        z=z,
        yaw=yaw,
        pitch=rng.normal(*p["pitch"], n),
        k_forward=True,
        speed=4.3,
    )
    labels.append(label(sid, seg, "move.walk", first, first + n - 1))
    return ticks, looks


def mining(rng, sid, p, first, labels) -> tuple[pl.DataFrame, dict]:
    """seg-0005: clearing an area, sneaking on and off in stretches."""
    seg = "seg-0005"
    n = 12000
    bout_m, bout_s, gap_m, gap_s = p["sneak"]
    crouching = np.zeros(n, dtype=bool)
    bouts, gaps = [], []
    t = int(lognormal(rng, gap_m, gap_s))
    while t < n:
        bout = max(1, round(lognormal(rng, bout_m, bout_s)))
        gap = max(1, round(lognormal(rng, gap_m, gap_s)))
        if t + bout + gap < n:  # wholly inside: what the fit can measure
            bouts.append(bout)
            gaps.append(gap)
        crouching[t : t + bout] = True
        t += bout + gap
    # Steps about the work: a Poisson stream of single-key bursts, plus one long walk (not a step).
    per_min, step_m, step_s, shares = p["steps"]
    keys = {k: np.zeros(n, dtype=bool) for k in movement.STEP_KEYS}
    steps: list[tuple[str, int]] = []
    walk = (n // 2, n // 2 + 200)
    keys["forward"][walk[0] : walk[1]] = True
    t = 10
    while True:
        t += 1 + int(rng.exponential(60 * 20 / per_min))
        length = max(1, min(movement.MINING_STEP_MAX, round(lognormal(rng, step_m, step_s))))
        if t + length + 2 >= n:
            break
        if t <= walk[1] + 1 and t + length + 1 >= walk[0]:
            continue
        key = list(movement.STEP_KEYS)[rng.choice(4, p=shares)]
        keys[key][t : t + length] = True
        steps.append((key, length))
        t += length
    labels.append(label(sid, seg, "act.area_clear", first, first + n - 1))
    ticks = ticks_frame(
        sid,
        seg,
        first,
        n,
        crouching=crouching,
        **{c: keys[k] for k, c in movement.STEP_KEYS.items()},
    )
    # Blocks broken while clearing: straight ahead (+z) of the player at (0.5, 64, 0.5), level
    # with the eye (y 65 holds the standing eye at 65.62, the crouching one at 65.27), so the
    # eye-to-block distance is the gap from the player's z to the near face, z - 0.5.
    reach = []
    events = []
    for t in rng.choice(n, size=60, replace=False):
        z = int(rng.integers(1, 6))
        events.append((seg, first + int(t), 0, 65, z))
        reach.append(z - 0.5)
    return ticks, {
        "share": crouching.mean(),
        "bouts": bouts,
        "gaps": gaps,
        "steps": steps,
        "reach": reach,
        "events": events,
    }


def write_session(store: Store, sid: str, source: str, p: dict, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    labels: list[dict] = []
    walk, keys, truth = straight_walk(rng, sid, p, 1, 3000, labels)
    jumps, truth["jumps"] = sprint_jumps(rng, sid, p, 3101, labels)
    starts = sprint_starts(rng, sid, p, 3101 + jumps.height + 100)
    loop, truth["looks"] = loop_walk(rng, sid, p, int(starts["tick"].max()) + 101, labels)
    mine, truth["sneak"] = mining(rng, sid, p, int(loop["tick"].max()) + 101, labels)
    ticks = pl.concat([walk, jumps, starts, loop, mine])
    truth["ticks"] = ticks.height
    # ... and one broken on the walk, which isn't mining
    rows = [*truth["sneak"]["events"], ("seg-0001", 10, 0, 64, 9)]
    events = pl.DataFrame(
        {
            "session_id": [sid] * len(rows),
            "segment_id": [r[0] for r in rows],
            "tick": [r[1] for r in rows],
            "kind": ["block_broken"] * len(rows),
            "x": [r[2] for r in rows],
            "y": [r[3] for r in rows],
            "z": [r[4] for r in rows],
        },
        schema={
            "session_id": pl.String,
            "segment_id": pl.String,
            "tick": pl.UInt64,
            "kind": pl.String,
            "x": pl.Int32,
            "y": pl.Int32,
            "z": pl.Int32,
        },
    )
    store.upsert_session(
        session_id=sid,
        source=source,
        started_unix_ms=0,
        play_day="2026-10-27",
        complete=1,
        labeler_fingerprint="f",
    )
    store.write_tables(sid, {"ticks_clean": ticks, "keys": keys, "events": events})
    store.replace_labels(sid, pl.from_dicts(labels, schema=LABELS_SCHEMA), 1, "f")
    header = pb.SessionHeader(session_id=sid, toggle_sprint=p["toggle_sprint"])
    (store.root / "sessions" / sid).mkdir(parents=True, exist_ok=True)
    (store.root / "sessions" / sid / "header.json").write_text(
        json_format.MessageToJson(header), encoding="utf-8"
    )
    return truth


@pytest.fixture(scope="module")
def played(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("hb-data"))
    truth = {
        "human0": write_session(store, "human0", "human", HUMAN, seed=1),
        "human1": write_session(store, "human1", "human", HUMAN, seed=2),
    }
    write_session(store, "bot0", "bot", BOT, seed=3)
    reasons = Counter()
    measures = movement.measurements(store, reasons)
    return store, truth, measures, reasons


def of(measures: pl.DataFrame, quantity: str) -> pl.DataFrame:
    return measures.filter(pl.col("quantity") == quantity)


def test_fit_recovers_the_players_parameters(played):
    _, truth, measures, reasons = played
    assert set(measures["session_id"]) == {"human0", "human1"}  # never the bot's play
    defaulted = []
    m = movement.fit(measures, defaulted)
    assert defaulted == []
    ticks = sum(t["ticks"] for t in truth.values())
    assert m["fitted_on"] == {"sessions": 2, "minutes": pytest.approx(ticks / 1200, abs=1e-3)}

    def lognormal_fits(fitted: dict, drawn: np.ndarray, median: float, sigma: float, what: str):
        """Close to the sample drawn (a few outliers may be trimmed) and to the parameters it
        was drawn with (only as close as a sample of this size allows)."""
        logs = np.log(drawn)
        assert fitted["median"] == pytest.approx(math.exp(logs.mean()), rel=0.05), what
        assert fitted["sigma"] == pytest.approx(logs.std(ddof=1), abs=0.05), what
        assert fitted["median"] == pytest.approx(median, rel=0.2), what
        assert fitted["sigma"] == pytest.approx(sigma, abs=0.25), what

    for name, (median, sigma) in HUMAN["hold"].items():
        # Every press measured exactly (key repeats are one press), then the fit.
        held = np.sort(np.concatenate([t["holds"][name] for t in truth.values()]))
        assert np.sort(of(measures, f"hold.{name}")["value"]) == pytest.approx(held), name
        lognormal_fits(m["hold_ticks"][name], held, median, sigma, name)
    gaps = np.sort(np.concatenate([t["regrips"] for t in truth.values()]))
    assert np.sort(of(measures, "regrip")["value"]) == pytest.approx(gaps)
    lognormal_fits(m["regrip_gap_ticks"], gaps, *HUMAN["regrip"], "regrip")

    starts = HUMAN["starts"]
    made = starts["toggle"] + starts["double_tap"]
    assert m["sprint_method"] == {
        "key": 0.0,
        "double_tap": pytest.approx(starts["double_tap"] / made, abs=1e-4),
        "toggle": pytest.approx(starts["toggle"] / made, abs=1e-4),
    }
    assert (
        reasons["sprint started without a sprint key or double tap (auto-sprint)"]
        == 2 * starts["auto"]
    )

    waits = np.concatenate([t["jumps"]["waits"] for t in truth.values()])
    assert of(measures, "jump_wait").height == len(waits)
    assert m["jump_after_landing"][:3] == pytest.approx(list(HUMAN["waits"]), abs=0.07)
    assert sum(m["jump_after_landing"]) == pytest.approx(1.0, abs=1e-3)
    assert reasons["takeoff more than 10 ticks after landing"] == 2 * 3
    sj = sum(t["jumps"]["sj_ticks"] for t in truth.values())
    open_ = sum(t["jumps"]["open_ticks"] for t in truth.values())
    assert m["sprint_jump_share"] == pytest.approx(sj / (sj + open_), abs=1e-3)

    theta, sd, mean = HUMAN["heading"]
    heading = of(measures, "heading")
    assert set(heading["segment_id"]) == {"seg-0001"}  # the loop's turns are never straight
    assert m["heading"]["theta_per_s"] == pytest.approx(theta, rel=0.25)
    assert m["heading"]["sigma_deg"] == pytest.approx(sd, rel=0.15)
    assert m["heading"]["mean_deg"] == pytest.approx(mean, abs=0.6)

    assert set(of(measures, "look_ahead")["segment_id"]) == {"seg-0004"}
    assert of(measures, "look_ahead_under").height > 0  # run-ups aimed along the straight
    assert m["look_ahead_blocks"]["median"] == pytest.approx(HUMAN["look"][0], rel=0.15)
    assert m["look_ahead_blocks"]["sigma"] == pytest.approx(HUMAN["look"][1], abs=0.12)

    assert m["walk_pitch"]["mean"] == pytest.approx(HUMAN["pitch"][0], abs=0.5)
    assert m["walk_pitch"]["sd"] == pytest.approx(HUMAN["pitch"][1], rel=0.08)


def test_sneaking_while_mining_is_measured_whole_stretches_only(played):
    _, truth, measures, _ = played
    for sid in ("human0", "human1"):
        mine = measures.filter(pl.col("session_id") == sid)
        assert sorted(of(mine, "mining_sneak_bout")["value"]) == sorted(
            truth[sid]["sneak"]["bouts"]
        )
        assert of(mine, "mining_sneak")["value"].mean() == pytest.approx(
            truth[sid]["sneak"]["share"]
        )
    m = movement.fit(measures)["mining_sneak"]
    share = np.mean([truth[s]["sneak"]["share"] for s in ("human0", "human1")])
    assert m["share"] == pytest.approx(share, abs=0.01)
    assert m["bout_ticks"]["median"] == pytest.approx(60.0, rel=0.15)
    assert m["gap_ticks"]["median"] == pytest.approx(30.0, rel=0.15)
    assert m["bout_ticks"]["sigma"] == pytest.approx(0.5, abs=0.1)


def test_steps_while_mining_are_counted_apart_from_walks(played):
    _, truth, measures, _ = played
    human = measures.filter(pl.col("session_id").is_in(["human0", "human1"]))
    for k in movement.STEP_KEYS:
        want = sorted(
            n for sid in ("human0", "human1") for key, n in truth[sid]["sneak"]["steps"] if key == k
        )
        have = sorted(of(human, f"mining_step.{k}")["value"].to_list())
        assert [n for n in have if n <= movement.MINING_STEP_MAX] == want, k
    assert (
        of(human, "mining_step.forward")["value"].max() == 200
    )  # the walk, measured but not a step
    m = movement.fit(measures)["mining_steps"]
    assert m["ticks"]["median"] == pytest.approx(6.0, rel=0.2)
    assert m["keys"]["forward"] == pytest.approx(0.5, abs=0.1)
    # The rate is per minute of mining in place: the walk's ticks don't count.
    n_steps = sum(len(truth[s]["sneak"]["steps"]) for s in ("human0", "human1"))
    in_place = 2 * (12000 - 200) / 1200
    assert m["per_min"] == pytest.approx(n_steps / in_place, rel=0.01)


def test_reach_is_measured_on_blocks_broken_while_mining(played):
    _, truth, measures, _ = played
    for sid in ("human0", "human1"):
        have = sorted(of(measures.filter(pl.col("session_id") == sid), "mining_reach")["value"])
        assert have == pytest.approx(sorted(truth[sid]["sneak"]["reach"]))  # not the walk's block
    m = movement.fit(measures)["mining_reach"]
    every = [r for sid in ("human0", "human1") for r in truth[sid]["sneak"]["reach"]]
    assert m["median"] == pytest.approx(np.median(every))
    assert m["q90"] == pytest.approx(np.quantile(every, 0.9))


def test_look_ahead_is_exact_where_the_camera_leads(played):
    """Where the camera points into a turn the measured distance is the one it was aimed at."""
    _, truth, measures, _ = played
    exact = of(measures, "look_ahead").filter(pl.col("session_id") == "human0")
    looks = truth["human0"]["looks"]
    assert exact.height > 100
    # Every exact value is one of the turn's look-aheads, to a few hundredths of a block.
    nearest = np.array([min(abs(v - lk) for lk in looks) for v in exact["value"]])
    assert np.quantile(nearest, 0.95) < 0.05


def test_excluded_play_and_review_verdicts_are_respected(tmp_path):
    store = Store(tmp_path)
    truth = write_session(store, "h", "human", HUMAN, seed=4)
    whole = movement.measurements(store)
    store.add_exclusion("h", "wurst search on", start_tick=1000, end_tick=1400)
    first, last = truth["jumps"]["spans"][2]
    store.add_correction(
        session_id="h",
        segment_id="seg-0002",
        start_tick=first,
        end_tick=last,
        label="move.sprint_jump",
        verdict="wrong",
        label_id=f"h-seg-0002-move.sprint_jump-{first}",
    )
    reasons = Counter()
    kept = movement.measurements(store, reasons)
    assert 0 < kept.height < whole.height
    # The two walk spans touching the exclusion (ticks 901-1500) are gone whole.
    walking = kept.filter(
        pl.col("quantity").is_in(["heading", "walk_pitch"]) & (pl.col("segment_id") == "seg-0001")
    )
    assert walking.height > 0 and not walking["tick"].is_between(901, 1500).any()
    held = kept.filter(pl.col("quantity").str.starts_with("hold."))
    assert held.height < whole.filter(pl.col("quantity").str.starts_with("hold.")).height
    released = held["tick"] + held["value"].ceil().cast(pl.Int64)
    assert not ((held["tick"] <= 1400) & (released >= 1000)).any()  # none touch the exclusion
    assert reasons["label spans touching excluded play"] == 2
    play = kept.filter(pl.col("quantity") == "play")["value"].sum()
    assert play == truth["ticks"] - 401
    # The span judged wrong measures nothing.
    for quantity in ("jump_wait", "sprint_open"):
        rows = of(kept, quantity)
        assert rows.height < of(whole, quantity).height
        assert not rows["tick"].is_between(first, last).any()


def test_model_json_has_exactly_the_schema(played, tmp_path):
    store, _, _, _ = played
    path = movement.export(store, tmp_path / "stage1")
    assert path == tmp_path / "stage1" / "movement.json"
    raw = path.read_bytes()
    assert raw.endswith(b"}\n") and b"\r" not in raw and raw.startswith(b'{\n "schema": 1')
    model = json.loads(raw)

    def same_shape(have, want, where="model"):
        if isinstance(want, dict):
            assert isinstance(have, dict) and list(have) == list(want), where
            for key, value in want.items():
                same_shape(have[key], value, f"{where}.{key}")
        elif isinstance(want, list):
            assert isinstance(have, list) and len(have) == len(want), where
            for i, (h, w) in enumerate(zip(have, want, strict=True)):
                same_shape(h, w, f"{where}[{i}]")
        elif isinstance(want, str):
            assert isinstance(have, str), where
        elif isinstance(want, int):
            assert type(have) is int, where
        else:
            assert isinstance(have, float) and math.isfinite(have), where

    same_shape(model, SCHEMA)
    assert model["schema"] == 1 and model["kind"] == "movement" and model["created"] > 0
    lines = movement.summary(model)
    assert len(lines) >= 5 and all(line.isascii() for line in lines)


def test_thin_data_falls_back_to_defaults():
    defaulted = []
    m = movement.fit(pl.DataFrame(schema=movement.MEASURE_SCHEMA), defaulted)
    assert m["fitted_on"] == {"sessions": 0, "minutes": 0.0}
    assert {k: m[k] for k in movement.DEFAULTS} == movement.DEFAULTS
    holds = [f"hold_ticks.{k}" for k in movement.KEYS]
    sneak = [f"mining_sneak.{k}" for k in movement.DEFAULTS["mining_sneak"]]
    steps = [f"mining_steps.{k}" for k in movement.DEFAULTS["mining_steps"]]
    nested = ("hold_ticks", "mining_sneak", "mining_steps", "mining_reach")
    assert defaulted == (
        holds + [k for k in movement.DEFAULTS if k not in nested] + sneak + steps + ["mining_reach"]
    )
    assert sum(movement.DEFAULTS["jump_after_landing"]) == pytest.approx(1.0)
    assert sum(movement.DEFAULTS["sprint_method"].values()) == pytest.approx(1.0)
