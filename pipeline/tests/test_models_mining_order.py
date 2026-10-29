"""Mining-order ranker on synthetic jobs whose "player" always breaks the top-most block within
reach, then the nearest."""

import json
import math
import os
from collections import Counter

import numpy as np
import polars as pl
import pytest

from hbpipe.label.traces import TRACE_SCHEMA, VOLUME_SCHEMA
from hbpipe.models.decision import mining_order as mo
from hbpipe.store import Store

SEGMENT = "seg-0001"
FACES = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
STEPS = 45

SCHEMA = {
    "schema": 0,
    "kind": "",
    "created": 0,
    "fitted_on": {"sessions": 0, "jobs": 0, "steps": 0, "candidates": 0},
    "features": [],
    "temperature": 0.0,
    "objective": "",
    "trees": [],
    "metrics": {"top1": 0.0, "top3": 0.0, "random_top1": 0.0, "nearest_top1": 0.0},
    "patterns": {"human": {}, "model": {}},
}


def top_then_nearest(p, d):
    return (-p[1], round(d, 9))


def bottom_then_nearest(p, d):
    return (p[1], round(d, 9))


def play(rng, origin, policy, n=STEPS):
    """One job in a deepslate box: the player stands in a two-high pocket with a short corridor
    off it and, without moving, breaks n blocks. Each time it takes the exposed solid block whose
    centre is within reach of the eye that ``policy`` ranks first (ties at random). Returns the
    starting volume, the stand position and the breaks."""
    ox, oy, oz = origin
    volume = {
        (x, y, z): "deepslate"
        for x in range(ox - 5, ox + 6)
        for y in range(oy - 2, oy + 5)
        for z in range(oz - 5, oz + 6)
    }
    dx, dz = [(1, 0), (-1, 0), (0, 1), (0, -1)][rng.integers(4)]
    for i in range(int(rng.integers(1, 4))):
        for y in (oy, oy + 1):
            volume[(ox + dx * i, y, oz + dz * i)] = "air"
    volume[(ox - dx, oy + 1, oz - dz)] = "gravel"  # in the pocket's wall: a candidate
    stand = (ox + rng.uniform(0.3, 0.7), float(oy), oz + rng.uniform(0.3, 0.7))
    eye = np.array([stand[0], stand[1] + 1.62, stand[2]])
    state = dict(volume)
    breaks = []
    for _ in range(n):
        options = []
        for p, b in state.items():
            d = float(np.linalg.norm(np.array(p) + 0.5 - eye))
            if b == "air" or d > 5.0:
                continue
            if any(state.get((p[0] + a, p[1] + b_, p[2] + c)) == "air" for a, b_, c in FACES):
                options.append((p, d))
        order = rng.permutation(len(options))
        p, _ = min((options[i] for i in order), key=lambda o: policy(*o))
        breaks.append((p, state[p]))
        state[p] = "air"
    return volume, stand, breaks


def look_at(rng, eye, p):
    to = np.array(p) + 0.5 - eye
    yaw = math.degrees(math.atan2(-to[0], to[2])) + rng.normal(0, 2)
    pitch = math.degrees(math.atan2(-to[1], math.hypot(to[0], to[2]))) + rng.normal(0, 2)
    return yaw + 360.0 * rng.integers(-2, 3), pitch  # yaw unwrapped, as the recorder keeps it


def write_session(store, sid, source, policy, jobs, seed):
    """A session of ``jobs`` mining jobs, 1000 ticks apart; returns their first and last ticks."""
    rng = np.random.default_rng(seed)
    traces, volumes, ticks, spans = [], [], [], []
    for j in range(jobs):
        tid = f"{sid}-job{j}"
        volume, stand, breaks = play(rng, (40 * j, 64, -30 * j), policy)
        eye = np.array([stand[0], stand[1] + 1.62, stand[2]])
        t0 = 100 + 1000 * j
        spans.append((t0, t0 + 10 * len(breaks)))
        for step, (p, block) in enumerate(breaks):
            tick = t0 + 10 * step + 9
            yaw, pitch = look_at(rng, eye, p)
            ticks.append({"segment_id": SEGMENT, "tick": tick, "yaw": yaw, "pitch": pitch})
            traces.append(
                {
                    "trace_id": tid,
                    "session_id": sid,
                    "segment_id": SEGMENT,
                    "label": "mine.area_clear" if j % 2 else "mine.tunnel",
                    "step": step,
                    "tick": tick,
                    "decision_tick": tick - 6,
                    "x": p[0],
                    "y": p[1],
                    "z": p[2],
                    "block": block,
                    "tool": "diamond_pickaxe",
                    "ticks_taken": 6,
                    "face": "north",
                    "stand_x": stand[0],
                    "stand_y": stand[1],
                    "stand_z": stand[2],
                    "yaw": yaw,
                    "pitch": pitch,
                    "moved": False,
                    "ticks_since_prev": None if step == 0 else 10,
                }
            )
        volumes += [
            {"trace_id": tid, "x": x, "y": y, "z": z, "block": b, "seen_at_start": True}
            for (x, y, z), b in volume.items()
        ]
    store.upsert_session(
        session_id=sid, source=source, started_unix_ms=0, play_day="2026-10-27", complete=1
    )
    store.write_tables(
        sid,
        {
            "traces": pl.from_dicts(traces, schema=TRACE_SCHEMA),
            "trace_volumes": pl.from_dicts(volumes, schema=VOLUME_SCHEMA),
            "ticks_clean": pl.from_dicts(
                ticks,
                schema={
                    "segment_id": pl.String,
                    "tick": pl.UInt64,
                    "yaw": pl.Float32,
                    "pitch": pl.Float32,
                },
            ),
        },
    )
    return spans


