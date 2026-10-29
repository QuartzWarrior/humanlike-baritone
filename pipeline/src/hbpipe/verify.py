"""Phase 1 gate checks on recorded sessions.

Round trip: every record must decode in Python to exactly what Java wrote. Each record is checked
two ways: re-serializing it must reproduce the bytes on disk (nothing lost or unknown), and, when
the session has a mirror.jsonl (the recorder's JSON copy, written with -Dhb.recorder.mirror=true),
it must equal the record Java printed.

Privacy: key records may only hold bound or GUI keys. When a test left a privacy-probe.json
(``{"windows": [[from_t_ns, to_t_ns], ...], "secrets": ["...", ...]}``) there must be no key record
inside a window where it typed into a text field, and none of the typed secrets may appear in any
file of the session.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from google.protobuf import json_format

from hbpipe.convert import hbr
from hbpipe.convert.session import Session
from hbpipe.schema import SCHEMA_VERSION, pb

# Mirrors hb.common.mc.McKeys.GUI_KEYS (GLFW codes): Escape, Enter, keypad Enter, Tab, arrows,
# shift, control and alt.
GUI_KEYS = frozenset({256, 257, 335, 258, 262, 263, 264, 265, 340, 344, 341, 345, 342, 346})


@dataclass
class Report:
    session_id: str
    records: int = 0
    mirrored: int = 0
    mismatches: list[str] = field(default_factory=list)
    privacy: list[str] = field(default_factory=list)
    probe_windows: int = 0
    lost_in_crash: int = 0

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.privacy


def verify(session: Session, *, max_reports: int = 20) -> Report:
    report = Report(session_id=session.session_id)
    mirror_file = session.path / "mirror.jsonl"
    mirror = mirror_file.read_text(encoding="utf-8").splitlines() if mirror_file.exists() else None
    probe_file = session.path / "privacy-probe.json"
    probe = json.loads(probe_file.read_text(encoding="utf-8")) if probe_file.exists() else None
    windows = [tuple(w) for w in probe["windows"]] if probe else []
    report.probe_windows = len(windows)

    def mismatch(text: str) -> None:
        if len(report.mismatches) < max_reports:
            report.mismatches.append(text)

    for i, data in enumerate(session.records()):
        report.records += 1
        env = pb.Envelope.FromString(data)
        if env.SerializeToString() != data:
            mismatch(f"record {i}: re-serializing changes its bytes")
        if env.schema != SCHEMA_VERSION:
            mismatch(f"record {i}: schema {env.schema}, expected {SCHEMA_VERSION}")
        if mirror is not None and i < len(mirror):
            expected = json_format.Parse(mirror[i], pb.Envelope())
            report.mirrored += 1
            if expected != env:
                mismatch(f"record {i} ({env.WhichOneof('body')}): differs from the Java mirror")
        if env.WhichOneof("body") == "key":
            if not env.key.binding and env.key.key not in GUI_KEYS:
                report.privacy.append(f"record {i}: unbound non-GUI key {env.key.key} recorded")
            for lo, hi in windows:
                if lo <= env.t_ns <= hi:
                    report.privacy.append(
                        f"record {i}: key {env.key.key} recorded while typing in a text field"
                    )
    if mirror is not None and len(mirror) != report.records:
        if not session.complete and len(mirror) > report.records:
            # A crash cuts both files at different points; the records that did reach the
            # chunks were all compared above.
            report.lost_in_crash = len(mirror) - report.records
        else:
            mismatch(f"{report.records} records on disk but {len(mirror)} in the mirror")

    if probe:
        files = [
            p for p in session.path.iterdir() if p.is_file() and p.name != "privacy-probe.json"
        ]
        contents = [
            (p.name, hbr.read_bytes(p, partial=True) if p.suffix == ".zst" else p.read_bytes())
            for p in files
        ]
        for secret in probe["secrets"]:
            needle = secret.encode("utf-8")
            for name, data in contents:
                if needle in data:
                    report.privacy.append(f"typed text {secret!r} found in {name}")
    return report
