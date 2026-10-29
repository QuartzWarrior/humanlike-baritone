"""Session-split evaluation shared by the three discriminators (spec, Component D: metrics and
leakage rules).

Clips are split by session, never by tick or clip: every fold's test sessions are unseen in
training, and the out-of-fold P(bot) of every clip is pooled for the scores. Accuracy and ROC AUC
get 95% intervals from bootstrapping whole sessions (human and bot sessions resampled
separately). Per-feature distances compare the human and bot distributions directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from scipy.stats import ks_2samp, wasserstein_distance
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold

MAX_FOLDS = 5
BOOTSTRAP = 1000

# fit_predict(train rows, train targets, test rows)
#     -> (P(bot) per test row, importance per feature or None)
FitPredict = Callable[[np.ndarray, np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray | None]]


@dataclass
class Scores:
    clips: int
    sessions: dict[str, int]  # per class
    accuracy: float
    accuracy_ci: tuple[float, float]
    auc: float
    auc_ci: tuple[float, float]
    oof: np.ndarray  # out-of-fold P(bot), one per clip
    importance: np.ndarray | None  # mean over folds, one per feature


def folds(y: np.ndarray, groups: np.ndarray) -> int:
    """How many session folds the data allows: each fold needs sessions of both classes."""
    per_class = [len(set(groups[y == c])) for c in (0, 1)]
    return min(MAX_FOLDS, *per_class)


def cross_validate(
    x: np.ndarray, y: np.ndarray, groups: np.ndarray, fit_predict: FitPredict, seed: int = 0
) -> Scores | None:
    """Out-of-fold scores, or None when either class has fewer than 2 sessions."""
    k = folds(y, groups)
    if k < 2:
        return None
    oof = np.full(len(y), np.nan)
    importance = []
    splitter = StratifiedGroupKFold(n_splits=k, shuffle=True, random_state=seed)
    for train, test in splitter.split(x, y, groups):
        if len(set(y[train])) < 2:
            continue
        p, imp = fit_predict(x[train], y[train], x[test])
        oof[test] = p
        if imp is not None:
            importance.append(imp)
    done = ~np.isnan(oof)
    acc, auc = _scores(y[done], oof[done])
    acc_ci, auc_ci = bootstrap(y[done], oof[done], groups[done], seed)
    return Scores(
        clips=int(done.sum()),
        sessions={"human": len(set(groups[y == 0])), "bot": len(set(groups[y == 1]))},
        accuracy=acc,
        accuracy_ci=acc_ci,
        auc=auc,
        auc_ci=auc_ci,
        oof=oof,
        importance=np.mean(importance, axis=0) if importance else None,
    )


def _scores(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    acc = float(((p >= 0.5) == (y == 1)).mean())
    auc = float(roc_auc_score(y, p)) if len(set(y)) == 2 else float("nan")
    return acc, auc


def bootstrap(
    y: np.ndarray, p: np.ndarray, groups: np.ndarray, seed: int = 0, n: int = BOOTSTRAP
) -> tuple[tuple[float, float], tuple[float, float]]:
    """95% intervals for accuracy and AUC, resampling whole sessions within each class."""
    rng = np.random.default_rng(seed)
    by_class = {c: sorted(set(groups[y == c])) for c in (0, 1)}
    rows = {g: np.flatnonzero(groups == g) for g in set(groups)}
    accs, aucs = [], []
    for _ in range(n):
        pick = np.concatenate(
            [rows[g] for c in (0, 1) for g in rng.choice(by_class[c], len(by_class[c]))]
        )
        a, u = _scores(y[pick], p[pick])
        accs.append(a)
        aucs.append(u)
    return _interval(accs), _interval(aucs)


def _interval(values: list[float]) -> tuple[float, float]:
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if not len(v):
        return (float("nan"), float("nan"))
    return (float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975)))


def distances(x: np.ndarray, y: np.ndarray, names: list[str]) -> list[dict]:
    """Per feature: Wasserstein distance between the human and bot values (in units of the
    pooled standard deviation) and the two-sample KS statistic and p-value."""
    out = []
    for j, name in enumerate(names):
        col = x[:, j]
        h, b = col[(y == 0) & ~np.isnan(col)], col[(y == 1) & ~np.isnan(col)]
        if len(h) < 2 or len(b) < 2:
            continue
        scale = np.nanstd(col) or 1.0
        ks = ks_2samp(h, b, method="asymp")
        out.append(
            {
                "feature": name,
                "wasserstein": float(wasserstein_distance(h, b) / scale),
                "ks": float(ks.statistic),
                "ks_p": float(ks.pvalue),
                "human_median": float(np.median(h)),
                "bot_median": float(np.median(b)),
            }
        )
    return sorted(out, key=lambda d: -d["wasserstein"])
