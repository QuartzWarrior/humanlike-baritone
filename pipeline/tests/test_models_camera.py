"""Stage 1 camera model on synthetic play generated with known parameters."""

import json
import math
from collections import Counter

import numpy as np
import polars as pl
import pytest

from hbpipe.cli import main
from hbpipe.label.detectors.camera import look_vectors
from hbpipe.label.run import LABELS_SCHEMA
from hbpipe.models.motor import camera
from hbpipe.store import Store

FRAME_MS = 1000 / 120
TICK_MS = 50.0
COUNT = 0.15  # degrees per mouse count at sensitivity 0.5
FEET = np.array([0.5, 64.0, 0.5])
EYE = FEET + [0.0, 1.62, 0.0]

HUMAN = {
    "a": 150.0,  # Fitts ms
    "b": 80.0,  # ms per bit
    "sigma": 0.15,
    "reaction": 250.0,
    "reaction_sigma": 0.3,
    "p_corrections": (0.4, 0.4, 0.2),  # 0, 1 or 2 corrections
    "p_overshoot": 0.3,  # of single corrections: the primary went past
    "gap": 80.0,
    "gap_sigma": 0.3,
    "correction": 100.0,
    "correction_sigma": 0.2,
    "landing": (2.0, 1.5),  # yaw (on screen), pitch sd, degrees
    "curvature": 0.05,
    "tremor": 0.03,  # chance per held frame of a one-count twitch
}
BOT = HUMAN | {"a": 60.0, "b": 0.0, "reaction": 90.0, "p_corrections": (1.0, 0.0, 0.0)}

SCHEMA = {
    "schema": 0,
    "kind": "",
    "created": 0,
    "fitted_on": {"sessions": 0, "acquisitions": 0},
    "reaction_ms": {"median": 0.0, "sigma": 0.0},
    "fitts": {"a_ms": 0.0, "b_ms": 0.0},
    "duration_sigma": 0.0,
    "peak_at": {"mean": 0.0, "sd": 0.0},
    "primary_gain": {"mean": 0.0, "sd": 0.0},
    "corrections": {"mean": 0.0, "max": 0},
    "correction_ms": {"median": 0.0, "sigma": 0.0},
    "correction_gap_ms": {"median": 0.0, "sigma": 0.0},
    "landing_sd_deg": {"yaw": 0.0, "pitch": 0.0},
    "landing_sd_frac": {"yaw": 0.0, "pitch": 0.0},
    "curvature_sd": 0.0,
    "tremor_deg": 0.0,
    "tremor_same_sign": 0.0,
    "frame_hz": 0.0,
}


def lognormal(rng, median, sigma):
    return median * math.exp(sigma * rng.standard_normal())


def ray_enters(yaw, pitch, block) -> bool:
    d = look_vectors(np.array([yaw]), np.array([pitch]))[0]
    d = np.where(np.abs(d) < 1e-12, 1e-12, d)
    near, far = (block - EYE) / d, (block + 1.0 - EYE) / d
    return np.maximum(near, far).min() >= max(np.minimum(near, far).max(), 0.0)


def direction(to) -> tuple[float, float]:
    return (
        math.degrees(math.atan2(-to[0], to[2])),
        math.degrees(math.atan2(-to[1], math.hypot(to[0], to[2]))),
    )


