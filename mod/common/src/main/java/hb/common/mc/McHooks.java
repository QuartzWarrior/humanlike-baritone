package hb.common.mc;

import hb.common.mc.mixin.MouseHandlerAccessor;
import hb.common.mc.mixin.MultiPlayerGameModeAccessor;
import hb.proto.v1.BlockPlaced;
import hb.proto.v1.ClickType;
import hb.proto.v1.DestroyAborted;
import hb.proto.v1.DestroyStarted;
import hb.proto.v1.GameEvent;
import hb.proto.v1.ItemDropped;
import hb.proto.v1.ItemPickedUp;
import hb.proto.v1.PositionCorrected;
import hb.proto.v1.ResultTaken;
import hb.proto.v1.SlotClick;
import hb.proto.v1.SoundHeard;
import net.minecraft.client.Minecraft;
import net.minecraft.client.MouseHandler;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.Holder;
import net.minecraft.sounds.SoundEvent;
import net.minecraft.sounds.SoundSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.FurnaceResultSlot;
import net.minecraft.world.inventory.ItemCombinerMenu;
import net.minecraft.world.inventory.MerchantResultSlot;
import net.minecraft.world.inventory.ResultSlot;
import net.minecraft.world.inventory.SmithingMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.inventory.StonecutterMenu;
import net.minecraft.world.inventory.AnvilMenu;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.context.BlockPlaceContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;
import org.lwjgl.glfw.GLFW;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Entry points for the hb.common.mc mixins. Each hook turns Minecraft state into hb.proto values
 * and hands them to every registered {@link McInputListener}. All hooks run on the client thread
 * and return immediately when nobody listens.
 */
public final class McHooks {
	private static final List<McInputListener> LISTENERS = new CopyOnWriteArrayList<>();
	private static final double SOUND_RANGE = 32;

	// Mouse: values captured before the game applies a frame's movement.
	private static double frameDx;
	private static double frameDy;
	private static float yawBefore;
	private static float pitchBefore;
	private static boolean turning;

	// Block breaking, for BlockBroken.ticks_taken and face.
	private static long clientTicks;
	private static BlockPos destroyPos;
	private static Direction destroyFace;
	private static long destroyStartTick;
	private static boolean wasDestroying;
	private static BlockPos oldDestroyPos;
	private static float oldDestroyProgress;

	// The block hit that started the current use, for BlockPlaced.clicked_pos.
	private static BlockHitResult lastUseHit;

	// LocalPlayer.drop: the stack before it leaves the hotbar.
	private static ItemStack dropping = ItemStack.EMPTY;
	private static boolean droppingAll;

	private static Vec3 positionBeforeCorrection;

	private McHooks() {
	}

	public static void addListener(McInputListener listener) {
		LISTENERS.add(listener);
	}

	public static void removeListener(McInputListener listener) {
		LISTENERS.remove(listener);
	}

	static boolean listening() {
		return !LISTENERS.isEmpty();
	}

	static void clientTick() {
		clientTicks++;
	}

	static long clientTicks() {
		return clientTicks;
	}

	static Direction destroyFace(BlockPos pos) {
		return pos.equals(destroyPos) ? destroyFace : null;
	}

	static long destroyTicks(BlockPos pos) {
		return pos.equals(destroyPos) ? clientTicks - destroyStartTick : 0;
	}

	static void emit(long nanoTime, GameEvent event) {
		for (McInputListener l : LISTENERS) {
			l.onGameEvent(nanoTime, event);
		}
	}

	// --- MouseHandler ---------------------------------------------------------------------------

	public static void beforeAccumulatedMovement(MouseHandler handler) {
		McInput.injectFrame(handler); // first, so listeners see injected motion as the frame's input
		if (!listening()) {
			return;
		}
		MouseHandlerAccessor acc = (MouseHandlerAccessor) handler;
		frameDx = acc.hb$accumulatedDX();
		frameDy = acc.hb$accumulatedDY();
		Minecraft mc = Minecraft.getInstance();
		turning = handler.isMouseGrabbed() && mc.player != null && mc.isWindowActive();
		if (turning) {
			yawBefore = mc.player.getYRot();
			pitchBefore = mc.player.getXRot();
		}
	}

