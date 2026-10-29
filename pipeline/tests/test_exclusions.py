import polars as pl

from hbpipe.cli import main
from hbpipe.exclusions import END, Exclusion, drop_excluded, for_session, training_summary
from hbpipe.label.run import _drop_excluded_jobs
from hbpipe.store import Store

WURST = "net.wurstclient.navigator.NavigatorMainScreen"


def session(tmp_path, sid="abc123", fingerprint="f") -> Store:
    store = Store(tmp_path)
    store.upsert_session(
        session_id=sid,
        source="human",
        started_unix_ms=0,
        play_day="2026-10-27",
        complete=1,
        labeler_fingerprint=fingerprint,
    )
    return store


def segment(store, sid, segment_id, start, end, end_reason="screen"):
    store.db.execute(
        "INSERT INTO segments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (sid, segment_id, start, end, start * 50_000_000, end * 50_000_000, "paused",
         end_reason, end - start + 1, 0, 0),
    )  # fmt: skip
    store.db.commit()


def test_drop_excluded_by_tick_and_by_span():
    excluded = [Exclusion(10, 20, "search"), Exclusion(100, END, "search")]
    ticks = pl.DataFrame({"tick": [5, 10, 20, 21, 99, 100, 5000]})
    assert drop_excluded(ticks, excluded)["tick"].to_list() == [5, 21, 99]
    spans = pl.DataFrame({"start": [0, 0, 21, 90], "end": [9, 10, 99, 100]})
    kept = drop_excluded(spans, excluded, "start", "end")
    assert kept.rows() == [(0, 9), (21, 99)]  # touching an end counts
    assert drop_excluded(ticks, []).height == ticks.height


def test_exclusions_are_stored_and_relabel_their_session(tmp_path):
    store = session(tmp_path)
    store.add_exclusion("abc123", "wurst search on", start_tick=100)
    assert for_session(store, "abc123") == [Exclusion(100, END, "wurst search on")]
    # The traces are rebuilt on the next labeling run, so the session counts as unlabeled.
    assert store.session("abc123")["labeler_fingerprint"] is None
    store.upsert_session(session_id="abc123", labeler_fingerprint="f", source="human",
                         started_unix_ms=0, play_day="2026-10-27", complete=1)  # fmt: skip
    assert store.clear_exclusions("abc123") == 1
    assert for_session(store, "abc123") == []
    assert store.session("abc123")["labeler_fingerprint"] is None


def test_excluded_mining_jobs_leave_the_traces():
    traces = pl.DataFrame(
        {"trace_id": ["a", "a", "b", "b"], "tick": [10, 50, 200, 260], "step": [0, 1, 0, 1]}
    )
    volumes = pl.DataFrame({"trace_id": ["a", "b"], "x": [0, 1]})
    t, v = _drop_excluded_jobs(traces, volumes, [Exclusion(60, 190, "search")])
    assert t.rows() == traces.rows()  # between the jobs
    # A job is the whole stretch from its first break to its last, not just the break ticks.
    t, v = _drop_excluded_jobs(traces, volumes, [Exclusion(40, 45, "search")])
    assert t["trace_id"].unique().to_list() == ["b"] and v["trace_id"].to_list() == ["b"]


def test_report_counts_excluded_play_and_flags_hack_menus_outside_it(tmp_path):
    store = session(tmp_path)
    segment(store, "abc123", "seg-0001", 0, 1199)
    segment(store, "abc123", "seg-0002", 1300, 2499, end_reason="camera_detached")
    events = pl.DataFrame(
        {
            "tick": [600, 2000],
            "t_ns": [30_000_000_000, 100_000_000_000],
            "kind": ["screen_opened", "screen_opened"],
            "screen": [WURST, WURST],
        },
        schema_overrides={"tick": pl.UInt64, "t_ns": pl.UInt64},
    )
    store.write_tables("abc123", {"events": events})
    lines = training_summary(store)
    assert lines[0].startswith("Training data: 0.0 of 2.0 min")
    assert (
        lines[2]
        == "  abc123  2 hack-client menus (first at 0:30), 1 freecam pauses (first at 2:04)"
    )

    store.add_exclusion("abc123", "wurst search on", start_tick=1300)
    lines = training_summary(store)
    assert lines[0].startswith("Training data: 1.0 of 2.0 min of play excluded (1 stretches")
    assert lines[2] == "  abc123  1 hack-client menus (first at 0:30)"
    assert all(line.isascii() for line in lines)

    # Looked into (the menu was for freecam): no longer flagged.
    store.mark_checked("abc123", "freecam only")
    lines = training_summary(store)
    assert "1 sessions checked" in lines[0] and len(lines) == 1


def test_cli_exclude_adds_lists_and_clears(tmp_path, capsys):
    session(tmp_path)
    root = str(tmp_path)
    assert main(["exclude", root, "abc", "--reason", "wurst search on", "--from-tick", "50"]) == 0
    assert main(["exclude", root]) == 0
    assert "abc123  ticks           50-end  wurst search on" in capsys.readouterr().out
    assert main(["exclude", root, "abc"]) == 1  # needs a reason
    assert main(["exclude", root, "zzz", "--reason", "x"]) == 1  # no such session
    assert main(["exclude", root, "abc", "--checked", "freecam only"]) == 0
    assert main(["exclude", root, "--list"]) == 0
    assert "abc123  checked: freecam only" in capsys.readouterr().out
    assert main(["exclude", root, "abc", "--clear"]) == 0
    assert "cleared 1 exclusions" in capsys.readouterr().out
    assert Store(tmp_path).hack_checks() == {}  # clearing drops the check too
