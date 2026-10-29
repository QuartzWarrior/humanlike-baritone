package hb.executor.decide;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * The Stage 1 mining-order ranker's features (spec, Component B: decision models, mine.area_clear,
 * mine.tunnel, mine.strip): for every block the executor could break next, the 20 numbers
 * {@link MiningOrderModel} scores, computed exactly as the pipeline computed them for training
 * (hbpipe.models.decision.mining_order: _step, _features, block_class). The model only knows the
 * player through those numbers, so any drift here (a sign, a centre vs a face, a block class)
 * would make the bot choose like nobody.
 *
 * <p>Conventions are the pipeline's, which are Minecraft's: y up, block (x, y, z) spans x..x+1 and
 * its centre is (x + 0.5, y + 0.5, z + 0.5); yaw 0 looks along +z, 90 along -x, pitch +90 looks
 * straight down; the eye is always {@link #EYE_HEIGHT} above the stand position (also when
 * sneaking: training used the standing height). Block categories come from block ids by name, as
 * the pipeline's do (hbpipe.label.blocks), not from the game's collision shapes, so "passable"
 * here can differ from the game's (snow layers and cobwebs are passable, leaves are solid).
 *
 * <p>No Minecraft classes: the game is reached through a {@link World} the caller adapts.
 */
public final class BreakFeatures {
	/** The features in the model's order (mining_order.FEATURES). */
	public static final List<String> NAMES = List.of(
		"dist", "angle", "dyaw", "dpitch", "rel_y",
		"dy_prev", "dh_prev", "along_sweep", "manhattan_prev", "adjacent_prev",
		"same_layer", "same_column", "same_row", "open_faces", "below_feet",
		"under_stand_level", "block_class", "falls", "air_neighbours_beyond", "skipped_before");
	public static final int COUNT = NAMES.size();
	public static final double EYE_HEIGHT = 1.62;
	/** Blocks, eye to candidate centre. */
	public static final double REACH = 5.0;
	static final double FEET_EPS = 0.001;

	/** block_class codes (mining_order.BLOCK_CLASSES), compared as numbers by the trees. */
	public static final int STONE = 0;
	public static final int SOIL = 1;
	public static final int ORE = 2;
	public static final int WOOD = 3;
	public static final int OTHER = 4;

	private static final int[][] NEIGHBOURS = {{1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}, {0, 0, 1}, {0, 0, -1}};

	private BreakFeatures() {
	}

	/**
	 * What the features read of the world. Training saw the job's starting volume (the breaks'
	 * bounding box grown by 2, as far as the recorder's 9x9x9 cube around the player ever saw it)
	 * with the job's own breaks turned to air; nothing else that changed during the job. An
	 * adapter that wants the bot to choose from the same kind of set answers like that volume.
	 */
	public interface World {
		/**
		 * The block's id or full state ("deepslate", "minecraft:oak_log[axis=y]"), or null where it
		 * isn't known (never seen, not loaded, outside the job's volume). Unknown blocks are never
		 * candidates and, as a neighbour, count as closed.
		 */
		String block(int x, int y, int z);
	}

	/** A block the executor could break now, with its features in {@link #NAMES} order. */
	public record Candidate(int x, int y, int z, double[] features) {
	}

	/**
	 * What a job remembers between breaks, updated by {@link #broke} the way the pipeline's _job
	 * updates its history: every break, whether or not the ranker chose it.
	 */
	public static final class Job {
		private final Set<Pos> broken = new HashSet<>();
		private final Set<Pos> skipped = new HashSet<>();
		private boolean started;
		private int prevX;
		private int prevY;
		private int prevZ;
		// Sign vector of the last break-to-break move that changed x or z; 0, 0 before one.
		private int lastX;
		private int lastZ;

		/** An independent copy: to look one break ahead without changing this job. */
		public Job copy() {
			Job c = new Job();
			c.broken.addAll(broken);
			c.skipped.addAll(skipped);
			c.started = started;
			c.prevX = prevX;
			c.prevY = prevY;
			c.prevZ = prevZ;
			c.lastX = lastX;
			c.lastZ = lastZ;
			return c;
		}

		/** True once a block was broken: the ranker chooses from the second break on. */
		public boolean started() {
			return started;
		}

