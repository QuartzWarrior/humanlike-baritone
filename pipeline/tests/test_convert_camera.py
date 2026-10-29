from hbpipe.convert.camera import DECOUPLED_FRAMES, drop_detached_camera
from hbpipe.convert.tables import convert_envelopes
from hbpipe.schema import pb


def env(tick: int, **body) -> pb.Envelope:
    return pb.Envelope(schema=1, segment_id="seg-0001", tick=tick, t_ns=tick * 50_000_000, **body)


def turn(tick, d_yaw=0.3):
    return env(tick, mouse=pb.MouseMove(dx=2.0, d_yaw=d_yaw))


def tick(t):
    return env(t, tick_state=pb.Tick(x=float(t)))


def stream(decoupled: int) -> list[pb.Envelope]:
    out = [env(0, state=pb.StateChange(state=pb.StateChange.RECORDING))]
    t = 0
    for _ in range(3):
        t += 1
        out += [turn(t), tick(t)]
    for _ in range(decoupled):  # freecam: the mouse moves, the player doesn't turn
        t += 1
        out += [turn(t, d_yaw=0.0), tick(t)]
    for _ in range(3):
        t += 1
        out += [turn(t), tick(t)]
    return out


def test_freecam_play_is_dropped_and_recording_resumes_on_a_real_turn():
    t = convert_envelopes(
        drop_detached_camera(stream(8)), session_id="s", source="human", complete=True
    )
    segs = t["segments"].select("segment_id", "end_state", "end_reason").rows()
    assert segs == [
        ("seg-0001", "paused", "camera_detached"),
        ("seg-0001-cam1", "stopped", None),
    ]
    xs = t["ticks"]["x"].to_list()
    assert xs == [1.0, 2.0, 3.0, 12.0, 13.0, 14.0]  # ticks 4-11 were freecam
    assert t["ticks"]["segment_id"].to_list()[3:] == ["seg-0001-cam1"] * 3


def test_a_short_decoupled_run_is_kept():
    out = list(drop_detached_camera(stream(DECOUPLED_FRAMES - 1)))
    assert out == stream(DECOUPLED_FRAMES - 1)


def test_a_real_resume_ends_the_drop():
    envs = stream(6)[:-6]  # still detached at the end
    resume = env(20, state=pb.StateChange(state=pb.StateChange.RECORDING))
    resume.segment_id = "seg-0002"
    envs.append(resume)
    after = env(21, tick_state=pb.Tick(x=21.0))
    after.segment_id = "seg-0002"
    envs.append(after)
    t = convert_envelopes(drop_detached_camera(envs), session_id="s", source="human")
    assert t["segments"]["segment_id"].to_list() == ["seg-0001", "seg-0002"]
    assert t["ticks"]["x"].to_list()[-1] == 21.0