@pytest.fixture(scope="module")
def played(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("hb-data"))
    write_session(store, "human0", "human", top_then_nearest, jobs=3, seed=1)
    write_session(store, "human1", "human", top_then_nearest, jobs=3, seed=2)
    write_session(store, "bot0", "bot", bottom_then_nearest, jobs=3, seed=3)
    reasons = Counter()
    cands = mo.steps(store, reasons)
    report = {}
    model = mo.fit(cands, report)
    return store, cands, reasons, model, report


def test_candidates_follow_the_definition(played):
    _, cands, reasons, _, _ = played
    assert set(cands["session_id"]) == {"human0", "human1"}  # never the bot's play
    per_step = cands.group_by("trace_id", "step").agg(
        n=pl.len(), chosen=pl.col("chosen").sum(), dist=pl.col("dist").max()
    )
    assert per_step.height == 6 * (STEPS - 1)  # every step after the first, none dropped
    assert not [k for k in reasons if k.startswith("steps:") and reasons[k]]
    assert (per_step["chosen"] == 1).all() and per_step["dist"].max() <= mo.REACH
    assert cands["open_faces"].min() >= 1
    assert cands.filter(pl.col("falls") == 1)["block_class"].unique().to_list() == [mo.SOIL]
    assert (cands["below_feet"].sum() > 0) and (cands["angle"].is_between(0, 180)).all()
    assert cands.filter(pl.col("adjacent_prev") == 1)["manhattan_prev"].unique().to_list() == [1]
    # The player's own choices are top-most within reach of a fixed stand, so never below eye
    # level while anything is left above.
    chosen = cands.filter(pl.col("chosen") == 1)
    assert chosen["rel_y"].mean() > cands["rel_y"].mean() + 1


def test_ranker_recovers_top_most_then_nearest(played):
    _, cands, _, model, report = played
    m = model["metrics"]
    assert report["defaulted"] is False
    assert model["fitted_on"] == {
        "sessions": 2,
        "jobs": 6,
        "steps": 6 * (STEPS - 1),
        "candidates": cands.height,
    }
    # The best any ranker can do: the policy's own pick, ties for it broken at random.
    best = (
        cands.with_columns(top=pl.col("y") == pl.col("y").max().over("trace_id", "step"))
        .filter("top")
        .with_columns(tied=(pl.col("dist") - pl.col("dist").min().over("trace_id", "step")) < 1e-9)
        .group_by("trace_id", "step")
        .agg(pl.col("tied").sum())
    )
    oracle = float((1.0 / best["tied"]).mean())
    assert m["random_top1"] < 0.05 and oracle > 0.9
    assert m["top1"] > 0.6 and m["top1"] > 15 * m["random_top1"]
    assert m["top1"] > m["nearest_top1"] + 0.2 and m["top3"] > 0.85
    assert report["loglik"] > report["random_loglik"] + 1.5
    assert 0.01 < model["temperature"] < 100
    assert {"rel_y", "dpitch", "dist", "open_faces"} & set(list(mo.importance(model))[:3])
    human, bot_like = model["patterns"]["human"], model["patterns"]["model"]
    assert list(human) == list(bot_like) == list(mo.PATTERNS)
    for key in ("down_share", "above_eye_share", "same_layer_share"):
        assert bot_like[key] == pytest.approx(human[key], abs=0.15), key
    text = mo.summary(model)
    assert "top-1" in text and text.isascii()


def test_tree_evaluator_matches_lightgbm(played):
    _, cands, _, model, report = played
    exported = json.loads(json.dumps(model))  # what the Java side reads
    x = mo._matrix(cands)
    want = report["booster"].predict(x, raw_score=True)
    assert np.abs(mo.score(exported, x) - want).max() < 1e-9
    assert np.abs(mo.score(exported, x.tolist()) - want).max() < 1e-9


