"""Runs the discriminators from scratch and writes the per-set report (spec, Component D).

    hb-data/discrim/<run>/report.json       per set and model: clips, sessions, accuracy and AUC
                                            with 95% intervals; for the feature model the five
                                            features that best give the bot away; per-feature
                                            distances (Wasserstein, KS)
    hb-data/discrim/<run>/clips.parquet     the eval set with every model's out-of-fold P(bot)
    hb-data/discrim/<run>/features.parquet  the clip statistics

The gates, by which bot sessions are compared (eval_set.pick_bots):
    plain       Phase 3: plain Baritone is detected at GATE_ACCURACY or better (feature model,
                pooled over every scored clip): proof the discriminator works.
    humanized   Phase 4: the executor's play is detected at HUMANIZED_ACCURACY or worse in every
                scored set, by every model that scored it.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np
import polars as pl

from hbpipe.discrim import feature_model
from hbpipe.discrim.eval_set import build, judged_sessions, pick_bots
from hbpipe.discrim.evaluate import cross_validate, distances
from hbpipe.discrim.features import DEFAULT_GROUPS, clip_features, names
from hbpipe.store import Store

GATE_ACCURACY = 0.95
HUMANIZED_ACCURACY = 0.75
TOP_FEATURES = 5
MIN_CLIPS = 10  # per class; with fewer, session-split scores are noise (a set can land under 50%)
MODELS = ("feature", "sequence", "decision")


def run(
    store: Store,
    models: Sequence[str] = MODELS,
    seed: int = 0,
    log: Callable[[str], None] = lambda _: None,
    bot: str = "auto",
    bot_since_ms: int | None = None,
    bot_until_ms: int | None = None,
) -> dict:
    """Builds the eval set (human against the ``bot`` sessions, see eval_set.pick_bots),
    evaluates every model per set, writes the run directory and returns the report."""
    began = time.time()
    bot = pick_bots(store, bot)
    ev = build(store, seed, bot, bot_since_ms, bot_until_ms)
    log(
        f"bot sessions: {bot}"
        + (f", started from {bot_since_ms} (unix ms)" if bot_since_ms else "")
        + (f", started before {bot_until_ms} (unix ms)" if bot_until_ms else "")
    )
    log(f"eval set: {ev.clips.height} clips over {ev.clips['label'].n_unique()} sets")
    feats = clip_features(store, ev)
    cols = names(DEFAULT_GROUPS)
    clips = ev.clips.join(feats, on="clip_id", how="left")
    report: dict = {
        "created": int(began),
        "models": list(models),
        "feature_groups": list(DEFAULT_GROUPS),
        "bot": bot,
        "bot_since_ms": bot_since_ms,
        "bot_until_ms": bot_until_ms,
        "sets": {},
    }
    oof_cols: dict[str, np.ndarray] = {m: np.full(clips.height, np.nan) for m in models}
    index = {c: i for i, c in enumerate(clips["clip_id"].to_list())}

    for label in sorted(clips["label"].unique().to_list()):
        g = clips.filter(pl.col("label") == label)
        y = (g["source"] == "bot").cast(pl.Int8).to_numpy()
        groups = g["session_id"].to_numpy()
        rows = np.array([index[c] for c in g["clip_id"]])
        entry: dict = {
            "level": g["level"][0],
            "clips": {"human": int((y == 0).sum()), "bot": int((y == 1).sum())},
        }
        x = g.select(cols).to_numpy().astype(float)
        entry["distances"] = distances(x, y, cols)[:15]
        report["sets"][label] = entry
        if min(entry["clips"].values()) < MIN_CLIPS:
            for model in set(models) & {"feature", "sequence"}:
                entry[model] = {"status": f"needs {MIN_CLIPS}+ clips of each class"}
            log(f"  {label}: {_short(entry)}")
            continue
        if "feature" in models:
            scores = cross_validate(x, y, groups, feature_model.fit_predict, seed)
            entry["feature"] = _model_entry(scores, cols, x, y)
            if scores is not None:
                oof_cols["feature"][rows] = scores.oof
        if "sequence" in models:
            from hbpipe.discrim import sequence_model

            scores = sequence_model.evaluate(store, ev, g, seed)
            entry["sequence"] = _model_entry(scores)
            if scores is not None:
                oof_cols["sequence"][rows] = scores.oof
        log(f"  {label}: {_short(entry)}")

    if "decision" in models:
        from hbpipe.discrim import decision_model

        jx, jy, jgroups = decision_model.jobs(
            store, judged_sessions(store, bot, bot_since_ms, bot_until_ms)
        )
        have = {"human": int((jy == 0).sum()), "bot": int((jy == 1).sum())}
        if min(have.values()) < MIN_CLIPS:
            report["decision"] = {
                "status": f"needs {MIN_CLIPS}+ mining jobs of each class "
                f"(have {have['human']} human, {have['bot']} bot)"
            }
        else:
            scores = cross_validate(jx, jy, jgroups, decision_model.fit_predict, seed)
            report["decision"] = _model_entry(scores)

    report["overall"] = _overall(clips, oof_cols)
    report["gate"] = _gate(report["overall"], report["sets"], bot)
    report["seconds"] = round(time.time() - began, 1)
    out = store.root / "discrim" / time.strftime("%Y%m%d-%H%M%S", time.localtime(began))
    out.mkdir(parents=True, exist_ok=True)
    clips.select(ev.clips.columns).with_columns(
        [pl.Series(f"p_bot_{m}", oof_cols[m]) for m in models]
    ).write_parquet(out / "clips.parquet")
    feats.write_parquet(out / "features.parquet")
    (out / "report.json").write_text(json.dumps(_clean(report), indent=1) + "\n", encoding="utf-8")
    report["dir"] = str(out)
    return report


def _model_entry(scores, cols: list[str] | None = None, x=None, y=None) -> dict:
    if scores is None:
        return {"status": "needs 2+ sessions of each class"}
    entry = {
        "accuracy": scores.accuracy,
        "accuracy_ci": list(scores.accuracy_ci),
        "auc": scores.auc,
        "auc_ci": list(scores.auc_ci),
        "sessions": scores.sessions,
        "clips": scores.clips,
    }
    if cols is not None and scores.importance is not None:
        top = np.argsort(-scores.importance)[:TOP_FEATURES]
        entry["top_features"] = [
            {
                "feature": cols[j],
                "importance": float(scores.importance[j]),
                "human_median": _median(x[y == 0, j]),
                "bot_median": _median(x[y == 1, j]),
            }
            for j in top
        ]
    return entry


def _median(v: np.ndarray) -> float:
    v = v[~np.isnan(v)]
    return float(np.median(v)) if len(v) else float("nan")


def _overall(clips: pl.DataFrame, oof: dict[str, np.ndarray]) -> dict:
    """Pooled out-of-fold accuracy per model over every evaluated clip."""
    y = (clips["source"] == "bot").to_numpy()
    out = {}
    for model, p in oof.items():
        done = ~np.isnan(p)
        if done.any():
            out[model] = {
                "accuracy": float(((p[done] >= 0.5) == y[done]).mean()),
                "clips": int(done.sum()),
            }
    return out


def _gate(overall: dict, sets: dict, bot: str) -> dict:
    if bot != "humanized":
        acc = overall.get("feature", {}).get("accuracy")
        return {
            "phase": 3,
            "goal": f"plain Baritone detected at {GATE_ACCURACY:.0%}+ (feature model, pooled)",
            "accuracy": acc,
            "pass": acc is not None and acc >= GATE_ACCURACY,
        }
    # Phase 4: the best any model does on any scored set is what has to be low enough.
    worst, worst_set = None, None
    for label, e in sets.items():
        for model in ("feature", "sequence"):
            acc = e.get(model, {}).get("accuracy")
            if acc is not None and (worst is None or acc > worst):
                worst, worst_set = acc, f"{label} ({model})"
    return {
        "phase": 4,
        "goal": f"the executor detected at {HUMANIZED_ACCURACY:.0%} or less in every set",
        "accuracy": worst,
        "worst_set": worst_set,
        "pass": worst is not None and worst <= HUMANIZED_ACCURACY,
    }


def _short(entry: dict) -> str:
    f = entry.get("feature", {})
    if "accuracy" not in f:
        return f"{entry['clips']} {f.get('status', '')}"
    n = entry["clips"]
    return f"{n['human']}+{n['bot']} clips, accuracy {f['accuracy']:.0%}, AUC {f['auc']:.2f}"


def _clean(o):
    """JSON without NaN (null instead)."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_clean(v) for v in o]
    if isinstance(o, float) and math.isnan(o):
        return None
    return o


