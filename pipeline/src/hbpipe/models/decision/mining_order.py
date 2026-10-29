"""Stage 1 mining-order ranker (spec, Component B: Decision models, mine.area_clear, mine.tunnel,
mine.strip).

At each step of a mining job the Java executor picks the next block to break from the blocks it
could break right now. This module learns how the player picks: a LightGBM ranker scores every
candidate, and the executor samples from a softmax over the scores, probability proportional to
exp(score / temperature), with the temperature fitted so the bot's choices are as varied as the
player's (not always the top pick).

Data: human sessions only (bot play never), from the decision traces (hbpipe.label.traces: one
row per break, and the job's starting volume). Jobs that touch play kept out of training are
already missing from the traces; they are dropped here too (hbpipe.exclusions), as are jobs
whose mining label a reviewer judged wrong.

Steps and candidates (steps):
    step        every break of a job but the first. The player chooses from where they stood
                at the previous break (its stand position; the eye EYE_HEIGHT above it) and with
                the look they had when that block broke (yaw and pitch from ticks_clean at the
                previous break's tick): that is "the current crosshair".
    state       the job's starting volume with the job's own breaks so far turned to air. Other
                changes during the job (falling gravel, placed blocks, flowing water) aren't
                tracked. Positions the recorder never saw are unknown: never candidates, and as
                a neighbour they count as closed (not passable).
    candidate   a solid block (hbpipe.label.blocks.solid) of the state, not broken yet, with at
                least one open face (a passable neighbour: air, water, grass, torches...), whose
                centre is within REACH blocks of the eye. Steps whose chosen block isn't a
                candidate are dropped, with the reason counted: the block was already broken
                (something refilled it), isn't solid (grass), is out of reach (the player walked
                there first: "move first" isn't modelled here), or has no open face as far as
                the state knows.

Features, in the order the model uses them (FEATURES). Coordinates are Minecraft's: y up, block
(x, y, z) spans x..x+1 and so on, its centre is (x + 0.5, y + 0.5, z + 0.5). Yaw 0 looks along
+z, -90 along +x, +90 along -x; pitch +90 looks straight down. The look vector of (yaw, pitch)
is (-sin yaw cos pitch, -sin pitch, cos yaw cos pitch). "Previous" is the block broken at the
previous step; "eye" is the stand position plus EYE_HEIGHT; booleans are 0.0 or 1.0.
    dist                    eye to block centre, blocks
    angle                   degrees between the look vector and eye -> block centre (0..180)
    dyaw                    yaw of eye -> centre, atan2(-dx, dz), minus the look's yaw, wrapped
                            to [-180, 180): positive means turning right
    dpitch                  pitch of eye -> centre, atan2(-dy, hypot(dx, dz)), minus the look's
                            pitch: positive means looking further down
    rel_y                   block centre y minus eye y
    dy_prev                 candidate y minus previous y (signed: up is positive)
    dh_prev                 horizontal offset from the previous block, |dx| + |dz| (dx_prev and
                            dz_prev in world axes were dropped: which compass direction a job
                            runs in is an accident of where it was dug, so a model that learns
                            "prefer +x" from 17 jobs has learned that job, not the player)
    along_sweep             sign agreement with the last horizontal move: sign(dx) * last_x +
                            sign(dz) * last_z, where (last_x, last_z) is the sign vector of the
                            most recent break-to-break move in this job that changed x or z
                            (0 before the first one). +1 continues the sweep, -1 reverses it
    manhattan_prev          |dx| + |dy| + |dz| from the previous block
    adjacent_prev           shares a face with the previous block (manhattan 1)
    same_layer              same y as the previous block
    same_column             same x and z as the previous block (above or below it)
    same_row                same y, and same x or same z, as the previous block (on a straight
                            horizontal line through it)
    open_faces              how many of the 6 face neighbours are passable (0..6)
    below_feet              the block the player stands on: (floor(sx), floor(sy - 0.001),
                            floor(sz)) for stand position (sx, sy, sz)
    under_stand_level       block y <= floor(sy - 0.001): at or below the level of the block
                            under the feet
    block_class             BLOCK_CLASSES: 0 stone-like (pickaxe), 1 dirt/sand/gravel-like
                            (shovel), 2 ore, 3 wood, 4 other; compared as a number (<= splits)
    falls                   gravity block (sand, gravel, concrete powder: blocks.falls)
    air_neighbours_beyond   passable face neighbours whose shared face the eye can't see: the
                            eye isn't in front of that face's plane, (eye - centre) . n <= 0.5
                            for face normal n. Space behind the block, e.g. a cave it opens into
    skipped_before          at an earlier step of this job the block was a candidate sharing a
                            face with that step's previous block, and another block was chosen
                            (it was passed over); the executor knows this from its own history

The first break of a job has no previous block: the executor picks it some other way (Baritone's
own choice) and the ranker takes over from the second. Dropped from the spec's list:
    dx_prev, dz_prev        world-axis offsets (see dh_prev and along_sweep): no better than
                            dh_prev held out, and they tie the model to the compass
    liquid_neighbour        a face neighbour is water or lava: never true in the recorded jobs,
                            so there is nothing to learn (keeping clear of lava is a rule for the
                            executor, as in Baritone, not a habit to fit)
    ore_neighbour           a face neighbour is an ore: mostly ores behind the block, which the
                            player can't see, so their choices can't follow it; any weight on it
                            would make the bot uncover hidden ore more (or less) often than a
                            player does, what anti-xray ore-ratio checks look for. It added
                            nothing held out
air_neighbours_beyond can also read space the player can't see (a cave behind the block); in the
recorded jobs it is almost always the player's own digging, and the model gives it little
weight. summary() lists the features the model leans on.

The model (fit, mining_order.json):
    objective   "lambdarank": LightGBM ranking, one query per step, the chosen block relevant
                (1), every other candidate not (0). Ranking compares candidates of the same step
                only, which is all the executor needs (a softmax within the step), and doesn't
                spend capacity on predicting how many candidates a step has, as a binary
                chosen-vs-not classifier would. Modest capacity (PARAMS, ROUNDS): there are only
                a few hundred to a thousand steps from a handful of jobs.
    metrics     leave-one-job-out cross-validation: each job is scored by a model fitted on the
                others. top1 and top3: the share of steps whose chosen block the held-out model
                ranks first (top three), ties split evenly; random_top1 (1 / candidates) and
                nearest_top1 (the candidate nearest the crosshair, smallest angle) for scale.
    temperature T maximizing the held-out log-likelihood of the chosen blocks under
                softmax(score / T) over each step's candidates, scores from the held-out models
                (the log-likelihood is concave in 1 / T: a golden-section search).
    trees       the final model, fitted on every step: LightGBM's booster.dump_model()
                ["tree_info"]. score() is the reference evaluator the Java port must match.
    patterns    the spec's pattern statistics for the player's choices ("human") and for the
                model's ("model"), on the same steps: the expected value over the model's
                softmax (held-out scores, fitted T), what sampling it many times would give.
                Every step's history is the player's, so these compare one choice at a time
                (how often it goes down, turns, skips), not whole free-running jobs (PATTERNS).

With fewer than MIN_STEPS steps or MIN_JOBS jobs nothing is fitted: the model is DEFAULT_TREES,
a generic prior (near the crosshair, next to the previous block, close, not under the feet, not
gravel or sand), not this player.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path

import numpy as np
import polars as pl

from hbpipe import exclusions
from hbpipe.label.blocks import block_id, falls, is_log, is_ore, passable, solid
from hbpipe.label.traces import TRACE_LABELS, trace_id
from hbpipe.store import Store

FILE = "mining_order.json"
SCHEMA_VERSION = 1
EYE_HEIGHT = 1.62
REACH = 5.0  # blocks, eye to candidate centre
FEET_EPS = 0.001
NEIGHBOURS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))

FEATURES = (
    "dist",
    "angle",
    "dyaw",
    "dpitch",
    "rel_y",
    "dy_prev",
    "dh_prev",
    "along_sweep",
    "manhattan_prev",
    "adjacent_prev",
    "same_layer",
    "same_column",
    "same_row",
    "open_faces",
    "below_feet",
    "under_stand_level",
    "block_class",
    "falls",
    "air_neighbours_beyond",
    "skipped_before",
)

OBJECTIVE = "lambdarank"
PARAMS = {
    "objective": OBJECTIVE,
    "num_leaves": 7,
    "max_depth": 3,
    "min_data_in_leaf": 40,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "verbose": -1,
    "seed": 7,
    "deterministic": True,
    "force_col_wise": True,
    "num_threads": 1,
}
ROUNDS = 300
MIN_STEPS = 50
MIN_JOBS = 3

# block_class: 0 stone-like, 1 dirt/sand/gravel-like, 2 ore, 3 wood, 4 other. Ores first, then
# the explicit lists, then the suffix rules; anything else is 4.
STONE = 0
SOIL = 1
ORE = 2
WOOD = 3
OTHER = 4
_STONE_IDS = frozenset(
    """
    stone deepslate cobbled_deepslate cobblestone mossy_cobblestone tuff granite diorite andesite
    calcite dripstone_block smooth_basalt basalt polished_basalt blackstone netherrack end_stone
    sandstone red_sandstone obsidian crying_obsidian bedrock terracotta amethyst_block
    budding_amethyst magma_block
    """.split()  # noqa: SIM905 (a word list reads better than quoted strings)
)
_SOIL_IDS = frozenset(
    """
    dirt coarse_dirt rooted_dirt grass_block podzol mycelium dirt_path farmland mud clay sand
    red_sand gravel suspicious_sand suspicious_gravel soul_sand soul_soil snow_block powder_snow
    moss_block muddy_mangrove_roots
    """.split()  # noqa: SIM905
)
BLOCK_CLASSES = {
    "stone-like (pickaxe)": STONE,
    "dirt/sand/gravel-like (shovel)": SOIL,
    "ore": ORE,
    "wood": WOOD,
    "other": OTHER,
}


def block_class(block: str) -> int:
    """The block_class of a block id (see the module docstring and _STONE_IDS, _SOIL_IDS)."""
    if is_ore(block):
        return ORE
    if block in _STONE_IDS or block.startswith("infested_") or block.endswith("_terracotta"):
        return STONE
    if block in _SOIL_IDS or block.endswith("concrete_powder"):
        return SOIL
    if is_log(block) or block.endswith("_planks") or block.startswith("stripped_"):
        return WOOD
    return OTHER


STEP_SCHEMA = {
    "session_id": pl.String,
    "trace_id": pl.String,
    "label": pl.String,
    "step": pl.Int32,
    "x": pl.Int32,
    "y": pl.Int32,
    "z": pl.Int32,
    "chosen": pl.Int8,
    **{name: pl.Float64 for name in FEATURES},
    # For the pattern statistics only, not features:
    "turn": pl.Int8,  # null: no horizontal move, or none before it in the job
    "moved": pl.Boolean,  # the player's position changed before this break (the trace's flag)
}


# --- Steps and candidates ----------------------------------------------------------------------


def steps(store: Store, reasons: Counter | None = None) -> pl.DataFrame:
    """One row per candidate per step in the human sessions' mining jobs (STEP_SCHEMA), sorted
    so each step's candidates are contiguous. With ``reasons``, also counts the jobs and steps
    left out, by why."""
    reasons = Counter() if reasons is None else reasons
    rows: list[dict] = []
    for s in store.sessions(source="human"):
        rows += _session(store, s["session_id"], reasons)
    if not rows:
        return pl.DataFrame(schema=STEP_SCHEMA)
    return pl.from_dicts(rows, schema=STEP_SCHEMA)


def _session(store: Store, session_id: str, reasons: Counter) -> list[dict]:
    paths = {t: store.table_path(t, session_id) for t in ("traces", "trace_volumes", "ticks_clean")}
    if not paths["traces"].exists():
        return []
    traces = pl.read_parquet(paths["traces"])
    if traces.is_empty():
        return []
    if not all(p.exists() for p in paths.values()):
        reasons["jobs: session tables missing"] += traces["trace_id"].n_unique()
        return []
    traces = _usable_jobs(store, session_id, traces, reasons)
    if traces.is_empty():
        return []
    ticks = pl.read_parquet(paths["ticks_clean"], columns=["segment_id", "tick", "yaw", "pitch"])
    traces = traces.with_columns(pl.col("tick").cast(pl.Int64)).join(
        ticks.select(
            "segment_id",
            pl.col("tick").cast(pl.Int64),
            look_yaw=pl.col("yaw").cast(pl.Float64),
            look_pitch=pl.col("pitch").cast(pl.Float64),
        ).unique(["segment_id", "tick"]),
        on=["segment_id", "tick"],
        how="left",
    )
    volumes = pl.read_parquet(paths["trace_volumes"])
    out = []
    for (tid,), job in traces.sort("trace_id", "step").group_by("trace_id", maintain_order=True):
        vol = volumes.filter(pl.col("trace_id") == tid)
        state = {
            (x, y, z): block_id(b) for x, y, z, b in vol.select("x", "y", "z", "block").iter_rows()
        }
        out += _job(session_id, tid, job.to_dicts(), state, reasons)
    return out


def _usable_jobs(store: Store, session_id: str, traces: pl.DataFrame, reasons: Counter):
    """``traces`` without the jobs that touch excluded play or whose label a reviewer rejected."""
    jobs = traces.group_by("trace_id").agg(
        start=pl.col("decision_tick").min().cast(pl.Int64), end=pl.col("tick").max().cast(pl.Int64)
    )
    kept = exclusions.drop_excluded(jobs, exclusions.for_session(store, session_id), "start", "end")
    reasons["jobs: excluded play"] += jobs.height - kept.height
    keep = set(kept["trace_id"])
    c = store.corrections(session_id=session_id)
    if not c.is_empty():
        latest = c.sort("id").unique("label_id", keep="last", maintain_order=True)
        wrong = latest.filter(
            pl.col("label_id").is_not_null()
            & (pl.col("verdict") == "wrong")
            & pl.col("label").is_in(TRACE_LABELS)
        )
        rejected = {
            trace_id(session_id, seg, label, start)
            for seg, label, start in wrong.select("segment_id", "label", "start_tick").iter_rows()
        }
        reasons["jobs: label judged wrong"] += len(keep & rejected)
        keep -= rejected
    return traces.filter(pl.col("trace_id").is_in(sorted(keep)))


def _job(session_id: str, tid: str, breaks: list[dict], state: dict, reasons: Counter):
    """Candidate rows for every step of one job after the first."""
    out: list[dict] = []
    broken: set[tuple[int, int, int]] = set()
    skipped: set[tuple[int, int, int]] = set()
    last_dir: tuple[int, int] | None = None

    def at(p):
        return "air" if p in broken else state.get(p)

    def is_open(p) -> bool:
        b = at(p)
        return b is not None and passable(b)

    for k, row in enumerate(breaks):
        pos = (row["x"], row["y"], row["z"])
        if k > 0:
            prev = breaks[k - 1]
            cands = _step(session_id, tid, row, prev, at, is_open, broken, skipped, last_dir)
            why = _why_not(pos, cands, at, is_open, broken, prev)
            if why:
                reasons[f"steps: {why}"] += 1
            else:
                out += cands
            # The history counts every step, kept or not, as the executor's would.
            skipped |= {
                (c["x"], c["y"], c["z"])
                for c in cands
                if c["adjacent_prev"] == 1.0 and (c["x"], c["y"], c["z"]) != pos
            }
            dx, dz = pos[0] - prev["x"], pos[2] - prev["z"]
            if dx or dz:
                last_dir = (_sign(dx), _sign(dz))
        broken.add(pos)
    return out


def _why_not(pos, cands, at, is_open, broken, prev) -> str | None:
    """Why the chosen block at ``pos`` isn't among ``cands`` (None when it is)."""
    if any(c["chosen"] for c in cands):
        return None
    if prev["stand_x"] is None or prev["look_yaw"] is None:
        return "no stand position or look at the previous break"
    if pos in broken:
        return "chosen block was already broken in this job (refilled)"
    b = at(pos)
    if b is None or not solid(b):
        return "chosen block not solid (e.g. grass)"
    if not any(is_open(_add(pos, n)) for n in NEIGHBOURS):
        return "chosen block has no open face in the tracked state"
    return "chosen block out of reach of the previous stand (moved first)"


