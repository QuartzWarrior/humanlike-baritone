"""hbpipe command line.

uv run hbpipe inspect <hb-data | sessions dir | session dir> ...
uv run hbpipe verify  <hb-data | sessions dir | session dir> ...
uv run hbpipe convert  <hb-data> [--force]    sessions -> tables/<table>/<session>.parquet
uv run hbpipe label    <hb-data> [--force]    convert, then label new sessions (passes 1-4)
uv run hbpipe discover <hb-data>              cluster the unlabeled stretches (pass 5)
uv run hbpipe report   <hb-data>              Phase 2 gate numbers
uv run hbpipe review   <hb-data>              open the review tool in a browser
uv run hbpipe train-activities <hb-data>      fit the activity classifier on checked labels
uv run hbpipe exclude  <hb-data> <session> --reason TEXT [--from-tick N] [--to-tick N]
                                              keep play out of training (a hack was on)
uv run hbpipe exclude  <hb-data> [--list]      show what's excluded
uv run hbpipe exclude  <hb-data> <session> --checked NOTE
                                              its hack menus or freecam were looked into: keep it
uv run hbpipe exclude  <hb-data> <session> --clear
uv run hbpipe discrim  <hb-data> [--models feature,sequence,decision]
                                              Phase 3: train fresh discriminators, per-set report
uv run hbpipe fit-models <hb-data> [--out DIR]
                                              Phase 4: fit the Stage 1 motor models on human
                                              play (default DIR: hb-models/stage1 next to hb-data)
"""

from __future__ import annotations

import argparse
import sys

from hbpipe.convert.session import find_sessions
from hbpipe.inspect import format_summary, summarize
from hbpipe.verify import verify


