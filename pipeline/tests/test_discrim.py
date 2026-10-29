import numpy as np
import polars as pl
import pytest

from hbpipe.discrim import decision_model, sequence_model
from hbpipe.discrim.eval_set import MIN_TICKS, WINDOW_TICKS, balance, build, clips_of
from hbpipe.discrim.evaluate import cross_validate, distances, folds
from hbpipe.discrim.feature_model import fit_predict
from hbpipe.discrim.features import KEYS
from hbpipe.discrim.report import format_report, run
from hbpipe.label.report import set_report
from hbpipe.label.run import LABELS_SCHEMA
from hbpipe.store import Store

N = 800  # ticks per session: 4 clips each, 12 per class over 3 sessions


def spans(*rows, session="s", segment="seg-0001") -> pl.DataFrame:
    return pl.from_dicts(
        [
            {
                "label_id": f"{session}-{i}",
                "session_id": session,
                "segment_id": segment,
                "start_tick": a,
                "end_tick": b,
                "start_t_ns": a * 50_000_000,
                "end_t_ns": b * 50_000_000,
                "label": label,
                "level": "primitive",
                "confidence": 0.9,
                "detector_version": 1,
            }
            for i, (label, a, b) in enumerate(rows)
        ],
        schema=LABELS_SCHEMA,
    )


def test_clips_cut_a_sets_ticks_into_windows():
    labels = spans(("move.walk", 0, 249), ("move.walk", 240, 300), ("move.walk", 400, 420))
    clips = clips_of(labels)
    # 0-300 and 400-420 are 322 ticks: one full window and one of 122 (the rest).
    assert [len(t) for _, _, _, t in clips] == [WINDOW_TICKS, 322 - WINDOW_TICKS]
    assert clips[0][3][0] == 0 and clips[1][3][-1] == 420
    short = spans(("move.walk", 0, MIN_TICKS - 2))
    assert clips_of(short) == []


def test_balance_samples_the_larger_class_down_keeping_every_session():
    clips = pl.DataFrame(
        {
            "clip_id": [f"c{i}" for i in range(9)],
            "label": ["a"] * 7 + ["b"] * 2,
            "source": ["human"] * 5 + ["bot"] * 2 + ["human"] * 2,
            "session_id": ["big"] * 4 + ["small", "bot1", "bot2", "big", "big"],
        }
    )
    out = balance(clips).filter(pl.col("label") == "a")
    assert out["source"].value_counts().sort("source").rows() == [("bot", 2), ("human", 2)]
    # Two human clips out of five: one from each session, not two from the big one.
    assert sorted(out.filter(pl.col("source") == "human")["session_id"]) == ["big", "small"]
    assert "b" not in balance(clips)["label"].to_list()  # no bot clips: nothing to compare