	public static void afterAccumulatedMovement() {
		if (!listening() || frameDx == 0 && frameDy == 0) {
			return;
		}
		long now = System.nanoTime();
		Minecraft mc = Minecraft.getInstance();
		if (turning && mc.player != null) {
			float dYaw = mc.player.getYRot() - yawBefore;
			float dPitch = mc.player.getXRot() - pitchBefore;
			for (McInputListener l : LISTENERS) {
				l.onMouseTurn(now, frameDx, frameDy, dYaw, dPitch);
			}
		} else {
			for (McInputListener l : LISTENERS) {
				l.onCursorMove(now, frameDx, frameDy);
			}
		}
		frameDx = 0;
		frameDy = 0;
	}

	static void guiCursor(long nanoTime, double x, double y) {
		for (McInputListener l : LISTENERS) {
			l.onGuiCursor(nanoTime, x, y);
		}
	}

	public static void mouseButton(int button, int action, int mods) {
		if (!listening()) {
			return;
		}
		long now = System.nanoTime();
		boolean inGui = Minecraft.getInstance().gui.screen() != null;
		for (McInputListener l : LISTENERS) {
			l.onMouseButton(now, button, action == GLFW.GLFW_PRESS, mods, inGui);
		}
	}

	public static void scroll(double dx, double dy) {
		if (!listening()) {
			return;
		}
		long now = System.nanoTime();
		for (McInputListener l : LISTENERS) {
			l.onScroll(now, dx, dy);
		}
	}

	// --- KeyboardHandler ------------------------------------------------------------------------

	public static void key(int action, KeyEvent event) {
		if (!listening()) {
			return;
		}
		long now = System.nanoTime();
		String binding = null;
		boolean gui = false;
		boolean text = McScreens.textInputActive(McScreens.current());
		boolean recordable = !text;
		if (recordable) {
			binding = McKeys.bindingFor(event);
			gui = McKeys.isGuiKey(event.key());
			recordable = binding != null || gui;
		}
		for (McInputListener l : LISTENERS) {
			if (recordable) {
				l.onKey(now, event.key(), event.scancode(), action, event.modifiers(), binding, gui);
			} else {
				l.onPrivateKey(now, text);
			}
		}
	}

	// --- MultiPlayerGameMode --------------------------------------------------------------------

	public static void beforeStartDestroy(MultiPlayerGameMode gameMode) {
		MultiPlayerGameModeAccessor acc = (MultiPlayerGameModeAccessor) gameMode;
		wasDestroying = gameMode.isDestroying();
		oldDestroyPos = acc.hb$destroyBlockPos();
		oldDestroyProgress = acc.hb$destroyProgress();
	}

	public static void afterStartDestroy(MultiPlayerGameMode gameMode, BlockPos pos, Direction face) {
		BlockPos current = ((MultiPlayerGameModeAccessor) gameMode).hb$destroyBlockPos();
		boolean switched = wasDestroying && !oldDestroyPos.equals(pos);
		boolean started = gameMode.isDestroying() && current.equals(pos) && (!wasDestroying || switched);
		if (started || !gameMode.isDestroying()) {
			// Remember the face even for instant breaks, which never set isDestroying.
			destroyPos = pos.immutable();
			destroyFace = face;
			destroyStartTick = clientTicks;
		}
		if (!listening()) {
			return;
		}
		long now = System.nanoTime();
		if (switched) {
			emit(now, GameEvent.newBuilder().setDestroyAborted(DestroyAborted.newBuilder()
				.setPos(McProto.pos(oldDestroyPos)).setProgress(oldDestroyProgress)).build());
		}
		if (started) {
			Minecraft mc = Minecraft.getInstance();
			emit(now, GameEvent.newBuilder().setDestroyStarted(DestroyStarted.newBuilder()
				.setPos(McProto.pos(pos))
				.setFace(McProto.direction(face))
				.setBlock(McProto.blockId(mc.level.getBlockState(pos)))
				.setTool(mc.player.getMainHandItem().isEmpty() ? "" : McProto.itemId(mc.player.getMainHandItem().getItem()))).build());
		}
	}

