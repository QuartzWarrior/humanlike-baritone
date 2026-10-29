import hashlib
from pathlib import Path

import hbpipe.proto
from hbpipe.schema import SCHEMA_VERSION, pb

PROTO = Path(__file__).resolve().parents[2] / "proto" / "hb" / "v1" / "hb.proto"


def test_python_bindings_are_generated_from_the_current_proto():
    digest = hashlib.sha256(PROTO.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    recorded = (Path(hbpipe.proto.__file__).parent / "hb_proto.sha256").read_text().strip()
    assert digest == recorded, "stale Python bindings: run ./gradlew :mod:common:syncPythonProto"


def test_schema_version():
    assert SCHEMA_VERSION == pb.SCHEMA_VERSION_1 == 1