def separable(n=80, sessions=4, shift=4.0, seed=0):
    rng = np.random.default_rng(seed)
    y = np.array([0, 1] * (n // 2))
    x = rng.normal(size=(n, 3))
    x[:, 0] += shift * y
    groups = np.array([f"{'bot' if c else 'hum'}{i // 2 % sessions}" for i, c in enumerate(y)])
    return x, y, groups


def test_cross_validation_splits_by_session_and_scores_with_intervals():
    x, y, groups = separable()
    scores = cross_validate(x, y, groups, fit_predict)
    assert scores.accuracy > 0.9 and scores.auc > 0.95
    assert scores.accuracy_ci[0] <= scores.accuracy <= scores.accuracy_ci[1]
    assert scores.sessions == {"human": 4, "bot": 4}
    assert scores.importance.argmax() == 0  # the shifted feature gives the bot away
    # One bot session can't be split: no score rather than a leaky one.
    one = np.where(y == 1, "bot0", groups)
    assert folds(y, one) == 1 and cross_validate(x, y, one, fit_predict) is None
    d = distances(x, y, ["shifted", "noise1", "noise2"])
    assert d[0]["feature"] == "shifted" and d[0]["ks"] > 0.5


def test_sequence_and_decision_models_learn_separable_data():
    rng = np.random.default_rng(0)
    y = np.array([0, 1] * 20)
    clips = rng.normal(size=(40, len(sequence_model.STREAMS) + 1, WINDOW_TICKS)).astype(np.float32)
    clips[:, 0, :] += 2.0 * y[:, None]  # bots turn harder
    clips[:, -1, :] = 1.0
    train, test = np.arange(40) < 30, np.arange(40) >= 30
    p, _ = sequence_model.fit_predict(clips[train], y[train], clips[test])
    assert ((p >= 0.5) == y[test]).mean() >= 0.8

    jobs = rng.normal(size=(40, decision_model.MAX_STEPS, decision_model.STEP_FEATURES + 1))
    jobs = jobs.astype(np.float32)
    jobs[:, :, -1] = 0
    jobs[:, :10, -1] = 1  # ten real steps each
    jobs[:, :10, 0] += 2.0 * y[:, None]
    p, _ = decision_model.fit_predict(jobs[train], y[train], jobs[test])
    assert ((p >= 0.5) == y[test]).mean() >= 0.8


def write_session(
    store: Store,
    sid: str,
    source: str,
    snap: bool,
    strength: float | None = None,
    like: str | None = None,
    started: int = 0,
) -> None:
    """A session of N ticks walking along x; bots snap the camera 90 degrees every 40 ticks,
    humans turn smoothly. ``strength``: a bot session's humanization strength; ``like``: draw
    the same random play as that session."""
    rng = np.random.default_rng(sum((like or sid).encode()))  # stable across runs, unlike hash()
    store.upsert_session(
        session_id=sid,
        source=source,
        started_unix_ms=started,
        play_day="2026-10-27",
        complete=1,
        labeler_fingerprint="f",
        bot_strength=strength,
    )
    t = np.arange(N)
    if snap:
        yaw_rate = np.where(t % 40 == 0, 1800.0, 0.0)
    else:
        yaw_rate = 40 * np.sin(t / 15) + rng.normal(0, 8, N)
    ticks = pl.DataFrame(
        {
            "segment_id": ["seg-0001"] * N,
            "tick": t.astype(np.uint64),
            "yaw": np.cumsum(yaw_rate / 20) % 360,
            "pitch": rng.normal(10, 3, N),
            "vx": np.full(N, 0.2),
            "vz": np.zeros(N),
            "speed": np.full(N, 4.3) + rng.normal(0, 0.1, N),
            "vspeed": np.zeros(N),
            "accel": rng.normal(0, 1, N),
            "yaw_rate": yaw_rate,
            "pitch_rate": rng.normal(0, 5, N) * (0 if snap else 1),
            "yaw_jerk": np.gradient(np.gradient(yaw_rate)),
            "pitch_jerk": np.zeros(N),
            "on_ground": np.ones(N, bool),
            "sprinting": np.zeros(N, bool),
            "crouching": np.zeros(N, bool),
            "ticks_since_landing": np.full(N, 100),
            **{k: (np.ones(N, bool) if k == "k_forward" else np.zeros(N, bool)) for k in KEYS},
        }
    )
    events = pl.DataFrame(
        schema={
            "segment_id": pl.String,
            "tick": pl.UInt64,
            "kind": pl.String,
            "ticks_taken": pl.UInt32,
            "while_paused": pl.Boolean,
        }
    )
    store.write_tables(sid, {"ticks_clean": ticks, "events": events})
    store.replace_labels(sid, spans(("move.walk", 0, N - 1), session=sid), 1, "f")


@pytest.fixture
def mixed(tmp_path) -> Store:
    store = Store(tmp_path)
    for i in range(3):
        write_session(store, f"human{i}", "human", snap=False)
        write_session(store, f"bot{i}", "bot", snap=True)
    return store


def test_snapping_bots_are_caught_and_reported(mixed):
    ev = build(mixed)
    assert ev.clips["source"].value_counts().sort("source").rows() == [("bot", 12), ("human", 12)]
    report = run(mixed, models=("feature", "sequence"))
    walk = report["sets"]["move.walk"]
    assert walk["feature"]["accuracy"] >= 0.9 and walk["feature"]["sessions"] == {
        "human": 3,
        "bot": 3,
    }
    tops = [t["feature"] for t in walk["feature"]["top_features"]]
    assert any(f.startswith(("yaw", "pitch")) for f in tops)
    assert report["gate"]["pass"]
    text = format_report(report)
    assert "move.walk" in text and text.isascii()
    assert (mixed.root / "discrim").exists()
    # The bot's labels never reach the human gate numbers.
    assert set_report(mixed)["spans"].sum() == 3


def test_humanized_bot_sessions_are_judged_against_the_phase_4_gate(mixed):
    from hbpipe.discrim.eval_set import pick_bots

    assert pick_bots(mixed) == "plain"  # only plain Baritone so far: the Phase 3 baseline
    for i in range(3):  # the executor, playing exactly like the humans
        write_session(mixed, f"exec{i}", "bot", snap=False, strength=1.0, like=f"human{i}")
    assert pick_bots(mixed) == "humanized"
    ev = build(mixed)
    bots = ev.clips.filter(pl.col("source") == "bot")["session_id"].unique().to_list()
    assert sorted(bots) == ["exec0", "exec1", "exec2"]  # the plain ones left out
    # Judging only a newer executor build: sessions started before it are left out.
    write_session(mixed, "exec-new", "bot", snap=False, strength=1.0, like="human0", started=5000)
    newer = build(mixed, bot_since_ms=5000).clips.filter(pl.col("source") == "bot")
    assert newer["session_id"].unique().to_list() == ["exec-new"]
    older = build(mixed, bot_until_ms=5000).clips.filter(pl.col("source") == "bot")
    assert sorted(older["session_id"].unique().to_list()) == ["exec0", "exec1", "exec2"]
    # The mining-job model judges the same bot sessions as the clips.
    from hbpipe.discrim.eval_set import judged_sessions

    judged = judged_sessions(mixed, bot_since_ms=5000)
    assert [s["session_id"] for s in judged if s["source"] == "bot"] == ["exec-new"]
    report = run(mixed, models=("feature",))
    assert report["bot"] == "humanized" and report["gate"]["phase"] == 4
    # Indistinguishable from the humans here, so it passes: at most 75% in every set.
    assert report["gate"]["pass"], report["gate"]
    assert "Phase 4 gate" in format_report(report)
    plain = run(mixed, models=("feature",), bot="plain")
    assert plain["gate"]["phase"] == 3 and plain["gate"]["pass"]
