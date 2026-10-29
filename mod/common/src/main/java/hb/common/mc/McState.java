package hb.common.mc;

import com.mojang.blaze3d.platform.Window;
import hb.common.mc.mixin.MultiPlayerGameModeAccessor;
import hb.proto.v1.DamageTaken;
import hb.proto.v1.Death;
import hb.proto.v1.DimensionChange;
import hb.proto.v1.Effect;
import hb.proto.v1.GameEvent;
import hb.proto.v1.HitResult;
import hb.proto.v1.NearbyEntity;
import hb.proto.v1.Respawn;
import hb.proto.v1.Tick;
import hb.proto.v1.TickContext;
import hb.proto.v1.TickFlag;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.client.multiplayer.PlayerInfo;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceKey;
import net.minecraft.tags.FluidTags;
import net.minecraft.world.damagesource.DamageType;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.InventoryMenu;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.LightLayer;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.Vec3;

import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** Reads per-tick game state into hb.proto messages, and derives events from tick-to-tick changes. */
public final class McState {
	public static final double ENTITY_RANGE = 16;
	public static final int MAX_ENTITIES = 48;
	private static final EquipmentSlot[] ARMOR = {EquipmentSlot.FEET, EquipmentSlot.LEGS, EquipmentSlot.CHEST, EquipmentSlot.HEAD};
	private static final Map<ResourceKey<Level>, String> DIMENSIONS = new ConcurrentHashMap<>();

	// Polled-event state (client thread).
	private static LocalPlayer lastPlayer;
	private static ResourceKey<Level> lastDimension;
	private static float lastHealth;
	private static boolean lastDead;
	private static String damageType = "";
	private static int damageAttackerId = -1;
	private static String damageAttackerType = "";
	private static long damageTick = Long.MIN_VALUE;

	private McState() {
	}

	public static boolean inWorld() {
		Minecraft mc = Minecraft.getInstance();
		return mc.player != null && mc.level != null && mc.gameMode != null;
	}

	/** Focused and not minimized. */
	public static boolean windowFocused() {
		Window window = Minecraft.getInstance().getWindow();
		return window.isFocused() && !window.isIconified() && !window.isMinimized();
	}

	public static GameType gameMode() {
		MultiPlayerGameMode gameMode = Minecraft.getInstance().gameMode;
		return gameMode == null ? GameType.SURVIVAL : gameMode.getPlayerMode();
	}

	public static String gameModeName() {
		return gameMode().getName();
	}

	public static boolean creativeOrSpectator() {
		GameType mode = gameMode();
		return mode == GameType.CREATIVE || mode == GameType.SPECTATOR;
	}

	/** The view is the player's own (false while the camera sits on another entity). */
	public static boolean cameraOnPlayer() {
		Minecraft mc = Minecraft.getInstance();
		return mc.getCameraEntity() == null || mc.getCameraEntity() == mc.player;
	}

	public static int fps() {
		return Minecraft.getInstance().getFps();
	}

	public static String dimension(Level level) {
		return DIMENSIONS.computeIfAbsent(level.dimension(), k -> k.identifier().toString());
	}

	public static String menuId(AbstractContainerMenu menu) {
		if (menu instanceof InventoryMenu) {
			return "minecraft:inventory"; // the player inventory has no menu type
		}
		try {
			return BuiltInRegistries.MENU.getKey(menu.getType()).toString();
		} catch (UnsupportedOperationException e) {
			return menu.getClass().getName();
		}
	}

