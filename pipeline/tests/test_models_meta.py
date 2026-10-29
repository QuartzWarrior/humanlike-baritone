"""Stage 1 meta model on synthetic play generated with known parameters.

A session is one 25-minute segment: 8 minutes of mining (a block broken every 5 s), 4 of walking,
3 of building and 10 of other play. Hesitation labels are placed at known rates and durations;
glances, turns that start a walk and mistakes are played out in the ticks, and the real
look.idle_scan and meta.mistake detectors label them.
"""

import json
import math
from collections import Counter

import numpy as np
import polars as pl
import pytest
from labelkit import START_TICK, TICK_NS, detect, ev, row, seg

from hbpipe.label.base import Seg
from hbpipe.label.run import LABELS_SCHEMA
from hbpipe.models import meta
from hbpipe.store import Store

N = 30000  # ticks: 25 minutes
BLOCKS = {
    "mining": (0, 9600),
    "walking": (9600, 14400),
    "building": (14400, 18000),
    "other": (18000, N),
}
ACTS = {"mining": "act.strip_mine", "walking": "act.travel", "building": "act.build"}
BREAK_EVERY = 100  # ticks; every 4th break is followed by a hesitation, so 25%
GAP = 60  # still ticks after each glance or mistake

HUMAN = {
    "rates": {"walking": 2.0, "building": 4.0, "other": 3.0},  # mining's come with the breaks
    "duration": (700.0, 0.4),  # ms: median, sigma
    "glances": 50,
    "angle": (40.0, 0.5),  # degrees
    "hold": (600.0, 0.4),  # ms
    "set_offs": 8,  # stationary turns straight into a walk
    "pushing": 3,  # turns with a movement key held against a wall
    "mistakes": {"wrong_slot": 10, "missed_click": 8, "overshoot": 6, "misplaced_block": 4},
}
BOT = HUMAN | {
    "rates": {"walking": 15.0, "building": 15.0, "other": 15.0},
    "duration": (2000.0, 0.1),
    "angle": (150.0, 0.1),
    "hold": (2000.0, 0.1),
    "mistakes": {"wrong_slot": 0, "missed_click": 0, "overshoot": 0, "misplaced_block": 20},
}

SCHEMA = {
    "schema": 0,
    "kind": "",
    "created": 0,
    "fitted_on": {"sessions": 0, "active_minutes": 0.0},
    "hesitation": {
        "per_min": {"mining": 0.0, "walking": 0.0, "building": 0.0, "other": 0.0},
        "after_break_p": 0.0,
        "duration_ms": {"median": 0.0, "sigma": 0.0},
    },
    "glance": {
        "per_min": 0.0,
        "angle_deg": {"median": 0.0, "sigma": 0.0},
        "hold_ms": {"median": 0.0, "sigma": 0.0},
    },
    "mistake": {
        "per_min": 0.0,
        "kinds": {"wrong_slot": 0.0, "missed_click": 0.0, "overshoot": 0.0, "misplaced_block": 0.0},
    },
}


def lognormal(rng, median, sigma):
    return median * math.exp(sigma * rng.standard_normal())


class Play:
    """Per-tick columns, events, buttons and spans of one segment, built item by item."""

    def __init__(self):
        self.speed = np.zeros(N)
        self.x = np.full(N, 0.5)
        self.yaw_rate = np.zeros(N)
        self.slot = np.zeros(N, dtype=np.int64)
        self.keys = {k: np.zeros(N, dtype=bool) for k in ("k_forward", "k_back", "k_attack")}
        self.events: list[dict] = []
        self.buttons: list[dict] = []

    def turn(self, at: int, deg: float, n: int) -> None:
        self.yaw_rate[at : at + n] = deg / n * 20.0

    def walk(self, at: int, n: int) -> None:
        self.speed[at : at + n] = 4.3
        self.keys["k_forward"][at : at + n] = True


def long_seg(events: list[dict], buttons: list[dict], **columns: np.ndarray) -> Seg:
    """labelkit.seg for N ticks: its default row repeated, then ``columns`` (seg builds every row
    from Python lists, which takes seconds at this length)."""
    one = seg(1, events=events, buttons=buttons)
    schema = one.ticks.schema
    tick = START_TICK + np.arange(N)
    ticks = one.ticks[[0] * N].with_columns(
        pl.Series("tick", tick, dtype=schema["tick"]),
        pl.Series("t_ns", tick * TICK_NS, dtype=schema["t_ns"]),
        *(pl.Series(name, values, dtype=schema[name]) for name, values in columns.items()),
    )
    return Seg(
        session_id=one.session_id,
        segment_id=one.segment_id,
        header=one.header,
        ticks=ticks,
        events=one.events,
        mouse=one.mouse,
        keys=one.keys,
        buttons=one.buttons,
        gui=one.gui,
    )


