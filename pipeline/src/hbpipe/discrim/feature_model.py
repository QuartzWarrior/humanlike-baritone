"""Model 1: gradient boosting over the clip statistics (spec, Component D). Its feature
importances are the to-do list for the behavior models: the top features are what still gives
the bot away."""

from __future__ import annotations

import warnings

import lightgbm as lgb
import numpy as np

PARAMS = {
    "n_estimators": 200,
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_child_samples": 5,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "importance_type": "gain",
    "verbose": -1,
    "random_state": 0,
}


def fit_predict(
    x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """P(bot) for the test clips, and gain importance per feature (normalized to sum 1)."""
    model = lgb.LGBMClassifier(**PARAMS)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # LightGBM warns about feature names on numpy input
        model.fit(x_train, y_train)
        p = model.predict_proba(x_test)[:, 1]
    imp = model.feature_importances_.astype(float)
    return p, imp / imp.sum() if imp.sum() else imp
