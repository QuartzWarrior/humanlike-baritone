import numpy as np
import polars as pl
from labelkit import row, seg, t

from hbpipe.label.base import Span
from hbpipe.label.discovery import (
    FEATURES,
    MIN_TICKS,
    WINDOW,
    claim,
    cluster,
    embed,
    features,
    name_cluster,
    named_sets,
    windows,
)
from hbpipe.store import Store

U = "other.unlabeled"


def test_windows_skip_short_spans_and_cut_long_ones():
    assert windows(Span(0, MIN_TICKS - 2, U, 1.0)) == []
    assert windows(Span(0, WINDOW - 1, U, 1.0)) == [(0, WINDOW - 1)]
    # 2.5 windows: the half window stands alone; a tail under MIN_TICKS joins the piece before.
    assert windows(Span(0, 2 * WINDOW + 99, U, 1.0)) == [
        (0, WINDOW - 1),
        (WINDOW, 2 * WINDOW - 1),
        (2 * WINDOW, 2 * WINDOW + 99),
    ]
    assert windows(Span(0, WINDOW + 4, U, 1.0)) == [(0, WINDOW + 4)]


def test_features_measure_motion_inputs_and_events():
    s = seg(
        40,
        speed=[4.0] * 20 + [0.0] * 20,
        crouching=[True] * 10 + [False] * 30,
        k_attack=True,
        x=[0.5 + 0.2 * i for i in range(40)],
        mouse=[row(i, d_yaw=1.0, d_pitch=-0.5) for i in range(0, 40, 2)],
        keys=[row(5, key=87, down=True), row(9, key=87, down=False)],
    )
    f = features(s, 0, 39)
    assert set(f) == set(FEATURES)
    assert f["duration_s"] == 2.0
    assert f["speed_mean"] == 2.0
    assert f["crouch_share"] == 0.25 and f["attack_share"] == 1.0
    assert f["mouse_rate"] == 10.0 and f["mouse_path_rate"] == 15.0
    assert f["key_press_rate"] == 0.5
    assert abs(f["straightness"] - 1.0) < 1e-9


def blobs(n: int = 30, seed: int = 0) -> pl.DataFrame:
    """Two groups of feature rows that differ across most features, as real behaviors do."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(2 * n):
        f = {k: float(rng.normal(3.0 if i >= n else 0.0, 0.1)) for k in FEATURES}
        f["duration_s"] = 2.0
        rows.append({"session_id": "s", "segment_id": "seg", "start_tick": i, "end_tick": i} | f)
    return pl.from_dicts(rows)


def test_cluster_finds_separate_groups_and_explains_them():
    c = cluster(blobs())
    labels = c.windows["cluster"].to_list()
    assert len(set(labels[:30]) - {-1}) == 1 and len(set(labels[30:]) - {-1}) == 1
    assert labels[0] != labels[-1]
    assert c.summary.height == 2
    assert c.summary["seconds"].to_list() == [60.0, 60.0]
    assert all(len(d) == 5 for d in c.summary["distinct"].to_list())
    assert all(s.isascii() for d in c.summary["distinct"].to_list() for s in d)  # CLI output


def test_a_named_cluster_claims_matching_unlabeled_stretches(tmp_path):
    store = Store(tmp_path)
    crouch_dash, standing = seg(40, speed=5.0, crouching=True), seg(40)
    span = Span(t(0), t(39), U, 1.0)
    c = cluster(pl.from_dicts(embed(crouch_dash, [span]) * 10 + embed(standing, [span]) * 10))
    c.windows = c.windows.with_columns(cluster=pl.Series([0] * 10 + [1] * 10, dtype=pl.Int32))
    name_cluster(store.db, c, 0, "move.crouch_dash")
    sets = named_sets(store.db)
    assert [s.name for s in sets] == ["move.crouch_dash"]

    claimed, rest = claim(crouch_dash, [span], sets)
    assert [(s.label, s.start_tick, s.end_tick) for s in claimed] == [
        ("move.crouch_dash", t(0), t(39))
    ]
    assert rest == []
    claimed, rest = claim(standing, [span], sets)
    assert claimed == [] and [s.label for s in rest] == [U]
    store.close()


def test_set_names_need_a_family(tmp_path):
    import pytest

    store = Store(tmp_path)
    c = cluster(blobs())
    with pytest.raises(ValueError):
        name_cluster(store.db, c, 0, "unnamed")
    with pytest.raises(ValueError):
        name_cluster(store.db, c, 0, "other.thing")
    store.close()