def place_hesitations(rng, p, starts: list[int], room: int) -> list[tuple[int, int]]:
    out = []
    for s in starts:
        ticks = round(lognormal(rng, *p["duration"]) / 50)
        out.append((s, s + min(max(ticks, 6), 60, room) - 1))
    return out


def glance(rng, play: Play, at: int, p: dict, truth: dict) -> int:
    """A look away, a hold, then back (half the time) or walking on. Returns the last tick."""
    angle = min(max(lognormal(rng, *p["angle"]), 12.0), 170.0)
    n = min(max(math.ceil(angle / 12), 3), 10)
    hold = max(round(lognormal(rng, *p["hold"]) / 50), 3)
    sign = rng.choice([-1.0, 1.0])
    play.turn(at, sign * angle, n)
    q = at + n - 1 + hold  # first tick of what ends the hold
    truth["glances"].append((angle, hold * 50.0))
    if rng.random() < 0.5:
        play.turn(q, -sign * angle, n)
        return q + n - 1
    play.walk(q, 20)
    return q + 19


def mistake(kind: str, play: Play, a: int, k: int) -> int:
    """One mistake of ``kind`` starting at tick a, played out the way meta.mistake finds it.
    Returns the last tick."""
    if kind == "wrong_slot":  # a slot picked, another 6 ticks later and used
        s = int(play.slot[a - 1])
        play.slot[a:] = (s + 2) % 9
        play.slot[a + 6 :] = (s + 4) % 9
        play.keys["k_attack"][a + 10 : a + 14] = True
        return a + 14
    if kind == "missed_click":  # an attack at nothing
        play.buttons += [
            row(a, kind="button", button=0, down=True, in_gui=False),
            row(a + 2, kind="button", button=0, down=False, in_gui=False),
        ]
        return a + 2
    if kind == "overshoot":  # 2.15 blocks forward, then 1 back
        x0 = play.x[a - 1]
        play.x[a : a + 10] = x0 + 0.215 * np.arange(1, 11)
        play.x[a + 10 :] = x0 + 2.15
        play.x[a + 15 : a + 20] = x0 + 2.15 - 0.2 * np.arange(1, 6)
        play.x[a + 20 :] = x0 + 1.15
        play.speed[a : a + 10] = 4.3
        play.speed[a + 15 : a + 20] = 4.0
        play.keys["k_forward"][a : a + 10] = True
        play.keys["k_back"][a + 15 : a + 20] = True
        return a + 19
    play.events += [  # misplaced_block: placed, broken again
        ev(a, "block_placed", x=5, y=64, z=k, block="minecraft:stone"),
        ev(a + 30, "block_broken", x=5, y=64, z=k, block="minecraft:stone"),
    ]
    return a + 30


