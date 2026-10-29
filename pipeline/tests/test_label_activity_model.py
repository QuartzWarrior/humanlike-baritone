import numpy as np
import polars as pl
from labelkit import seg, t

from hbpipe.label import activity_model as am
from hbpipe.label.detectors import activities as act
from hbpipe.store import Store


def synthetic(n: int = 120, seed: int = 0):
    """Windows where breaks mean area_clear and distance means travel."""
    rng = np.random.default_rng(seed)
    x, y, groups = [], [], []
    for i in range(n):
        row = dict.fromkeys(am.INPUTS, 0.0)
        label = ("act.area_clear", "act.travel", am.NONE)[i % 3]
        if label == "act.area_clear":
            row[am.INPUTS[1]] = 30 + rng.normal(0, 2)
        elif label == "act.travel":
            row[am.INPUTS[2]] = 40 + rng.normal(0, 2)
        x.append([row[k] for k in am.INPUTS])
        y.append(label)
        groups.append(f"s{i % 4}")
    return np.array(x), y, groups


def test_fit_learns_and_cross_validates_by_session(tmp_path):
    x, y, groups = synthetic()
    model, accuracy = am.fit(x, y, groups)
    assert accuracy is not None and accuracy > 0.9
    assert model.classes == sorted(set(y))
    f = dict(zip(am.INPUTS, x[0], strict=True))
    assert model.predict(f)[0] == y[0]

    model.save(tmp_path)
    again = am.load(tmp_path)
    assert again is not None and again.predict(f) == model.predict(f)


def test_a_loaded_model_chooses_the_primary_activity():
    x, y, groups = synthetic()
    model, _ = am.fit(x, y, groups)
    travel = dict(zip(am.INPUTS, x[1], strict=True))
    try:
        act.use_model(model)
        out = act.decide({k: travel[k] for k in act.FEATURE_KEYS})
        assert list(out) == ["act.travel"] and out["act.travel"] >= act.FIRE
        nothing = act.decide({k: x[2][i] for i, k in enumerate(am.INPUTS) if k in act.FEATURE_KEYS})
        assert not set(nothing) & set(act.PRIMARY)
    finally:
        act.use_model(None)


def test_verified_activities_turn_corrections_into_targets(tmp_path):
    store = Store(tmp_path)
    common = {"session_id": "s", "segment_id": "seg-0001"}
    store.add_correction(**common, start_tick=0, end_tick=99, label="act.tunnel", verdict="correct")
    store.add_correction(
        **common,
        start_tick=100,
        end_tick=199,
        label="act.tunnel",
        verdict="wrong",
        new_label="act.area_clear",
    )
    store.add_correction(
        **common, start_tick=200, end_tick=299, label="act.travel", verdict="wrong"
    )
    store.add_correction(**common, start_tick=300, end_tick=399, label="move.walk", verdict="wrong")
    v = am.verified_activities(store)
    assert v.select("start_tick", "label").rows() == [
        (0, "act.tunnel"),
        (100, "act.area_clear"),
        (200, am.NONE),
    ]
    store.close()


def test_window_targets_take_the_label_covering_most_of_the_core():
    s = seg(400)
    verified = pl.DataFrame({"label": ["act.travel"], "start_tick": [t(0)], "end_tick": [t(300)]})
    (row,) = am.window_targets(s, verified)  # a short segment is one window
    assert row[3] == "act.travel"
    assert am.window_targets(s, verified.with_columns(end_tick=pl.lit(t(100)))) == []