@pytest.mark.skipif(not os.environ.get("HB_DATA"), reason="set HB_DATA to an hb-data folder")
def test_tree_evaluator_matches_lightgbm_on_recorded_play():
    cands = mo.steps(Store(os.environ["HB_DATA"]))
    if cands.is_empty():
        pytest.skip("no human mining jobs there")
    booster = mo.train(cands)
    trees = json.loads(json.dumps(booster.dump_model()["tree_info"]))
    x = mo._matrix(cands)
    assert np.abs(mo.score({"trees": trees}, x) - booster.predict(x, raw_score=True)).max() < 1e-9


@pytest.mark.parametrize("zero_as_missing", [False, True])
def test_tree_evaluator_handles_missing_values_like_lightgbm(zero_as_missing):
    import lightgbm as lgb

    rng = np.random.default_rng(0)
    x = rng.normal(size=(3000, 3))
    x[:, 2] = np.where(rng.random(3000) < 0.3, 0.0, x[:, 2])
    y = np.nan_to_num(x[:, 0]) + (x[:, 2] == 0) - 0.5 * np.nan_to_num(x[:, 1])
    x[rng.random(x.shape) < 0.2] = np.nan
    params = {"objective": "regression", "num_leaves": 7, "verbose": -1}
    booster = lgb.train(
        params | {"zero_as_missing": zero_as_missing}, lgb.Dataset(x, y), num_boost_round=20
    )
    trees = json.loads(json.dumps(booster.dump_model()["tree_info"]))
    kinds = Counter()

    def walk(node):
        if "leaf_value" not in node:
            kinds[node["missing_type"]] += 1
            walk(node["left_child"])
            walk(node["right_child"])

    for t in trees:
        walk(t["tree_structure"])
    assert kinds["Zero" if zero_as_missing else "NaN"] > 0
    probe = np.vstack([x[:500], [[np.nan, 0.0, -0.0], [0.0, np.nan, np.nan], [1e-36, 0, 1]]])
    got = mo.score({"trees": trees}, probe)
    assert np.abs(got - booster.predict(probe)).max() < 1e-9


def test_model_json_has_exactly_the_schema(played, tmp_path):
    store, _, _, _, _ = played
    path = mo.export(store, tmp_path / "stage1")
    assert path == tmp_path / "stage1" / "mining_order.json"
    raw = path.read_bytes()
    assert raw.endswith(b"}\n") and b"\r" not in raw and raw.startswith(b'{\n "schema": 1')
    model = json.loads(raw)

    def same_shape(have, want, where="model"):
        assert isinstance(have, dict) and list(have) == list(want), where
        for key, value in want.items():
            if isinstance(value, dict) and value:
                same_shape(have[key], value, f"{where}.{key}")
            elif isinstance(value, dict | list):
                assert isinstance(have[key], type(value)), f"{where}.{key}"
            elif isinstance(value, str):
                assert isinstance(have[key], str), f"{where}.{key}"
            elif isinstance(value, int):
                assert type(have[key]) is int, f"{where}.{key}"
            else:
                assert isinstance(have[key], float) and math.isfinite(have[key]), f"{where}.{key}"

    same_shape(model, SCHEMA)
    assert model["kind"] == "mining_order" and model["created"] > 0
    assert model["features"] == list(mo.FEATURES) and model["objective"] == "lambdarank"
    for side in ("human", "model"):
        assert list(model["patterns"][side]) == list(mo.PATTERNS)
    assert 0 < len(model["trees"]) <= mo.ROUNDS
    node = model["trees"][0]["tree_structure"]
    assert {"split_feature", "threshold", "decision_type", "default_left", "missing_type"} <= set(
        node
    )
    assert {"left_child", "right_child"} <= set(node)


def test_excluded_play_is_left_out(tmp_path):
    store = Store(tmp_path)
    spans = write_session(store, "h", "human", top_then_nearest, jobs=3, seed=4)
    store.add_exclusion("h", "wurst search on", start_tick=spans[1][0], end_tick=spans[1][0] + 5)
    reasons = Counter()
    cands = mo.steps(store, reasons)
    assert set(cands["trace_id"]) == {"h-job0", "h-job2"}
    assert reasons["jobs: excluded play"] == 1


def test_thin_data_falls_back_to_the_prior(played):
    _, cands, _, _, _ = played
    report = {}
    m = mo.fit(cands.filter(pl.col("trace_id") == cands["trace_id"][0]), report)
    assert report["defaulted"] is True
    assert m["trees"] == mo.DEFAULT_TREES and m["temperature"] == 1.0
    assert m["metrics"]["top1"] > m["metrics"]["random_top1"]  # the prior beats chance

    empty = mo.fit(pl.DataFrame(schema=mo.STEP_SCHEMA))
    assert empty["fitted_on"] == {"sessions": 0, "jobs": 0, "steps": 0, "candidates": 0}
    assert empty["trees"] == mo.DEFAULT_TREES
    assert empty["metrics"] == dict.fromkeys(SCHEMA["metrics"], 0.0)
    x = np.zeros((1, len(mo.FEATURES)))
    assert mo.score(empty, x)[0] == pytest.approx(1.0 + 0.5 + 0.5)  # angle 0, dist 0
