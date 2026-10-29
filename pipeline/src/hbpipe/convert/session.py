"""Session directories written by hb-recorder (and later hb-executor).

hb-data/sessions/<session-id>/
    header.json          SessionHeader, proto3 JSON mapping
    chunk-0000.hbr.zst   records; a new chunk every 5 minutes of session time
    stats.json           written on close; missing if the game crashed
    mirror.jsonl         optional JSON copy of every record (round-trip tests only)
"""

from __future__ import annotations

import io
import json
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from google.protobuf import json_format

from hbpipe.convert import hbr
from hbpipe.schema import pb


@dataclass
class Session:
    path: Path
    header: pb.SessionHeader
    chunks: list[Path]
    stats: dict | None = field(default=None)

    @property
    def session_id(self) -> str:
        return self.header.session_id

    @property
    def complete(self) -> bool:
        """False when the recorder never closed the session (crash, kill)."""
        return self.stats is not None

    def records(self) -> Iterator[bytes]:
        """Serialized records in order. The last chunk of an incomplete session may be cut off;
        its complete records are kept."""
        for i, chunk in enumerate(self.chunks):
            partial = not self.complete and i == len(self.chunks) - 1
            data = hbr.read_bytes(chunk, partial=partial)
            yield from hbr.iter_records(io.BytesIO(data), partial=partial)

    def envelopes(self, *, apply_discards: bool = False) -> Iterator[pb.Envelope]:
        envelopes = (pb.Envelope.FromString(r) for r in self.records())
        if apply_discards:
            yield from drop_discarded(envelopes)
        else:
            yield from envelopes

    def bytes_on_disk(self) -> int:
        return sum(c.stat().st_size for c in self.chunks)


def open_session(path: str | Path) -> Session:
    path = Path(path)
    header = json_format.Parse(
        (path / "header.json").read_text(encoding="utf-8"), pb.SessionHeader()
    )
    chunks = sorted(path.glob("chunk-*.hbr.zst"))
    stats_file = path / "stats.json"
    stats = json.loads(stats_file.read_text(encoding="utf-8")) if stats_file.exists() else None
    return Session(path=path, header=header, chunks=chunks, stats=stats)


def find_sessions(root: str | Path) -> list[Session]:
    """Sessions under an hb-data dir, a sessions dir, or a single session dir, oldest first."""
    root = Path(root)
    if (root / "header.json").exists():
        return [open_session(root)]
    base = root / "sessions" if (root / "sessions").is_dir() else root
    sessions = [open_session(d) for d in base.iterdir() if (d / "header.json").exists()]
    return sorted(sessions, key=lambda s: s.header.started_unix_ms)


# The recorder's F9 drops the last 60 s, so a tombstone can only reach records this recent.
DISCARD_HORIZON_NS = 120 * 1_000_000_000


def drop_discarded(envelopes: Iterable[pb.Envelope]) -> Iterator[pb.Envelope]:
    """Applies F9 tombstones: a Discard removes the records before it whose t_ns falls in its
    [from_t_ns, to_t_ns] span. Records after the tombstone are kept even if their time overlaps.

    Streams: only the last two minutes of records are held back."""
    held: deque[pb.Envelope] = deque()
    released = -1  # t_ns of the newest record already passed on
    for envelope in envelopes:
        if envelope.WhichOneof("body") == "discard":
            lo, hi = envelope.discard.from_t_ns, envelope.discard.to_t_ns
            if lo <= released:
                raise ValueError(f"discard from t_ns {lo} reaches records already passed on")
            held = deque(e for e in held if not lo <= e.t_ns <= hi)
            continue
        held.append(envelope)
        while held and held[0].t_ns < envelope.t_ns - DISCARD_HORIZON_NS:
            released = max(released, held[0].t_ns)
            yield held.popleft()
    yield from held
