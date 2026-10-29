"""Model 3: a sequence model over a whole mining job (spec, Component D), judging human vs bot
at the activity level from break order, standing positions and timing.

A job is one decision trace (hbpipe.label.traces): its breaks in order, at most MAX_STEPS. Per
step: the move from the previous broken block (dx, dy, dz), the block relative to where the
player stood (dx, dy, dz), the camera turn since the previous decision (yaw, pitch), log ticks
since the previous break, whether the player moved, and a mask. A GRU reads the steps and its
last state scores the job. Excluded human jobs never reach the traces.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import torch
from torch import nn

from hbpipe.store import Store

MAX_STEPS = 64
MIN_STEPS = 6
STEP_FEATURES = 10  # plus the mask
EPOCHS = 60
LEARNING_RATE = 3e-3


def job_steps(trace: pl.DataFrame) -> np.ndarray:
    """(MAX_STEPS, STEP_FEATURES + 1) for one job's breaks, ordered by step."""
    t = trace.sort("step").head(MAX_STEPS)
    pos = t.select("x", "y", "z").to_numpy().astype(float)
    stand = t.select("stand_x", "stand_y", "stand_z").to_numpy().astype(float)
    look = t.select("yaw", "pitch").to_numpy().astype(float)
    move = np.clip(np.diff(pos, axis=0, prepend=pos[:1]), -4, 4)
    rel = np.clip(pos + 0.5 - stand, -6, 6)
    turn = np.diff(look, axis=0, prepend=look[:1])
    turn[:, 0] = (turn[:, 0] + 180.0) % 360.0 - 180.0
    since = np.log1p(t["ticks_since_prev"].fill_null(0).to_numpy().astype(float).clip(0, 2000))
    moved = t["moved"].fill_null(False).to_numpy().astype(float)
    steps = np.column_stack([move, rel, turn / 90.0, since, moved, np.ones(len(t))])
    out = np.zeros((MAX_STEPS, STEP_FEATURES + 1), dtype=np.float32)
    out[: len(t)] = steps
    return out


def jobs(store: Store, sessions=None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(jobs x steps x features, P(bot) target, session per job) over ``sessions`` (store rows;
    default every human and bot session)."""
    xs, ys, groups = [], [], []
    for s in store.sessions() if sessions is None else sessions:
        if s["source"] not in ("human", "bot"):
            continue
        path = store.table_path("traces", s["session_id"])
        if not path.exists():
            continue
        for (_,), trace in pl.read_parquet(path).group_by("trace_id"):
            if trace.height >= MIN_STEPS:
                xs.append(job_steps(trace))
                ys.append(int(s["source"] == "bot"))
                groups.append(s["session_id"])
    if not xs:
        return (
            np.zeros((0, MAX_STEPS, STEP_FEATURES + 1), np.float32),
            np.zeros(0, int),
            np.zeros(0, str),
        )
    return np.stack(xs), np.array(ys), np.array(groups)


class Net(nn.Module):
    def __init__(self):
        super().__init__()
        self.gru = nn.GRU(STEP_FEATURES, 32, batch_first=True)
        self.head = nn.Linear(32, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        lengths = x[:, :, -1].sum(1).clamp(min=1).long()
        packed = nn.utils.rnn.pack_padded_sequence(
            x[:, :, :-1], lengths, batch_first=True, enforce_sorted=False
        )
        _, h = self.gru(packed)
        return self.head(h[-1]).squeeze(1)


def fit_predict(
    x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray
) -> tuple[np.ndarray, None]:
    torch.manual_seed(0)
    net = Net()
    opt = torch.optim.Adam(net.parameters(), lr=LEARNING_RATE)
    # Classes are unbalanced here (jobs aren't sampled down): weight the rarer one up.
    pos = max(1, int(y_train.sum()))
    weight = torch.tensor((len(y_train) - pos) / pos, dtype=torch.float32)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=weight)
    xt, yt = torch.from_numpy(x_train), torch.from_numpy(y_train.astype(np.float32))
    for _ in range(EPOCHS):
        opt.zero_grad()
        loss_fn(net(xt), yt).backward()
        opt.step()
    with torch.no_grad():
        return torch.sigmoid(net(torch.from_numpy(x_test))).numpy(), None