def _step(session_id, tid, row, prev, at, is_open, broken, skipped, last_dir) -> list[dict]:
    if prev["stand_x"] is None or prev["look_yaw"] is None:
        return []
    sx, sy, sz = prev["stand_x"], prev["stand_y"], prev["stand_z"]
    eye = (sx, sy + EYE_HEIGHT, sz)
    yaw, pitch = prev["look_yaw"], prev["look_pitch"]
    look = _look_vector(yaw, pitch)
    feet_y = math.floor(sy - FEET_EPS)
    below = (math.floor(sx), feet_y, math.floor(sz))
    pp = (prev["x"], prev["y"], prev["z"])
    chosen = (row["x"], row["y"], row["z"])
    r = math.ceil(REACH) + 1
    ex, ey, ez = (math.floor(v) for v in eye)
    out = []
    for x in range(ex - r, ex + r + 1):
        for y in range(ey - r, ey + r + 1):
            for z in range(ez - r, ez + r + 1):
                p = (x, y, z)
                to = (x + 0.5 - eye[0], y + 0.5 - eye[1], z + 0.5 - eye[2])
                dist = math.sqrt(to[0] ** 2 + to[1] ** 2 + to[2] ** 2)
                if dist > REACH or p in broken:
                    continue
                b = at(p)
                if b is None or not solid(b):
                    continue
                around = [(n, _add(p, n)) for n in NEIGHBOURS]
                open_n = [n for n, q in around if is_open(q)]
                if not open_n:
                    continue
                f = _features(to, dist, yaw, pitch, look)
                dx, dy, dz = x - pp[0], y - pp[1], z - pp[2]
                manhattan = abs(dx) + abs(dy) + abs(dz)
                hdir = (_sign(dx), _sign(dz))
                turn = None
                if last_dir is not None and hdir != (0, 0):
                    turn = int(hdir != last_dir)
                # to = centre - eye, so (eye - centre) . n <= 0.5: the eye isn't in front of it
                beyond = sum(
                    1 for n in open_n if sum(a * b for a, b in zip(to, n, strict=True)) >= -0.5
                )
                out.append(
                    {
                        "session_id": session_id,
                        "trace_id": tid,
                        "label": row["label"],
                        "step": row["step"],
                        "x": x,
                        "y": y,
                        "z": z,
                        "chosen": int(p == chosen),
                        **f,
                        "rel_y": to[1],
                        "dy_prev": float(dy),
                        "dh_prev": float(abs(dx) + abs(dz)),
                        "along_sweep": float(
                            hdir[0] * last_dir[0] + hdir[1] * last_dir[1] if last_dir else 0
                        ),
                        "manhattan_prev": float(manhattan),
                        "adjacent_prev": float(manhattan == 1),
                        "same_layer": float(dy == 0),
                        "same_column": float(dx == 0 and dz == 0),
                        "same_row": float(dy == 0 and (dx == 0 or dz == 0)),
                        "open_faces": float(len(open_n)),
                        "below_feet": float(p == below),
                        "under_stand_level": float(y <= feet_y),
                        "block_class": float(block_class(b)),
                        "falls": float(falls(b)),
                        "air_neighbours_beyond": float(beyond),
                        "skipped_before": float(p in skipped),
                        "turn": turn,
                        "moved": row["moved"],
                    }
                )
    return out


