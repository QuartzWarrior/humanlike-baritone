package hb.integration;

import hb.common.mc.McInput;
import hb.executor.HbController;
import hb.executor.HbExecutorClient;
import net.fabricmc.fabric.api.client.gametest.v1.FabricClientGameTest;
import net.fabricmc.fabric.api.client.gametest.v1.context.ClientGameTestContext;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestDedicatedServerConnection;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestDedicatedServerContext;
import net.minecraft.core.BlockPos;
import net.minecraft.server.permissions.Permissions;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * A server on this computer counts as one the player owns: on a dedicated server reached through
 * localhost, with no operator permission at all, {@code /hb run} starts and the bot walks.
 */
@SuppressWarnings("UnstableApiUsage")
public final class LocalServerClientGameTest implements FabricClientGameTest {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-local-server-test");
	private static final int RUN_TIMEOUT_TICKS = 30 * 20;

	@Override
	public void runTest(ClientGameTestContext context) {
		if (RecorderBenchmarkClientGameTest.active() || BaselineRunsClientGameTest.active() || Phase4GateClientGameTest.active()
			|| BuildClientGameTest.active()) {
			return;
		}
		HbController hb = HbExecutorClient.controller();
		List<String> replies = new ArrayList<>();
		try (TestDedicatedServerContext server = context.worldBuilder().createServer();
			 TestDedicatedServerConnection connection = server.connect()) {
			connection.waitForChunksRender();
			server.runCommand("difficulty peaceful");
			boolean[] where = context.computeOnClient(mc -> new boolean[]{
				mc.hasSingleplayerServer(),
				mc.player.permissions().hasPermission(Permissions.COMMANDS_OWNER),
				McInput.ownsWorld(),
			});
			String address = context.computeOnClient(mc -> String.valueOf(mc.getConnection().getConnection().getRemoteAddress()));
			LOGGER.info("connected to {}: singleplayer {}, owner-level operator {}, owns the world {}", address, where[0], where[1], where[2]);
			if (where[0] || where[1]) {
				throw new AssertionError("expected a dedicated server without operator permission (singleplayer " + where[0] + ", owner " + where[1] + ")");
			}
			if (!where[2]) {
				throw new AssertionError("a server on this computer (" + address + ") should count as owned");
			}

			BlockPos start = context.computeOnClient(mc -> mc.player.blockPosition());
			BlockPos goal = start.offset(6, 0, 0);
			context.runOnClient(mc -> hb.handle(String.format(Locale.ROOT, "run goto %d %d %d", goal.getX(), goal.getY(), goal.getZ()), replies::add));
			if (replies.isEmpty() || !replies.getFirst().startsWith("Running")) {
				throw new AssertionError("/hb run didn't start on the local server: " + replies);
			}
			context.waitFor(mc -> !hb.running(), RUN_TIMEOUT_TICKS);
			BlockPos at = context.computeOnClient(mc -> mc.player.blockPosition());
			LOGGER.info("goto on the local server: started {}, goal {}, ended {}; replies {}", start, goal, at, replies);
			if (at.distSqr(start) < 9) {
				throw new AssertionError("the bot didn't walk on the local server: " + start + " -> " + at + "; " + replies);
			}
		}
	}
}
