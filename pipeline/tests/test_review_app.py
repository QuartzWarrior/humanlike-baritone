"""The review tool runs end to end on a small converted and labeled store (headless)."""

from pathlib import Path

import pytest
import zstandard
from google.protobuf import json_format

from hbpipe.convert import hbr
from hbpipe.convert.run import convert_all
from hbpipe.label.run import label_all
from hbpipe.schema import pb
from hbpipe.store import Store

APP = Path(__file__).parents[1] / "src" / "hbpipe" / "dashboard" / "review.py"
KEY_ORDER = ["key.attack", "key.use", "key.forward", "key.sprint"]


def walking_session(root: Path) -> None:
    """Two seconds standing, three walking east, a block broken, two standing."""
    d = root / "sessions" / "s-1"
    d.mkdir(parents=True)
    header = pb.SessionHeader(
        schema=1,
        session_id="s-1",
        source=pb.SOURCE_HUMAN,
        started_unix_ms=1_790_000_000_000,
        key_order=KEY_ORDER,
    )
    (d / "header.json").write_text(json_format.MessageToJson(header), encoding="utf-8")
    envs = [pb.Envelope(segment_id="seg-0001", tick=0, state=pb.StateChange())]
    envs.append(
        pb.Envelope(
            segment_id="seg-0001",
            tick=0,
            cube=pb.WorldCube(
                origin=pb.BlockPos(x=-4, y=60, z=-4),
                size=9,
                palette=["minecraft:air", "minecraft:stone"],
                indices=[1] * 243 + [0] * 486,
            ),
        )
    )
    x = 0.5
    for tick in range(140):
        walking = 40 <= tick < 100
        if walking:
            x += 0.2
        envs.append(
            pb.Envelope(
                segment_id="seg-0001",
                tick=tick,
                t_ns=tick * 50_000_000,
                tick_state=pb.Tick(
                    x=x,
                    y=63.0,
                    z=0.5,
                    vx=0.2 if walking else 0.0,
                    flags=pb.TICK_FLAG_ON_GROUND,
                    keys_down=0b100 if walking else 0,
                ),
            )
        )
    envs.append(
        pb.Envelope(
            segment_id="seg-0001",
            tick=110,
            t_ns=110 * 50_000_000,
            event=pb.GameEvent(
                block_broken=pb.BlockBroken(
                    pos=pb.BlockPos(x=13, y=62, z=0), block="minecraft:stone"
                )
            ),
        )
    )
    envs.sort(key=lambda e: e.tick)
    (d / "chunk-0000.hbr.zst").write_bytes(zstandard.ZstdCompressor().compress(hbr.encode(envs)))
    (d / "stats.json").write_text('{"duration_s": 7.0}', encoding="utf-8")


@pytest.fixture
def app(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    walking_session(tmp_path)
    convert_all(tmp_path)
    label_all(str(tmp_path))
    monkeypatch.setenv("HBPIPE_REVIEW_ROOT", str(tmp_path))
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at, tmp_path


def test_check_mode_saves_a_verdict(app):
    at, root = app
    walk = [o for o in at.selectbox[0].options if o.startswith("move.walk")]
    assert walk, at.selectbox[0].options
    at.selectbox[0].set_value("move.walk").run()
    correct = next(b for b in at.button if b.label == "✓ Correct")
    correct.click().run()
    assert not at.exception, at.exception
    rows = Store(root).corrections()
    assert rows["label"].to_list() == ["move.walk"] and rows["verdict"].to_list() == ["correct"]


def test_grid_mode_saves_a_page_of_verdicts(app):
    at, root = app
    at.selectbox[0].set_value("move.walk").run()
    at.toggle(key="grid").set_value(True).run()
    assert not at.exception, at.exception
    save = next(b for b in at.button if b.label.startswith("Save page"))
    save.click().run()
    assert not at.exception, at.exception
    rows = Store(root).corrections()
    assert rows.height >= 1 and set(rows["verdict"]) == {"correct"}


def test_browse_discover_and_report_render(app):
    at, _ = app
    for mode in ("Browse", "Discover", "Report", "Discriminator"):
        at.sidebar.radio[0].set_value(mode).run()
        assert not at.exception, (mode, at.exception)


def test_labels_are_redone_when_their_inputs_change(tmp_path, monkeypatch):
    from hbpipe.label import run

    walking_session(tmp_path)
    convert_all(tmp_path)
    assert list(label_all(str(tmp_path))) == ["s-1"]
    assert label_all(str(tmp_path)) == {}  # nothing changed

    monkeypatch.setattr(run, "fingerprint", lambda store: "changed-detectors")
    assert list(label_all(str(tmp_path))) == ["s-1"]
    assert label_all(str(tmp_path)) == {}

    convert_all(tmp_path, force=True)  # new tables invalidate the labels
    assert list(label_all(str(tmp_path))) == ["s-1"]


def test_an_old_index_gains_new_columns(tmp_path):
    import sqlite3

    db = sqlite3.connect(tmp_path / "index.sqlite")
    db.execute(
        "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, source TEXT NOT NULL, "
        "started_unix_ms INTEGER NOT NULL, play_day TEXT NOT NULL, complete INTEGER NOT NULL)"
    )
    db.commit()
    db.close()
    store = Store(tmp_path)
    cols = {r["name"] for r in store.db.execute("PRAGMA table_info(sessions)")}
    assert "labeler_fingerprint" in cols
    store.close()


def test_the_review_server_stays_on_this_machine():
    from hbpipe.cli import review_command

    cmd = review_command(APP, Path("hb-data"))
    assert cmd[cmd.index("--server.address") + 1] == "localhost"
    assert cmd[cmd.index("--browser.gatherUsageStats") + 1] == "false"
    assert cmd[cmd.index("--server.headless") + 1] == "true"  # no first-run email prompt


def test_discriminator_page_shows_the_newest_run(app):
    import json

    at, root = app
    run_dir = root / "discrim" / "20261027-120000"
    run_dir.mkdir(parents=True)
    walk = {
        "level": "primitive",
        "clips": {"human": 9, "bot": 9},
        "feature": {"accuracy": 0.97, "accuracy_ci": [0.9, 1.0], "auc": 0.99, "top_features": []},
        "distances": [{"feature": "yaw_snap_share", "wasserstein": 3.1, "ks": 1.0, "ks_p": 0.0}],
    }
    report = {
        "gate": {"goal": "plain Baritone detected at 95%+", "accuracy": 0.97, "pass": True},
        "feature_groups": ["motion"],
        "sets": {"move.walk": walk},
        "decision": {"status": "needs 2+ sessions of each class"},
    }
    (run_dir / "report.json").write_text(json.dumps(report), encoding="utf-8")
    at.sidebar.radio[0].set_value("Discriminator").run()
    assert not at.exception, at.exception
    assert at.metric[0].value == "97.0%"