def _features(to, dist, yaw, pitch, look) -> dict:
    """dist, angle, dyaw and dpitch of a block centre at ``to`` from the eye."""
    cos = (to[0] * look[0] + to[1] * look[1] + to[2] * look[2]) / max(dist, 1e-9)
    target_yaw = math.degrees(math.atan2(-to[0], to[2]))
    target_pitch = math.degrees(math.atan2(-to[1], math.hypot(to[0], to[2])))
    return {
        "dist": dist,
        "angle": math.degrees(math.acos(max(-1.0, min(1.0, cos)))),
        "dyaw": (target_yaw - yaw + 180.0) % 360.0 - 180.0,
        "dpitch": target_pitch - pitch,
    }


def _look_vector(yaw: float, pitch: float) -> tuple[float, float, float]:
    y, p = math.radians(yaw), math.radians(pitch)
    return (-math.sin(y) * math.cos(p), -math.sin(p), math.cos(y) * math.cos(p))


def _add(p, n):
    return (p[0] + n[0], p[1] + n[1], p[2] + n[2])


def _sign(v: int) -> int:
    return (v > 0) - (v < 0)


# --- Fitting ----------------------------------------------------------------------------------


def _groups(cands: pl.DataFrame) -> np.ndarray:
    """Start offsets of each step's rows, plus the end (cands sorted by step)."""
    if cands.is_empty():
        return np.zeros(1, dtype=np.int64)
    key = cands.select(pl.concat_str(["trace_id", "step"], separator="|")).to_series()
    change = np.flatnonzero(key.to_numpy()[1:] != key.to_numpy()[:-1]) + 1
    return np.concatenate([[0], change, [cands.height]]).astype(np.int64)


