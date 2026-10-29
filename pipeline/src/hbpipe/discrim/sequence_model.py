"""Model 2: a 1D CNN over a clip's per-tick streams (spec, Component D), to catch patterns no
hand-built statistic covers.

Channels per tick: yaw and pitch rate, speed, vertical speed, acceleration, the ground, sprint and
crouch flags, the nine movement and action keys, and a mask (1 on real ticks, 0 on padding).
They're standardized on each training fold. Raw mouse frames stay out, as in the feature model,
until the bot sends real input.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import polars as pl
import torch
from torch import nn

from hbpipe.discrim.eval_set import WINDOW_TICKS, EvalSet
from hbpipe.discrim.evaluate import Scores, cross_validate
from hbpipe.discrim.features import KEYS, _per_tick
from hbpipe.store import Store

STREAMS = (
    "yaw_rate",
    "pitch_rate",
    "speed",
    "vspeed",
    "accel",
    "on_ground",
    "sprinting",
    "crouching",
    *KEYS,
)
EPOCHS = 30
BATCH = 32
LEARNING_RATE = 1e-3


class Net(nn.Module):
    def __init__(self, channels: int):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(channels, 32, 5, padding=2),
            nn.ReLU(),
            nn.Conv1d(32, 32, 5, padding=2),
            nn.ReLU(),
        )
        self.head = nn.Linear(64, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        mask = x[:, -1:, :]
        h = self.conv(x) * mask
        mean = h.sum(-1) / mask.sum(-1).clamp(min=1)
        peak = h.masked_fill(mask == 0, -1e4).amax(-1)
        return self.head(torch.cat([mean, peak], 1)).squeeze(1)


@lru_cache(maxsize=32)
def _streams(root: str, session_id: str) -> pl.DataFrame:
    t = _per_tick(Store(root), session_id)
    return t.select(
        "segment_id", "tick", *[pl.col(c).cast(pl.Float32).fill_null(0) for c in STREAMS]
    )


def tensors(store: Store, ev: EvalSet, clips: pl.DataFrame) -> np.ndarray:
    """(clips, channels + mask, WINDOW_TICKS), zero-padded after each clip's ticks."""
    out = np.zeros((clips.height, len(STREAMS) + 1, WINDOW_TICKS), dtype=np.float32)
    for i, (clip_id, sid, seg) in enumerate(
        clips.select("clip_id", "session_id", "segment_id").iter_rows()
    ):
        ticks = ev.ticks[clip_id]
        t = _streams(str(store.root), sid)
        rows = t.filter(pl.col("segment_id") == seg).join(
            pl.DataFrame({"tick": ticks.astype(np.uint64)}), on="tick", how="right"
        )
        n = len(ticks)
        out[i, :-1, :n] = np.nan_to_num(rows.select(STREAMS).to_numpy().T)
        out[i, -1, :n] = 1.0
    return out


def fit_predict(
    x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray
) -> tuple[np.ndarray, None]:
    """P(bot) for the test clips. Channels are standardized with the training clips' real ticks."""
    real = x_train[:, -1, :] > 0
    mean = np.array([x_train[:, c, :][real].mean() for c in range(x_train.shape[1] - 1)])
    std = np.array([x_train[:, c, :][real].std() or 1.0 for c in range(x_train.shape[1] - 1)])

    def scale(x: np.ndarray) -> torch.Tensor:
        z = x.copy()
        z[:, :-1, :] = (z[:, :-1, :] - mean[None, :, None]) / std[None, :, None]
        z[:, :-1, :] *= z[:, -1:, :]
        return torch.from_numpy(z)

    torch.manual_seed(0)
    net = Net(x_train.shape[1])
    opt = torch.optim.Adam(net.parameters(), lr=LEARNING_RATE)
    loss_fn = nn.BCEWithLogitsLoss()
    xt, yt = scale(x_train), torch.from_numpy(y_train.astype(np.float32))
    for _ in range(EPOCHS):
        order = torch.randperm(len(yt))
        for b in range(0, len(yt), BATCH):
            idx = order[b : b + BATCH]
            opt.zero_grad()
            loss_fn(net(xt[idx]), yt[idx]).backward()
            opt.step()
    with torch.no_grad():
        return torch.sigmoid(net(scale(x_test))).numpy(), None


def evaluate(store: Store, ev: EvalSet, clips: pl.DataFrame, seed: int = 0) -> Scores | None:
    """Session-split scores for one set's clips."""
    x = tensors(store, ev, clips)
    y = (clips["source"] == "bot").cast(pl.Int8).to_numpy()
    return cross_validate(x, y, clips["session_id"].to_numpy(), fit_predict, seed)
