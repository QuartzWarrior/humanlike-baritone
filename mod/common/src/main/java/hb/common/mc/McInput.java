package hb.common.mc;

import hb.common.mc.mixin.KeyMappingAccessor;
import hb.common.mc.mixin.MouseHandlerAccessor;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.MouseHandler;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.core.BlockPos;
import net.minecraft.server.permissions.Permissions;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.util.HashMap;
import java.util.Map;

/**
 * Input the executor sends into the game at the same entry points a player's hardware uses
 * (spec, Component C: input injection rule): raw mouse counts into the mouse handler's per-frame
 * accumulator, before the game applies its own sensitivity, and key states through KeyMapping
 * press, release and click counts. Never a direct rotation, never a packet.
 *
 * <p>Injected motion goes in before McHooks reads the frame's raw motion, so recorders see it as
 * that frame's mouse input, exactly like a real mouse.
 */
public final class McInput {
	/** Supplies a frame's raw mouse motion, in counts; called once per rendered frame. */
	public interface MouseSource {
		/** {@code out[0]} = dx, {@code out[1]} = dy (positive dy looks down). */
		void frame(long nanoTime, double[] out);
	}

	private static volatile MouseSource source;
	private static final double[] FRAME = new double[2];
	private static final Map<String, KeyMapping> BY_NAME = new HashMap<>();

	private McInput() {
	}

	/** Sets (or with null clears) the source of injected mouse motion. */
	public static void setMouseSource(MouseSource mouseSource) {
		source = mouseSource;
	}

	static void injectFrame(MouseHandler handler) {
		MouseSource s = source;
		Minecraft mc = Minecraft.getInstance();
		if (s == null || mc.player == null || !handler.isMouseGrabbed()) {
			return;
		}
		FRAME[0] = 0;
		FRAME[1] = 0;
		s.frame(System.nanoTime(), FRAME);
		if (FRAME[0] != 0 || FRAME[1] != 0) {
			MouseHandlerAccessor acc = (MouseHandlerAccessor) handler;
			acc.hb$setAccumulatedDX(acc.hb$accumulatedDX() + FRAME[0]);
			acc.hb$setAccumulatedDY(acc.hb$accumulatedDY() + FRAME[1]);
		}
	}

	/**
	 * Degrees the camera turns per raw mouse count at the current sensitivity (the game's own
	 * turn math: (s * 0.6 + 0.2)^3 * 8 * 0.15). Spyglass zoom and cinematic camera aren't modeled.
	 */
	public static double degreesPerCount() {
		double s = Minecraft.getInstance().options.sensitivity().get() * 0.6 + 0.2;
		return s * s * s * 8.0 * 0.15;
	}

	/** +1, or -1 when the player inverted that mouse axis in the options. */
	public static int[] axisSigns() {
		var options = Minecraft.getInstance().options;
		return new int[]{options.invertMouseX().get() ? -1 : 1, options.invertMouseY().get() ? -1 : 1};
	}

	public static boolean sprinting() {
		Minecraft mc = Minecraft.getInstance();
		return mc.player != null && mc.player.isSprinting();
	}

	/** On solid ground, out of water and lava, off ladders and vines: a footing where pausing is harmless. */
	public static boolean steadyFooting() {
		Minecraft mc = Minecraft.getInstance();
		var p = mc.player;
		return p != null && p.onGround() && !p.isInWater() && !p.isInLava() && !p.onClimbable() && !p.isPassenger();
	}

	/** Standing on something or holding on to a ladder or vine, out of water: not mid-jump or swimming. */
	public static boolean supported() {
		var p = Minecraft.getInstance().player;
		return p != null && (p.onGround() || p.onClimbable()) && !p.isInWater() && !p.isInLava();
	}

	/** The open screen's class name, "" with none (for traces). */
	public static String screenName() {
		return McScreens.name(McScreens.current());
	}

	/** A container's screen is open: a chest, a hopper, a furnace. */
	public static boolean containerOpen() {
		return McScreens.isContainer(McScreens.current());
	}

	/** Closes the open container, as Escape does. */
	public static void closeContainer() {
		var p = Minecraft.getInstance().player;
		if (p != null && containerOpen()) {
			p.closeContainer();
		}
	}

	/** In water or lava, where a player bobs up and down without going anywhere. */
	public static boolean inFluid() {
		Minecraft mc = Minecraft.getInstance();
		return mc.player != null && (mc.player.isInWater() || mc.player.isInLava());
	}