def _matrix(cands: pl.DataFrame) -> np.ndarray:
    return cands.select(FEATURES).to_numpy().astype(np.float64)


def train(cands: pl.DataFrame, rounds: int | None = None, params: dict | None = None):
    """A LightGBM ranker (lightgbm.Booster) on every step of ``cands`` (ROUNDS and PARAMS by
    default)."""
    import lightgbm as lgb

    offsets = _groups(cands)
    data = lgb.Dataset(
        _matrix(cands),
        cands["chosen"].to_numpy().astype(np.int32),
        group=np.diff(offsets),
        feature_name=list(FEATURES),
        free_raw_data=False,
    )
    return lgb.train(params or PARAMS, data, num_boost_round=rounds or ROUNDS)


def _topk(scores: np.ndarray, chosen: np.ndarray, offsets: np.ndarray, k: int) -> np.ndarray:
    """Per step, the chance the chosen block lands in the top k when ties break at random."""
    out = np.zeros(len(offsets) - 1)
    for i, (a, b) in enumerate(zip(offsets[:-1], offsets[1:], strict=True)):
        s = scores[a:b]
        c = s[int(np.argmax(chosen[a:b]))]
        above, tied = int((s > c).sum()), int((s == c).sum())
        out[i] = min(max((k - above) / tied, 0.0), 1.0)
    return out


