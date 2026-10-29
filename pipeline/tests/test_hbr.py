"""Python half of the cross-language contract test (Java half: HbrGoldenTest)."""

import io

import golden_samples
import pytest

from hbpipe.convert import hbr
from hbpipe.schema import pb


def test_python_serialization_matches_java_golden():
    assert hbr.encode(golden_samples.envelopes()) == golden_samples.GOLDEN_RAW.read_bytes()


def test_java_written_zstd_chunk_decodes():
    assert hbr.read_file(golden_samples.GOLDEN_ZSTD) == golden_samples.envelopes()


def test_golden_values_survive_the_round_trip():
    tick = hbr.read_file(golden_samples.GOLDEN_RAW)[2]
    assert tick.WhichOneof("body") == "tick_state"
    assert tick.tick_state.z == -12.25
    assert tick.tick_state.vy == -0.078125
    assert tick.tick_state.flags & pb.TICK_FLAG_SPRINTING
    assert tick.tick_state.hit.face == pb.DIRECTION_UP
    assert tick.tick_state.main_hand.enchantments[0].level == 2


def test_round_trip_with_multibyte_length_prefix():
    big = pb.Envelope(segment_id="s", cube=pb.WorldCube(size=9, indices=list(range(729))))
    records = [big, pb.Envelope(segment_id="s", tick=1)]
    data = hbr.encode(records)
    assert data[0] & 0x80  # the cube needs a two-byte length prefix
    assert hbr.decode(data) == records


def test_truncated_stream_is_an_error():
    data = hbr.encode(golden_samples.envelopes())
    with pytest.raises(EOFError):
        list(hbr.iter_envelopes(io.BytesIO(data[:-3])))