	/** Horizontal speed in blocks per second. */
	public static double speed() {
		Minecraft mc = Minecraft.getInstance();
		if (mc.player == null) {
			return 0;
		}
		Vec3 v = mc.player.getDeltaMovement();
		return Math.hypot(v.x, v.z) * 20.0;
	}

	/**
	 * Danger close by (spec, Component C, safety filter): lava or fire within {@code radius} blocks
	 * of the feet, or a drop of {@code fatalDrop}+ blocks right next to the player (the eight
	 * neighbouring columns open that far down).
	 */
	public static boolean hazardNear(int radius, int fatalDrop) {
		Minecraft mc = Minecraft.getInstance();
		if (mc.player == null || mc.level == null) {
			return true;
		}
		if (lavaOrFireNear(radius)) {
			return true;
		}
		BlockPos feet = mc.player.blockPosition();
		for (int dx = -1; dx <= 1; dx++) {
			for (int dz = -1; dz <= 1; dz++) {
				if (dx == 0 && dz == 0) {
					continue;
				}
				int open = 0;
				for (int dy = 0; dy < fatalDrop && passable(feet.getX() + dx, feet.getY() - 1 - dy, feet.getZ() + dz); dy++) {
					open++;
				}
				if (open >= fatalDrop) {
					return true;
				}
			}
		}
		return false;
	}

	/** Lava or fire within {@code radius} blocks across and 2 up or down. */
	public static boolean lavaOrFireNear(int radius) {
		Minecraft mc = Minecraft.getInstance();
		if (mc.player == null || mc.level == null) {
			return true;
		}
		BlockPos feet = mc.player.blockPosition();
		for (BlockPos pos : BlockPos.betweenClosed(feet.offset(-radius, -2, -radius), feet.offset(radius, 2, radius))) {
			var state = mc.level.getBlockState(pos);
			if (state.getFluidState().is(net.minecraft.tags.FluidTags.LAVA) || state.is(net.minecraft.tags.BlockTags.FIRE)) {
				return true;
			}
		}
		return false;
	}

	/** Whether a player can move through the block at (x, y, z): no collision shape. */
	public static boolean passable(int x, int y, int z) {
		Minecraft mc = Minecraft.getInstance();
		if (mc.level == null) {
			return false;
		}
		BlockPos pos = new BlockPos(x, y, z);
		return mc.level.getBlockState(pos).getCollisionShape(mc.level, pos).isEmpty();
	}

	/** The player's eye position (x, y, z), lower while sneaking, or null outside a world. */
	public static double[] eye() {
		Minecraft mc = Minecraft.getInstance();
		if (mc.player == null) {
			return null;
		}
		Vec3 e = mc.player.getEyePosition();
		return new double[]{e.x, e.y, e.z};
	}

	/** The player's feet position (x, y, z), or null outside a world. */
	public static double[] position() {
		Minecraft mc = Minecraft.getInstance();
		return mc.player == null ? null : new double[]{mc.player.getX(), mc.player.getY(), mc.player.getZ()};
	}

	/** The player's yaw and pitch in degrees, or null outside a world. */
	public static float[] rotation() {
		Minecraft mc = Minecraft.getInstance();
		return mc.player == null ? null : new float[]{mc.player.getYRot(), mc.player.getXRot()};
	}

	/**
	 * The block a look in direction (yaw, pitch) would hit within the player's reach, and its
	 * angular width from the eyes in degrees (2 atan(0.5 / distance to its centre)); null if the
	 * look hits no block.
	 */
	public static BlockAim blockAlong(float yaw, float pitch) {
		Minecraft mc = Minecraft.getInstance();
		if (mc.player == null || mc.level == null) {
			return null;
		}
		Vec3 eye = mc.player.getEyePosition();
		Vec3 end = eye.add(Vec3.directionFromRotation(pitch, yaw).scale(mc.player.blockInteractionRange()));
		BlockHitResult hit = mc.level.clip(new ClipContext(eye, end, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, mc.player));
		if (hit.getType() != HitResult.Type.BLOCK) {
			return null;
		}
		BlockPos pos = hit.getBlockPos();
		double distance = eye.distanceTo(Vec3.atCenterOf(pos));
		return new BlockAim(pos.asLong(), hit.getDirection().ordinal(), Math.toDegrees(2 * Math.atan(0.5 / Math.max(distance, 0.5))));
	}