def _log_probs(scores: np.ndarray, offsets: np.ndarray, temperature: float) -> np.ndarray:
    """Per row, log softmax(score / T) within its step."""
    out = np.empty_like(scores)
    for a, b in zip(offsets[:-1], offsets[1:], strict=True):
        z = scores[a:b] / temperature
        m = z.max()
        out[a:b] = z - m - math.log(float(np.exp(z - m).sum()))
    return out


def _loglik(scores, chosen, offsets, temperature: float) -> float:
    """Mean log-likelihood per step of the chosen blocks."""
    lp = _log_probs(scores, offsets, temperature)
    return float(lp[chosen == 1].sum()) / (len(offsets) - 1)


def fit_temperature(scores: np.ndarray, chosen: np.ndarray, offsets: np.ndarray) -> float:
    """T maximizing the log-likelihood of the chosen blocks under softmax(score / T): a
    golden-section search on log(1 / T) (the log-likelihood is concave in 1 / T)."""
    lo, hi = math.log(1e-3), math.log(1e3)
    g = (math.sqrt(5) - 1) / 2

    def f(log_beta: float) -> float:
        return _loglik(scores, chosen, offsets, math.exp(-log_beta))

    a, b = hi - g * (hi - lo), lo + g * (hi - lo)
    fa, fb = f(a), f(b)
    for _ in range(60):
        if fa > fb:  # the maximum is in [lo, b]
            hi, b, fb = b, a, fa
            a = hi - g * (hi - lo)
            fa = f(a)
        else:  # in [a, hi]
            lo, a, fa = a, b, fb
            b = lo + g * (hi - lo)
            fb = f(b)
    return math.exp(-(lo + hi) / 2)


