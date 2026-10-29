package hb.integration;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import hb.executor.HbController;
import hb.executor.HbExecutorClient;
import hb.executor.record.BotRecorder;
import net.fabricmc.fabric.api.client.gametest.v1.FabricClientGameTest;
import net.fabricmc.fabric.api.client.gametest.v1.context.ClientGameTestContext;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestSingleplayerContext;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.block.Blocks;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Phase 4, first slice: the humanized executor drives Baritone through real input. In a fresh
 * singleplayer world it walks to a goal and chops a small tree with {@code /hb run}; the player
 * has to arrive and the logs have to break (through the game's own attack key, since the fork no
 * longer clicks for Baritone), and the bot session has to hold real mouse frames.
 */
@SuppressWarnings("UnstableApiUsage")
public final class HumanizedRunClientGameTest implements FabricClientGameTest {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-humanized-test");
	private static final int RUN_TIMEOUT_TICKS = 60 * 20;
	private static final int MAX_WALK_TICKS = 15 * 20;

	@Override
	public void runTest(ClientGameTestContext context) {
		if (RecorderBenchmarkClientGameTest.active() || BaselineRunsClientGameTest.active() || Phase4GateClientGameTest.active()
			|| BuildClientGameTest.active()) {
			return;
		}
		HbController hb = HbExecutorClient.controller();
		BotRecorder bot = HbExecutorClient.botRecorder();
		List<String> replies = new ArrayList<>();
		try (TestSingleplayerContext sp = context.worldBuilder().create()) {
			sp.getConnection().waitForChunksRender();
			sp.getServer().runCommand("difficulty peaceful");
			sp.getServer().runCommand("give @a diamond_axe");

			// Walk 20 blocks east.
			BlockPos start = context.computeOnClient(mc -> mc.player.blockPosition());
			BlockPos goal = start.offset(20, 0, 0);
			context.runOnClient(mc -> hb.handle(String.format(Locale.ROOT, "run goto %d %d %d", goal.getX(), goal.getY(), goal.getZ()), replies::add));
			int walked = context.waitFor(mc -> !hb.running(), RUN_TIMEOUT_TICKS);
			BlockPos at = context.computeOnClient(mc -> mc.player.blockPosition());
			LOGGER.info("goto: started {}, goal {}, ended {} after {} ticks; replies {}", start, goal, at, walked, replies);
			if (at.distSqr(goal) > 4) {
				throw new AssertionError("humanized goto ended at " + at + ", goal " + goal + "; " + replies);
			}
			// A person walks 20 blocks in about 5 s; the run also idles 3 s before it ends.
			if (walked > MAX_WALK_TICKS) {
				throw new AssertionError("humanized goto took " + walked + " ticks for 20 blocks (at most " + MAX_WALK_TICKS + ")");
			}
			Path walk = bot.sessionDir();
			assertHasMouseInput(walk, "goto");

			// A three-log trunk two blocks away; chop it with the axe in hand.
			BlockPos base = at.offset(0, 0, 3);
			for (int dy = 0; dy < 3; dy++) {
				sp.getServer().runCommand(String.format(Locale.ROOT, "setblock %d %d %d minecraft:oak_log", base.getX(), base.getY() + dy, base.getZ()));
			}
			context.waitTicks(5);
			// With a count, Baritone stops once it holds 3 logs; without one it explores for more forever.
			context.runOnClient(mc -> hb.handle("run mine 3 oak_log", replies::add));
			context.waitFor(mc -> !hb.running(), RUN_TIMEOUT_TICKS);
			int left = context.computeOnClient(mc -> {
				int n = 0;
				for (int dy = 0; dy < 3; dy++) {
					n += mc.level.getBlockState(base.above(dy)).is(Blocks.OAK_LOG) ? 1 : 0;
				}
				return n;
			});
			LOGGER.info("mine: {} of 3 logs left; replies {}", left, replies);
			if (left > 0) {
				throw new AssertionError(left + " of 3 logs still standing after the humanized mine run; " + replies);
			}
			assertHasMouseInput(bot.sessionDir(), "mine");
			context.takeScreenshot("hb-humanized-after-mine");
		}
	}

	/** The run's bot session closed with mouse frames in it: the camera moved through the mouse. */
	private static void assertHasMouseInput(Path session, String what) {
		try {
			JsonObject stats = JsonParser.parseString(Files.readString(session.resolve("stats.json"))).getAsJsonObject();
			JsonObject byType = stats.getAsJsonObject("records_by_type");
			long mouse = byType.has("mouse") ? byType.get("mouse").getAsLong() : 0;
			LOGGER.info("{}: bot session {} has {} mouse frames, {}", what, session.getFileName(), mouse, byType);
			if (mouse == 0) {
				throw new AssertionError(what + ": the bot session has no mouse frames, so the camera didn't move through the mouse");
			}
		} catch (java.io.IOException e) {
			throw new AssertionError(what + ": can't read the bot session's stats", e);
		}
	}
}