	/** Player state, inputs as the game sees them, crosshair, items, nearby entities and context. */
	public static Tick tick(long keysDown) {
		Minecraft mc = Minecraft.getInstance();
		LocalPlayer p = mc.player;
		ClientLevel level = mc.level;
		Vec3 v = p.getDeltaMovement();
		ItemStack main = p.getMainHandItem();
		Tick.Builder t = Tick.newBuilder()
			.setX(p.getX()).setY(p.getY()).setZ(p.getZ())
			.setVx((float) v.x).setVy((float) v.y).setVz((float) v.z)
			.setYaw(p.getYRot()).setPitch(p.getXRot())
			.setFlags(flags(p))
			.setKeysDown(keysDown)
			.setSlot(p.getInventory().getSelectedSlot())
			.setHeldItem(main.isEmpty() ? "" : McProto.itemId(main.getItem()))
			.setAttackCooldown(p.getAttackStrengthScale(0))
			.setHealth(p.getHealth())
			.setFood(p.getFoodData().getFoodLevel())
			.setSaturation(p.getFoodData().getSaturationLevel())
			.setAir(p.getAirSupply())
			.setFallDistance((float) p.fallDistance)
			.setFps(mc.getFps())
			.setMainHand(McProto.stack(main))
			.setOffHand(McProto.stack(p.getOffhandItem()));
		for (EquipmentSlot slot : ARMOR) {
			t.addArmor(McProto.stack(p.getItemBySlot(slot)));
		}
		t.setHit(hit(mc, level));
		MultiPlayerGameMode gameMode = mc.gameMode;
		if (gameMode.isDestroying()) {
			MultiPlayerGameModeAccessor acc = (MultiPlayerGameModeAccessor) gameMode;
			t.setDestroyProgress(Math.min(acc.hb$destroyProgress(), 1)).setBreakingPos(McProto.pos(acc.hb$destroyBlockPos()));
		}
		for (MobEffectInstance effect : p.getActiveEffects()) {
			t.addEffects(Effect.newBuilder().setId(McProto.id(effect.getEffect())).setAmplifier(effect.getAmplifier()).setDurationTicks(effect.getDuration()));
		}
		addEntities(t, p, level);
		t.setContext(context(mc, p, level));
		return t.build();
	}

	private static int flags(LocalPlayer p) {
		int f = 0;
		if (p.onGround()) f |= TickFlag.TICK_FLAG_ON_GROUND_VALUE;
		if (p.isSprinting()) f |= TickFlag.TICK_FLAG_SPRINTING_VALUE;
		if (p.isCrouching()) f |= TickFlag.TICK_FLAG_CROUCHING_VALUE;
		if (p.isSwimming()) f |= TickFlag.TICK_FLAG_SWIMMING_VALUE;
		if (p.isFallFlying()) f |= TickFlag.TICK_FLAG_GLIDING_VALUE;
		if (p.horizontalCollision) f |= TickFlag.TICK_FLAG_H_COLLISION_VALUE;
		if (p.verticalCollision) f |= TickFlag.TICK_FLAG_V_COLLISION_VALUE;
		if (p.verticalCollision && !p.verticalCollisionBelow) f |= TickFlag.TICK_FLAG_V_COLLISION_UP_VALUE;
		if (p.isInWater()) f |= TickFlag.TICK_FLAG_IN_WATER_VALUE;
		if (p.isInLava()) f |= TickFlag.TICK_FLAG_IN_LAVA_VALUE;
		if (p.onClimbable()) f |= TickFlag.TICK_FLAG_ON_CLIMBABLE_VALUE;
		if (p.isPassenger()) f |= TickFlag.TICK_FLAG_PASSENGER_VALUE;
		if (p.isUsingItem()) f |= TickFlag.TICK_FLAG_USING_ITEM_VALUE;
		if (p.isBlocking()) f |= TickFlag.TICK_FLAG_BLOCKING_VALUE;
		if (p.isEyeInFluid(FluidTags.WATER)) f |= TickFlag.TICK_FLAG_EYES_IN_WATER_VALUE;
		return f;
	}

	private static HitResult hit(Minecraft mc, ClientLevel level) {
		net.minecraft.world.phys.HitResult hr = mc.hitResult;
		HitResult.Builder out = HitResult.newBuilder();
		if (hr == null) {
			return out.build();
		}
		out.setLocation(McProto.vec(hr.getLocation()));
		if (hr instanceof BlockHitResult block && hr.getType() == net.minecraft.world.phys.HitResult.Type.BLOCK) {
			out.setType(HitResult.Type.BLOCK)
				.setBlockPos(McProto.pos(block.getBlockPos()))
				.setFace(McProto.direction(block.getDirection()))
				.setBlock(McProto.blockId(level.getBlockState(block.getBlockPos())));
		} else if (hr instanceof EntityHitResult entity) {
			out.setType(HitResult.Type.ENTITY)
				.setEntityId(entity.getEntity().getId())
				.setEntityType(McProto.entityType(entity.getEntity()));
		}
		return out.build();
	}