def cross_validate(cands: pl.DataFrame, rounds: int | None = None, params: dict | None = None):
    """Held-out scores for every row: each job scored by a model fitted on the other jobs."""
    out = np.zeros(cands.height)
    jobs = cands["trace_id"].to_numpy()
    for job in np.unique(jobs):
        held = jobs == job
        booster = train(cands.filter(pl.Series(~held)), rounds, params)
        out[held] = booster.predict(_matrix(cands.filter(pl.Series(held))), raw_score=True)
    return out


PATTERNS = {
    "down_share": "of the choices that change layer, the share that go down (top-down work)",
    "same_layer_share": "choices on the previous block's layer",
    "above_eye_share": "choices whose centre is above the eye",
    "same_row_share": "choices on a horizontal line through the previous block",
    "same_column_share": "choices straight above or below the previous block",
    "adjacent_share": "choices sharing a face with the previous block",
    "turn_rate": "of the choices that move horizontally, the share that leave the direction of "
    "the last horizontal move (sweep direction changes)",
    "skip_share": "of the steps with a candidate next to the previous block, the share choosing "
    "one that isn't",
    "revisit_share": "choices that jump back (not next to the previous block) to a block passed "
    "over earlier in the job (skipped_before)",
    "reach_mean": "mean eye-to-block distance of the choice, blocks",
    "under_stand_share": "choices at or below the level of the block under the feet",
}


def patterns(cands: pl.DataFrame, weights: np.ndarray) -> dict[str, float]:
    """PATTERNS over the steps of ``cands``, each candidate counted with its weight: the chosen
    flags for the player's own choices, the model's probabilities for its expected choices."""
    offsets = _groups(cands)
    n = len(offsets) - 1
    w = np.asarray(weights, dtype=np.float64)

    def col(name: str) -> np.ndarray:
        return cands[name].to_numpy().astype(np.float64)

    def share(mask: np.ndarray, of: np.ndarray | None = None) -> float:
        den = float(w.sum()) if of is None else float((w * of).sum())
        return float((w * mask).sum()) / den if den > 0 else 0.0

    if n <= 0:
        return dict.fromkeys(PATTERNS, 0.0)
    dy, adjacent = col("dy_prev"), col("adjacent_prev")
    turn = cands["turn"].fill_null(-1).to_numpy()
    step_has_adjacent = np.add.reduceat(adjacent, offsets[:-1]) > 0
    has_adjacent = np.repeat(step_has_adjacent, np.diff(offsets)).astype(np.float64)
    return {
        "down_share": share(dy < 0, dy != 0),
        "same_layer_share": share(dy == 0),
        "above_eye_share": share(col("rel_y") > 0),
        "same_row_share": share(col("same_row")),
        "same_column_share": share(col("same_column")),
        "adjacent_share": share(adjacent),
        "turn_rate": share(turn == 1, turn >= 0),
        "skip_share": share((adjacent == 0) * has_adjacent, has_adjacent),
        "revisit_share": share((adjacent == 0) * col("skipped_before")),
        "reach_mean": float((w * col("dist")).sum() / n),
        "under_stand_share": share(col("under_stand_level")),
    }


