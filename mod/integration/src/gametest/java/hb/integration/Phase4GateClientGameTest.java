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
import net.minecraft.world.Difficulty;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.levelgen.Heightmap;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Files;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Phase 4 gate (spec): 20 runs each of four scripted tasks with zero deaths and zero stuck
 * failures: travel 500 blocks, strip-mine 200 blocks, clear a 10x10x5 room, chop 5 trees. Every
 * run goes through {@code /hb run} at full humanization, so each is a humanized bot session too
 * (the other half of the gate: {@code hbpipe discrim} on them).
 *
 * <p>Off unless asked for, because it runs for real time:
 * <pre>
 *   ./gradlew :mod:integration:runClientGameTest -Phb.gateRuns=20                  the gate
 *   ./gradlew :mod:integration:runClientGameTest -Phb.gateRuns=5 -Phb.gateFirst=5  runs 6-10 of it
 *   ./gradlew :mod:integration:runClientGameTest -Phb.gateRuns=1 -Phb.gateScale=small
 *                       travel 100, tunnel 30, a 5x5x3 room, 2 trees: a quick check of the harness
 * </pre>
 * Writes hb-gate.json in the run directory: per task, runs, passes, deaths, stuck, timeouts,
 * failures (done but the task isn't), and the seconds each run took.
 */
@SuppressWarnings("UnstableApiUsage")
public final class Phase4GateClientGameTest implements FabricClientGameTest {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-gate");
	private static final String SEED = "hb-phase4-gate";
	private static final int SPACING = 700; // blocks between runs, so each starts on new ground
	private static final int UNDERGROUND_Y = -30;

	private ClientGameTestContext ctx;
	private TestSingleplayerContext sp;
	private HbController hb;
	private final List<String> replies = new ArrayList<>();

	static boolean active() {
		return Integer.getInteger("hb.gate.runs", 0) > 0;
	}

	private record Scale(int travel, int tunnel, int room, int roomHeight, int trees) {
	}

	private record Outcome(String result, double seconds, String detail) {
	}

	@Override
	public void runTest(ClientGameTestContext context) {
		if (!active()) {
			return;
		}
		ctx = context;
		hb = HbExecutorClient.controller();
		int runs = Integer.getInteger("hb.gate.runs");
		Scale scale = "small".equals(System.getProperty("hb.gate.scale"))
			? new Scale(100, 30, 5, 3, 2) : new Scale(500, 200, 10, 5, 5);
		Map<String, List<Outcome>> results = new LinkedHashMap<>();
		try (TestSingleplayerContext world = context.worldBuilder()
			.setUseConsistentSettings(false)
			.adjustSettings(s -> {
				s.setSeed(SEED);
				s.setGameMode(WorldCreationUiState.SelectedGameMode.SURVIVAL);
				s.setDifficulty(Difficulty.PEACEFUL);
				s.setAllowCommands(true);
			})
			.create()) {
			sp = world;
			sp.getConnection().waitForChunksRender();
			// -Phb.gateStrength=0: the same runs as plain Baritone, to tell its limits from the executor's
			String strength = System.getProperty("hb.gate.strength", "1");
			ctx.runOnClient(mc -> hb.handle("strength " + strength, replies::add));
			String only = System.getProperty("hb.gate.tasks", "");
			int first = Integer.getInteger("hb.gate.first", 0); // a later chunk of the gate: new ground
			for (int run = 0; run < runs; run++) {
				int base = (first + run + 1) * SPACING;
				if (only.isEmpty() || only.contains("travel")) {
					results.computeIfAbsent("travel", k -> new ArrayList<>()).add(travel(base, 0, scale));
				}
				if (only.isEmpty() || only.contains("strip_mine")) {
					results.computeIfAbsent("strip_mine", k -> new ArrayList<>()).add(stripMine(base, SPACING / 2, scale));
				}
				if (only.isEmpty() || only.contains("clear_room")) {
					results.computeIfAbsent("clear_room", k -> new ArrayList<>()).add(clearRoom(-base, 0, scale));
				}
				if (only.isEmpty() || only.contains("chop_trees")) {
					results.computeIfAbsent("chop_trees", k -> new ArrayList<>()).add(chopTrees(-base, SPACING / 2, scale));
				}
				LOGGER.info("gate: run {} of {} done: {}", run + 1, runs, summary(results));
			}
		}
		write(results, runs, scale);
	}

	// --- Tasks ----------------------------------------------------------------------------------

	private Outcome travel(int x, int z, Scale scale) {
		surfaceAt(x, z);
		BlockPos from = playerPos();
		String cmd = String.format(Locale.ROOT, "goto %d %d", from.getX() + scale.travel(), from.getZ());
		return runTask("travel", cmd, 240 + scale.travel() / 2, () -> {
			BlockPos at = playerPos();
			double off = Math.hypot(at.getX() - (from.getX() + scale.travel()), at.getZ() - from.getZ());
			return off <= 3 ? null : String.format(Locale.ROOT, "ended %.0f blocks from the goal", off);
		});
	}

	private Outcome stripMine(int x, int z, Scale scale) {
		BlockPos at = undergroundAt(x, z);
		// tp faced the player north (-z): the tunnel runs that way. Its line is solid deepslate first,
		// so every block of it has to be dug (natural caves would do part of the work).
		BlockPos from = at.offset(0, 0, -2);
		BlockPos to = at.offset(0, 1, -1 - scale.tunnel());
		run(String.format(Locale.ROOT, "fill %d %d %d %d %d %d minecraft:deepslate", from.getX(), from.getY(), from.getZ(), to.getX(), to.getY(), to.getZ()));
		String cmd = String.format(Locale.ROOT, "tunnel 2 1 %d", scale.tunnel());
		int blocks = 2 * scale.tunnel();
		return runTask("strip_mine", cmd, 60 + scale.tunnel() * 3, () -> {
			int dug = ctx.computeOnClient(mc -> {
				int n = 0;
				for (BlockPos p : BlockPos.betweenClosed(from, to)) {
					n += mc.level.getBlockState(p).isAir() ? 1 : 0;
				}
				return n;
			});
			return dug >= blocks - 4 ? null : "dug " + dug + " of " + blocks + " tunnel blocks";
		});
	}

	private Outcome clearRoom(int x, int z, Scale scale) {
		BlockPos at = undergroundAt(x, z);
		BlockPos a = at.offset(1, 0, 1);
		BlockPos b = a.offset(scale.room() - 1, scale.roomHeight() - 1, scale.room() - 1);
		// Solid deepslate to clear (what the player clears most): a cave in the way would make it look done.
		run(String.format(Locale.ROOT, "fill %d %d %d %d %d %d minecraft:deepslate", a.getX(), a.getY(), a.getZ(), b.getX(), b.getY(), b.getZ()));
		ctx.runOnClient(mc -> HbExecutorClient.planner().execute("sel clear"));
		ctx.runOnClient(mc -> HbExecutorClient.planner().execute(String.format(Locale.ROOT, "sel 1 %d %d %d", a.getX(), a.getY(), a.getZ())));
		ctx.runOnClient(mc -> HbExecutorClient.planner().execute(String.format(Locale.ROOT, "sel 2 %d %d %d", b.getX(), b.getY(), b.getZ())));
		int volume = scale.room() * scale.room() * scale.roomHeight();
		return runTask("clear_room", "sel cleararea", 120 + volume, () -> {
			int left = ctx.computeOnClient(mc -> {
				int n = 0;
				for (BlockPos p : BlockPos.betweenClosed(a, b)) {
					n += mc.level.getBlockState(p).isAir() ? 0 : 1;
				}
				return n;
			});
			return left <= 2 ? null : left + " of " + volume + " blocks left";
		});
	}

	private Outcome chopTrees(int x, int z, Scale scale) {
		surfaceAt(x, z);
		BlockPos at = playerPos();
		// A flat grass plot, cleared overhead, with a row of oaks four blocks apart.
		run(String.format(Locale.ROOT, "fill %d %d %d %d %d %d minecraft:grass_block", at.getX() - 4, at.getY() - 1, at.getZ() - 4, at.getX() + 4 * scale.trees() + 4, at.getY() - 1, at.getZ() + 8));
		run(String.format(Locale.ROOT, "fill %d %d %d %d %d %d minecraft:air", at.getX() - 4, at.getY(), at.getZ() - 4, at.getX() + 4 * scale.trees() + 4, at.getY() + 12, at.getZ() + 8));
		for (int i = 0; i < scale.trees(); i++) {
			run(String.format(Locale.ROOT, "place feature minecraft:oak %d %d %d", at.getX() + 4 + 4 * i, at.getY(), at.getZ() + 4));
		}
		ctx.waitTicks(10);
		BlockPos lo = at.offset(-4, 0, -4);
		BlockPos hi = at.offset(4 * scale.trees() + 4, 12, 8);
		int logs = countLogs(lo, hi);
		if (logs == 0) {
			return new Outcome("setup_failed", 0, "no trees grew");
		}
		return runTask("chop_trees", "mine " + logs + " oak_log", 120 + logs * 15, () -> {
			int left = countLogs(lo, hi);
			return left == 0 ? null : left + " of " + logs + " logs left";
		});
	}

	// --- Running one task -----------------------------------------------------------------------

	private interface Check {
		/** Null when the task got done, else what's missing. */
		String missing();
	}

	private Outcome runTask(String name, String cmd, int timeoutSeconds, Check check) {
		run("clear @a"); // a fresh kit: logs left from an earlier chop would count toward "mine 9"
		run("kill @e[type=item]"); // ... and so would logs still lying where an earlier chop dropped them
		// The player's own kit: their recorded breaks (deepslate in 3 ticks, logs in 2) mean Efficiency V.
		for (String item : new String[]{"diamond_pickaxe[enchantments={efficiency:5}]", "diamond_axe[enchantments={efficiency:5}]",
			"diamond_shovel", "cobblestone 64", "dirt 64", "torch 64", "cooked_beef 16"}) {
			run("give @a " + item);
		}
		ctx.waitTicks(5);
		long start = System.nanoTime();
		replies.clear();
		ctx.runOnClient(mc -> hb.handle("run " + cmd, replies::add));
		boolean died = false;
		long deadline = start + timeoutSeconds * 1_000_000_000L;
		while (System.nanoTime() < deadline && ctx.computeOnClient(mc -> hb.running())) {
			ctx.waitTicks(20);
			if (ctx.computeOnClient(mc -> mc.gui.screen() instanceof DeathScreen)) {
				died = true;
				break;
			}
		}
		boolean timedOut = ctx.computeOnClient(mc -> hb.running());
		ctx.runOnClient(mc -> {
			if (hb.running()) {
				hb.handle("stop", replies::add);
			}
		});
		double seconds = (System.nanoTime() - start) / 1e9;
		if (died) {
			ctx.runOnClient(mc -> {
				mc.player.respawn();
				mc.gui.setScreen(null);
			});
			ctx.waitTicks(40);
			return log(name, new Outcome("died", seconds, String.join("; ", replies)));
		}
		if (timedOut) {
			return log(name, new Outcome("timeout", seconds, String.join("; ", replies)));
		}
		String outcome = ctx.computeOnClient(mc -> hb.lastOutcome());
		if ("stuck".equals(outcome)) {
			return log(name, new Outcome("stuck", seconds, "the watchdog gave up after " + hb.repaths() + " repaths"));
		}
		String missing = check.missing();
		return log(name, new Outcome(missing == null ? "pass" : "failed", seconds, missing == null ? "" : missing));
	}

	private Outcome log(String name, Outcome outcome) {
		LOGGER.info("gate: {} {} in {} s {}", name, outcome.result(), String.format(Locale.ROOT, "%.0f", outcome.seconds()), outcome.detail());
		return outcome;
	}

	// --- World setup ----------------------------------------------------------------------------

	/**
	 * Puts the player on dry land at (x, z), or as near as spreadplayers finds some, and waits for
	 * the area to load. spreadplayers refuses a spot it finds unsafe (water, lava), so wider spreads
	 * next (an ocean can take hundreds of blocks), and last the surface's height from the server: a
	 * task set up wherever the player was left is no test (once that was beside the lava the task
	 * before had let in), and one set up at sea isn't either (a tree plot on the sea floor).
	 */
	private void surfaceAt(int x, int z) {
		for (int range : new int[]{1, 8, 64, 256}) {
			run(String.format(Locale.ROOT, "spreadplayers %d %d 0 %d false @a", x, z, range));
			sp.getConnection().waitForChunksRender();
			ctx.waitTicks(20);
			BlockPos at = playerPos();
			if (Math.abs(at.getX() - x) <= range + 1 && Math.abs(at.getZ() - z) <= range + 1) {
				return;
			}
		}
		run(String.format(Locale.ROOT, "forceload add %d %d", x, z));
		int y = sp.getServer().computeOnServer(server -> server.overworld().getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, x, z));
		LOGGER.info("gate: spreadplayers found no spot near {}, {}; put on the surface at y {}", x, z, y);
		run(String.format(Locale.ROOT, "tp @a %d %d %d", x, y, z));
		sp.getConnection().waitForChunksRender();
		ctx.waitTicks(20);
	}

	/** Carves a 3x3x3 pocket at UNDERGROUND_Y under (x, z), stands the player in it facing north. */
	private BlockPos undergroundAt(int x, int z) {
		// fill only reaches loaded chunks, and spreadplayers sometimes finds no spot to land on, so
		// the chunk is force-loaded, and the pocket checked before the player goes in.
		run(String.format(Locale.ROOT, "forceload add %d %d", x, z));
		surfaceAt(x, z);
		for (int attempt = 0; attempt < 5 && !pocketCarved(x, z); attempt++) {
			ctx.waitTicks(20);
			run(String.format(Locale.ROOT, "fill %d %d %d %d %d %d minecraft:air", x - 1, UNDERGROUND_Y, z - 1, x + 1, UNDERGROUND_Y + 2, z + 1));
			run(String.format(Locale.ROOT, "fill %d %d %d %d %d %d minecraft:stone", x - 1, UNDERGROUND_Y - 1, z - 1, x + 1, UNDERGROUND_Y - 1, z + 1));
			ctx.waitTicks(5);
		}
		if (!pocketCarved(x, z)) {
			throw new AssertionError("couldn't carve the pocket at " + x + ", " + z);
		}
		run(String.format(Locale.ROOT, "tp @a %d %d %d 180 0", x, UNDERGROUND_Y, z));
		sp.getConnection().waitForChunksRender();
		ctx.waitTicks(20);
		return playerPos();
	}

	/** The pocket's middle column is air, on the server (fill's own world). */
	private boolean pocketCarved(int x, int z) {
		return sp.getServer().computeOnServer(server -> {
			var level = server.overworld();
			BlockPos feet = new BlockPos(x, UNDERGROUND_Y, z);
			return level.isLoaded(feet) && level.getBlockState(feet).isAir() && level.getBlockState(feet.above()).isAir()
				&& level.getBlockState(feet.below()).is(net.minecraft.world.level.block.Blocks.STONE);
		});
	}

	private BlockPos playerPos() {
		return ctx.computeOnClient(mc -> mc.player.blockPosition());
	}

	/**
	 * Oak logs in the plot, counted on the server: the client's copy can still be missing some of
	 * the blocks the trees were just placed with (one run counted 19 of 24 and asked for 19).
	 */
	private int countLogs(BlockPos lo, BlockPos hi) {
		return sp.getServer().computeOnServer(server -> {
			int n = 0;
			for (BlockPos p : BlockPos.betweenClosed(lo, hi)) {
				n += server.overworld().getBlockState(p).is(Blocks.OAK_LOG) ? 1 : 0;
			}
			return n;
		});
	}

	private void run(String command) {
		sp.getServer().runCommand(command);
	}

	// --- Report ---------------------------------------------------------------------------------

	private static String summary(Map<String, List<Outcome>> results) {
		StringBuilder s = new StringBuilder();
		results.forEach((task, outcomes) -> s.append(task).append(' ')
			.append(outcomes.stream().filter(o -> o.result().equals("pass")).count()).append('/').append(outcomes.size()).append("  "));
		return s.toString().trim();
	}

	private static void write(Map<String, List<Outcome>> results, int runs, Scale scale) {
		Map<String, Object> report = new LinkedHashMap<>();
		report.put("runs_per_task", runs);
		report.put("scale", scale.toString());
		boolean gate = true;
		Map<String, Object> tasks = new LinkedHashMap<>();
		for (var e : results.entrySet()) {
			Map<String, Object> t = new LinkedHashMap<>();
			List<Outcome> outcomes = e.getValue();
			for (String r : new String[]{"pass", "died", "stuck", "timeout", "failed", "setup_failed"}) {
				t.put(r, outcomes.stream().filter(o -> o.result().equals(r)).count());
			}
			t.put("seconds", outcomes.stream().map(o -> Math.round(o.seconds())).toList());
			t.put("details", outcomes.stream().map(o -> o.result() + (o.detail().isEmpty() ? "" : ": " + o.detail())).toList());
			gate &= (long) t.get("died") == 0 && (long) t.get("stuck") == 0 && (long) t.get("timeout") == 0;
			tasks.put(e.getKey(), t);
		}
		report.put("tasks", tasks);
		report.put("zero_deaths_and_stuck", gate);
		try {
			Files.writeString(FabricLoader.getInstance().getGameDir().resolve("hb-gate.json"),
				new GsonBuilder().setPrettyPrinting().create().toJson(report) + "\n");
		} catch (Exception e) {
			throw new AssertionError(e);
		}
		LOGGER.info("gate: {} (zero deaths and stuck: {})", summary(results), gate);
	}
}