def write_session(store: Store, sid: str, source: str, p: dict, seed: int) -> dict:
    """One synthetic session; returns what was put in it."""
    rng = np.random.default_rng(seed)
    play = Play()
    truth: dict = {"glances": [], "mistakes": Counter()}
    spans: list[tuple[int, int, str]] = []  # rows, label
    for name, act in ACTS.items():
        a, b = BLOCKS[name]
        spans.append((a, b - 1, act))
    spans.append((18000, 19199, "act.fight"))  # an overlay: still other play

    # Mining: a block broken every BREAK_EVERY ticks, a hesitation 5 ticks after every 4th and
    # another 50 ticks after every 4th but two.
    breaks = list(range(50, BLOCKS["mining"][1], BREAK_EVERY))
    for k, r in enumerate(breaks):
        play.events.append(ev(r, "block_broken", x=100 + k, y=60, z=0, block="minecraft:stone"))
    hes = {"mining": place_hesitations(rng, p, [b + 5 for b in breaks[::4]], 40)}
    hes["mining"] += place_hesitations(rng, p, [b + 50 for b in breaks[2::4]], 40)
    offsets = {"walking": 100, "building": 50, "other": 20}
    for name, rate in p["rates"].items():
        a, b = BLOCKS[name]
        every = round(1200 / rate)
        starts = list(range(a + offsets[name], b - 70, every))[: round((b - a) / 1200 * rate)]
        hes[name] = place_hesitations(rng, p, starts, every - 10)
    a, b = BLOCKS["walking"]
    play.walk(a, b - a)

    # Other play: glances, turns into walks, turns pushing a wall and mistakes, in random order.
    items = ["glance"] * p["glances"] + ["set_off"] * p["set_offs"] + ["push"] * p["pushing"]
    items += [kind for kind, n in p["mistakes"].items() for _ in range(n)]
    at = BLOCKS["other"][0] + 40
    for k, item in enumerate(rng.permutation(items)):
        if item == "glance":
            end = glance(rng, play, at, p, truth)
        elif item in ("set_off", "push"):
            angle = rng.uniform(20, 90)
            n = max(math.ceil(angle / 12), 3)  # idle_scan needs 3 turning ticks
            play.turn(at, angle, n)
            if item == "set_off":
                play.walk(at + n, 20)
                end = at + n + 19
            else:
                play.keys["k_forward"][at - 5 : at + n + 5] = True
                end = at + n + 5
        else:
            end = mistake(item, play, at, k)
            truth["mistakes"][item] += 1
        at = end + GAP
    assert at < N - 100

    s = long_seg(
        play.events,
        play.buttons,
        x=play.x,
        speed=play.speed,
        yaw=np.cumsum(play.yaw_rate) / 20.0,
        yaw_rate=play.yaw_rate,
        slot=play.slot,
        **play.keys,
    )
    for label in ("look.idle_scan", "meta.mistake"):
        spans += [
            (sp.start_tick - START_TICK, sp.end_tick - START_TICK, label) for sp in detect(label, s)
        ]
    for i0, i1 in (r for rows in hes.values() for r in rows):
        spans.append((i0, i1, "meta.hesitate"))

    rows = [
        {
            "label_id": f"{sid}-{i}",
            "session_id": sid,
            "segment_id": "seg-0001",
            "start_tick": START_TICK + i0,
            "end_tick": START_TICK + i1,
            "start_t_ns": (START_TICK + i0) * 50_000_000,
            "end_t_ns": (START_TICK + i1) * 50_000_000,
            "label": label,
            "level": "activity" if label.startswith("act.") else "primitive",
            "confidence": 0.8,
            "detector_version": 1,
        }
        for i, (i0, i1, label) in enumerate(sorted(spans))
    ]
    store.upsert_session(
        session_id=sid,
        source=source,
        started_unix_ms=0,
        play_day="2026-10-27",
        complete=1,
        labeler_fingerprint="f",
    )
    store.write_tables(sid, {"ticks_clean": s.ticks, "events": s.events, "buttons": s.buttons})
    store.replace_labels(sid, pl.from_dicts(rows, schema=LABELS_SCHEMA), 1, "f")

    all_breaks = [e["tick"] for e in play.events if e["kind"] == "block_broken"]
    starts = [i0 for rows in hes.values() for i0, _ in rows]
    truth["hesitations"] = {c: len(rows) for c, rows in hes.items()}
    truth["durations"] = [(i1 - i0 + 1) * 50.0 for rows in hes.values() for i0, i1 in rows]
    truth["breaks"] = len(all_breaks)
    truth["after_break"] = sum(any(r <= x <= r + 20 for x in starts) for r in all_breaks)
    still = (play.speed < 0.5) & ~play.keys["k_forward"] & ~play.keys["k_back"]
    truth["stationary_min"] = float(still.sum()) / 1200
    return truth


@pytest.fixture(scope="module")
def played(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("hb-data"))
    truth = {
        "human0": write_session(store, "human0", "human", HUMAN, seed=1),
        "human1": write_session(store, "human1", "human", HUMAN, seed=2),
    }
    write_session(store, "bot0", "bot", BOT, seed=3)
    return store, truth


