"""The gradient-boosted activity classifier that fixes the rules' mistakes (spec, pass 3).

The rules come first. Once the review tool holds hand-checked activity labels (about two hours
of them), `hbpipe train-activities` fits a LightGBM classifier on 60 s windows: the inputs are
the window's features plus every rule's score, the target is the checked label of the window's
core (or "none" where a reviewer rejected an activity without naming another). From then on the
labeler lets the model choose each window's primary activity; overlays (fight,
manage_inventory, smelt_craft) stay rule-based. Splits are by session, never by window.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl

from hbpipe.label.base import Seg
from hbpipe.label.detectors import activities as act
from hbpipe.store import Store

NONE = "none"
MIN_WINDOWS = 50
MODEL_DIR = "models"
MODEL_NAME = "activity"
INPUTS = (*act.FEATURE_KEYS, *(f"rule_{name}" for name in act.PRIMARY))


def window_inputs(seg: Seg) -> list[tuple[int, int, dict[str, float]]]:
    """(core start row, core end row, inputs) for each window of a segment."""
    out = []
    ctx = act._context(seg)
    for i0, i1, c0, c1 in act.windows(len(seg)):
        f = act._features(ctx, i0, i1)
        f |= {f"rule_{name}": act.RULES[name](f) for name in act.PRIMARY}
        out.append((c0, c1, f))
    return out


def window_targets(seg: Seg, verified: pl.DataFrame) -> list[tuple[int, int, dict, str]]:
    """Windows of ``seg`` whose core a checked activity label covers by half or more, with that
    label. ``verified`` holds the segment's checked spans: label (an act.* set or NONE),
    start_tick, end_tick."""
    out = []
    if verified.is_empty():
        return out
    ticks = seg.tick
    for c0, c1, f in window_inputs(seg):
        t0, t1 = int(ticks[c0]), int(ticks[c1])
        core = t1 - t0 + 1
        best, best_cover = None, 0
        for label, a, b in verified.select("label", "start_tick", "end_tick").iter_rows():
            cover = min(b, t1) - max(a, t0) + 1
            if cover > best_cover:
                best, best_cover = label, cover
        if best is not None and best_cover * 2 >= core:
            out.append((c0, c1, f, best))
    return out


def verified_activities(store: Store) -> pl.DataFrame:
    """Checked activity spans from the corrections: confirmed, relabeled, added, and rejected
    without a new label (target NONE)."""
    c = store.corrections()
    if c.is_empty():
        return pl.DataFrame(
            schema={
                "session_id": pl.String,
                "segment_id": pl.String,
                "start_tick": pl.Int64,
                "end_tick": pl.Int64,
                "label": pl.String,
            }
        )
    c = c.sort("id").unique(
        ["session_id", "segment_id", "start_tick", "end_tick", "label"],
        keep="last",
        maintain_order=True,
    )
    target = (
        pl.when(pl.col("verdict") == "wrong")
        .then(pl.col("new_label").fill_null(NONE))
        .otherwise(pl.col("label"))
    )
    out = c.filter(pl.col("label").str.starts_with("act.")).with_columns(label=target)
    return out.filter(pl.col("label").is_in([*act.PRIMARY, NONE])).select(
        "session_id", "segment_id", "start_tick", "end_tick", "label"
    )


@dataclass
class ActivityModel:
    booster: object  # lightgbm.Booster
    classes: list[str]

    def predict(self, f: dict[str, float]) -> tuple[str, float]:
        x = np.array([[f.get(k, 0.0) for k in INPUTS]])
        p = np.asarray(self.booster.predict(x))[0]
        k = int(np.argmax(p))
        return self.classes[k], float(p[k])

    def save(self, root: Path) -> Path:
        d = root / MODEL_DIR
        d.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(d / f"{MODEL_NAME}.lgbm"))
        (d / f"{MODEL_NAME}.json").write_text(
            json.dumps({"inputs": list(INPUTS), "classes": self.classes}, indent=1),
            encoding="utf-8",
        )
        return d / f"{MODEL_NAME}.lgbm"


def load(root: Path) -> ActivityModel | None:
    """The trained model under hb-data/models, if any and if it matches today's inputs."""
    import lightgbm as lgb

    d = Path(root) / MODEL_DIR
    if not (d / f"{MODEL_NAME}.lgbm").exists():
        return None
    meta = json.loads((d / f"{MODEL_NAME}.json").read_text(encoding="utf-8"))
    if meta["inputs"] != list(INPUTS):
        return None  # features changed since training: retrain
    return ActivityModel(lgb.Booster(model_file=str(d / f"{MODEL_NAME}.lgbm")), meta["classes"])


def fit(x: np.ndarray, y: list[str], groups: list[str]) -> tuple[ActivityModel, float | None]:
    """Fits the classifier; also returns its accuracy under leave-sessions-out cross-validation
    (None with fewer than two sessions)."""
    import lightgbm as lgb
    from sklearn.model_selection import GroupKFold

    classes = sorted(set(y))
    yi = np.array([classes.index(v) for v in y])
    params = {
        "objective": "multiclass",
        "num_class": len(classes),
        "num_leaves": 15,
        "min_data_in_leaf": 5,
        "learning_rate": 0.05,
        "verbose": -1,
    }

    def train(idx: np.ndarray):
        return lgb.train(params, lgb.Dataset(x[idx], yi[idx]), num_boost_round=200)

    accuracy = None
    n_groups = len(set(groups))
    if n_groups >= 2:
        hits = 0
        for tr, te in GroupKFold(n_splits=min(5, n_groups)).split(x, yi, groups):
            if len(set(yi[tr])) < len(classes):
                continue
            pred = np.argmax(np.asarray(train(tr).predict(x[te])), axis=1)
            hits += int((pred == yi[te]).sum())
        accuracy = hits / len(yi)
    return ActivityModel(train(np.arange(len(yi))), classes), accuracy


def train_from_store(store: Store) -> tuple[ActivityModel, float | None, int]:
    """Trains on every checked activity label in the store and saves the model."""
    from hbpipe.label.run import prepare

    verified = verified_activities(store)
    rows, targets, groups = [], [], []
    for sid in verified["session_id"].unique().to_list():
        v = verified.filter(pl.col("session_id") == sid)
        for seg in prepare(store, sid).segments:
            for _, _, f, label in window_targets(
                seg, v.filter(pl.col("segment_id") == seg.segment_id)
            ):
                rows.append([f.get(k, 0.0) for k in INPUTS])
                targets.append(label)
                groups.append(sid)
    if len(rows) < MIN_WINDOWS or len(set(targets)) < 2:
        raise ValueError(
            f"{len(rows)} checked windows over {len(set(targets))} classes; need {MIN_WINDOWS}+ "
            "windows and 2+ classes (check more activity spans in the review tool)"
        )
    model, accuracy = fit(np.array(rows, dtype=float), targets, groups)
    model.save(store.root)
    return model, accuracy, len(rows)