def plan(rng, n, p):
    """n acquisitions in a row, standing still: each target block breaks (the trigger for the
    next) after a hold. Returns the strokes (onset, ms, (dyaw, dpitch), bow), the holds, and per
    acquisition its block, trigger, onset, settle and corrections."""
    yaw, pitch, t = 0.0, 10.0, 1000.0
    strokes, holds, acqs = [], [], []
    for _ in range(n):
        n_fix = int(rng.choice(3, p=p["p_corrections"]))
        least = (15.0, 40.0, 80.0)[n_fix]
        while True:
            planned = rng.uniform(least, 120.0)
            angle = rng.uniform(0, 2 * math.pi)
            ty, tp = yaw + planned * math.cos(angle), pitch + planned * math.sin(angle)
            if not -40 <= tp <= 40:
                continue
            dist = rng.uniform(2.5, 4.5)
            block = np.floor(EYE + look_vectors(np.array([ty]), np.array([tp]))[0] * dist)
            cy, cp = direction(block + 0.5 - EYE)
            off_yaw, off_pitch = rng.normal(0, p["landing"][0]), rng.normal(0, p["landing"][1])
            land_yaw = yaw + (cy + off_yaw / math.cos(math.radians(cp)) - yaw + 180) % 360 - 180
            land_pitch = cp + off_pitch
            d = np.array([land_yaw - yaw, land_pitch - pitch])
            if ray_enters(land_yaw, land_pitch, block) and np.hypot(*d) >= least:
                break
        width = math.degrees(2 * math.atan(0.5 / np.linalg.norm(block + 0.5 - EYE)))
        trigger = t + rng.uniform(400, 700)
        onset = trigger + lognormal(rng, p["reaction"], p["reaction_sigma"])
        holds.append((t, onset))
        ms = (p["a"] + p["b"] * math.log2(np.hypot(*d) / width + 1)) * math.exp(
            p["sigma"] * rng.standard_normal()
        )
        if n_fix == 0:
            gain = 1.0
        elif n_fix == 1:
            gain = (1.15 if rng.random() < p["p_overshoot"] else 0.8) + rng.normal(0, 0.03)
        else:
            gain = 0.75 + rng.normal(0, 0.03)
        strokes.append((onset, ms, gain * d, rng.normal(0, p["curvature"])))
        end = onset + ms
        for part in [(1 - gain) * d / max(n_fix, 1)] * n_fix:
            gap = lognormal(rng, p["gap"], p["gap_sigma"])
            fix = lognormal(rng, p["correction"], p["correction_sigma"])
            strokes.append((end + gap, fix, part, 0.0))
            end += gap + fix
        acqs.append({"block": block, "trigger": trigger, "onset": onset, "settle": end})
        yaw, pitch, t = land_yaw, land_pitch, end
    holds.append((t, t + 1000))
    return strokes, holds, acqs, t + 1500


