package hb.integration;

import hb.executor.HbExecutorClient;
import hb.executor.planner.PlannerBackend;
import net.fabricmc.fabric.api.client.gametest.v1.FabricClientGameTest;
import net.fabricmc.fabric.api.client.gametest.v1.context.ClientGameTestContext;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestSingleplayerContext;
import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.ModContainer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.List;

/**
 * Phase 0 gate: hb-recorder, hb-executor and the Baritone fork load together in a 26.2 client,
 * and Baritone is reachable through the executor's planner backend once a world is open.
 */
@SuppressWarnings("UnstableApiUsage")
public final class HbStackClientGameTest implements FabricClientGameTest {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-integration-test");
	private static final List<String> REQUIRED_MODS = List.of("minecraft", "hb-common", "hb-recorder", "hb-executor", "baritone");

	@Override
	public void runTest(ClientGameTestContext context) {
		if (RecorderBenchmarkClientGameTest.active() || BaselineRunsClientGameTest.active() || Phase4GateClientGameTest.active()
			|| BuildClientGameTest.active()) {
			return;
		}
		for (String id : REQUIRED_MODS) {
			ModContainer mod = FabricLoader.getInstance().getModContainer(id)
				.orElseThrow(() -> new AssertionError("mod not loaded: " + id));
			LOGGER.info("loaded {} {}", id, mod.getMetadata().getVersion().getFriendlyString());
		}

		try (TestSingleplayerContext singleplayer = context.worldBuilder().create()) {
			singleplayer.getConnection().waitForChunksRender();

			PlannerBackend planner = HbExecutorClient.planner();
			if (!context.computeOnClient(client -> planner.isReady())) {
				throw new AssertionError("Baritone is loaded but not bound to the player");
			}
			if (!context.computeOnClient(client -> planner.execute("version"))) {
				throw new AssertionError("Baritone did not recognize the 'version' command");
			}
			if (context.computeOnClient(client -> planner.isActive())) {
				throw new AssertionError("Baritone reports activity before any task was given");
			}

			context.takeScreenshot("hb-stack-in-world");
		}
	}
}