		/** Broken in this job: air to the features, whatever the world says (a refill isn't tracked). */
		public boolean broken(int x, int y, int z) {
			return broken.contains(new Pos(x, y, z));
		}

		/**
		 * Call after each break of the job, with the candidates {@link BreakFeatures#candidates} gave for this
		 * step (null for the first break, or if none were computed). Blocks next to the previous
		 * break that weren't the one broken become skipped_before.
		 */
		public void broke(int x, int y, int z, List<Candidate> candidates) {
			if (started) {
				if (candidates != null) {
					for (Candidate c : candidates) {
						int m = Math.abs(c.x() - prevX) + Math.abs(c.y() - prevY) + Math.abs(c.z() - prevZ);
						if (m == 1 && (c.x() != x || c.y() != y || c.z() != z)) {
							skipped.add(new Pos(c.x(), c.y(), c.z()));
						}
					}
				}
				int dx = x - prevX;
				int dz = z - prevZ;
				if (dx != 0 || dz != 0) {
					lastX = Integer.signum(dx);
					lastZ = Integer.signum(dz);
				}
			}
			broken.add(new Pos(x, y, z));
			prevX = x;
			prevY = y;
			prevZ = z;
			started = true;
		}
	}

	/**
	 * Every candidate for the next break, in the pipeline's scan order (x, then y, then z): a
	 * known solid block not broken in this job, with at least one passable face neighbour, whose
	 * centre is within {@link #REACH} of the eye. The stand position is the player's feet and the
	 * look is the current crosshair (the pipeline uses where the player stood and looked when the
	 * previous block broke).
	 *
	 * @throws IllegalStateException before the job's first break (the features need a previous block)
	 */
	public static List<Candidate> candidates(World world, Job job, double standX, double standY, double standZ, double yaw, double pitch) {
		View v = new View(world, job, standX, standY, standZ, yaw, pitch);
		int r = (int) Math.ceil(REACH) + 1;
		int ex = (int) Math.floor(v.eyeX);
		int ey = (int) Math.floor(v.eyeY);
		int ez = (int) Math.floor(v.eyeZ);
		List<Candidate> out = new ArrayList<>();
		for (int x = ex - r; x <= ex + r; x++) {
			for (int y = ey - r; y <= ey + r; y++) {
				for (int z = ez - r; z <= ez + r; z++) {
					double[] f = v.features(x, y, z);
					if (f != null) {
						out.add(new Candidate(x, y, z, f));
					}
				}
			}
		}
		return out;
	}

	/** One block's features, or null if it isn't a candidate (see {@link #candidates}). */
	public static double[] features(World world, Job job, double standX, double standY, double standZ, double yaw, double pitch, int x, int y, int z) {
		return new View(world, job, standX, standY, standZ, yaw, pitch).features(x, y, z);
	}

	/** One step's fixed quantities: the eye, the look vector, the block under the feet. */
	private static final class View {
		final World world;
		final Job job;
		final double eyeX;
		final double eyeY;
		final double eyeZ;
		final double yaw;
		final double pitch;
		final double lookX;
		final double lookY;
		final double lookZ;
		final int feetY;
		final int belowX;
		final int belowZ;

		View(World world, Job job, double sx, double sy, double sz, double yaw, double pitch) {
			if (!job.started) {
				throw new IllegalStateException("no previous break: the job's first block is Baritone's choice");
			}
			this.world = world;
			this.job = job;
			eyeX = sx;
			eyeY = sy + EYE_HEIGHT;
			eyeZ = sz;
			this.yaw = yaw;
			this.pitch = pitch;
			double y = Math.toRadians(yaw);
			double p = Math.toRadians(pitch);
			lookX = -Math.sin(y) * Math.cos(p);
			lookY = -Math.sin(p);
			lookZ = Math.cos(y) * Math.cos(p);
			feetY = (int) Math.floor(sy - FEET_EPS);
			belowX = (int) Math.floor(sx);
			belowZ = (int) Math.floor(sz);
		}