def write_session(store: Store, sid: str, source: str, n: int, p: dict, seed: int) -> list[dict]:
    """A session of n acquisitions at 120 frames per second, mouse turns in whole counts."""
    rng = np.random.default_rng(seed)
    strokes, holds, acqs, length = plan(rng, n, p)
    tf = np.arange(0.0, length, FRAME_MS)
    tf += rng.uniform(-0.3, 0.3, len(tf))
    path = np.zeros((len(tf), 2)) + [0.0, 10.0]
    for onset, ms, d, bow in strokes:
        x = np.clip((tf - onset) / ms, 0, 1)
        x = 10 * x**3 - 15 * x**4 + 6 * x**5
        path += np.outer(x, d) + np.outer(bow * np.sin(np.pi * x), [-d[1], d[0]])
    counts = np.round(path / COUNT).astype(np.int64)
    delta = np.diff(counts, axis=0, prepend=counts[:1])
    for a, b in holds:  # tremor: a one-count twitch and back two frames later
        for i in np.flatnonzero((tf > a + 30) & (tf < b - 40)):
            if rng.random() < p["tremor"]:
                axis, sign = rng.integers(2), rng.choice([-1, 1])
                delta[i, axis] += sign
                delta[i + 2, axis] -= sign
    look = (counts[0] + np.cumsum(delta, axis=0)) * COUNT
    moved = np.flatnonzero(np.any(delta != 0, axis=1))
    mouse_tick = np.ceil(tf[moved] / TICK_MS).astype(np.uint64)
    mouse = pl.DataFrame(
        {
            "session_id": sid,
            "segment_id": "seg-0001",
            "tick": mouse_tick,
            "t_ns": (tf[moved] * 1e6).astype(np.uint64),
            "dx": delta[moved, 0].astype(float),
            "dy": delta[moved, 1].astype(float),
            "d_yaw": (delta[moved, 0] * COUNT).astype(np.float32),
            "d_pitch": (delta[moved, 1] * COUNT).astype(np.float32),
        }
    )
    # One block at a time: each target stands from the previous one's break (its trigger) to
    # its own, the next acquisition's trigger.
    tt = np.arange(1.0, length // TICK_MS) * TICK_MS
    at = np.clip(np.searchsorted(tf, tt, side="right") - 1, 0, len(tf) - 1)
    breaks = np.array([a["trigger"] for a in acqs])
    current = np.searchsorted(breaks, tt, side="right") - 1
    hits = []
    for k in range(len(tt)):
        block = acqs[current[k]]["block"] if current[k] >= 0 else None
        on = block is not None and ray_enters(look[at[k], 0], look[at[k], 1], block)
        hits.append(block.astype(int).tolist() if on else [None] * 3)
    hit = np.array(hits, dtype=object)
    ticks = pl.DataFrame(
        {
            "segment_id": "seg-0001",
            "tick": np.arange(1, len(tt) + 1, dtype=np.uint64),
            "t_ns": (tt * 1e6).astype(np.uint64),
            "x": FEET[0],
            "y": FEET[1],
            "z": FEET[2],
            "yaw": look[at, 0].astype(np.float32),
            "pitch": look[at, 1].astype(np.float32),
            "crouching": False,
            "hit_type": ["block" if h[0] is not None else "miss" for h in hits],
            "hit_x": pl.Series(hit[:, 0].tolist(), dtype=pl.Int32),
            "hit_y": pl.Series(hit[:, 1].tolist(), dtype=pl.Int32),
            "hit_z": pl.Series(hit[:, 2].tolist(), dtype=pl.Int32),
            "hit_entity_id": pl.Series([None] * len(tt), dtype=pl.Int32),
        }
    )
    events = pl.DataFrame(
        {
            "segment_id": "seg-0001",
            "tick": np.ceil(breaks / TICK_MS).astype(np.uint64),
            "t_ns": (breaks * 1e6).astype(np.uint64),
            "kind": "block_broken",
            "while_paused": False,
        }
    )
    rows = []
    for i, a in enumerate(acqs):
        start = int(math.ceil(a["onset"] / TICK_MS))
        gone = acqs[i + 1]["trigger"] if i + 1 < len(acqs) else length - TICK_MS
        last = int(math.ceil(gone / TICK_MS)) - 2  # on the block until it breaks
        if hits[last - 1][0] is None:
            continue
        land = last
        while land > start and hits[land - 2][0] is not None:
            land -= 1
        a["span"] = (start, land + 5)
        rows.append(
            {
                "label_id": f"{sid}-{i}",
                "session_id": sid,
                "segment_id": "seg-0001",
                "start_tick": start,
                "end_tick": land + 5,
                "start_t_ns": int(start * TICK_MS * 1e6),
                "end_t_ns": int((land + 5) * TICK_MS * 1e6),
                "label": "look.acquire",
                "level": "primitive",
                "confidence": 0.9,
                "detector_version": 1,
            }
        )
    store.upsert_session(
        session_id=sid,
        source=source,
        started_unix_ms=0,
        play_day="2026-10-27",
        complete=1,
        labeler_fingerprint="f",
    )
    store.write_tables(sid, {"ticks_clean": ticks, "mouse": mouse, "events": events})
    store.replace_labels(sid, pl.from_dicts(rows, schema=LABELS_SCHEMA), 1, "f")
    return acqs


@pytest.fixture(scope="module")
def played(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("hb-data"))
    truth = {
        "human0": write_session(store, "human0", "human", 90, HUMAN, seed=1),
        "human1": write_session(store, "human1", "human", 90, HUMAN, seed=2),
    }
    write_session(store, "bot0", "bot", 60, BOT, seed=3)
    return store, truth


def test_fit_recovers_the_players_parameters(played):
    store, truth = played
    reasons = Counter()
    acqs = camera.acquisitions(store, reasons)
    spans = sum(1 for t in truth.values() for a in t if "span" in a)
    assert acqs.height >= 0.9 * spans, reasons
    assert set(acqs["session_id"]) == {"human0", "human1"}  # never the bot's play

    defaulted = []
    m = camera.fit(acqs, defaulted)
    assert defaulted == []
    assert m["fitted_on"] == {"sessions": 2, "acquisitions": acqs.height}
    assert m["fitts"]["a_ms"] == pytest.approx(HUMAN["a"], abs=20)
    assert m["fitts"]["b_ms"] == pytest.approx(HUMAN["b"], abs=15)
    assert m["duration_sigma"] == pytest.approx(HUMAN["sigma"], abs=0.06)
    assert m["reaction_ms"]["median"] == pytest.approx(HUMAN["reaction"], rel=0.12)
    assert m["reaction_ms"]["sigma"] == pytest.approx(HUMAN["reaction_sigma"], abs=0.08)
    assert m["peak_at"]["mean"] == pytest.approx(0.5, abs=0.04)  # minimum jerk is symmetric
    p0, p1, p2 = HUMAN["p_corrections"]
    assert m["corrections"]["mean"] == pytest.approx(p1 + 2 * p2, abs=0.15)
    assert m["corrections"]["max"] == 2
    over = p1 * HUMAN["p_overshoot"]
    gain = p0 + p1 * (over / p1 * 1.15 + (1 - over / p1) * 0.8) + p2 * 0.75
    assert m["primary_gain"]["mean"] == pytest.approx(gain, abs=0.04)
    assert (acqs["primary_gain"] > 1.05).mean() == pytest.approx(over, abs=0.07)
    assert m["correction_ms"]["median"] == pytest.approx(HUMAN["correction"], rel=0.15)
    assert m["correction_gap_ms"]["median"] == pytest.approx(HUMAN["gap"], rel=0.15)
    assert m["correction_gap_ms"]["sigma"] == pytest.approx(HUMAN["gap_sigma"], abs=0.12)
    assert m["landing_sd_deg"]["yaw"] == pytest.approx(HUMAN["landing"][0], rel=0.25)
    assert m["landing_sd_deg"]["pitch"] == pytest.approx(HUMAN["landing"][1], rel=0.25)
    assert m["curvature_sd"] == pytest.approx(HUMAN["curvature"], rel=0.3)
    # Twitches: two frames of one count on one axis, so per axis 0.15 sqrt(chance).
    assert m["tremor_deg"] == pytest.approx(COUNT * math.sqrt(HUMAN["tremor"]), rel=0.3)
    # ...and back two frames later: every turn reverses the one before, jitter not creep.
    assert m["tremor_same_sign"] < 0.2
    assert m["frame_hz"] == pytest.approx(120, abs=2)
    assert acqs["reaction_ms"].null_count() < 0.1 * acqs.height


def test_excluded_play_and_review_verdicts_are_respected(tmp_path):
    store = Store(tmp_path)
    write_session(store, "h", "human", 40, HUMAN, seed=4)
    labels = store.labels(session_id="h").sort("start_tick")
    whole = camera.acquisitions(store)
    mid = int(labels["end_tick"][len(labels) // 2])
    store.add_exclusion("h", "wurst search on", start_tick=0, end_tick=mid)
    wrong = labels.row(-3, named=True)
    store.add_correction(
        session_id="h",
        segment_id=wrong["segment_id"],
        start_tick=wrong["start_tick"],
        end_tick=wrong["end_tick"],
        label="look.acquire",
        verdict="wrong",
        label_id=wrong["label_id"],
    )
    reasons = Counter()
    kept = camera.acquisitions(store, reasons)
    assert 0 < kept.height < whole.height
    pad = math.ceil((camera.PRE_MS + camera.MAX_MOVE_MS) / camera.TICK_MS)
    assert kept["start_tick"].min() - pad > mid
    assert wrong["start_tick"] not in kept["start_tick"].to_list()
    assert reasons["excluded play"] >= len(labels) // 2


def test_model_json_has_exactly_the_schema(played, tmp_path):
    store, _ = played
    path = camera.export(store, tmp_path / "stage1")
    assert path == tmp_path / "stage1" / "camera.json"
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
    assert model["schema"] == 1 and model["kind"] == "camera" and model["created"] > 0


def test_thin_data_falls_back_to_defaults():
    defaulted = []
    m = camera.fit(pl.DataFrame(schema=camera.ACQ_SCHEMA), defaulted)
    assert m["fitted_on"] == {"sessions": 0, "acquisitions": 0}
    assert {k: m[k] for k in camera.DEFAULTS} == camera.DEFAULTS
    assert defaulted == list(camera.DEFAULTS)


def test_fit_models_command_writes_next_to_hb_data(played, tmp_path, capsys):
    store, _ = played
    root = tmp_path / "hb-data"
    root.mkdir()
    (root / "index.sqlite").write_bytes(store.db_path.read_bytes())
    for table in ("ticks_clean", "mouse", "events"):
        for f in (store.tables_dir / table).glob("*.parquet"):
            (root / "tables" / table).mkdir(parents=True, exist_ok=True)
            (root / "tables" / table / f.name).write_bytes(f.read_bytes())
    assert main(["fit-models", str(root), "--models", "camera"]) == 0
    out = capsys.readouterr().out
    assert (tmp_path / "hb-models" / "stage1" / "camera.json").exists()
    assert "look.acquire spans usable, from 2 sessions" in out and out.isascii()
    assert (
        main(["fit-models", str(root), "--models", "camera", "--out", str(tmp_path / "elsewhere")])
        == 0
    )
    assert (tmp_path / "elsewhere" / "camera.json").exists()