def main(argv: list[str] | None = None) -> int:
    # A Windows console encodes output as cp1252, which can't show every character a block or
    # set name might carry; print a ? rather than crash.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="hbpipe")
    sub = parser.add_subparsers(dest="command", required=True)
    p_inspect = sub.add_parser("inspect", help="summarize recorded sessions")
    p_inspect.add_argument("paths", nargs="+")
    p_verify = sub.add_parser("verify", help="Java-to-Python round trip and privacy checks")
    p_verify.add_argument("paths", nargs="+")
    p_convert = sub.add_parser("convert", help="convert sessions to Parquet tables")
    p_convert.add_argument("root", help="an hb-data directory")
    p_convert.add_argument("--force", action="store_true", help="also redo converted sessions")
    p_label = sub.add_parser("label", help="convert and label sessions")
    p_label.add_argument("root", help="an hb-data directory")
    p_label.add_argument("--force", action="store_true", help="also relabel labeled sessions")
    for name, text in (
        ("discover", "cluster unlabeled stretches"),
        ("report", "Phase 2 gate numbers"),
        ("review", "open the review tool"),
        ("train-activities", "fit the activity classifier on checked labels"),
    ):
        sub.add_parser(name, help=text).add_argument("root", help="an hb-data directory")
    p_exclude = sub.add_parser("exclude", help="keep play out of training (a hack was on)")
    p_exclude.add_argument("root", help="an hb-data directory")
    p_exclude.add_argument("session", nargs="?", help="a session id, or a unique start of one")
    p_exclude.add_argument("--reason", help="why, e.g. 'wurst search on'")
    p_exclude.add_argument("--from-tick", type=int, help="first tick (default: session start)")
    p_exclude.add_argument("--to-tick", type=int, help="last tick (default: session end)")
    p_exclude.add_argument(
        "--checked", metavar="NOTE", help="its hack menus or freecam were looked into: keep it"
    )
    p_exclude.add_argument("--clear", action="store_true", help="drop the session's exclusions")
    p_exclude.add_argument("--list", action="store_true", help="show every exclusion")
    p_discrim = sub.add_parser("discrim", help="train fresh discriminators: human vs bot per set")
    p_discrim.add_argument("root", help="an hb-data directory")
    p_discrim.add_argument("--models", default="feature,sequence,decision", help="comma-separated")
    p_discrim.add_argument(
        "--bot",
        default="auto",
        choices=("auto", "plain", "humanized", "all"),
        help="which bot sessions to compare with (auto: humanized ones when there are any)",
    )
    p_discrim.add_argument(
        "--bot-since",
        help="only bot sessions started at or after this local time (e.g. '2026-10-27 18:10')",
    )
    p_discrim.add_argument(
        "--bot-until",
        help="only bot sessions started before this local time (to judge an earlier build)",
    )
    p_fit = sub.add_parser("fit-models", help="fit the Stage 1 motor models on human play")
    p_fit.add_argument("root", help="an hb-data directory")
    p_fit.add_argument("--out", help="output directory (default: hb-models/stage1 next to hb-data)")
    p_fit.add_argument(
        "--models",
        default="camera,meta,movement,mining_order",
        help="comma-separated: camera, meta, movement, mining_order",
    )
    args = parser.parse_args(argv)

    if args.command == "convert":
        return _convert(args.root, args.force)
    if args.command == "label":
        return _label(args.root, args.force)
    if args.command == "discover":
        return _discover(args.root)
    if args.command == "report":
        from hbpipe.label.report import format_report
        from hbpipe.store import Store

        print(format_report(Store(args.root)))
        return 0
    if args.command == "review":
        return _review(args.root)
    if args.command == "train-activities":
        return _train_activities(args.root)
    if args.command == "exclude":
        return _exclude(args)
    if args.command == "discrim":
        return _discrim(args.root, args.models.split(","), args.bot, args.bot_since, args.bot_until)
    if args.command == "fit-models":
        return _fit_models(args.root, args.out, args.models.split(","))

    sessions = [s for path in args.paths for s in find_sessions(path)]
    if not sessions:
        print("no sessions found", file=sys.stderr)
        return 1
    if args.command == "inspect":
        for session in sessions:
            print(format_summary(summarize(session)))
        return 0

    failed = 0
    for session in sessions:
        report = verify(session)
        status = "ok" if report.ok else "FAIL"
        mirror = f", {report.mirrored} checked against the Java mirror" if report.mirrored else ""
        probe = (
            f", {report.probe_windows} text-field windows probed" if report.probe_windows else ""
        )
        crash = (
            f" (not closed; {report.lost_in_crash} mirrored records never reached a chunk)"
            if report.lost_in_crash
            else ""
        )
        print(
            f"{status} {session.session_id}{crash}: {report.records} records{mirror}{probe}, "
            f"{len(report.mismatches)} mismatches, {len(report.privacy)} privacy violations"
        )
        for line in report.mismatches + report.privacy:
            print(f"    {line}")
        failed += not report.ok
    return 1 if failed else 0


def _convert(root: str, force: bool) -> int:
    from hbpipe.convert.run import convert_all

    results = convert_all(root, force=force)
    for r in results:
        if r.skipped:
            print(f"up to date {r.session_id}")
        else:
            print(
                f"converted  {r.session_id}: {r.ticks} ticks, {r.segments} segments "
                f"in {r.seconds:.1f} s"
            )
    if not results:
        print("no sessions found", file=sys.stderr)
        return 1
    return 0


def _label(root: str, force: bool) -> int:
    from hbpipe.label.run import UNLABELED, label_all

    if _convert(root, False):
        return 1
    for session_id, labels in label_all(root, force=force).items():
        unl = labels.filter(labels["label"] == UNLABELED)
        share = _ticks(unl) / max(1, _active(labels))
        print(f"labeled    {session_id}: {labels.height} spans, {share:.0%} unlabeled")
    return 0


def _ticks(labels) -> int:
    return int((labels["end_tick"] - labels["start_tick"] + 1).sum())


def _active(labels) -> int:
    import polars as pl

    return int(
        labels.group_by("segment_id")
        .agg(n=pl.col("end_tick").max() - pl.col("start_tick").min() + 1)["n"]
        .sum()
    )