		double[] features(int x, int y, int z) {
			double tx = x + 0.5 - eyeX;
			double ty = y + 0.5 - eyeY;
			double tz = z + 0.5 - eyeZ;
			double dist = Math.sqrt(tx * tx + ty * ty + tz * tz);
			if (dist > REACH || job.broken(x, y, z)) {
				return null;
			}
			Kind b = kind(world, job, x, y, z);
			if (b == null || b.passable) {
				return null;
			}
			int open = 0;
			int beyond = 0;
			for (int[] n : NEIGHBOURS) {
				Kind q = kind(world, job, x + n[0], y + n[1], z + n[2]);
				if (q != null && q.passable) {
					open++;
					// to = centre - eye, so (eye - centre) . n <= 0.5: the eye isn't in front of that face
					if (tx * n[0] + ty * n[1] + tz * n[2] >= -0.5) {
						beyond++;
					}
				}
			}
			if (open == 0) {
				return null;
			}
			double cos = (tx * lookX + ty * lookY + tz * lookZ) / Math.max(dist, 1e-9);
			double targetYaw = Math.toDegrees(Math.atan2(-tx, tz));
			double targetPitch = Math.toDegrees(Math.atan2(-ty, Math.hypot(tx, tz)));
			int dx = x - job.prevX;
			int dy = y - job.prevY;
			int dz = z - job.prevZ;
			int manhattan = Math.abs(dx) + Math.abs(dy) + Math.abs(dz);
			double[] f = new double[COUNT];
			f[0] = dist;
			f[1] = Math.toDegrees(Math.acos(Math.max(-1.0, Math.min(1.0, cos))));
			f[2] = floorMod(targetYaw - yaw + 180.0, 360.0) - 180.0;
			f[3] = targetPitch - pitch;
			f[4] = ty;
			f[5] = dy;
			f[6] = Math.abs(dx) + Math.abs(dz);
			f[7] = Integer.signum(dx) * job.lastX + Integer.signum(dz) * job.lastZ;
			f[8] = manhattan;
			f[9] = bit(manhattan == 1);
			f[10] = bit(dy == 0);
			f[11] = bit(dx == 0 && dz == 0);
			f[12] = bit(dy == 0 && (dx == 0 || dz == 0));
			f[13] = open;
			f[14] = bit(x == belowX && y == feetY && z == belowZ);
			f[15] = bit(y <= feetY);
			f[16] = b.blockClass;
			f[17] = bit(b.falls);
			f[18] = beyond;
			f[19] = bit(job.skipped.contains(new Pos(x, y, z)));
			return f;
		}
	}

	private static double bit(boolean b) {
		return b ? 1.0 : 0.0;
	}

	/** Python's float %: the result takes the divisor's sign. */
	static double floorMod(double a, double b) {
		double m = a % b;
		if (m == 0) {
			return Math.copySign(0.0, b);
		}
		return (m < 0) != (b < 0) ? m + b : m;
	}

	private record Pos(int x, int y, int z) {
	}

	// --- Block categories by id (hbpipe.label.blocks, mining_order.block_class) ---------------

	private record Kind(boolean passable, boolean falls, int blockClass) {
		static Kind of(String state) {
			String id = blockId(state);
			return new Kind(BreakFeatures.passable(id), BreakFeatures.falls(id), BreakFeatures.blockClass(id));
		}
	}

	private static final Kind AIR = new Kind(true, false, OTHER); // what a broken block reads as
	private static final Map<String, Kind> KINDS = new ConcurrentHashMap<>();

	/** What the features see at x, y, z: air if broken in this job, null if unknown. */
	private static Kind kind(World world, Job job, int x, int y, int z) {
		if (job.broken(x, y, z)) {
			return AIR;
		}
		String state = world.block(x, y, z);
		return state == null ? null : KINDS.computeIfAbsent(state, Kind::of);
	}

	/** "minecraft:oak_stairs[facing=north]" -> "oak_stairs"; other namespaces are kept. */
	public static String blockId(String state) {
		if (state == null || state.isEmpty()) {
			return "";
		}
		int bracket = state.indexOf('[');
		String id = bracket < 0 ? state : state.substring(0, bracket);
		return id.startsWith("minecraft:") ? id.substring("minecraft:".length()) : id;
	}

