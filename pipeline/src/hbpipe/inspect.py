"""Per-session summary: size, rate, pauses, flags and the recorder's own cost.

The Phase 1 budget is checked here against each closed session: main-thread cost under 0.3 ms per
tick (mean, with p99 shown) and disk use under 50 MB per hour of session time.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from hbpipe.convert.session import Session
from hbpipe.schema import pb

BUDGET_US_PER_TICK = 300.0
BUDGET_MB_PER_HOUR = 50.0


@dataclass
class Summary:
    session_id: str
    complete: bool
    hours: float
    megabytes: float
    records: Counter
    segments: int
    pauses: Counter
    flags: Counter
    discards: int
    mean_us: float | None
    p99_us: float | None
    fps_mean: float | None

    @property
    def mb_per_hour(self) -> float:
        return self.megabytes / self.hours if self.hours > 0 else 0.0

    @property
    def main_thread_share(self) -> float | None:
        """Fraction of client-thread time the recorder used (20 ticks/s). It bounds the FPS loss
        when the game is limited by the client thread."""
        return None if self.mean_us is None else self.mean_us * 20 / 1e6

    @property
    def within_budget(self) -> bool | None:
        if self.mean_us is None or self.hours <= 0:
            return None
        return self.mean_us < BUDGET_US_PER_TICK and self.mb_per_hour < BUDGET_MB_PER_HOUR


def summarize(session: Session) -> Summary:
    records: Counter = Counter()
    pauses: Counter = Counter()
    flags: Counter = Counter()
    segments = set()
    discards = 0
    last_t = 0
    for env in session.envelopes():
        body = env.WhichOneof("body")
        records[body] += 1
        last_t = max(last_t, env.t_ns)
        if env.segment_id:
            segments.add(env.segment_id)
        if body == "state" and env.state.state == pb.StateChange.PAUSED:
            pauses[pb.PauseReason.Name(env.state.reason_code)] += 1
        elif body == "flag":
            flags[pb.SegmentFlag.Kind.Name(env.flag.kind)] += 1
        elif body == "discard":
            discards += 1
    stats = session.stats or {}
    perf = stats.get("main_thread_per_tick", {})
    duration_s = stats.get("duration_s", last_t / 1e9)
    return Summary(
        session_id=session.session_id,
        complete=session.complete,
        hours=duration_s / 3600,
        megabytes=session.bytes_on_disk() / 1e6,
        records=records,
        segments=len(segments),
        pauses=pauses,
        flags=flags,
        discards=discards,
        mean_us=perf.get("mean_us"),
        p99_us=perf.get("p99_us"),
        fps_mean=stats.get("fps_mean"),
    )


def format_summary(s: Summary) -> str:
    budget = {None: "n/a", True: "within budget", False: "OVER BUDGET"}[s.within_budget]
    lines = [
        f"session {s.session_id}{'' if s.complete else ' (not closed: crashed or still open)'}",
        f"  {s.hours * 60:.1f} min, {s.megabytes:.2f} MB on disk, {s.mb_per_hour:.1f} MB/h, "
        f"{s.segments} segments, {s.discards} discards",
    ]
    if s.mean_us is not None:
        lines.append(
            f"  main thread {s.mean_us:.1f} us/tick mean, {s.p99_us:.0f} us p99 "
            f"(budget {BUDGET_US_PER_TICK:.0f} us), {s.main_thread_share * 100:.2f}% of the "
            f"client thread; fps mean {s.fps_mean}; {budget}"
        )
    lines.append("  records: " + ", ".join(f"{k} {v}" for k, v in s.records.most_common()))
    if s.pauses:
        lines.append(
            "  pauses: "
            + ", ".join(
                f"{k.removeprefix('PAUSE_REASON_').lower()} {v}" for k, v in s.pauses.most_common()
            )
        )
    if s.flags:
        lines.append("  flags: " + ", ".join(f"{k.lower()} {v}" for k, v in s.flags.most_common()))
    return "\n".join(lines)