def latest(root: str | Path) -> dict | None:
    """The newest report under hb-data/discrim/, or None."""
    runs = sorted((Path(root) / "discrim").glob("*/report.json"))
    if not runs:
        return None
    report = json.loads(runs[-1].read_text(encoding="utf-8"))
    report["dir"] = str(runs[-1].parent)
    return report


def format_report(report: dict) -> str:
    def pct(v) -> str:
        return "-" if v is None else f"{v:.0%}"

    def ci(v) -> str:
        return "" if not v or v[0] is None else f"[{v[0]:.0%}, {v[1]:.0%}]"

    gate = report["gate"]
    lines = [
        f"Phase {gate.get('phase', 3)} gate: {gate['goal']}: {pct(gate['accuracy'])}"
        + (f" at worst, {gate['worst_set']}" if gate.get("worst_set") else "")
        + f" ({'pass' if gate['pass'] else 'not yet'})",
        "",
        f"  {'set':22s} {'human':>6s} {'bot':>5s}  {'feature acc':>11s} {'95% CI':>12s}"
        f"  {'AUC':>5s}"
        + ("  seq acc" if "sequence" in report["models"] else "")
        + "  gives the bot away",
    ]
    for label, e in report["sets"].items():
        f = e.get("feature", {})
        counts = f"  {label:22s} {e['clips']['human']:6d} {e['clips']['bot']:5d}"
        if "accuracy" not in f:
            lines.append(f"{counts}  {f.get('status', '')}")
            continue
        seq = e.get("sequence", {})
        seq_s = f"  {pct(seq.get('accuracy')):>7s}" if "sequence" in report["models"] else ""
        top = ", ".join(t["feature"] for t in f.get("top_features", [])[:3])
        lines.append(
            f"{counts}  {pct(f['accuracy']):>11s} {ci(f['accuracy_ci']):>12s}"
            f"  {f['auc']:5.2f}{seq_s}  {top}"
        )
    if "decision" in report:
        d = report["decision"]
        lines += [
            "",
            "Decision model (mining jobs): "
            + (
                f"accuracy {pct(d['accuracy'])} {ci(d['accuracy_ci'])}, AUC {d['auc']:.2f}"
                if "accuracy" in d
                else d.get("status", "")
            ),
        ]
    lines += ["", f"Written to {report['dir']}"]
    return "\n".join(lines)