	private static final Set<String> PASSABLE = words("""
		air cave_air void_air water lava bubble_column light structure_void
		short_grass tall_grass fern large_fern dead_bush seagrass tall_seagrass kelp kelp_plant
		sugar_cane vine glow_lichen sculk_vein cave_vines cave_vines_plant weeping_vines
		weeping_vines_plant twisting_vines twisting_vines_plant hanging_roots spore_blossom
		torch wall_torch soul_torch soul_wall_torch redstone_torch redstone_wall_torch
		copper_torch copper_wall_torch
		redstone_wire tripwire tripwire_hook lever rail powered_rail detector_rail activator_rail
		cobweb fire soul_fire nether_portal end_portal end_gateway
		wheat carrots potatoes beetroots melon_stem attached_melon_stem pumpkin_stem
		attached_pumpkin_stem sweet_berry_bush nether_wart torchflower_crop pitcher_crop
		brown_mushroom red_mushroom crimson_fungus warped_fungus crimson_roots warped_roots
		nether_sprouts dandelion poppy blue_orchid allium azure_bluet red_tulip orange_tulip
		white_tulip pink_tulip oxeye_daisy cornflower lily_of_the_valley wither_rose sunflower
		lilac rose_bush peony torchflower pitcher_plant pink_petals wildflowers leaf_litter
		firefly_bush short_dry_grass tall_dry_grass bush cactus_flower closed_eyeblossom
		open_eyeblossom snow
		""");
	private static final String[] PASSABLE_SUFFIXES = {
		"_sapling", "_button", "_pressure_plate", "_sign", "_wall_sign", "_hanging_sign",
		"_banner", "_wall_banner", "_carpet", "_coral", "_coral_fan", "_coral_wall_fan"};
	private static final Set<String> STONE_IDS = words("""
		stone deepslate cobbled_deepslate cobblestone mossy_cobblestone tuff granite diorite andesite
		calcite dripstone_block smooth_basalt basalt polished_basalt blackstone netherrack end_stone
		sandstone red_sandstone obsidian crying_obsidian bedrock terracotta amethyst_block
		budding_amethyst magma_block
		""");
	private static final Set<String> SOIL_IDS = words("""
		dirt coarse_dirt rooted_dirt grass_block podzol mycelium dirt_path farmland mud clay sand
		red_sand gravel suspicious_sand suspicious_gravel soul_sand soul_soil snow_block powder_snow
		moss_block muddy_mangrove_roots
		""");
	private static final Set<String> FALLING = Set.of("sand", "red_sand", "gravel", "suspicious_sand", "suspicious_gravel");

	/** True if the player can move through it, by id (blocks.passable; "" counts as passable). */
	public static boolean passable(String id) {
		return id.isEmpty() || PASSABLE.contains(id) || endsWithAny(id, PASSABLE_SUFFIXES);
	}

	/** Gravity blocks that refill a hole from above (blocks.falls). */
	public static boolean falls(String id) {
		return FALLING.contains(id) || id.endsWith("concrete_powder") || id.endsWith("anvil");
	}

	/** mining_order.block_class: ores first, then the explicit lists, then the suffix rules. */
	public static int blockClass(String id) {
		if (isOre(id)) {
			return ORE;
		}
		if (STONE_IDS.contains(id) || id.startsWith("infested_") || id.endsWith("_terracotta")) {
			return STONE;
		}
		if (SOIL_IDS.contains(id) || id.endsWith("concrete_powder")) {
			return SOIL;
		}
		if (isLog(id) || id.endsWith("_planks") || id.startsWith("stripped_")) {
			return WOOD;
		}
		return OTHER;
	}

	private static boolean isOre(String id) {
		return id.endsWith("_ore") || id.equals("ancient_debris") || id.equals("raw_iron_block");
	}

	private static boolean isLog(String id) {
		return endsWithAny(id, "_log", "_stem", "_wood", "_hyphae")
			&& !(id.startsWith("stripped_") || id.startsWith("attached_") || id.startsWith("melon") || id.startsWith("pumpkin"))
			|| id.equals("mangrove_roots");
	}

	private static boolean endsWithAny(String id, String... suffixes) {
		for (String s : suffixes) {
			if (id.endsWith(s)) {
				return true;
			}
		}
		return false;
	}

	private static Set<String> words(String list) {
		return Set.of(list.strip().split("\\s+"));
	}
}
