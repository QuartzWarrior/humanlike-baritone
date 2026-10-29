package hb.common.mc;

import hb.proto.v1.BlockBroken;
import hb.proto.v1.ChatSent;
import hb.proto.v1.DamageDealt;
import hb.proto.v1.GameEvent;
import hb.proto.v1.Interaction;
import hb.proto.v1.ScreenClosed;
import hb.proto.v1.ScreenOpened;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientLifecycleEvents;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.message.v1.ClientSendMessageEvents;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.client.screen.v1.ScreenEvents;
import net.fabricmc.fabric.api.event.client.player.ClientPlayerBlockBreakEvents;
import net.fabricmc.fabric.api.event.player.AttackBlockCallback;
import net.fabricmc.fabric.api.event.player.AttackEntityCallback;
import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.fabricmc.fabric.api.event.player.UseEntityCallback;
import net.fabricmc.fabric.api.event.player.UseItemCallback;
import net.minecraft.client.Minecraft;
import net.minecraft.client.MouseHandler;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.effect.MobEffects;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * Registers the Fabric API events hb uses, once, and forwards them as neutral callbacks or
 * hb.proto GameEvents. Mods call {@link #init()} from their client initializer.
 */
public final class McEvents {
	/** Runs at the end of every client tick while a world is loaded, after polled events fire. */
	public interface TickListener {
		void onTick(long nanoTime);
	}

	private static final List<TickListener> TICK = new CopyOnWriteArrayList<>();
	private static final List<Runnable> DISCONNECT = new CopyOnWriteArrayList<>();
	private static final List<Runnable> STOPPING = new CopyOnWriteArrayList<>();
	private static boolean initialized;
	private static boolean inWorld;
	private static volatile boolean disconnecting;
	private static Screen openScreen;
	private static double cursorX = Double.NaN;
	private static double cursorY = Double.NaN;

	private McEvents() {
	}

	public static void onTick(TickListener listener) {
		TICK.add(listener);
	}

	/** Leaving a world or server; runs on the client thread once per visit. */
	public static void onDisconnect(Runnable listener) {
		DISCONNECT.add(listener);
	}

	/** The client is shutting down. */
	public static void onStopping(Runnable listener) {
		STOPPING.add(listener);
	}

	public static synchronized void init() {
		if (initialized) {
			return;
		}
		initialized = true;

		ClientTickEvents.END_CLIENT_TICK.register(mc -> {
			McHooks.clientTick();
			boolean present = McState.inWorld();
			if (!present) {
				disconnecting = false; // the world is gone; the next one starts clean
			}
			if (!present || disconnecting) {
				if (inWorld) {
					inWorld = false;
					McState.forgetPlayer();
					DISCONNECT.forEach(Runnable::run);
				}
				return;
			}
			inWorld = true;
			long now = System.nanoTime();
			McState.pollEvents(now);
			for (TickListener l : TICK) {
				l.onTick(now);
			}
		});
		// Fires on the Netty thread in singleplayer, possibly while the player still exists. It only
		// marks the visit as over; the tick handler above notifies listeners on the client thread,
		// exactly once, so no tick can start a new session in between.
		ClientPlayConnectionEvents.DISCONNECT.register((handler, mc) -> disconnecting = true);
		ClientLifecycleEvents.CLIENT_STOPPING.register(mc -> STOPPING.forEach(Runnable::run));

		// Chat and commands: a flag only. The text parameter is ignored on purpose.
		ClientSendMessageEvents.CHAT.register(text -> chatSent(false));
		ClientSendMessageEvents.COMMAND.register(text -> chatSent(true));

		ClientPlayerBlockBreakEvents.AFTER.register((level, player, pos, state) -> {
			if (!McHooks.listening()) {
				return;
			}
			ItemStack tool = player.getMainHandItem();
			McHooks.emit(System.nanoTime(), GameEvent.newBuilder().setBlockBroken(BlockBroken.newBuilder()
				.setPos(McProto.pos(pos))
				.setBlock(McProto.blockId(state))
				.setTool(tool.isEmpty() ? "" : McProto.itemId(tool.getItem()))
				.setTicksTaken((int) McHooks.destroyTicks(pos))
				.setFace(McProto.direction(McHooks.destroyFace(pos)))).build());
		});

		AttackEntityCallback.EVENT.register((player, level, hand, entity, hit) -> {
			if (level.isClientSide() && McHooks.listening()) {
				McHooks.emit(System.nanoTime(), GameEvent.newBuilder().setDamageDealt(damageDealt(player, entity)).build());
			}
			return InteractionResult.PASS;
		});
		AttackBlockCallback.EVENT.register((player, level, hand, pos, face) -> {
			if (level.isClientSide() && McHooks.listening()) {
				interaction(Interaction.newBuilder().setKind(Interaction.Kind.ATTACK_BLOCK).setHand(McProto.hand(hand))
					.setPos(McProto.pos(pos)).setFace(McProto.direction(face))
					.setBlock(McProto.blockId(level.getBlockState(pos))).setItem(item(player, hand)));
			}
			return InteractionResult.PASS;
		});
		UseBlockCallback.EVENT.register((player, level, hand, hit) -> {
			if (level.isClientSide() && McHooks.listening()) {
				interaction(Interaction.newBuilder().setKind(Interaction.Kind.USE_BLOCK).setHand(McProto.hand(hand))
					.setPos(McProto.pos(hit.getBlockPos())).setFace(McProto.direction(hit.getDirection()))
					.setBlock(McProto.blockId(level.getBlockState(hit.getBlockPos()))).setItem(item(player, hand)));
			}
			return InteractionResult.PASS;
		});
		UseItemCallback.EVENT.register((player, level, hand) -> {
			if (level.isClientSide() && McHooks.listening()) {
				interaction(Interaction.newBuilder().setKind(Interaction.Kind.USE_ITEM).setHand(McProto.hand(hand)).setItem(item(player, hand)));
			}
			return InteractionResult.PASS;
		});
		UseEntityCallback.EVENT.register((player, level, hand, entity, hit) -> {
			if (level.isClientSide() && McHooks.listening()) {
				interaction(Interaction.newBuilder().setKind(Interaction.Kind.USE_ENTITY).setHand(McProto.hand(hand))
					.setEntityId(entity.getId()).setEntityType(McProto.entityType(entity)).setItem(item(player, hand)));
			}
			return InteractionResult.PASS;
		});

		ScreenEvents.AFTER_INIT.register((mc, screen, width, height) -> {
			if (screen == openScreen) {
				return; // re-init after a resize, not a new screen
			}
			closeOpenScreen();
			openScreen = screen;
			cursorX = Double.NaN;
			cursorY = Double.NaN;
			if (McHooks.listening()) {
				ScreenOpened.Builder opened = ScreenOpened.newBuilder().setScreen(McScreens.name(screen)).setGuiWidth(width).setGuiHeight(height);
				if (screen instanceof AbstractContainerScreen<?> container) {
					opened.setMenu(McState.menuId(container.getMenu()));
				}
				McHooks.emit(System.nanoTime(), GameEvent.newBuilder().setScreenOpened(opened).build());
			}
			ScreenEvents.remove(screen).register(removed -> {
				if (removed == openScreen) {
					closeOpenScreen();
				}
			});
			if (screen instanceof AbstractContainerScreen<?>) {
				ScreenEvents.afterExtract(screen).register((s, graphics, mouseX, mouseY, delta) -> guiCursor());
			}
		});
	}

	private static void closeOpenScreen() {
		if (openScreen != null && McHooks.listening()) {
			McHooks.emit(System.nanoTime(), GameEvent.newBuilder().setScreenClosed(ScreenClosed.newBuilder().setScreen(McScreens.name(openScreen))).build());
		}
		openScreen = null;
	}

	/** Per frame in container screens: the cursor in GUI-scaled coordinates, when it moved. */
	private static void guiCursor() {
		Minecraft mc = Minecraft.getInstance();
		MouseHandler mouse = mc.mouseHandler;
		double x = mouse.getScaledXPos(mc.getWindow());
		double y = mouse.getScaledYPos(mc.getWindow());
		if (x == cursorX && y == cursorY) {
			return;
		}
		cursorX = x;
		cursorY = y;
		McHooks.guiCursor(System.nanoTime(), x, y);
	}

	private static void chatSent(boolean command) {
		if (McHooks.listening()) {
			McHooks.emit(System.nanoTime(), GameEvent.newBuilder().setChatSent(ChatSent.newBuilder().setIsCommand(command)).build());
		}
	}

	private static void interaction(Interaction.Builder interaction) {
		McHooks.emit(System.nanoTime(), GameEvent.newBuilder().setInteraction(interaction).build());
	}

	private static String item(Player player, InteractionHand hand) {
		ItemStack stack = player.getItemInHand(hand);
		return stack.isEmpty() ? "" : McProto.itemId(stack.getItem());
	}

	private static DamageDealt damageDealt(Player player, Entity target) {
		float cooldown = player.getAttackStrengthScale(0.5f);
		// The vanilla critical-hit conditions, evaluated before the swing resets the cooldown.
		boolean critical = cooldown > 0.9f && player.fallDistance > 0 && !player.onGround() && !player.onClimbable()
			&& !player.isInWater() && !player.hasEffect(MobEffects.BLINDNESS) && !player.isPassenger()
			&& !player.isSprinting() && target instanceof LivingEntity;
		return DamageDealt.newBuilder()
			.setEntityId(target.getId())
			.setEntityType(McProto.entityType(target))
			.setCritical(critical)
			.setSprinting(player.isSprinting())
			.setAttackCooldown(cooldown)
			.build();
	}
}