	private static void addEntities(Tick.Builder t, LocalPlayer p, ClientLevel level) {
		AABB box = p.getBoundingBox().inflate(ENTITY_RANGE);
		List<Entity> nearby = level.getEntities(p, box, e -> e.distanceToSqr(p) <= ENTITY_RANGE * ENTITY_RANGE);
		if (nearby.size() > MAX_ENTITIES) {
			nearby.sort(Comparator.comparingDouble(e -> e.distanceToSqr(p)));
		}
		int n = Math.min(nearby.size(), MAX_ENTITIES);
		for (int i = 0; i < n; i++) {
			Entity e = nearby.get(i);
			Vec3 ev = e.getDeltaMovement();
			t.addEntities(NearbyEntity.newBuilder()
				.setId(e.getId())
				.setType(McProto.entityType(e))
				.setDx((float) (e.getX() - p.getX())).setDy((float) (e.getY() - p.getY())).setDz((float) (e.getZ() - p.getZ()))
				.setVx((float) ev.x).setVy((float) ev.y).setVz((float) ev.z)
				.setHealth(e instanceof LivingEntity living ? living.getHealth() : 0)
				.setYaw(e.getYRot()));
		}
	}

	private static TickContext context(Minecraft mc, LocalPlayer p, ClientLevel level) {
		BlockPos feet = p.blockPosition();
		TickContext.Builder c = TickContext.newBuilder()
			.setDimension(dimension(level))
			.setBiome(McProto.id(level.getBiome(feet)))
			.setBlockLight(level.getBrightness(LightLayer.BLOCK, feet))
			.setSkyLight(level.getBrightness(LightLayer.SKY, feet))
			.setDayTime(level.getDefaultClockTime())
			.setScreen(McScreens.name(mc.gui.screen()));
		if (!mc.isLocalServer() && mc.getConnection() != null) {
			PlayerInfo info = mc.getConnection().getPlayerInfo(p.getUUID());
			if (info != null) {
				c.setPingMs(info.getLatency());
			}
		}
		return c.build();
	}

	// --- Events derived from state changes ------------------------------------------------------

	/** From ClientPacketListener.handleDamageEvent: remembers the source for the next health drop. */
	public static void onDamageEvent(int entityId, Holder<DamageType> type, int causeId) {
		Minecraft mc = Minecraft.getInstance();
		if (mc.player == null || entityId != mc.player.getId()) {
			return;
		}
		damageType = McProto.id(type);
		Entity cause = causeId >= 0 && mc.level != null ? mc.level.getEntity(causeId) : null;
		damageAttackerId = cause == null ? -1 : cause.getId();
		damageAttackerType = cause == null ? "" : McProto.entityType(cause);
		damageTick = McHooks.clientTicks();
	}

	/** Damage taken, death, respawn and dimension change, from comparing this tick with the last. */
	static void pollEvents(long nanoTime) {
		Minecraft mc = Minecraft.getInstance();
		LocalPlayer p = mc.player;
		ResourceKey<Level> dim = mc.level.dimension();
		if (p != lastPlayer) {
			if (lastPlayer != null) {
				if (lastDead) {
					McHooks.emit(nanoTime, GameEvent.newBuilder().setRespawn(Respawn.getDefaultInstance()).build());
				}
				if (lastDimension != null && dim != lastDimension) {
					McHooks.emit(nanoTime, GameEvent.newBuilder().setDimensionChange(DimensionChange.newBuilder()
						.setFromDimension(DIMENSIONS.computeIfAbsent(lastDimension, k -> k.identifier().toString()))
						.setToDimension(dimension(mc.level))).build());
				}
			}
			lastPlayer = p;
			lastDimension = dim;
			lastHealth = p.getHealth();
			lastDead = p.isDeadOrDying();
			return;
		}
		boolean recentSource = McHooks.clientTicks() - damageTick <= 2;
		float health = p.getHealth();
		if (health < lastHealth - 1e-4f) {
			DamageTaken.Builder taken = DamageTaken.newBuilder().setAmount(lastHealth - health);
			if (recentSource) {
				taken.setSource(damageType).setAttackerId(damageAttackerId).setAttackerType(damageAttackerType);
			}
			McHooks.emit(nanoTime, GameEvent.newBuilder().setDamageTaken(taken).build());
		}
		lastHealth = health;
		boolean dead = p.isDeadOrDying();
		if (dead && !lastDead) {
			McHooks.emit(nanoTime, GameEvent.newBuilder().setDeath(Death.newBuilder().setCause(recentSource ? damageType : "")).build());
		}
		lastDead = dead;
		lastDimension = dim;
	}

	static void forgetPlayer() {
		lastPlayer = null;
		lastDimension = null;
	}
}