def fit(cands: pl.DataFrame, report: dict | None = None) -> dict:
    """The mining-order model (JSON-able) from ``cands`` (steps()). With ``report``, also fills in
    what doesn't go into the model: whether it fell back to DEFAULT_TREES, held-out scores,
    log-likelihoods and top-1 per job, top-3 baselines, the player's moved share, the booster.
    On thin data the metrics and model patterns are the prior's (nothing was fitted to hold
    out)."""
    report = {} if report is None else report
    offsets = _groups(cands)
    n_steps = len(offsets) - 1
    n_jobs = cands["trace_id"].n_unique()
    chosen = cands["chosen"].to_numpy()
    defaulted = n_steps < MIN_STEPS or n_jobs < MIN_JOBS
    report["defaulted"] = defaulted
    model = {
        "schema": SCHEMA_VERSION,
        "kind": "mining_order",
        "created": int(time.time()),
        "fitted_on": {
            "sessions": cands["session_id"].n_unique(),
            "jobs": n_jobs,
            "steps": n_steps,
            "candidates": cands.height,
        },
        "features": list(FEATURES),
        "temperature": 1.0,
        "objective": OBJECTIVE,
        "trees": DEFAULT_TREES,
        "metrics": {"top1": 0.0, "top3": 0.0, "random_top1": 0.0, "nearest_top1": 0.0},
        "patterns": {"human": patterns(cands, chosen), "model": dict.fromkeys(PATTERNS, 0.0)},
    }
    if not n_steps:
        return _rounded(model)

    if defaulted:
        held, temperature = score(model, _matrix(cands)), 1.0
    else:
        held = cross_validate(cands)
        temperature = fit_temperature(held, chosen, offsets)
    sizes = np.diff(offsets)
    nearest = -cands["angle"].to_numpy()
    top1 = _topk(held, chosen, offsets, 1)
    model["temperature"] = temperature
    model["metrics"] = {
        "top1": float(top1.mean()),
        "top3": float(_topk(held, chosen, offsets, 3).mean()),
        "random_top1": float((1.0 / sizes).mean()),
        "nearest_top1": float(_topk(nearest, chosen, offsets, 1).mean()),
    }
    model["patterns"]["model"] = patterns(cands, np.exp(_log_probs(held, offsets, temperature)))
    jobs = cands["trace_id"].to_numpy()[offsets[:-1]]
    report |= {
        "held_out_scores": held,
        "loglik": _loglik(held, chosen, offsets, temperature),
        "random_loglik": float(-np.log(sizes).mean()),
        "random_top3": float((np.minimum(3, sizes) / sizes).mean()),
        "nearest_top3": float(_topk(nearest, chosen, offsets, 3).mean()),
        "per_job_top1": {
            str(j): (int((jobs == j).sum()), float(top1[jobs == j].mean())) for j in np.unique(jobs)
        },
        "moved_share": float(
            cands.filter(pl.col("chosen") == 1)["moved"].cast(pl.Float64).mean() or 0.0
        ),
    }
    if not defaulted:
        booster = train(cands)
        report["booster"] = booster
        model["trees"] = booster.dump_model()["tree_info"]
    return _rounded(model)


def _rounded(value):
    """Rounds the report numbers; the trees are kept exactly as LightGBM wrote them."""
    if isinstance(value, dict):
        return {k: (v if k == "trees" else _rounded(v)) for k, v in value.items()}
    if isinstance(value, float):
        return round(value, 4)
    return value


# --- Evaluating (the reference for the Java port) ----------------------------------------------

ZERO = 1e-35  # LightGBM's kZeroThreshold: |v| <= this counts as zero for missing_type "Zero"


