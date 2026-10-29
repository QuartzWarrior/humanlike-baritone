package hb.integration;

import hb.executor.HbExecutorClient;
import hb.executor.planner.PlannerBackend;
import hb.executor.record.BotRecorder;
import net.fabricmc.fabric.api.client.gametest.v1.FabricClientGameTest;
import net.fabricmc.fabric.api.client.gametest.v1.context.ClientGameTestContext;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestSingleplayerContext;
import net.minecraft.client.gui.screens.DeathScreen;
import net.minecraft.client.gui.screens.worldselection.WorldCreationUiState;
import net.minecraft.core.BlockPos;
import net.minecraft.world.Difficulty;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;
import java.util.Locale;
import java.util.function.Function;

/**
 * Phase 3 baseline: plain Baritone on scripted tasks in a fresh survival world, recorded by the
 * executor's {@link BotRecorder} as source = bot sessions for the discriminator (spec, Component
 * D). The tasks mirror the kinds of play in the human recordings (travel, trees, clearing an area,
 * tunnels, ore mining, wandering) but not their exact spots: the human sessions come from a server
 * the bot can't run on, so Phase 3 compares unmatched clips.
 *
 * <p>Off unless asked for, because it runs for real minutes:
 * <pre>
 *   ./gradlew :mod:integration:runClientGameTest -Phb.baselineMinutes=20
 * </pre>
 * The tasks run in {@link #ROUNDS} rounds, each round somewhere else in the world, and every task
 * of every round is its own bot session (the discriminator splits by session, so more and smaller
 * sessions give it folds to work with). They land in the run directory's hb-data/sessions with
 * source SOURCE_BOT.
 */
@SuppressWarnings("UnstableApiUsage")
public final class BaselineRunsClientGameTest implements FabricClientGameTest {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-baseline");
	private static final String SEED = "hb-baseline-26.2";
	private static final int ROUNDS = 2;
	private static final int ROUND_SPACING = 1500; // blocks between rounds, so terrain differs

	/** A planner command built from the player's block position, and its share of the run. */
	private record Task(String name, double share, Function<BlockPos, List<String>> commands) {
	}

	private static final List<Task> TASKS = List.of(
		new Task("travel", 0.2, p -> List.of(String.format(Locale.ROOT, "goto %d %d", p.getX() + 400, p.getZ() + 150))),
		new Task("trees", 0.15, p -> List.of("mine oak_log birch_log spruce_log jungle_log acacia_log dark_oak_log cherry_log")),
		new Task("area_clear", 0.2, p -> List.of(
			"sel clear",
			String.format(Locale.ROOT, "sel 1 %d %d %d", p.getX() + 2, p.getY(), p.getZ() + 2),
			String.format(Locale.ROOT, "sel 2 %d %d %d", p.getX() + 9, p.getY() + 2, p.getZ() + 9),
			"sel cleararea")),
		new Task("tunnel", 0.15, p -> List.of("tunnel")),
		new Task("ores", 0.15, p -> List.of("mine coal_ore iron_ore copper_ore deepslate_iron_ore deepslate_coal_ore")),
		new Task("explore", 0.15, p -> List.of("explore")));

	private ClientGameTestContext ctx;
	private TestSingleplayerContext sp;

	/** True when this run records the baseline; the functional tests skip themselves then. */
	static boolean active() {
		return Integer.getInteger("hb.baseline.minutes", 0) > 0;
	}

	@Override
	public void runTest(ClientGameTestContext context) {
		if (!active()) {
			return;
		}
		ctx = context;
		long budgetTicks = Integer.getInteger("hb.baseline.minutes") * 60L * 20;
		PlannerBackend planner = HbExecutorClient.planner();
		BotRecorder bot = HbExecutorClient.botRecorder();
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
			for (String item : new String[]{"diamond_pickaxe", "diamond_axe", "diamond_shovel", "cobblestone 64", "dirt 64", "torch 64", "cooked_beef 32"}) {
				sp.getServer().runCommand("give @a " + item);
			}
			for (int round = 0; round < ROUNDS; round++) {
				if (round > 0) {
					// Somewhere new, on the surface.
					sp.getServer().runCommand(String.format(Locale.ROOT, "spreadplayers %d %d 0 20 false @a", round * ROUND_SPACING, round * ROUND_SPACING));
					sp.getConnection().waitForChunksRender();
				}
				for (Task task : TASKS) {
					ctx.runOnClient(mc -> bot.start(0)); // plain Baritone
					run(task, planner, bot, Math.round(budgetTicks * task.share() / ROUNDS));
					ctx.runOnClient(mc -> bot.stop("baseline task over"));
					if (bot.lastError() != null) {
						throw new AssertionError("bot session write failed: " + bot.lastError());
					}
					LOGGER.info("baseline: round {} {} recorded as {}", round + 1, task.name(), bot.sessionDir());
				}
			}
		}
	}

	private void run(Task task, PlannerBackend planner, BotRecorder bot, long ticks) {
		BlockPos at = ctx.computeOnClient(mc -> mc.player.blockPosition());
		List<String> commands = task.commands().apply(at);
		LOGGER.info("baseline: {} for {} s: {}", task.name(), ticks / 20, commands);
		ctx.runOnClient(mc -> bot.beginTask(task.name() + ": " + String.join("; ", commands)));
		for (String command : commands) {
			if (!ctx.computeOnClient(mc -> planner.execute(command))) {
				throw new AssertionError("Baritone did not recognize: " + command);
			}
		}
		String outcome = "time up";
		long idle = 0;
		for (long t = 0; t < ticks; t += 20) {
			ctx.waitTicks(20);
			if (ctx.computeOnClient(mc -> mc.gui.screen() instanceof DeathScreen)) {
				// Peaceful still has falls and lava; respawn and carry on with the same task.
				LOGGER.info("baseline: died during {}, respawning", task.name());
				ctx.runOnClient(mc -> {
					mc.player.respawn();
					mc.gui.setScreen(null);
				});
				ctx.waitTicks(40);
				for (String command : commands) {
					ctx.runOnClient(mc -> planner.execute(command));
				}
				continue;
			}
			// Done once the planner has had nothing to do for 3 s (it can idle between paths).
			idle = ctx.computeOnClient(mc -> planner.isActive()) ? 0 : idle + 20;
			if (idle >= 60) {
				outcome = "done";
				break;
			}
		}
		String ended = task.name() + " " + outcome;
		ctx.runOnClient(mc -> {
			planner.cancel();
			bot.endTask(ended);
		});
		ctx.waitTicks(10);
		LOGGER.info("baseline: {} {}", task.name(), outcome);
	}
}
