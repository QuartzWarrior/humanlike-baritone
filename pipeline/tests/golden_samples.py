"""Python half of the cross-language golden records.

Mirrors mod/common/src/test/java/hb/common/io/GoldenSamples.java value for value. Both must
serialize to the committed proto/testdata/golden-v1.hbr byte for byte.
"""

from pathlib import Path

from hbpipe.schema import SCHEMA_VERSION, pb

TESTDATA = Path(__file__).resolve().parents[2] / "proto" / "testdata"
GOLDEN_RAW = TESTDATA / "golden-v1.hbr"
GOLDEN_ZSTD = TESTDATA / "golden-v1.hbr.zst"

_SEGMENT = "seg-0001"


def _envelope(tick: int, t_ns: int, **body) -> pb.Envelope:
    return pb.Envelope(schema=SCHEMA_VERSION, segment_id=_SEGMENT, tick=tick, t_ns=t_ns, **body)


def _pos(x: int, y: int, z: int) -> pb.BlockPos:
    return pb.BlockPos(x=x, y=y, z=z)


def envelopes() -> list[pb.Envelope]:
    return [
        _envelope(
            0, 0, state=pb.StateChange(state=pb.StateChange.RECORDING, reason="session start")
        ),
        _envelope(
            0,
            1_000_000,
            cube=pb.WorldCube(
                origin=_pos(-4, 60, 12),
                size=2,
                palette=["minecraft:air", "minecraft:stone"],
                indices=[1, 1, 1, 1, 0, 0, 0, 0],
            ),
        ),
        _envelope(
            1,
            50_000_000,
            tick_state=pb.Tick(
                x=0.5,
                y=64.0,
                z=-12.25,
                vx=0.125,
                vy=-0.078125,
                vz=0.0,
                yaw=-90.5,
                pitch=12.75,
                flags=pb.TICK_FLAG_ON_GROUND | pb.TICK_FLAG_SPRINTING,
                keys_down=0b101,
                slot=3,
                held_item="minecraft:iron_pickaxe",
                hit=pb.HitResult(
                    type=pb.HitResult.BLOCK,
                    block_pos=_pos(1, 63, -12),
                    face=pb.DIRECTION_UP,
                    location=pb.Vec3(x=1.5, y=64.0, z=-11.5),
                    block="minecraft:stone",
                ),
                destroy_progress=0.25,
                attack_cooldown=1.0,
                health=20.0,
                food=18,
                fps=144.0,
                entities=[
                    pb.NearbyEntity(
                        id=42, type="minecraft:zombie", dx=3.5, dy=0.0, dz=-2.0, health=20.0
                    )
                ],
                main_hand=pb.ItemStack(
                    id="minecraft:iron_pickaxe",
                    count=1,
                    damage=7,
                    max_damage=250,
                    enchantments=[pb.Enchantment(id="minecraft:efficiency", level=2)],
                ),
                context=pb.TickContext(
                    dimension="minecraft:overworld",
                    biome="minecraft:plains",
                    sky_light=15,
                    day_time=6000,
                ),
            ),
        ),
        _envelope(1, 51_250_000, mouse=pb.MouseMove(dx=12.0, dy=-3.0, d_yaw=1.5, d_pitch=-0.375)),
        _envelope(1, 52_000_000, key=pb.Key(key=87, scancode=17, down=True, binding="key.forward")),
        _envelope(
            2,
            100_000_000,
            event=pb.GameEvent(
                block_broken=pb.BlockBroken(
                    pos=_pos(1, 63, -12),
                    block="minecraft:stone",
                    tool="minecraft:iron_pickaxe",
                    ticks_taken=8,
                    face=pb.DIRECTION_UP,
                )
            ),
        ),
        _envelope(
            2,
            100_000_000,
            delta=pb.WorldDelta(
                origin=_pos(-4, 60, 12),
                changes=[pb.BlockChange(pos=_pos(1, 63, -12), state="minecraft:air")],
            ),
        ),
        _envelope(
            3, 150_000_000, flag=pb.SegmentFlag(kind=pb.SegmentFlag.DEGRADED, reason="fps<20")
        ),
        _envelope(
            4,
            200_000_000,
            state=pb.StateChange(
                state=pb.StateChange.PAUSED,
                reason="no input for 10 s",
                reason_code=pb.PAUSE_REASON_IDLE,
            ),
        ),
    ]
