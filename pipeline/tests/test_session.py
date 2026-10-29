import json
from pathlib import Path

import pytest
import zstandard
from google.protobuf import json_format

from hbpipe.cli import main
from hbpipe.convert import hbr
from hbpipe.convert.session import drop_discarded, find_sessions, open_session
from hbpipe.inspect import summarize
from hbpipe.schema import pb
from hbpipe.verify import verify

S = 1_000_000_000


def env(t_s: float, **body) -> pb.Envelope:
    return pb.Envelope(
        schema=1, segment_id="seg-0001", tick=int(t_s * 20), t_ns=int(t_s * S), **body
    )


def records() -> list[pb.Envelope]:
    out = [env(0, state=pb.StateChange(state=pb.StateChange.RECORDING))]
    for i in range(1, 40):
        out.append(env(i * 0.05, tick_state=pb.Tick(x=i, y=64.0, z=-3.5, yaw=i * 0.1)))
        out.append(
            env(i * 0.05 + 0.01, mouse=pb.MouseMove(dx=1.5, dy=-0.5, d_yaw=0.225, d_pitch=-0.075))
        )
    out.append(env(1.0, key=pb.Key(key=87, scancode=17, down=True, binding="key.forward")))
    out.append(env(1.5, key=pb.Key(key=256, down=True)))  # Escape: a GUI key, unbound
    out.append(
        env(
            2.0,
            flag=pb.SegmentFlag(kind=pb.SegmentFlag.DEGRADED, reason="fps under 20 for over 2 s"),
        )
    )
    out.append(
        env(
            2.5, state=pb.StateChange(state=pb.StateChange.PAUSED, reason_code=pb.PAUSE_REASON_IDLE)
        )
    )
    return out


def write_session(
    root: Path, envelopes, *, session_id="s-1", closed=True, mirror=True, chunk_split=None
) -> Path:
    d = root / "sessions" / session_id
    d.mkdir(parents=True)
    header = pb.SessionHeader(
        schema=1, session_id=session_id, source=pb.SOURCE_HUMAN, started_unix_ms=1_790_000_000_000
    )
    (d / "header.json").write_text(json_format.MessageToJson(header), encoding="utf-8")
    parts = (
        [envelopes] if chunk_split is None else [envelopes[:chunk_split], envelopes[chunk_split:]]
    )
    for i, part in enumerate(parts):
        (d / f"chunk-{i:04d}.hbr.zst").write_bytes(
            zstandard.ZstdCompressor().compress(hbr.encode(part))
        )
    if mirror:
        lines = [json_format.MessageToJson(e, indent=None) for e in envelopes]
        (d / "mirror.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if closed:
        stats = {
            "duration_s": 3.0,
            "fps_mean": 144.0,
            "main_thread_per_tick": {"mean_us": 42.0, "p99_us": 110},
        }
        (d / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
    return d


def test_reads_a_session_across_chunks(tmp_path):
    expected = records()
    write_session(tmp_path, expected, chunk_split=30)
    [session] = find_sessions(tmp_path)
    assert session.complete
    assert session.header.session_id == "s-1"
    assert list(session.envelopes()) == expected


def test_crashed_session_keeps_complete_records_of_a_cut_off_chunk(tmp_path):
    expected = records()
    d = write_session(tmp_path, expected, closed=False, mirror=False, chunk_split=30)
    last = d / "chunk-0001.hbr.zst"
    last.write_bytes(last.read_bytes()[:-9])  # the game died mid-write
    session = open_session(d)
    assert not session.complete
    got = list(session.envelopes())
    assert got == expected[: len(got)]
    assert 30 <= len(got) < len(expected)
    with pytest.raises(EOFError):
        hbr.read_file(last)  # strict reading still refuses it


def test_discard_drops_earlier_records_in_its_span_only():
    stream = [
        env(1, mouse=pb.MouseMove(dx=1)),
        env(2, mouse=pb.MouseMove(dx=2)),
        env(3, mouse=pb.MouseMove(dx=3)),
    ]
    stream.append(env(3, discard=pb.Discard(from_t_ns=2 * S, to_t_ns=3 * S)))
    stream.append(env(3, state=pb.StateChange(state=pb.StateChange.RECORDING)))  # new segment at F9
    kept = list(drop_discarded(stream))
    assert [e.WhichOneof("body") for e in kept] == ["mouse", "state"]
    assert kept[0].t_ns == 1 * S


def test_verify_passes_when_python_reads_what_java_wrote(tmp_path):
    write_session(tmp_path, records())
    report = verify(open_session(tmp_path / "sessions" / "s-1"))
    assert report.ok, report
    assert report.records == report.mirrored == len(records())


def test_verify_accepts_a_crashed_session_whose_mirror_runs_further(tmp_path):
    d = write_session(tmp_path, records(), closed=False, chunk_split=30)
    last = d / "chunk-0001.hbr.zst"
    last.write_bytes(last.read_bytes()[:-9])
    report = verify(open_session(d))
    assert report.ok, report
    assert report.records == report.mirrored
    assert report.lost_in_crash == len(records()) - report.records > 0

    # A closed session has no excuse for a longer mirror.
    closed = write_session(tmp_path, records(), session_id="s-2")
    with (closed / "mirror.jsonl").open("a", encoding="utf-8") as f:
        f.write(json_format.MessageToJson(env(3.0, mouse=pb.MouseMove(dx=1)), indent=None) + "\n")
    assert not verify(open_session(closed)).ok


def test_verify_reports_a_value_mismatch(tmp_path):
    d = write_session(tmp_path, records())
    lines = (d / "mirror.jsonl").read_text(encoding="utf-8").splitlines()
    lines[3] = lines[3].replace('"x": 2.0', '"x": 2.5')
    (d / "mirror.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = verify(open_session(d))
    assert not report.ok
    assert report.mismatches == ["record 3 (tick_state): differs from the Java mirror"]


def test_verify_catches_keys_typed_into_a_text_field(tmp_path):
    leaked = records() + [env(5.0, key=pb.Key(key=72, down=True, binding="key.hb.leak"))]
    d = write_session(tmp_path, leaked)
    probe = {"windows": [[int(4.5 * S), int(5.5 * S)]], "secrets": ["hunter2"]}
    (d / "privacy-probe.json").write_text(json.dumps(probe), encoding="utf-8")
    (d / "stats.json").write_text(json.dumps({"note": "hunter2"}), encoding="utf-8")
    report = verify(open_session(d))
    assert any("while typing in a text field" in p for p in report.privacy)
    assert any("'hunter2' found in stats.json" in p for p in report.privacy)


def test_verify_rejects_unbound_non_gui_keys(tmp_path):
    d = write_session(tmp_path, records() + [env(6, key=pb.Key(key=65, down=True))])
    report = verify(open_session(d))
    assert report.privacy == [f"record {len(records())}: unbound non-GUI key 65 recorded"]


def test_inspect_summarizes_a_session(tmp_path):
    write_session(tmp_path, records())
    s = summarize(find_sessions(tmp_path)[0])
    assert s.records["tick_state"] == 39
    assert s.pauses == {"PAUSE_REASON_IDLE": 1}
    assert s.flags == {"DEGRADED": 1}
    assert s.within_budget
    assert s.main_thread_share == pytest.approx(42.0 * 20 / 1e6)


def test_cli(tmp_path, capsys):
    write_session(tmp_path, records())
    assert main(["verify", str(tmp_path)]) == 0
    assert main(["inspect", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "ok s-1" in out
    assert "MB/h" in out