	/** A block position packed into a long, as {@link BlockAim#block()} has it. */
	public static long blockKey(int x, int y, int z) {
		return BlockPos.asLong(x, y, z);
	}

	/** The x, y, z of a packed block position ({@link #blockKey}). */
	public static int[] unpackBlock(long key) {
		return new int[]{BlockPos.getX(key), BlockPos.getY(key), BlockPos.getZ(key)};
	}

	/** The id of the block at (x, y, z), e.g. "minecraft:deepslate", or null where it isn't loaded. */
	public static String blockId(int x, int y, int z) {
		Minecraft mc = Minecraft.getInstance();
		BlockPos pos = new BlockPos(x, y, z);
		return mc.level == null || !mc.level.isLoaded(pos) ? null : McProto.blockId(mc.level.getBlockState(pos));
	}

	/** The block and face under the crosshair (width 0), or null. */
	public static BlockAim crosshair() {
		HitResult hit = Minecraft.getInstance().hitResult;
		return hit instanceof BlockHitResult b && hit.getType() == HitResult.Type.BLOCK
			? new BlockAim(b.getBlockPos().asLong(), b.getDirection().ordinal(), 0) : null;
	}

	/** A block a look points at: its position (BlockPos.asLong), the face hit, its angular width in degrees. */
	public record BlockAim(long block, int face, double widthDeg) {
	}

	/** Holds or releases the key mapping named {@code binding} (e.g. "key.forward"). */
	public static void setKey(String binding, boolean down) {
		KeyMapping mapping = mapping(binding);
		if (mapping == null) {
			return;
		}
		if (toggles(mapping)) {
			// In toggle mode a press flips the key and a release does nothing: press only to flip
			// it to the state wanted, so a held key stays down and a released one comes up.
			if (mapping.isDown() != down) {
				mapping.setDown(true);
			}
		} else {
			mapping.setDown(down);
		}
	}

	/** The player's options have this key in toggle mode (toggle sprint, sneak, attack or use). */
	private static boolean toggles(KeyMapping mapping) {
		var o = Minecraft.getInstance().options;
		return mapping == o.keySprint && o.toggleSprint().get() || mapping == o.keyShift && o.toggleCrouch().get()
			|| mapping == o.keyAttack && o.toggleAttack().get() || mapping == o.keyUse && o.toggleUse().get();
	}

	/** One press of {@code binding}, as the game counts clicks (attack, use, hotbar...). */
	public static void clickKey(String binding) {
		KeyMapping mapping = mapping(binding);
		if (mapping != null) {
			KeyMappingAccessor acc = (KeyMappingAccessor) mapping;
			acc.hb$setClickCount(acc.hb$clickCount() + 1);
		}
	}

	public static boolean keyDown(String binding) {
		KeyMapping mapping = mapping(binding);
		return mapping != null && mapping.isDown();
	}

	private static KeyMapping mapping(String binding) {
		synchronized (BY_NAME) {
			if (BY_NAME.isEmpty()) {
				for (KeyMapping m : Minecraft.getInstance().options.keyMappings) {
					BY_NAME.put(m.getName(), m);
				}
			}
			return BY_NAME.get(binding);
		}
	}

	/**
	 * Whether automation may drive the player here: singleplayer (the integrated server, LAN host
	 * included), a server running on this computer, or a server where the player has owner-level
	 * operator permission, i.e. a server they run. Nowhere else (spec, scope: singleplayer and
	 * servers you own).
	 */
	public static boolean ownsWorld() {
		Minecraft mc = Minecraft.getInstance();
		if (mc.player == null) {
			return false;
		}
		return mc.hasSingleplayerServer() || onThisComputer(mc)
			|| mc.player.permissions().hasPermission(Permissions.COMMANDS_OWNER);
	}

	/**
	 * Connected to a server on this computer: the connection's own remote address is a loopback one
	 * (127.0.0.0/8 or ::1), whatever address was typed in. A LAN address doesn't count: that can be
	 * anyone's computer.
	 */
	private static boolean onThisComputer(Minecraft mc) {
		ClientPacketListener listener = mc.getConnection();
		return listener != null && isLoopback(listener.getConnection().getRemoteAddress());
	}

	static boolean isLoopback(SocketAddress address) {
		return address instanceof InetSocketAddress inet && inet.getAddress() != null && inet.getAddress().isLoopbackAddress();
	}
}
