"""Protobuf bindings for proto/hb/v1/hb.proto under one import.

from hbpipe.schema import pb
envelope = pb.Envelope.FromString(data)
"""

from hbpipe.proto.hb.v1 import hb_pb2 as pb

SCHEMA_VERSION: int = pb.SCHEMA_VERSION_CURRENT

__all__ = ["SCHEMA_VERSION", "pb"]