	public static void beforeStopDestroy(MultiPlayerGameMode gameMode) {
		if (!listening() || !gameMode.isDestroying()) {
			return;
		}
		MultiPlayerGameModeAccessor acc = (MultiPlayerGameModeAccessor) gameMode;
		emit(System.nanoTime(), GameEvent.newBuilder().setDestroyAborted(DestroyAborted.newBuilder()
			.setPos(McProto.pos(acc.hb$destroyBlockPos())).setProgress(acc.hb$destroyProgress())).build());
	}

	public static void useItemOn(BlockHitResult hit) {
		lastUseHit = hit;
	}

	public static void containerInput(int containerId, int slotNum, int button, ContainerInput input, Player player) {
		if (!listening()) {
			return;
		}
		long now = System.nanoTime();
		AbstractContainerMenu menu = player.containerMenu;
		if (menu.containerId != containerId) {
			return; // the game ignores mismatched clicks too
		}
		Slot slot = slotNum >= 0 && slotNum < menu.slots.size() ? menu.slots.get(slotNum) : null;
		ItemStack inSlot = slot == null ? ItemStack.EMPTY : slot.getItem();
		String menuId = McState.menuId(menu);
		SlotClick click = SlotClick.newBuilder()
			.setSlotId(slotNum)
			.setButton(button)
			.setClickType(clickType(input))
			.setSlotItem(McProto.stack(inSlot))
			.setCarried(McProto.stack(menu.getCarried()))
			.setMenu(menuId)
			.setContainerId(containerId)
			.build();
		for (McInputListener l : LISTENERS) {
			l.onSlotClick(now, click);
		}
		ResultTaken.Kind kind = slot == null || inSlot.isEmpty() ? null : resultKind(menu, slot, slotNum);
		if (kind != null && input != ContainerInput.QUICK_CRAFT && input != ContainerInput.CLONE) {
			emit(now, GameEvent.newBuilder().setResultTaken(ResultTaken.newBuilder()
				.setKind(kind)
				.setResult(McProto.stack(inSlot))
				.setMenu(menuId)
				.setQuickMove(input == ContainerInput.QUICK_MOVE)).build());
		}
	}

	private static ResultTaken.Kind resultKind(AbstractContainerMenu menu, Slot slot, int slotNum) {
		if (slot instanceof ResultSlot) {
			return ResultTaken.Kind.CRAFT;
		}
		if (slot instanceof FurnaceResultSlot) {
			return ResultTaken.Kind.SMELT;
		}
		if (slot instanceof MerchantResultSlot) {
			return ResultTaken.Kind.TRADE;
		}
		if (menu instanceof ItemCombinerMenu combiner && slotNum == combiner.getResultSlot()) {
			if (menu instanceof AnvilMenu) {
				return ResultTaken.Kind.ANVIL;
			}
			return menu instanceof SmithingMenu ? ResultTaken.Kind.SMITH : ResultTaken.Kind.OTHER;
		}
		if (menu instanceof StonecutterMenu && slotNum == StonecutterMenu.RESULT_SLOT) {
			return ResultTaken.Kind.STONECUT;
		}
		return null;
	}

	private static ClickType clickType(ContainerInput input) {
		return switch (input) {
			case PICKUP -> ClickType.CLICK_TYPE_PICKUP;
			case QUICK_MOVE -> ClickType.CLICK_TYPE_QUICK_MOVE;
			case SWAP -> ClickType.CLICK_TYPE_SWAP;
			case CLONE -> ClickType.CLICK_TYPE_CLONE;
			case THROW -> ClickType.CLICK_TYPE_THROW;
			case QUICK_CRAFT -> ClickType.CLICK_TYPE_QUICK_CRAFT;
			case PICKUP_ALL -> ClickType.CLICK_TYPE_PICKUP_ALL;
		};
	}

	// --- BlockItem ------------------------------------------------------------------------------