def test_observations_find_what_was_played(played):
    store, truth = played
    reasons = Counter()
    obs = meta.observations(store, reasons)
    assert set(obs["session_id"]) == {"human0", "human1"}  # never the bot's play
    for sid, t in truth.items():
        mine = obs.filter(pl.col("session_id") == sid)
        hes = mine.filter(pl.col("kind") == "hesitate")
        assert Counter(hes["context"]) == {c: n for c, n in t["hesitations"].items()}
        assert sorted(hes["ms"]) == sorted(t["durations"])
        glances = mine.filter(pl.col("kind") == "glance").sort("tick")
        assert glances.height == len(t["glances"])
        assert glances["angle_deg"].to_list() == pytest.approx([a for a, _ in t["glances"]])
        assert glances["ms"].to_list() == [h for _, h in t["glances"]]
        kinds = mine.filter(pl.col("kind") == "mistake")["mistake_kind"]
        assert Counter(kinds) == t["mistakes"]
        breaks = mine.filter(pl.col("kind") == "break")
        assert (breaks.height, breaks["after_break"].sum()) == (t["breaks"], t["after_break"])
    p = HUMAN
    assert reasons["look.idle_scan: a turn to walk off"] == 2 * p["set_offs"]
    assert reasons["look.idle_scan: moving or a movement key held"] == 2 * p["pushing"]
    assert reasons["look.idle_scan: the look coming back from a glance"] > 0


def test_fit_recovers_the_players_parameters(played):
    store, truth = played
    defaulted = []
    m = meta.fit(meta.observations(store), defaulted)
    assert defaulted == []
    assert m["fitted_on"] == {"sessions": 2, "active_minutes": 2 * N / 1200}
    h = m["hesitation"]
    for c, (a, b) in BLOCKS.items():
        count = sum(t["hesitations"][c] for t in truth.values())
        assert h["per_min"][c] == pytest.approx(count / (2 * (b - a) / 1200), abs=1e-4)
    assert h["per_min"]["mining"] == pytest.approx(6.0, abs=0.1)  # 2 every 4 breaks, 12 a minute
    assert h["per_min"]["other"] == pytest.approx(HUMAN["rates"]["other"], abs=0.1)
    after = sum(t["after_break"] for t in truth.values())
    breaks = sum(t["breaks"] for t in truth.values())
    assert h["after_break_p"] == pytest.approx(after / breaks, abs=1e-4)
    assert h["after_break_p"] == pytest.approx(0.25, abs=0.03)
    assert h["duration_ms"]["median"] == pytest.approx(HUMAN["duration"][0], rel=0.1)
    assert h["duration_ms"]["sigma"] == pytest.approx(HUMAN["duration"][1], abs=0.06)

    g = m["glance"]
    still = sum(t["stationary_min"] for t in truth.values())
    assert g["per_min"] == pytest.approx(2 * HUMAN["glances"] / still, rel=1e-3)
    assert g["angle_deg"]["median"] == pytest.approx(HUMAN["angle"][0], rel=0.12)
    assert g["angle_deg"]["sigma"] == pytest.approx(HUMAN["angle"][1], abs=0.08)
    assert g["hold_ms"]["median"] == pytest.approx(HUMAN["hold"][0], rel=0.12)
    assert g["hold_ms"]["sigma"] == pytest.approx(HUMAN["hold"][1], abs=0.08)

    mis = m["mistake"]
    kinds = HUMAN["mistakes"]
    total = sum(kinds.values())
    assert mis["per_min"] == pytest.approx(total / (N / 1200), abs=1e-4)
    assert mis["kinds"] == pytest.approx({k: n / total for k, n in kinds.items()}, abs=1e-4)
    assert sum(mis["kinds"].values()) == pytest.approx(1.0, abs=1e-3)


