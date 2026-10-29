package hb.common.io;

import hb.common.HbCommon;
import hb.proto.v1.BlockBroken;
import hb.proto.v1.BlockChange;
import hb.proto.v1.BlockPos;
import hb.proto.v1.Direction;
import hb.proto.v1.Enchantment;
import hb.proto.v1.Envelope;
import hb.proto.v1.GameEvent;
import hb.proto.v1.HitResult;
import hb.proto.v1.ItemStack;
import hb.proto.v1.Key;
import hb.proto.v1.MouseMove;
import hb.proto.v1.NearbyEntity;
import hb.proto.v1.PauseReason;
import hb.proto.v1.SegmentFlag;
import hb.proto.v1.StateChange;
import hb.proto.v1.Tick;
import hb.proto.v1.TickContext;
import hb.proto.v1.TickFlag;
import hb.proto.v1.Vec3;
import hb.proto.v1.WorldCube;
import hb.proto.v1.WorldDelta;

import java.util.List;

/**
 * The cross-language golden records. pipeline/tests/golden_samples.py builds the same values in
 * Python; both sides must serialize them to the committed proto/testdata/golden-v1.hbr byte for
 * byte. Floats are chosen to be exact in binary so neither language rounds differently.
 */
final class GoldenSamples {
	private static final String SEGMENT = "seg-0001";

	private GoldenSamples() {
	}

	static List<Envelope> envelopes() {
		return List.of(
			envelope(0, 0).setState(StateChange.newBuilder()
				.setState(StateChange.S.RECORDING)
				.setReason("session start")).build(),
			envelope(0, 1_000_000).setCube(WorldCube.newBuilder()
				.setOrigin(pos(-4, 60, 12))
				.setSize(2)
				.addPalette("minecraft:air")
				.addPalette("minecraft:stone")
				.addAllIndices(List.of(1, 1, 1, 1, 0, 0, 0, 0))).build(),
			envelope(1, 50_000_000).setTickState(Tick.newBuilder()
				.setX(0.5).setY(64.0).setZ(-12.25)
				.setVx(0.125f).setVy(-0.078125f).setVz(0.0f)
				.setYaw(-90.5f).setPitch(12.75f)
				.setFlags(TickFlag.TICK_FLAG_ON_GROUND_VALUE | TickFlag.TICK_FLAG_SPRINTING_VALUE)
				.setKeysDown(0b101)
				.setSlot(3)
				.setHeldItem("minecraft:iron_pickaxe")
				.setHit(HitResult.newBuilder()
					.setType(HitResult.Type.BLOCK)
					.setBlockPos(pos(1, 63, -12))
					.setFace(Direction.DIRECTION_UP)
					.setLocation(Vec3.newBuilder().setX(1.5).setY(64.0).setZ(-11.5))
					.setBlock("minecraft:stone"))
				.setDestroyProgress(0.25f)
				.setAttackCooldown(1.0f)
				.setHealth(20.0f)
				.setFood(18)
				.setFps(144.0f)
				.addEntities(NearbyEntity.newBuilder()
					.setId(42).setType("minecraft:zombie")
					.setDx(3.5f).setDy(0.0f).setDz(-2.0f)
					.setHealth(20.0f))
				.setMainHand(ItemStack.newBuilder()
					.setId("minecraft:iron_pickaxe").setCount(1).setDamage(7).setMaxDamage(250)
					.addEnchantments(Enchantment.newBuilder().setId("minecraft:efficiency").setLevel(2)))
				.setContext(TickContext.newBuilder()
					.setDimension("minecraft:overworld")
					.setBiome("minecraft:plains")
					.setSkyLight(15)
					.setDayTime(6000))).build(),
			envelope(1, 51_250_000).setMouse(MouseMove.newBuilder()
				.setDx(12.0).setDy(-3.0).setDYaw(1.5f).setDPitch(-0.375f)).build(),
			envelope(1, 52_000_000).setKey(Key.newBuilder()
				.setKey(87).setScancode(17).setDown(true).setBinding("key.forward")).build(),
			envelope(2, 100_000_000).setEvent(GameEvent.newBuilder()
				.setBlockBroken(BlockBroken.newBuilder()
					.setPos(pos(1, 63, -12))
					.setBlock("minecraft:stone")
					.setTool("minecraft:iron_pickaxe")
					.setTicksTaken(8)
					.setFace(Direction.DIRECTION_UP))).build(),
			envelope(2, 100_000_000).setDelta(WorldDelta.newBuilder()
				.setOrigin(pos(-4, 60, 12))
				.addChanges(BlockChange.newBuilder().setPos(pos(1, 63, -12)).setState("minecraft:air"))).build(),
			envelope(3, 150_000_000).setFlag(SegmentFlag.newBuilder()
				.setKind(SegmentFlag.Kind.DEGRADED)
				.setReason("fps<20")).build(),
			envelope(4, 200_000_000).setState(StateChange.newBuilder()
				.setState(StateChange.S.PAUSED)
				.setReason("no input for 10 s")
				.setReasonCode(PauseReason.PAUSE_REASON_IDLE)).build());
	}

	private static Envelope.Builder envelope(long tick, long tNs) {
		return Envelope.newBuilder()
			.setSchema(HbCommon.SCHEMA_VERSION)
			.setSegmentId(SEGMENT)
			.setTick(tick)
			.setTNs(tNs);
	}

	private static BlockPos pos(int x, int y, int z) {
		return BlockPos.newBuilder().setX(x).setY(y).setZ(z).build();
	}
}
