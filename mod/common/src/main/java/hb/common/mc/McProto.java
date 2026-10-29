package hb.common.mc;

import hb.proto.v1.Enchantment;
import hb.proto.v1.Hand;
import net.minecraft.commands.arguments.blocks.BlockStateParser;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.sounds.SoundEvent;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.enchantment.ItemEnchantments;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Minecraft values to hb.proto values. Registry ids and block-state strings are cached per
 * instance (items, blocks, states and holders are canonical objects), so a tick doesn't rebuild
 * the same strings.
 */
public final class McProto {
	private static final Map<Item, String> ITEMS = new ConcurrentHashMap<>();
	private static final Map<Block, String> BLOCKS = new ConcurrentHashMap<>();
	private static final Map<BlockState, String> STATES = new ConcurrentHashMap<>();
	private static final Map<EntityType<?>, String> ENTITIES = new ConcurrentHashMap<>();
	private static final Map<Holder<?>, String> HOLDERS = new ConcurrentHashMap<>();

	private McProto() {
	}

	public static hb.proto.v1.BlockPos pos(BlockPos p) {
		return hb.proto.v1.BlockPos.newBuilder().setX(p.getX()).setY(p.getY()).setZ(p.getZ()).build();
	}

	public static hb.proto.v1.Vec3 vec(Vec3 v) {
		return hb.proto.v1.Vec3.newBuilder().setX(v.x).setY(v.y).setZ(v.z).build();
	}

	public static hb.proto.v1.Direction direction(Direction d) {
		if (d == null) {
			return hb.proto.v1.Direction.DIRECTION_UNSPECIFIED;
		}
		return switch (d) {
			case DOWN -> hb.proto.v1.Direction.DIRECTION_DOWN;
			case UP -> hb.proto.v1.Direction.DIRECTION_UP;
			case NORTH -> hb.proto.v1.Direction.DIRECTION_NORTH;
			case SOUTH -> hb.proto.v1.Direction.DIRECTION_SOUTH;
			case WEST -> hb.proto.v1.Direction.DIRECTION_WEST;
			case EAST -> hb.proto.v1.Direction.DIRECTION_EAST;
		};
	}

	public static Hand hand(InteractionHand hand) {
		return hand == InteractionHand.OFF_HAND ? Hand.HAND_OFF : Hand.HAND_MAIN;
	}

	public static String itemId(Item item) {
		return ITEMS.computeIfAbsent(item, i -> BuiltInRegistries.ITEM.getKey(i).toString());
	}

	public static String blockId(BlockState state) {
		return BLOCKS.computeIfAbsent(state.getBlock(), b -> BuiltInRegistries.BLOCK.getKey(b).toString());
	}

	/** Full block state, e.g. "minecraft:oak_stairs[facing=north,half=bottom,...]". Never block-entity data. */
	public static String state(BlockState state) {
		return STATES.computeIfAbsent(state, BlockStateParser::serialize);
	}

	public static String entityType(Entity entity) {
		return ENTITIES.computeIfAbsent(entity.getType(), t -> EntityType.getKey(t).toString());
	}

	/** Registry id of a holder ("minecraft:efficiency"), or "" for direct holders. */
	public static String id(Holder<?> holder) {
		return HOLDERS.computeIfAbsent(holder, h -> h.unwrapKey().map(k -> k.identifier().toString()).orElse(""));
	}

	/**
	 * A sound's id. Sounds a server or command plays by name arrive as direct holders with no
	 * registry key, so the id comes from the event itself.
	 */
	public static String sound(Holder<SoundEvent> sound) {
		return sound.value().location().toString();
	}

	/** Id, count, durability and enchantments only. Custom names and other text are never copied. */
	public static hb.proto.v1.ItemStack stack(ItemStack stack) {
		hb.proto.v1.ItemStack.Builder out = hb.proto.v1.ItemStack.newBuilder();
		if (stack.isEmpty()) {
			return out.build();
		}
		out.setId(itemId(stack.getItem())).setCount(stack.getCount());
		if (stack.isDamageableItem()) {
			out.setDamage(stack.getDamageValue()).setMaxDamage(stack.getMaxDamage());
		}
		ItemEnchantments enchantments = stack.getEnchantments();
		if (!enchantments.isEmpty()) {
			for (var entry : enchantments.entrySet()) {
				out.addEnchantments(Enchantment.newBuilder().setId(id(entry.getKey())).setLevel(entry.getIntValue()));
			}
		}
		return out.build();
	}
}