def test_excluded_play_afk_and_review_verdicts_are_respected(tmp_path):
    store = Store(tmp_path)
    write_session(store, "h", "human", HUMAN, seed=4)
    whole = meta.observations(store)
    lo, hi = START_TICK + 20000, START_TICK + 24999  # play kept out of training
    afk = START_TICK + N - meta.AFK_TICKS  # the held key that flagged the segment
    store.add_exclusion("h", "wurst search on", start_tick=lo, end_tick=hi)
    store.db.execute(
        "INSERT INTO segments (session_id, segment_id, suspect_afk) VALUES ('h', 'seg-0001', 1)"
    )
    labels = store.labels(session_id="h")
    wrong = labels.filter(pl.col("label") == "meta.hesitate").sort("start_tick").row(0, named=True)
    store.add_correction(
        session_id="h",
        segment_id="seg-0001",
        start_tick=wrong["start_tick"],
        end_tick=wrong["end_tick"],
        label="meta.hesitate",
        verdict="wrong",
        label_id=wrong["label_id"],
    )
    added = START_TICK + 15000  # building: nothing there for a mistake's parts to explain
    store.add_correction(
        session_id="h",
        segment_id="seg-0001",
        start_tick=added,
        end_tick=added + 5,
        label="meta.mistake",
        verdict="added",
    )
    reasons = Counter()
    obs = meta.observations(store, reasons)

    end = pl.col("tick") + pl.col("ticks").fill_null(1) - 1
    touches = ((pl.col("tick") <= hi) & (end >= lo)) | (end >= afk)
    gone = whole.filter(pl.col("kind").is_in(["hesitate", "glance", "mistake"]) & touches)
    assert gone.height > 10
    spans = obs.filter(pl.col("kind").is_in(["hesitate", "glance", "mistake", "break"]))
    assert spans.filter(touches).is_empty()
    active = obs.filter(pl.col("kind") == "active")["ticks"].sum()
    assert active == N - (hi - lo + 1) - meta.AFK_TICKS

    hes = obs.filter(pl.col("kind") == "hesitate")
    left = gone.filter(pl.col("kind") == "hesitate").height
    assert reasons["meta.hesitate: excluded play"] == left
    assert hes.height == whole.filter(pl.col("kind") == "hesitate").height - left - 1
    assert wrong["start_tick"] not in hes["tick"].to_list()

    mistakes = obs.filter(pl.col("kind") == "mistake")
    assert mistakes.filter(pl.col("mistake_kind") == "unknown")["tick"].to_list() == [added]
    defaulted = []
    m = meta.fit(obs, defaulted)
    assert m["mistake"]["per_min"] == pytest.approx(mistakes.height / (active / 1200), abs=1e-4)
    assert sum(m["mistake"]["kinds"].values()) == pytest.approx(1.0, abs=1e-3)
    assert "unknown" not in m["mistake"]["kinds"]


def test_a_thin_context_takes_the_all_play_rate(played):
    store, _ = played
    obs = meta.observations(store)
    thin = obs.filter(~((pl.col("kind") == "active") & (pl.col("context") == "building")))
    defaulted = []
    m = meta.fit(thin, defaulted)
    assert defaulted == ["hesitation.per_min.building (all-play rate)"]
    hes = thin.filter(pl.col("kind") == "hesitate").height
    minutes = thin.filter(pl.col("kind") == "active")["ticks"].sum() / 1200
    assert m["hesitation"]["per_min"]["building"] == pytest.approx(hes / minutes, abs=1e-4)


def test_model_json_has_exactly_the_schema(played, tmp_path):
    store, _ = played
    path = meta.export(store, tmp_path / "stage1")
    assert path == tmp_path / "stage1" / "meta.json"
    raw = path.read_bytes()
    assert raw.endswith(b"}\n") and b"\r" not in raw and raw.startswith(b'{\n "schema": 1')
    model = json.loads(raw)

    def same_shape(have, want, where="model"):
        assert isinstance(have, dict) and list(have) == list(want), where
        for key, value in want.items():
            if isinstance(value, dict):
                same_shape(have[key], value, f"{where}.{key}")
            elif isinstance(value, str):
                assert isinstance(have[key], str), f"{where}.{key}"
            elif isinstance(value, int):
                assert type(have[key]) is int, f"{where}.{key}"
            else:
                assert isinstance(have[key], float) and math.isfinite(have[key]), f"{where}.{key}"

    same_shape(model, SCHEMA)
    assert model["schema"] == 1 and model["kind"] == "meta" and model["created"] > 0
    lines = meta.summary(model)
    assert all(line.isascii() for line in lines) and "per stationary minute" in "".join(lines)


def test_thin_data_falls_back_to_defaults():
    defaulted = []
    m = meta.fit(pl.DataFrame(schema=meta.OBS_SCHEMA), defaulted)
    assert m["fitted_on"] == {"sessions": 0, "active_minutes": 0.0}
    assert {k: m[k] for k in meta.DEFAULTS} == meta.DEFAULTS
    assert defaulted == [
        "hesitation.per_min.mining",
        "hesitation.per_min.walking",
        "hesitation.per_min.building",
        "hesitation.per_min.other",
        "hesitation.after_break_p",
        "hesitation.duration_ms",
        "glance.per_min",
        "glance.angle_deg",
        "glance.hold_ms",
        "mistake.per_min",
        "mistake.kinds",
    ]
