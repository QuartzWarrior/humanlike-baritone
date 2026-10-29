"""Labeling pass 5: discovery.

Stretches no detector claims (other.unlabeled, at least MIN_TICKS long; longer ones are cut into
WINDOW-tick pieces) are embedded from motor statistics (mouse, keys, motion, and event rates) and
clustered with HDBSCAN. The review tool shows the clusters; naming one makes it a new set. A named
set's detector is the cluster itself: on later runs, any unlabeled stretch whose embedding falls
within the cluster's radius of its centroid gets the new label instead of other.unlabeled. A
hand-written detector can replace it later.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import polars as pl

from hbpipe.label.base import Seg, Span

if TYPE_CHECKING:
    from hbpipe.store import Store

MIN_TICKS = 20
WINDOW = 200
RADIUS_QUANTILE = 0.9  # a named set claims stretches as close as 90% of its examples
MIN_RADIUS = 0.5  # standardized units; keeps a tight cluster from claiming only exact copies

FEATURES = (
    "duration_s",
    "speed_mean",
    "speed_p90",
    "speed_std",
    "vspeed_std",
    "straightness",
    "ground_share",
    "crouch_share",
    "sprint_share",
    "water_share",
    "screen_share",
    "yaw_rate_abs_mean",
    "yaw_rate_p90",
    "pitch_rate_abs_mean",
    "mouse_rate",
    "mouse_path_rate",
    "key_press_rate",
    "move_key_share",
    "jump_share",
    "attack_share",
    "use_share",
    "click_rate",
    "scroll_rate",
    "slot_click_rate",
    "break_rate",
    "place_rate",
    "interact_rate",
    "pickup_rate",
)


def features(seg: Seg, i0: int, i1: int) -> dict[str, float]:
    """Motor statistics of rows i0..i1 (inclusive) of a segment."""
    sl = slice(i0, i1 + 1)
    n = i1 - i0 + 1
    secs = n / 20.0
    t0, t1 = int(seg.tick[i0]), int(seg.tick[i1])

    def share(col: str) -> float:
        return float(seg.col(col)[sl].mean())

    def count(df: pl.DataFrame, *cond: pl.Expr) -> int:
        return df.filter(pl.col("tick").is_between(t0, t1), *cond).height

    speed = seg.col("speed")[sl]
    yaw_rate = np.abs(seg.col("yaw_rate")[sl])
    x, z = seg.col("x")[sl], seg.col("z")[sl]
    path = float(np.hypot(np.diff(x), np.diff(z)).sum())
    net = float(np.hypot(x[-1] - x[0], z[-1] - z[0]))
    mouse = seg.mouse.filter(pl.col("tick").is_between(t0, t1))
    moving = seg.col("k_forward") | seg.col("k_back") | seg.col("k_left") | seg.col("k_right")
    ev = seg.events
    return {
        "duration_s": secs,
        "speed_mean": float(speed.mean()),
        "speed_p90": float(np.quantile(speed, 0.9)),
        "speed_std": float(speed.std()),
        "vspeed_std": float(seg.col("vspeed")[sl].std()),
        "straightness": net / path if path > 0.5 else 0.0,
        "ground_share": share("on_ground"),
        "crouch_share": share("crouching"),
        "sprint_share": share("sprinting"),
        "water_share": share("in_water"),
        "screen_share": share("screen_open"),
        "yaw_rate_abs_mean": float(yaw_rate.mean()),
        "yaw_rate_p90": float(np.quantile(yaw_rate, 0.9)),
        "pitch_rate_abs_mean": float(np.abs(seg.col("pitch_rate")[sl]).mean()),
        "mouse_rate": mouse.height / secs,
        "mouse_path_rate": float((mouse["d_yaw"].abs() + mouse["d_pitch"].abs()).sum()) / secs,
        "key_press_rate": count(seg.keys, pl.col("down")) / secs,
        "move_key_share": float(moving[sl].mean()),
        "jump_share": share("k_jump"),
        "attack_share": share("k_attack"),
        "use_share": share("k_use"),
        "click_rate": count(seg.buttons, pl.col("kind") == "button", pl.col("down")) / secs,
        "scroll_rate": count(seg.buttons, pl.col("kind") == "scroll") / secs,
        "slot_click_rate": count(seg.gui, pl.col("kind") == "slot") / secs,
        "break_rate": count(ev, pl.col("kind") == "block_broken") / secs,
        "place_rate": count(ev, pl.col("kind") == "block_placed") / secs,
        "interact_rate": count(ev, pl.col("kind") == "interaction") / secs,
        "pickup_rate": count(ev, pl.col("kind") == "item_picked_up") / secs,
    }


def windows(span: Span) -> list[tuple[int, int]]:
    """Tick ranges to embed for one unlabeled span: none if shorter than MIN_TICKS, else pieces
    of at most WINDOW ticks (a short tail joins the piece before it)."""
    if span.ticks < MIN_TICKS:
        return []
    out = []
    t = span.start_tick
    while t <= span.end_tick:
        end = min(t + WINDOW - 1, span.end_tick)
        if span.end_tick - end < MIN_TICKS:
            end = span.end_tick
        out.append((t, end))
        t = end + 1
    return out


def embed(seg: Seg, spans: list[Span]) -> list[dict]:
    rows = []
    for span in spans:
        for t0, t1 in windows(span):
            rows.append(
                {
                    "session_id": seg.session_id,
                    "segment_id": seg.segment_id,
                    "start_tick": t0,
                    "end_tick": t1,
                    **features(seg, seg.index_of(t0), seg.index_of(t1)),
                }
            )
    return rows


@dataclass
class Clustering:
    windows: pl.DataFrame  # embedded windows plus a cluster column (-1 = noise)
    summary: pl.DataFrame  # per cluster: size, seconds, the features that set it apart
    mean: np.ndarray
    scale: np.ndarray


def cluster(windows: pl.DataFrame, *, min_cluster_size: int | None = None) -> Clustering:
    from sklearn.cluster import HDBSCAN

    x = windows.select(FEATURES).to_numpy().astype(float)
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1.0
    z = (x - mean) / scale
    if len(z) < 2:
        labels = np.full(len(z), -1)
    else:
        size = min_cluster_size or max(5, len(z) // 50)
        labels = HDBSCAN(min_cluster_size=min(size, len(z)), copy=True).fit_predict(z)
    out = windows.with_columns(pl.Series("cluster", labels, dtype=pl.Int32))
    rows = []
    for c in sorted(set(labels) - {-1}):
        member = labels == c
        centroid = z[member].mean(axis=0)
        top = np.argsort(-np.abs(centroid))[:5]
        rows.append(
            {
                "cluster": int(c),
                "windows": int(member.sum()),
                "seconds": float(x[member, 0].sum()),
                "distinct": [f"{FEATURES[i]} {centroid[i]:+.1f} sd" for i in top],
            }
        )
    summary = pl.DataFrame(
        rows,
        schema={
            "cluster": pl.Int32,
            "windows": pl.Int64,
            "seconds": pl.Float64,
            "distinct": pl.List(pl.String),
        },
    )
    return Clustering(out, summary, mean, scale)


def discover(store: Store, *, min_cluster_size: int | None = None) -> Clustering | None:
    """Embeds every labeled session's unlabeled stretches and clusters them. Saves the result
    under hb-data/discovery/ for the review tool."""
    from hbpipe.label.run import UNLABELED, prepare

    rows: list[dict] = []
    for row in store.sessions(source="human"):
        sid = row["session_id"]
        if row["labeler_version"] is None:
            continue
        labels = store.read("labels", sid).filter(pl.col("label") == UNLABELED)
        if labels.is_empty():
            continue
        segs = {s.segment_id: s for s in prepare(store, sid).segments}
        for (segment_id,), g in labels.partition_by("segment_id", as_dict=True).items():
            spans = [
                Span(a, b, UNLABELED, 1.0)
                for a, b in g.select("start_tick", "end_tick").iter_rows()
            ]
            rows += embed(segs[segment_id], spans)
    if not rows:
        return None
    result = cluster(pl.from_dicts(rows), min_cluster_size=min_cluster_size)
    out = store.root / "discovery"
    out.mkdir(parents=True, exist_ok=True)
    result.windows.write_parquet(out / "windows.parquet")
    result.summary.write_parquet(out / "summary.parquet")
    return result


def load(store: Store) -> Clustering | None:
    """The last discovery run, if any."""
    d = store.root / "discovery"
    if not (d / "windows.parquet").exists():
        return None
    windows = pl.read_parquet(d / "windows.parquet")
    x = windows.select(FEATURES).to_numpy().astype(float)
    scale = x.std(axis=0)
    scale[scale == 0] = 1.0
    return Clustering(windows, pl.read_parquet(d / "summary.parquet"), x.mean(axis=0), scale)


# --- Named sets -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class NamedSet:
    name: str
    mean: np.ndarray
    scale: np.ndarray
    centroid: np.ndarray
    radius: float

    def distance(self, f: dict[str, float]) -> float:
        z = (np.array([f[k] for k in FEATURES]) - self.mean) / self.scale
        return float(np.linalg.norm(z - self.centroid))


def name_cluster(db, clustering: Clustering, cluster_id: int, name: str) -> NamedSet:
    """Saves cluster ``cluster_id`` as the set ``name`` (e.g. "move.boat_drift")."""
    if "." not in name or name.startswith("other."):
        raise ValueError("a set name looks like family.name and isn't in other.")
    member = clustering.windows["cluster"].to_numpy() == cluster_id
    x = clustering.windows.select(FEATURES).to_numpy().astype(float)[member]
    z = (x - clustering.mean) / clustering.scale
    centroid = z.mean(axis=0)
    radius = max(
        MIN_RADIUS, float(np.quantile(np.linalg.norm(z - centroid, axis=1), RADIUS_QUANTILE))
    )
    db.execute(
        "INSERT INTO discovered_sets (name, created_at, features, mean, scale, centroid, radius, "
        "examples) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (name) DO UPDATE SET "
        "created_at = excluded.created_at, features = excluded.features, mean = excluded.mean, "
        "scale = excluded.scale, centroid = excluded.centroid, radius = excluded.radius, "
        "examples = excluded.examples",
        (
            name,
            int(time.time()),
            json.dumps(list(FEATURES)),
            json.dumps(clustering.mean.tolist()),
            json.dumps(clustering.scale.tolist()),
            json.dumps(centroid.tolist()),
            radius,
            int(member.sum()),
        ),
    )
    db.commit()
    return NamedSet(name, clustering.mean, clustering.scale, centroid, radius)


def named_sets(db) -> list[NamedSet]:
    out = []
    for r in db.execute("SELECT * FROM discovered_sets ORDER BY name"):
        if json.loads(r["features"]) != list(FEATURES):
            continue  # embedded with an older feature list; name the cluster again
        out.append(
            NamedSet(
                r["name"],
                np.array(json.loads(r["mean"])),
                np.array(json.loads(r["scale"])),
                np.array(json.loads(r["centroid"])),
                r["radius"],
            )
        )
    return out


def claim(seg: Seg, unlabeled: list[Span], sets: list[NamedSet]) -> tuple[list[Span], list[Span]]:
    """Splits unlabeled spans into (claimed by a named set, still unlabeled)."""
    if not sets:
        return [], unlabeled
    claimed, rest = [], []
    for span in unlabeled:
        pieces = windows(span)
        if not pieces:
            rest.append(span)
            continue
        for t0, t1 in pieces:
            f = features(seg, seg.index_of(t0), seg.index_of(t1))
            dist, best = min((s.distance(f), s) for s in sets)
            if dist <= best.radius:
                claimed.append(
                    Span(t0, t1, best.name, round(max(0.5, 1 - dist / best.radius / 2), 3))
                )
            else:
                rest.append(Span(t0, t1, span.label, span.confidence))
    return claimed, rest
