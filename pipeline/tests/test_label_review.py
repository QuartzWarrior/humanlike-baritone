"""Corrections, the gate report and the review tool's sampling, on a small store."""

import polars as pl

from hbpipe.label.corrections import effective_labels
from hbpipe.label.report import (
    CHECK_SAMPLE,
    format_report,
    set_report,
    unlabeled_share,
    wilson_lower,
)
from hbpipe.label.run import LABELS_SCHEMA
from hbpipe.store import Store


def labels(*rows, ids: str = "id") -> pl.DataFrame:
    out = []
    for i, (label, start, end) in enumerate(rows):
        out.append(
            {
                "label_id": f"{ids}{i}",
                "session_id": "s",
                "segment_id": "seg-0001",
                "start_tick": start,
                "end_tick": end,
                "start_t_ns": start * 50_000_000,
                "end_t_ns": end * 50_000_000,
                "label": label,
                "level": "activity" if label.startswith("act.") else "primitive",
                "confidence": 0.8,
                "detector_version": 1,
            }
        )
    return pl.from_dicts(out, schema=LABELS_SCHEMA)


def store_with(tmp_path, df: pl.DataFrame) -> Store:
    store = Store(tmp_path)
    store.upsert_session(
        session_id="s", source="human", started_unix_ms=0, play_day="2026-10-26", complete=1
    )
    store.replace_labels("s", df, 1)
    return store


def judge(store, label_id, verdict, new_label=None):
    row = store.labels(label_id=label_id).row(0, named=True)
    store.add_correction(
        label_id=label_id,
        session_id=row["session_id"],
        segment_id=row["segment_id"],
        start_tick=row["start_tick"],
        end_tick=row["end_tick"],
        label=row["label"],
        verdict=verdict,
        new_label=new_label,
    )


def test_corrections_win_over_detector_output(tmp_path):
    store = store_with(
        tmp_path,
        labels(("move.walk", 0, 9), ("move.sneak", 10, 19), ("mine.strip", 20, 99)),
    )
    judge(store, "id0", "correct")
    judge(store, "id1", "wrong", new_label="move.walk")
    judge(store, "id2", "wrong")
    store.add_correction(
        session_id="s",
        segment_id="seg-0001",
        start_tick=20,
        end_tick=99,
        label="mine.tunnel",
        verdict="added",
    )
    eff = effective_labels(store.labels(), store.corrections())
    got = sorted(eff.select("label", "start_tick", "source").iter_rows())
    assert got == [
        ("mine.tunnel", 20, "correction"),
        ("move.walk", 0, "checked"),
        ("move.walk", 10, "correction"),
    ]


def test_the_newest_verdict_on_a_span_wins(tmp_path):
    store = store_with(tmp_path, labels(("move.walk", 0, 9)))
    judge(store, "id0", "wrong")
    judge(store, "id0", "correct")
    eff = effective_labels(store.labels(), store.corrections())
    assert eff["source"].to_list() == ["checked"]


def test_report_counts_live_verdicts_and_flags_sets_without_enough_data(tmp_path):
    rows = [("move.walk", i * 10, i * 10 + 5) for i in range(CHECK_SAMPLE)]
    rows += [("move.swim", 5000, 5010), ("other.unlabeled", 5011, 5020)]
    store = store_with(tmp_path, labels(*rows))
    for i in range(CHECK_SAMPLE):
        judge(store, f"id{i}", "correct" if i % 10 else "wrong")  # 90% right
    report = {r["label"]: r for r in set_report(store).iter_rows(named=True)}
    walk = report["move.walk"]
    assert walk["checked"] == CHECK_SAMPLE and walk["precision"] == 0.9
    assert walk["enough_data"] and walk["passes"]
    assert report["move.swim"]["passes"] is None  # not enough data to gate
    assert "other.unlabeled" not in report
    # Printed by hbpipe report, so it has to survive a cp1252 Windows console.
    assert "90% (>=" in format_report(store) and format_report(store).isascii()

    # A relabel that changes the spans (new label ids) makes the old verdicts stale.
    store.replace_labels("s", labels(*rows, ids="new"), 1)
    walk = {r["label"]: r for r in set_report(store).iter_rows(named=True)}["move.walk"]
    assert walk["stale"] == CHECK_SAMPLE and walk["checked"] == 0


def test_unlabeled_share_is_over_active_ticks(tmp_path):
    store = store_with(tmp_path, labels(("move.walk", 0, 29), ("other.unlabeled", 30, 39)))
    assert unlabeled_share(store) == 0.25


def test_wilson_lower_bound():
    assert wilson_lower(0, 0) == 0.0
    assert 0.85 < wilson_lower(180, 200) < 0.9
    assert wilson_lower(200, 200) < 1.0


def test_every_detector_has_a_definition_for_reviewers():
    from hbpipe.label import detectors  # noqa: F401
    from hbpipe.label.base import REGISTRY
    from hbpipe.label.definitions import DEFINITIONS

    assert set(REGISTRY) == set(DEFINITIONS)
    assert all(covers and signal for covers, signal in DEFINITIONS.values())


def test_sets_read_straight_from_an_event_need_only_a_spot_check(tmp_path):
    from hbpipe.label.report import SPOT_CHECK, check_target

    rows = [("move.sprint", i * 10, i * 10 + 5) for i in range(CHECK_SAMPLE)]
    rows += [("look.acquire", 5000 + i * 10, 5000 + i * 10 + 5) for i in range(CHECK_SAMPLE)]
    store = store_with(tmp_path, labels(*rows))
    for i in range(SPOT_CHECK):
        judge(store, f"id{i}", "correct")  # sprint spans
        judge(store, f"id{CHECK_SAMPLE + i}", "correct")  # look.acquire spans
    report = {r["label"]: r for r in set_report(store).iter_rows(named=True)}
    assert check_target("move.sprint") == SPOT_CHECK and report["move.sprint"]["passes"]
    assert check_target("look.acquire") == CHECK_SAMPLE and not report["look.acquire"]["passes"]
    assert f"check {CHECK_SAMPLE - SPOT_CHECK} more" in format_report(store)