def _discover(root: str) -> int:
    from hbpipe.label.discovery import discover
    from hbpipe.store import Store

    result = discover(Store(root))
    if result is None:
        print("nothing unlabeled to cluster (run hbpipe label first)")
        return 0
    noise = int((result.windows["cluster"] == -1).sum())
    print(f"{result.windows.height} windows, {result.summary.height} clusters, {noise} unclustered")
    for r in result.summary.iter_rows(named=True):
        print(
            f"  cluster {r['cluster']:3d}: {r['windows']:4d} windows, {r['seconds']:6.0f} s  "
            + ", ".join(r["distinct"])
        )
    return 0


def _train_activities(root: str) -> int:
    from hbpipe.label.activity_model import train_from_store
    from hbpipe.store import Store

    try:
        model, accuracy, n = train_from_store(Store(root))
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    acc = "n/a (one session)" if accuracy is None else f"{accuracy:.0%}"
    print(f"trained on {n} windows, classes {model.classes}; leave-sessions-out accuracy {acc}")
    print("relabel with: uv run hbpipe label <hb-data> --force")
    return 0


def _exclude(args) -> int:
    from hbpipe.store import Store

    store = Store(args.root)
    if args.session and not args.list:
        matches = [
            s["session_id"] for s in store.sessions() if s["session_id"].startswith(args.session)
        ]
        if len(matches) != 1:
            print(f"{len(matches)} sessions match {args.session!r}", file=sys.stderr)
            return 1
        sid = matches[0]
        if args.clear:
            print(f"cleared {store.clear_exclusions(sid)} exclusions from {sid}")
        elif args.checked:
            store.mark_checked(sid, args.checked)
            print(f"checked {sid}: {args.checked}")
            return 0
        elif not args.reason:
            print("say why with --reason", file=sys.stderr)
            return 1
        else:
            store.add_exclusion(sid, args.reason, args.from_tick, args.to_tick)
            span = f"ticks {args.from_tick or 'start'}-{args.to_tick or 'end'}"
            print(f"excluded {sid} {span}: {args.reason}")
        print("run hbpipe label to rebuild its decision traces")
        return 0
    rows = store.exclusions()
    for r in rows:
        span = f"{r['start_tick'] or 'start'}-{r['end_tick'] or 'end'}"
        print(f"{r['session_id']}  ticks {span:>16s}  {r['reason']}")
    if not rows:
        print("nothing excluded")
    for sid, note in store.hack_checks().items():
        print(f"{sid}  checked: {note}")
    return 0


def _discrim(
    root: str,
    models: list[str],
    bot: str = "auto",
    bot_since: str | None = None,
    bot_until: str | None = None,
) -> int:
    from datetime import datetime

    from hbpipe.discrim.report import MODELS, format_report, run
    from hbpipe.store import Store

    unknown = set(models) - set(MODELS)
    if unknown:
        print(
            f"unknown models: {', '.join(sorted(unknown))} (have {', '.join(MODELS)})",
            file=sys.stderr,
        )
        return 1
    bounds: dict[str, int | None] = {}
    for flag, value in (("--bot-since", bot_since), ("--bot-until", bot_until)):
        try:
            bounds[flag] = int(datetime.fromisoformat(value).timestamp() * 1000) if value else None
        except ValueError:
            print(f"{flag}: not a date and time: {value!r}", file=sys.stderr)
            return 1
    if _label(root, False):
        return 1
    report = run(
        Store(root),
        models,
        log=lambda line: print(line, flush=True),
        bot=bot,
        bot_since_ms=bounds["--bot-since"],
        bot_until_ms=bounds["--bot-until"],
    )
    print(format_report(report))
    return 0