def score(model: dict, rows) -> np.ndarray:
    """Scores ``rows`` (one sequence of feature values per candidate, in model["features"] order)
    by walking model["trees"] exactly as LightGBM does; the Java evaluator does the same:

        score = sum over trees of the leaf value reached from tree["tree_structure"]
        at a node with "leaf_value": stop, that's the leaf
        otherwise (decision_type "<="; no other kind is used) with v = row[node["split_feature"]]:
            if v is NaN and node["missing_type"] != "NaN": v = 0
            if (missing_type == "Zero" and |v| <= 1e-35) or (missing_type == "NaN" and v is NaN):
                go left if node["default_left"] else right
            else go left if v <= node["threshold"] else right ("left_child", "right_child")

    The executor's choice probability is then softmax(score / model["temperature"]) over the
    step's candidates."""
    out = np.zeros(len(rows))
    for i, row in enumerate(rows):
        total = 0.0
        for tree in model["trees"]:
            node = tree["tree_structure"]
            while "leaf_value" not in node:
                if node["decision_type"] != "<=":
                    raise ValueError(f"unsupported split {node['decision_type']!r}")
                node = node["left_child"] if _goes_left(node, row) else node["right_child"]
            total += node["leaf_value"]
        out[i] = total
    return out


def _goes_left(node: dict, row) -> bool:
    v = float(row[node["split_feature"]])
    missing = node["missing_type"]
    if math.isnan(v) and missing != "NaN":
        v = 0.0
    if (missing == "Zero" and -ZERO <= v <= ZERO) or (missing == "NaN" and math.isnan(v)):
        return bool(node["default_left"])
    return v <= node["threshold"]


def _stump(index: int, feature: str, threshold: float, left: float, right: float) -> dict:
    """A one-split tree in LightGBM's dump format."""
    return {
        "tree_index": index,
        "num_leaves": 2,
        "num_cat": 0,
        "shrinkage": 1,
        "tree_structure": {
            "split_index": 0,
            "split_feature": FEATURES.index(feature),
            "split_gain": 0.0,
            "threshold": threshold,
            "decision_type": "<=",
            "default_left": True,
            "missing_type": "None",
            "internal_value": 0.0,
            "internal_weight": 0.0,
            "internal_count": 0,
            "left_child": _leaf(0, left),
            "right_child": _leaf(1, right),
        },
    }


def _leaf(index: int, value: float) -> dict:
    return {"leaf_index": index, "leaf_value": value, "leaf_weight": 0.0, "leaf_count": 0}


DEFAULT_TREES = [
    _stump(0, "angle", 30.0, 1.0, 0.0),  # near the crosshair
    _stump(1, "angle", 60.0, 0.5, 0.0),
    _stump(2, "adjacent_prev", 0.5, 0.0, 1.0),  # next to the previous block
    _stump(3, "dist", 3.5, 0.5, 0.0),
    _stump(4, "below_feet", 0.5, 0.0, -4.0),  # never the block underfoot
    _stump(5, "falls", 0.5, 0.0, -1.0),
]


# --- Output -----------------------------------------------------------------------------------


def write(model: dict, out_dir: str | Path) -> Path:
    """Writes ``model`` to out_dir/mining_order.json (indent 1, trailing newline)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(model, indent=1) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


def export(store: Store, out_dir: str | Path) -> Path:
    """Fits the mining-order model on the store's human mining jobs and writes it; returns the
    file."""
    return write(fit(steps(store)), out_dir)


def importance(model: dict) -> dict[str, float]:
    """Each feature's share of the total split gain in the model's trees, largest first."""
    gain = dict.fromkeys(model["features"], 0.0)

    def walk(node: dict) -> None:
        if "leaf_value" in node:
            return
        gain[model["features"][node["split_feature"]]] += node.get("split_gain", 0.0)
        walk(node["left_child"])
        walk(node["right_child"])

    for tree in model["trees"]:
        walk(tree["tree_structure"])
    total = sum(gain.values()) or 1.0
    return dict(sorted(((k, v / total) for k, v in gain.items()), key=lambda kv: -kv[1]))


def summary(model: dict) -> str:
    """The fitted model in a few report lines."""
    m, f = model["metrics"], model["fitted_on"]
    top = [f"{k} {v:.0%}" for k, v in list(importance(model).items())[:6] if v > 0]
    h, b = model["patterns"]["human"], model["patterns"]["model"]
    lines = [
        f"fitted on    {f['jobs']} jobs, {f['steps']} steps, {f['candidates']} candidates "
        f"({f['candidates'] / max(f['steps'], 1):.0f} per step) from {f['sessions']} sessions",
        f"held out     top-1 {m['top1']:.2f}, top-3 {m['top3']:.2f} (leave one job out); random "
        f"top-1 {m['random_top1']:.2f}, nearest the crosshair {m['nearest_top1']:.2f}",
        f"sampling     softmax(score / {model['temperature']:.3f}), {len(model['trees'])} trees",
        "leans on     " + (", ".join(top) or "nothing (the generic prior)"),
        "patterns     player / model",
    ]
    lines += [f"  {k:<18} {h[k]:6.2f} / {b[k]:.2f}" for k in PATTERNS]
    return "\n".join(lines)