	public static void blockPlaced(BlockPlaceContext context) {
		Minecraft mc = Minecraft.getInstance();
		if (!listening() || !context.getLevel().isClientSide() || context.getPlayer() != mc.player || mc.player == null) {
			return; // the integrated server places the same block on its own thread
		}
		BlockPos pos = context.getClickedPos();
		BlockPlaced.Builder placed = BlockPlaced.newBuilder()
			.setPos(McProto.pos(pos))
			.setClickedFace(McProto.direction(context.getClickedFace()))
			.setBlock(McProto.blockId(context.getLevel().getBlockState(pos)))
			.setHand(McProto.hand(context.getHand()))
			.setHit(McProto.vec(context.getClickLocation()));
		if (lastUseHit != null) {
			placed.setClickedPos(McProto.pos(lastUseHit.getBlockPos()));
		}
		emit(System.nanoTime(), GameEvent.newBuilder().setBlockPlaced(placed).build());
	}

	// --- LocalPlayer ----------------------------------------------------------------------------

	public static void beforeDrop(LocalPlayer player, boolean all) {
		ItemStack selected = player.getInventory().getSelectedItem();
		dropping = selected.isEmpty() ? ItemStack.EMPTY : selected.copyWithCount(all ? selected.getCount() : 1);
		droppingAll = all;
	}

	public static void afterDrop(boolean dropped) {
		if (dropped && listening() && !dropping.isEmpty()) {
			emit(System.nanoTime(), GameEvent.newBuilder().setItemDropped(ItemDropped.newBuilder()
				.setItem(McProto.stack(dropping)).setWholeStack(droppingAll)).build());
		}
		dropping = ItemStack.EMPTY;
	}

	// --- ClientPacketListener -------------------------------------------------------------------

	public static void itemTaken(int itemEntityId, int collectorId, int amount) {
		Minecraft mc = Minecraft.getInstance();
		if (!listening() || mc.player == null || collectorId != mc.player.getId()) {
			return;
		}
		if (mc.level.getEntity(itemEntityId) instanceof ItemEntity item) {
			emit(System.nanoTime(), GameEvent.newBuilder().setItemPickedUp(ItemPickedUp.newBuilder()
				.setItem(McProto.stack(item.getItem().copyWithCount(amount)))).build());
		}
	}

	public static void beforePositionPacket() {
		LocalPlayer player = Minecraft.getInstance().player;
		positionBeforeCorrection = player == null ? null : player.position();
	}

	public static void afterPositionPacket() {
		LocalPlayer player = Minecraft.getInstance().player;
		if (!listening() || player == null || positionBeforeCorrection == null) {
			return;
		}
		emit(System.nanoTime(), GameEvent.newBuilder().setPositionCorrected(PositionCorrected.newBuilder()
			.setBefore(McProto.vec(positionBeforeCorrection)).setAfter(McProto.vec(player.position()))).build());
	}

	public static void sound(Holder<SoundEvent> sound, SoundSource source, double x, double y, double z, float volume) {
		Minecraft mc = Minecraft.getInstance();
		if (!listening() || mc.player == null || !audible(source)) {
			return;
		}
		Vec3 eyes = mc.player.getEyePosition();
		double dx = x - eyes.x;
		double dy = y - eyes.y;
		double dz = z - eyes.z;
		if (dx * dx + dy * dy + dz * dz > SOUND_RANGE * SOUND_RANGE) {
			return;
		}
		emit(System.nanoTime(), GameEvent.newBuilder().setSoundHeard(SoundHeard.newBuilder()
			.setSound(McProto.sound(sound))
			.setCategory(source.getName())
			.setDx((float) dx).setDy((float) dy).setDz((float) dz)
			.setVolume(volume)).build());
	}

	public static void entitySound(int entityId, Holder<SoundEvent> sound, SoundSource source, float volume) {
		Minecraft mc = Minecraft.getInstance();
		Entity entity = mc.level == null ? null : mc.level.getEntity(entityId);
		if (entity != null) {
			sound(sound, source, entity.getX(), entity.getY(), entity.getZ(), volume);
		}
	}

	/** World sounds a player reacts to; music, ambience, weather, UI and voice chat are skipped. */
	private static boolean audible(SoundSource source) {
		return switch (source) {
			case BLOCKS, HOSTILE, NEUTRAL, PLAYERS, MASTER -> true;
			default -> false;
		};
	}
}
