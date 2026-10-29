package hb.integration;

import com.google.gson.GsonBuilder;
import hb.executor.HbController;
import hb.executor.HbExecutorClient;
import net.fabricmc.fabric.api.client.gametest.v1.FabricClientGameTest;
import net.fabricmc.fabric.api.client.gametest.v1.context.ClientGameTestContext;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestSingleplayerContext;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.gui.screens.DeathScreen;
import net.minecraft.client.gui.screens.worldselection.WorldCreationUiState;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.ListTag;
import net.minecraft.nbt.NbtAccounter;
import net.minecraft.nbt.NbtIo;
import net.minecraft.resources.Identifier;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.Difficulty;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.piston.PistonBaseBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.Property;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * Builds a Litematica schematic underground with the humanized executor ({@code /hb run build}),
 * and checks the result block by block. The schematic's deepslate stands for any solid rock (the
 * farm is dug into it), so it is built in a solid block of stone (a simulated underground, without
 * the caves, lava and water of a real one), with the player in a small room over it. The player is
 * invincible (resistance V, fire resistance) and has every block the schematic needs, half again
 * over, with the tools.
 * <pre>
 *   ./gradlew :mod:integration:runClientGameTest -Phb.buildSchematic=PATH [-Phb.buildMinutes=120]
 * </pre>
 * A run that ends (stuck, or the planner idle) with the build unfinished is started again, as a
 * player would, up to {@link #ATTEMPTS} times. Writes hb-build.json in the run directory: the
 * attempts, and what is still wrong.
 */
@SuppressWarnings("UnstableApiUsage")
public final class BuildClientGameTest implements FabricClientGameTest {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-build");
	private static final String SEED = "hb-phase4-gate";
	// -Phb.buildAttempts=N: a layer by layer build of a whole farm starts over (at the layer it's on)
	// more often than that
	private static final int ATTEMPTS = Integer.getInteger("hb.build.attempts", 12);
	private static final int X = 2000;
	private static final int Y = -50;
	private static final int Z = 2000;
	private static final int MARGIN = 4; // solid stone around the schematic
	/** Stands for "any solid rock" in the schematic. */
	private static final Block FILLER = Blocks.DEEPSLATE;
	/**
	 * What a player digging underground meets; any of it is right where the schematic has filler. A
	 * hole is filled with the first of them the player has: cobbled deepslate, what deepslate drops.
	 */
	private static final String ROCK = "cobbled_deepslate,cobblestone,deepslate,stone,tuff,andesite,diorite,granite,calcite,"
		+ "smooth_basalt,dirt,gravel,coal_ore,iron_ore,copper_ore,gold_ore,redstone_ore,lapis_ore,diamond_ore,emerald_ore,"
		+ "deepslate_coal_ore,deepslate_iron_ore,deepslate_copper_ore,deepslate_gold_ore,deepslate_redstone_ore,"
		+ "deepslate_lapis_ore,deepslate_diamond_ore,deepslate_emerald_ore";
	/**
	 * Properties a block takes from its neighbours or from use, not from how it's placed: the
	 * builder can't place them as the schematic has them (a repeater goes down at delay 1, a chest
	 * placed sneaking stays single, a creaking heart wakes at night), and would break and place the
	 * block again forever. The check lists them instead.
	 */
	private static final String IGNORED = "power,powered,lit,enabled,extended,locked,delay,north,south,east,west,up,"
		+ "creaking_heart_state,type,waterlogged";
	/** Properties fixed by how a block is placed: the check holds the build to these. */
	private static final Set<String> PLACED = Set.of("facing", "axis", "face", "half", "rotation", "hinge", "attachment", "shape");

	private ClientGameTestContext ctx;
	private TestSingleplayerContext sp;
	private HbController hb;
	private final List<String> replies = new ArrayList<>();

	static boolean active() {
		return !System.getProperty("hb.build.schematic", "").isEmpty();
	}

	/** The schematic: its size and, per position, the block state wanted there. */
	private record Schematic(String file, int sx, int sy, int sz, BlockState[] states) {
		BlockState at(int x, int y, int z) {
			return states[(y * sz + z) * sx + x];
		}
	}

	private record Attempt(String outcome, double seconds, int wrongAfter) {
	}

	@Override
	public void runTest(ClientGameTestContext context) {
		if (!active()) {
			return;
		}
		ctx = context;
		hb = HbExecutorClient.controller();
		Path source = Path.of(System.getProperty("hb.build.schematic"));
		Schematic s = read(source);
		LOGGER.info("build: {} is {} x {} x {}", s.file(), s.sx(), s.sy(), s.sz());
		try {
			Path dir = FabricLoader.getInstance().getGameDir().resolve("schematics");
			Files.createDirectories(dir);
			Files.copy(source, dir.resolve(s.file()), StandardCopyOption.REPLACE_EXISTING);
		} catch (Exception e) {
			throw new AssertionError(e);
		}
		// Game time, not the clock's: a laptop asleep stops the game, but its clock runs on
		long budget = Long.getLong("hb.build.minutes", 120) * 60 * 20;
		long[] used = {0}; // ticks the attempts have taken
		List<Attempt> attempts = new ArrayList<>();
		Map<String, Object> wrong;
		try (TestSingleplayerContext world = context.worldBuilder()
			.setUseConsistentSettings(false)
			.adjustSettings(w -> {
				w.setSeed(SEED);
				w.setGameMode(WorldCreationUiState.SelectedGameMode.SURVIVAL);
				w.setDifficulty(Difficulty.PEACEFUL);
				w.setAllowCommands(true);
			})
			.create()) {
			sp = world;
			sp.getConnection().waitForChunksRender();
			ctx.runOnClient(mc -> hb.handle("strength " + System.getProperty("hb.build.strength", "1"), replies::add));
			site(s);
			kit(s);
			// -Phb.buildLayers=true: one layer at a time from the bottom, Baritone's buildInLayers: the
			// rock over the layer under way isn't the build's yet, so it's dug through freely to get at
			// the work and filled when its own layer comes; each layer a flat job
			if (Boolean.getBoolean("hb.build.layers")) {
				for (String set : new String[]{"buildInLayers true", "layerOrder false", "layerHeight 1"}) {
					ctx.runOnClient(mc -> HbExecutorClient.planner().execute("set " + set));
				}
			}
			for (String set : new String[]{
				"allowInventory true", // the kit doesn't fit the hotbar
				"buildSubstitutes " + BuiltInRegistries.BLOCK.getKey(FILLER).getPath() + "->" + ROCK,
				"buildIgnoreProperties " + IGNORED,
				"buildIgnoreBlocks piston_head,moving_piston", // where the farm's pistons push, once it runs
				// all of it, not only what's within 14 blocks while any is: else stuck work nearby keeps the
				// player from the rest (the farm is 32 long)
				"distanceTrim false",
				// time to plan the way into the farm's far end, through 15 blocks of rock (half a second
				// went unfound, and the player wandered at the near end)
				// a block shut in but for its top (a prop between a piston and a heart) is broken as a
				// player would, from beside the hole over it
				"breakFromAbove true",
				"goalBreakFromAbove true",
				"primaryTimeoutMS 2000",
				"failureTimeoutMS 6000",
				"chatDebug " + Boolean.getBoolean("hb.humanizer.trace"), // with the trace, Baritone's own account in the log
			}) {
				ctx.runOnClient(mc -> HbExecutorClient.planner().execute("set " + set));
			}
			// -Phb.buildRockLast=true: holes dug through the rock to get at the farm stay open, rather
			// than each being filled at once and dug again for the next block (the way in through the
			// rock over a shaft was filled and dug round and round), and the next attempt fills them,
			// then the next leaves them again, and so on: wire on rock the bot dug from under it can't
			// go in until that's filled
			boolean rockLast = Boolean.getBoolean("hb.build.rockLast");
			boolean alternate = rockLast;
			String cmd = String.format(Locale.ROOT, "build %s %d %d %d", s.file(), X, Y, Z);
			for (int i = 0; i < ATTEMPTS && used[0] < budget; i++) {
				if (i > 0) {
					topUp(s);
				}
				if (alternate) {
					String okIfAir = rockLast ? "okIfAir " + ROCK : "reset okIfAir";
					ctx.runOnClient(mc -> HbExecutorClient.planner().execute("set " + okIfAir));
					LOGGER.info("build: attempt {} {}", i + 1, rockLast ? "leaves holes in the rock" : "fills the holes in the rock");
				}
				Attempt a = attempt(s, cmd, budget, used);
				attempts.add(a);
				LOGGER.info("build: attempt {} {} in {} s, {} positions wrong", i + 1, a.outcome(),
					String.format(Locale.ROOT, "%.0f", a.seconds()), a.wrongAfter());
				if (a.wrongAfter() == 0 || a.outcome().equals("died")) {
					break;
				}
				rockLast = alternate && !rockLast;
			}
			wrong = check(s, true);
		}
		write(s, attempts, wrong);
	}

	// --- One run of the build ------------------------------------------------------------------

	/** One run of the build, until it ends or the attempts together have had {@code budget} ticks. */
	private Attempt attempt(Schematic s, String cmd, long budget, long[] used) {
		long ticks = 0;
		replies.clear();
		ctx.runOnClient(mc -> hb.handle("run " + cmd, replies::add));
		LOGGER.info("build: {}", String.join("; ", replies));
		boolean died = false;
		while (used[0] < budget && ctx.computeOnClient(mc -> hb.running())) {
			ctx.waitTicks(20);
			ticks += 20;
			used[0] += 20;
			if (ctx.computeOnClient(mc -> mc.gui.screen() instanceof DeathScreen)) {
				died = true;
				break;
			}
			if (ticks % 1200 == 0) {
				LOGGER.info("build: {} min, {}", ticks / 1200, check(s, false));
			}
		}
		boolean timedOut = ctx.computeOnClient(mc -> hb.running());
		ctx.runOnClient(mc -> {
			if (hb.running()) {
				hb.handle("stop", replies::add);
			}
		});
		double seconds = ticks / 20.0; // game time
		String outcome = died ? "died" : timedOut ? "timeout" : ctx.computeOnClient(mc -> hb.lastOutcome());
		if (died) {
			ctx.runOnClient(mc -> {
				mc.player.respawn();
				mc.gui.setScreen(null);
			});
			ctx.waitTicks(40);
		}
		ctx.waitTicks(20);
		return new Attempt(outcome, seconds, (int) check(s, false).get("wrong"));
	}

	// --- World setup ----------------------------------------------------------------------------

	/**
	 * A solid block of stone, MARGIN over the schematic each way, with a room above the schematic's
	 * ladder shaft (its way in), and the player in the room.
	 */
	private void site(Schematic s) {
		int x0 = X - MARGIN, y0 = Y - MARGIN, z0 = Z - MARGIN;
		int x1 = X + s.sx() - 1 + MARGIN, y1 = Y + s.sy() + 3, z1 = Z + s.sz() - 1 + MARGIN;
		run(String.format(Locale.ROOT, "forceload add %d %d %d %d", x0, z0, x1, z1));
		// The room: 5 x 3 x 5 over the schematic's top, centred on the first ladder it finds up there
		BlockPos shaft = topLadder(s);
		int rx = X + shaft.getX(), ry = Y + s.sy(), rz = Z + shaft.getZ();
		for (int attempt = 0; attempt < 10 && !siteReady(x0, y0, z0, rx, ry, rz); attempt++) {
			ctx.waitTicks(20);
			run(String.format(Locale.ROOT, "fill %d %d %d %d %d %d minecraft:stone", x0, y0, z0, x1, y1, z1));
			run(String.format(Locale.ROOT, "fill %d %d %d %d %d %d minecraft:air", rx - 2, ry, rz - 2, rx + 2, ry + 2, rz + 2));
			ctx.waitTicks(5);
		}
		if (!siteReady(x0, y0, z0, rx, ry, rz)) {
			throw new AssertionError("couldn't set up the site at " + X + ", " + Y + ", " + Z);
		}
		String leave = System.getProperty("hb.build.leave", "");
		int below = Integer.getInteger("hb.build.below", 0);
		if (!leave.isEmpty() || below > 0) {
			prefill(s, leave.isEmpty() ? Set.of() : Set.of(leave.split(",")), below > 0 ? below : Integer.MAX_VALUE);
		}
		run(String.format(Locale.ROOT, "tp @a %d %d %d 180 30", rx, ry, rz));
		sp.getConnection().waitForChunksRender();
		ctx.waitTicks(20);
		for (String effect : new String[]{"resistance infinite 255", "fire_resistance infinite 0", "water_breathing infinite 0",
			"saturation infinite 0", "night_vision infinite 0"}) {
			run("effect give @a minecraft:" + effect + " true");
		}
		LOGGER.info("build: site {} {} {} to {} {} {}, room over {} {} {}", x0, y0, z0, x1, y1, z1, rx, ry - 1, rz);
	}

	/**
	 * -Phb.buildLeave=ladder,repeater: the test puts the farm in itself, all but those blocks (air, as
	 * if dug out for them), so the run starts where a whole build ends: at its last, hardest blocks.
	 * -Phb.buildBelow=N: only its layers under N (schematic y), the rest left rock: a layer by layer
	 * run starts at layer N, as one from scratch gets there.
	 */
	private void prefill(Schematic s, Set<String> leave, int below) {
		int[] left = {0};
		sp.getServer().runOnServer(server -> forEach(s, (p, want) -> {
			if (p.getY() - Y >= below) {
				return;
			}
			boolean out = leave.contains(BuiltInRegistries.BLOCK.getKey(want.getBlock()).getPath());
			left[0] += out ? 1 : 0;
			BlockState put = want.is(FILLER) ? Blocks.STONE.defaultBlockState() : out ? Blocks.AIR.defaultBlockState() : want;
			server.overworld().setBlock(p, put, Block.UPDATE_CLIENTS);
		}));
		ctx.waitTicks(20);
		LOGGER.info("build: put the farm in{} but for {} blocks ({})", below < Integer.MAX_VALUE ? " under layer " + below : "", left[0], String.join(", ", leave));
	}

	/** The top of the schematic's ladder shaft (schematic coordinates), or its middle if it has none. */
	private static BlockPos topLadder(Schematic s) {
		for (int y = s.sy() - 1; y >= 0; y--) {
			for (int z = 0; z < s.sz(); z++) {
				for (int x = 0; x < s.sx(); x++) {
					if (s.at(x, y, z).is(Blocks.LADDER)) {
						return new BlockPos(x, y, z);
					}
				}
			}
		}
		return new BlockPos(s.sx() / 2, 0, s.sz() / 2);
	}

	private boolean siteReady(int x0, int y0, int z0, int rx, int ry, int rz) {
		return sp.getServer().computeOnServer(server -> {
			ServerLevel level = server.overworld();
			BlockPos corner = new BlockPos(x0, y0, z0);
			BlockPos room = new BlockPos(rx, ry, rz);
			return level.isLoaded(corner) && level.getBlockState(corner).is(Blocks.STONE)
				&& level.getBlockState(new BlockPos(X, Y, Z)).is(Blocks.STONE)
				&& level.getBlockState(room).isAir() && level.getBlockState(room.above()).isAir()
				&& level.getBlockState(room.below()).is(Blocks.STONE);
		});
	}

	// the kit's tools, with Unbreaking III as a player's would have, and given again when worn out:
	// a whole farm dug out of rock wore the pickaxe out, and the player went on digging deepslate
	// with the shovel
	private static final String[] TOOLS = {"diamond_pickaxe", "diamond_shovel", "diamond_axe"};

	private void giveTool(String tool) {
		run("give @a " + tool + "[enchantments={efficiency:5,unbreaking:3}]");
	}

	/** Tools, and every block the schematic needs half again over; the filler as rock to fill holes with. */
	private void kit(Schematic s) {
		run("clear @a");
		for (String tool : TOOLS) {
			giveTool(tool);
		}
		needed(s).forEach((item, n) -> give(item, n + (n + 1) / 2));
		give("cobbled_deepslate", 192);
		ctx.waitTicks(5);
	}

	/** Before another attempt: enough of each block still to place, with a little over, and rock. */
	private void topUp(Schematic s) {
		Map<String, Integer> missing = new TreeMap<>();
		sp.getServer().runOnServer(server -> {
			forEach(s, (p, want) -> {
				BlockState have = server.overworld().getBlockState(p);
				if (!want.is(FILLER) && !want.isAir() && !have.is(want.getBlock())) {
					missing.merge(itemFor(want.getBlock()), 5, (a, b) -> a + 1); // one per block, 4 over
				}
			});
			missing.put("cobbled_deepslate", 64);
			for (String tool : TOOLS) {
				missing.put(tool, 1);
			}
			for (ItemStack stack : server.getPlayerList().getPlayers().getFirst().getInventory().getNonEquipmentItems()) {
				missing.computeIfPresent(BuiltInRegistries.ITEM.getKey(stack.getItem()).getPath(), (k, n) -> n - stack.getCount());
			}
		});
		missing.values().removeIf(n -> n <= 0);
		for (String tool : TOOLS) {
			if (missing.remove(tool) != null) {
				giveTool(tool); // worn out
			}
		}
		missing.forEach(this::give);
		LOGGER.info("build: topped up {}", missing);
	}

	private void give(String item, int n) {
		for (int left = n; left > 0; left -= 64) {
			run(String.format(Locale.ROOT, "give @a minecraft:%s %d", item, Math.min(64, left)));
		}
	}

	/** Items the schematic's blocks are placed from, and how many of each, filler and air aside. */
	private static Map<String, Integer> needed(Schematic s) {
		Map<String, Integer> n = new TreeMap<>();
		for (BlockState want : s.states()) {
			if (!want.is(FILLER) && !want.isAir()) {
				n.merge(itemFor(want.getBlock()), 1, Integer::sum);
			}
		}
		return n;
	}

	private static String itemFor(Block block) {
		Item item = block.asItem();
		return item == Items.AIR ? BuiltInRegistries.BLOCK.getKey(block).getPath() : BuiltInRegistries.ITEM.getKey(item).getPath();
	}

	// --- The check ------------------------------------------------------------------------------

	private interface Visit {
		void at(BlockPos world, BlockState want);
	}

	private static void forEach(Schematic s, Visit v) {
		for (int y = 0; y < s.sy(); y++) {
			for (int z = 0; z < s.sz(); z++) {
				for (int x = 0; x < s.sx(); x++) {
					v.at(new BlockPos(X + x, Y + y, Z + z), s.at(x, y, z));
				}
			}
		}
	}

	/**
	 * The world against the schematic, on the server. Filler is right if solid, air if air (or a
	 * piston's head), any other block if it's the block, placed the way the schematic has it
	 * ({@link #PLACED}). With {@code detail}, also lists what's wrong and what differs only in the
	 * properties the builder can't place ({@link #IGNORED}).
	 */
	private Map<String, Object> check(Schematic s, boolean detail) {
		return sp.getServer().computeOnServer(server -> {
			ServerLevel level = server.overworld();
			int[] wrong = new int[3]; // filler, air, blocks
			List<String> list = new ArrayList<>();
			Map<String, Integer> unplaceable = new TreeMap<>();
			forEach(s, (p, want) -> {
				BlockState have = level.getBlockState(p);
				String where = (p.getX() - X) + " " + (p.getY() - Y) + " " + (p.getZ() - Z);
				if (want.is(FILLER)) {
					if (!have.isCollisionShapeFullBlock(level, p)) {
						wrong[0]++;
						if (detail && list.size() < 100) {
							list.add(where + ": wants rock, has " + name(have));
						}
					}
				} else if (want.isAir()) {
					if (!have.isAir() && !have.is(Blocks.PISTON_HEAD) && !have.is(Blocks.MOVING_PISTON)) {
						wrong[1]++;
						if (detail && list.size() < 100) {
							list.add(where + ": wants air, has " + name(have));
						}
					}
				} else if (have.is(Blocks.MOVING_PISTON) && want.getBlock() instanceof PistonBaseBlock) {
					// a piston of the farm moving, its redstone firing it: that piston
				} else if (!have.is(want.getBlock()) || !same(have, want, PLACED)) {
					wrong[2]++;
					if (detail && list.size() < 100) {
						list.add(where + ": wants " + name(want) + ", has " + name(have));
					}
				} else if (detail && !have.equals(want)) {
					for (Property<?> prop : want.getProperties()) {
						if (!want.getValue(prop).equals(have.getValue(prop))) {
							unplaceable.merge(BuiltInRegistries.BLOCK.getKey(want.getBlock()).getPath() + " " + prop.getName() + " "
								+ have.getValue(prop) + " (wants " + want.getValue(prop) + ")", 1, Integer::sum);
						}
					}
				}
			});
			Map<String, Object> r = new LinkedHashMap<>();
			r.put("wrong", wrong[0] + wrong[1] + wrong[2]);
			r.put("rock_missing", wrong[0]);
			r.put("not_dug", wrong[1]);
			r.put("blocks_wrong", wrong[2]);
			if (detail) {
				r.put("wrong_positions", list);
				r.put("right_block_other_state", unplaceable);
			}
			return r;
		});
	}

	private static boolean same(BlockState a, BlockState b, Set<String> props) {
		for (Property<?> prop : b.getProperties()) {
			if (props.contains(prop.getName()) && !b.getValue(prop).equals(a.getValue(prop))) {
				return false;
			}
		}
		return true;
	}

	private static String name(BlockState state) {
		String s = state.toString(); // Block{minecraft:chest}[facing=west,...]
		return s.startsWith("Block{minecraft:") ? s.substring("Block{minecraft:".length()).replaceFirst("}", "") : s;
	}

	// --- The schematic file ---------------------------------------------------------------------

	/** A Litematica schematic's first region (the format Baritone reads: version 7, Minecraft 1.21 on). */
	private static Schematic read(Path file) {
		CompoundTag nbt;
		try (InputStream in = Files.newInputStream(file)) {
			nbt = NbtIo.readCompressed(in, NbtAccounter.unlimitedHeap());
		} catch (Exception e) {
			throw new AssertionError("can't read " + file, e);
		}
		CompoundTag regions = nbt.getCompound("Regions").orElseThrow();
		if (regions.size() != 1) {
			throw new AssertionError(file + " has " + regions.size() + " regions; the test reads one");
		}
		CompoundTag region = (CompoundTag) regions.values().iterator().next();
		CompoundTag size = region.getCompound("Size").orElseThrow();
		int sx = Math.abs(size.getInt("x").orElseThrow()), sy = Math.abs(size.getInt("y").orElseThrow()), sz = Math.abs(size.getInt("z").orElseThrow());
		ListTag paletteTag = region.getListOrEmpty("BlockStatePalette");
		BlockState[] palette = new BlockState[paletteTag.size()];
		for (int i = 0; i < palette.length; i++) {
			palette[i] = state((CompoundTag) paletteTag.get(i));
		}
		long[] packed = region.getLongArray("BlockStates").orElseThrow();
		int bits = Math.max(2, 32 - Integer.numberOfLeadingZeros(palette.length - 1));
		long mask = (1L << bits) - 1;
		BlockState[] states = new BlockState[sx * sy * sz];
		for (int i = 0; i < states.length; i++) {
			long bit = (long) i * bits;
			int word = (int) (bit >>> 6), off = (int) (bit & 63);
			long v = packed[word] >>> off;
			if (off + bits > 64) {
				v |= packed[word + 1] << (64 - off);
			}
			states[i] = palette[(int) (v & mask)]; // x fastest, then z, then y
		}
		return new Schematic(file.getFileName().toString(), sx, sy, sz, states);
	}

	private static BlockState state(CompoundTag tag) {
		Block block = BuiltInRegistries.BLOCK.get(Identifier.parse(tag.getString("Name").orElseThrow()))
			.map(Holder.Reference::value).orElseThrow();
		BlockState state = block.defaultBlockState();
		CompoundTag props = tag.getCompound("Properties").orElse(new CompoundTag());
		for (String key : props.keySet()) {
			Property<?> prop = block.getStateDefinition().getProperty(key);
			if (prop != null) {
				state = withValue(state, prop, props.getString(key).orElseThrow());
			}
		}
		return state;
	}

	private static <T extends Comparable<T>> BlockState withValue(BlockState state, Property<T> prop, String value) {
		return state.setValue(prop, prop.getValue(value).orElseThrow());
	}

	// --- Report ---------------------------------------------------------------------------------

	private static void write(Schematic s, List<Attempt> attempts, Map<String, Object> wrong) {
		Map<String, Object> report = new LinkedHashMap<>();
		report.put("schematic", s.file());
		report.put("origin", List.of(X, Y, Z));
		report.put("attempts", attempts.stream().map(a -> Map.of("outcome", a.outcome(),
			"seconds", Math.round(a.seconds()), "wrong_after", a.wrongAfter())).toList());
		report.put("built", (int) wrong.get("wrong") == 0);
		report.putAll(wrong);
		try {
			Files.writeString(FabricLoader.getInstance().getGameDir().resolve("hb-build.json"),
				new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create().toJson(report) + "\n");
		} catch (Exception e) {
			throw new AssertionError(e);
		}
		LOGGER.info("build: {} after {} attempts: {} wrong (rock missing {}, not dug {}, blocks wrong {})", s.file(), attempts.size(),
			wrong.get("wrong"), wrong.get("rock_missing"), wrong.get("not_dug"), wrong.get("blocks_wrong"));
	}

	private void run(String command) {
		sp.getServer().runCommand(command);
	}
}