def _fit_models(root: str, out: str | None, models: list[str] | None = None) -> int:
    """Fits every Stage 1 model from the labels as they are; unlike discrim it never converts or
    labels, so fitting leaves hb-data untouched (it says when the labels are out of date)."""
    from collections import Counter
    from pathlib import Path

    from hbpipe.label.run import fingerprint
    from hbpipe.models import meta
    from hbpipe.models.decision import mining_order
    from hbpipe.models.motor import camera, movement
    from hbpipe.store import Store

    store = Store(root)
    humans = store.sessions(source="human")
    if not humans:
        print("no human sessions (run hbpipe label first)", file=sys.stderr)
        return 1
    current = fingerprint(store)
    stale = sum(s["labeler_fingerprint"] != current for s in humans)
    if stale:
        print(f"{stale} human sessions have out-of-date labels (hbpipe label updates them)")
    out_dir = Path(out) if out else Path(root).resolve().parent / "hb-models" / "stage1"

    def report(name: str, module, model: dict, defaulted: list[str], reasons: Counter) -> None:
        path = module.write(model, out_dir)
        if any(reasons.values()):
            left = ", ".join(f"{n} {why}" for why, n in reasons.most_common() if n)
            print(f"  left out: {left}")
        lines = module.summary(model)
        for line in lines.splitlines() if isinstance(lines, str) else lines:
            print(f"  {line}")
        if defaulted:
            print(f"  too little data, defaults used: {', '.join(defaulted)}")
        print(f"wrote {path}")

    models = models or ["camera", "meta", "movement", "mining_order"]
    unknown = set(models) - {"camera", "meta", "movement", "mining_order"}
    if unknown:
        print(f"unknown models: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 1
    if "camera" in models:
        _fit_camera(store, camera, report)
    if "meta" in models:
        reasons = Counter()
        defaulted = []
        model = meta.fit(meta.observations(store, reasons), defaulted)
        print(
            f"meta: {model['fitted_on']['active_minutes']:.0f} active minutes "
            f"from {model['fitted_on']['sessions']} sessions"
        )
        report("meta", meta, model, defaulted, reasons)
    if "movement" in models:
        reasons = Counter()
        defaulted = []
        model = movement.fit(movement.measurements(store, reasons), defaulted)
        print(
            f"movement: {model['fitted_on']['minutes']:.0f} minutes "
            f"from {model['fitted_on']['sessions']} sessions"
        )
        report("movement", movement, model, defaulted, reasons)
    if "mining_order" in models:
        reasons = Counter()
        model = mining_order.fit(mining_order.steps(store, reasons))
        fitted = model["fitted_on"]
        print(
            f"mining order: {fitted['steps']} steps of {fitted['jobs']} jobs "
            f"from {fitted['sessions']} sessions"
        )
        report("mining_order", mining_order, model, [], reasons)
    return 0


def _fit_camera(store, camera, report) -> None:
    from collections import Counter

    reasons: Counter = Counter()
    acqs = camera.acquisitions(store, reasons)
    defaulted: list[str] = []
    model = camera.fit(acqs, defaulted)
    spans = acqs.height + sum(reasons.values())
    print(
        f"camera: {acqs.height} of {spans} look.acquire spans usable, "
        f"from {model['fitted_on']['sessions']} sessions"
    )
    report("camera", camera, model, defaulted, reasons)


def _review(root: str) -> int:
    import subprocess
    from pathlib import Path

    app = Path(__file__).parent / "dashboard" / "review.py"
    print("review tool: open http://localhost:8501 in your browser (Ctrl+C stops it)", flush=True)
    return subprocess.call(review_command(app, Path(root).resolve()))


def review_command(app, root) -> list[str]:
    """Streamlit for the review tool. It listens on this machine only (the data is the user's
    play), sends Streamlit no usage statistics and runs headless, which skips Streamlit's
    first-run prompt for an email address (it blocks a non-interactive start, and nothing should
    leave the machine). Headless also means no browser opens: the URL is printed instead."""
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app),
        "--server.address",
        "localhost",
        "--browser.gatherUsageStats",
        "false",
        "--server.headless",
        "true",
        "--",
        "--root",
        str(root),
    ]


if __name__ == "__main__":
    sys.exit(main())
