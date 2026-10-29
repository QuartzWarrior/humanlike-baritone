"""Drops play where the view wasn't the player's, in sessions recorded before the recorder could
tell (it pauses with CAMERA_DETACHED since then).

The rule is the recorder's: DECOUPLED_FRAMES mouse frames in a row that move the mouse sideways
(dx != 0) without turning the player (d_yaw == 0) mean a freecam mod or a camera on another
entity has the mouse. Everything from the first such frame is dropped, the segment ends there
with a CAMERA_DETACHED pause, and recording resumes, under a new segment id, at the next mouse
frame that turns the player again. A real pause or resume in the recording ends the drop too.
On sessions from today's recorder this finds nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from hbpipe.schema import pb

DECOUPLED_FRAMES = 5  # hb.recorder.Recorder.DECOUPLED_FRAMES


def _decoupled(e: pb.Envelope) -> bool | None:
    """True for a mouse frame that didn't turn the player, False for one that did, None for
    anything else."""
    if e.WhichOneof("body") != "mouse" or e.mouse.dx == 0:
        return None
    return e.mouse.d_yaw == 0


def _with_segment(e: pb.Envelope, segment_id: str) -> pb.Envelope:
    out = pb.Envelope()
    out.CopyFrom(e)
    out.segment_id = segment_id
    return out


def drop_detached_camera(envelopes: Iterable[pb.Envelope]) -> Iterator[pb.Envelope]:
    held: list[pb.Envelope] = []  # since the first frame of a decoupled run that may be one
    run = 0
    detached = False
    rename: str | None = None  # segment id for records after a resume this filter made
    resumes = 0
    for e in envelopes:
        is_state = e.WhichOneof("body") == "state"
        if is_state:
            rename = None
            if detached:
                detached = False
                run = 0
            yield from held
            held, run = [], 0
            yield e
            continue
        if detached:
            if _decoupled(e) is False:
                detached = False
                resumes += 1
                rename = f"{e.segment_id}-cam{resumes}"
                yield pb.Envelope(
                    schema=e.schema,
                    segment_id=rename,
                    tick=e.tick,
                    t_ns=e.t_ns,
                    state=pb.StateChange(state=pb.StateChange.RECORDING),
                )
                yield _with_segment(e, rename)
            continue
        out = _with_segment(e, rename) if rename else e
        d = _decoupled(e)
        if d is True:
            run += 1
            held.append(out)
            if run == DECOUPLED_FRAMES:
                first = held[0]
                yield pb.Envelope(
                    schema=first.schema,
                    segment_id=first.segment_id,
                    tick=first.tick,
                    t_ns=first.t_ns,
                    state=pb.StateChange(
                        state=pb.StateChange.PAUSED,
                        reason_code=pb.PAUSE_REASON_CAMERA_DETACHED,
                        reason="mouse moved without turning the player (found after recording)",
                    ),
                )
                held, run, detached = [], 0, True
            continue
        if d is False:
            yield from held
            held, run = [], 0
            yield out
            continue
        if held:
            held.append(out)  # ticks and events inside a run that may still turn out decoupled
        else:
            yield out
    yield from held
