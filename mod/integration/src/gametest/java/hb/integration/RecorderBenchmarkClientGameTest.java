package hb.integration;

import com.google.gson.GsonBuilder;
import hb.recorder.HbRecorderClient;
import net.fabricmc.fabric.api.client.gametest.v1.FabricClientGameTest;
import net.fabricmc.fabric.api.client.gametest.v1.TestInput;
import net.fabricmc.fabric.api.client.gametest.v1.context.ClientGameTestContext;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestSingleplayerContext;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.CrashReport;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.inventory.InventoryScreen;
import org.lwjgl.glfw.GLFW;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Files;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Random;

/**
 * Phase 1 performance gate: a long scripted session of input-heavy play (sprinting with constant
 * mouse motion, jumping, mining, placing, inventory clicks, animals in range).
 *
 * <p>Off unless asked for, because it runs for real minutes:
 * <pre>
 *   ./gradlew :mod:integration:runClientGameTest -Phb.benchMinutes=60          one continuous session
 *   ./gradlew :mod:integration:runClientGameTest -Phb.benchMinutes=20 -Phb.benchAb=true
 *                                                  alternate 2-minute blocks with the recorder on/off
 *   ./gradlew :mod:integration:runClientGameTest -Phb.benchMinutes=2 -Phb.benchCrash=exit
 *                                                  end in a game crash, or with =halt a hard kill
 *                                                  (the build then fails; check the session after)
 * </pre>
 * The recorded session's stats.json holds main-thread µs per tick and bytes on disk
 * (`uv run hbpipe inspect` reports them against the budget); the A/B mode writes FPS with and
 * without the recorder to hb-bench.json in the run directory.
 */
@SuppressWarnings("UnstableApiUsage")
public final class RecorderBenchmarkClientGameTest implements FabricClientGameTest {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-bench");
	private static final long S = 1_000_000_000L;
	private static final long BLOCK_NS = 120 * S;

	private final Random random = new Random(26_2);
	private ClientGameTestContext ctx;

	/** True when this run is a benchmark; the functional tests skip themselves then. */
	static boolean active() {
		return Integer.getInteger("hb.bench.minutes", 0) > 0;
	}

	@Override
	public void runTest(ClientGameTestContext context) {
		if (!active()) {
			return;
		}
		ctx = context;
		long minutes = Integer.getInteger("hb.bench.minutes");
		boolean ab = Boolean.getBoolean("hb.bench.ab");
		// Uncapped, so FPS reflects cost rather than the limiter.
		ctx.runOnClient(mc -> {
			mc.options.framerateLimit().set(260);
			mc.options.enableVsync().set(false);
		});

		double[] fpsSum = new double[2];
		long[] fpsSamples = new long[2];
		try (TestSingleplayerContext sp = context.worldBuilder().create()) {
			sp.getConnection().waitForChunksRender();
			sp.getServer().runCommand("difficulty peaceful");
			sp.getServer().runCommand("give @a diamond_pickaxe");
			sp.getServer().runCommand("give @a dirt 64");
			for (String mob : new String[]{"cow", "sheep", "pig", "chicken", "rabbit"}) {
				for (int i = 0; i < 3; i++) {
					sp.getServer().runCommand(String.format(Locale.ROOT, "summon %s ~%d ~ ~%d", mob, random.nextInt(17) - 8, random.nextInt(17) - 8));
				}
			}

			long start = System.nanoTime();
			long end = start + minutes * 60 * S;
			long nextSample = start + S;
			int loop = 0;
			while (System.nanoTime() < end) {
				boolean on = !ab || ((System.nanoTime() - start) / BLOCK_NS) % 2 == 0;
				ctx.runOnClient(mc -> HbRecorderClient.recorder().setEnabled(on));
				if (++loop % 4 == 0) {
					// Keep animals in range as the player moves on, and keep blocks to place.
					sp.getServer().runCommand("execute at @p run summon cow ~4 ~ ~4");
					sp.getServer().runCommand("execute at @p run summon sheep ~-4 ~ ~3");
					sp.getServer().runCommand("give @a dirt 16");
				}
				playLoop(); // ~25 s of play
				if (System.nanoTime() >= nextSample) {
					int fps = ctx.computeOnClient(mc -> mc.getFps());
					fpsSum[on ? 1 : 0] += fps;
					fpsSamples[on ? 1 : 0]++;
					nextSample = System.nanoTime() + S;
				}
				LOGGER.info("bench: {} of {} min", String.format(Locale.ROOT, "%.1f", (System.nanoTime() - start) / 60e9), minutes);
			}
			ctx.runOnClient(mc -> HbRecorderClient.recorder().setEnabled(true));
			String crash = System.getProperty("hb.bench.crash", "");
			if (crash.equals("exit")) {
				// The game's own crash path: crash report, then System.exit. The client-stopping event
				// never fires, so the recorder's exit hook has to close the session. (Not
				// emergencySaveAndCrash: its world save waits on the integrated server, which the test
				// framework holds in lockstep with this blocked test thread.)
				LOGGER.info("bench: crashing the game on purpose");
				ctx.runOnClient(mc -> Minecraft.crash(mc, mc.gameDirectory, CrashReport.forThrowable(new IllegalStateException("hb crash test"), "hb crash test"), -1));
			} else if (crash.equals("halt")) {
				// Like a killed process or a power cut: no hooks run at all.
				LOGGER.info("bench: halting the JVM on purpose");
				ctx.runOnClient(mc -> Runtime.getRuntime().halt(3));
			}
		}

		Map<String, Object> report = new LinkedHashMap<>();
		report.put("minutes", minutes);
		report.put("ab", ab);
		if (ab) {
			double off = fpsSum[0] / Math.max(1, fpsSamples[0]);
			double on = fpsSum[1] / Math.max(1, fpsSamples[1]);
			report.put("fps_recorder_off", off);
			report.put("fps_recorder_on", on);
			report.put("fps_loss_percent", (off - on) / off * 100);
			report.put("samples_off", fpsSamples[0]);
			report.put("samples_on", fpsSamples[1]);
			LOGGER.info("bench FPS: off {} on {} loss {}%", off, on, String.format(Locale.ROOT, "%.2f", (off - on) / off * 100));
		}
		try {
			Files.writeString(FabricLoader.getInstance().getGameDir().resolve("hb-bench.json"),
				new GsonBuilder().setPrettyPrinting().create().toJson(report) + "\n");
		} catch (Exception e) {
			throw new AssertionError(e);
		}
	}

	/** One round of play, sampling FPS once a second along the way. */
	private void playLoop() {
		TestInput in = ctx.getInput();
		// Sprint-jump across the field with constant camera motion.
		in.holdKey(o -> o.keyUp);
		in.holdKey(o -> o.keySprint);
		for (int t = 0; t < 160; t++) {
			look(random.nextGaussian() * 6, random.nextGaussian() * 1.5);
			if (t % 25 == 0) {
				in.pressKey(o -> o.keyJump);
			}
		}
		in.releaseKey(o -> o.keySprint);
		in.releaseKey(o -> o.keyUp);

		// Mine the ground in front, three spots.
		in.pressKey(o -> o.keyHotbarSlots[0]);
		for (int spot = 0; spot < 3; spot++) {
			ctx.runOnClient(mc -> mc.player.setXRot(55 + random.nextFloat() * 20));
			in.holdMouse(GLFW.GLFW_MOUSE_BUTTON_LEFT);
			for (int t = 0; t < 30; t++) {
				look(random.nextGaussian() * 0.8, random.nextGaussian() * 0.4);
			}
			in.releaseMouse(GLFW.GLFW_MOUSE_BUTTON_LEFT);
			look(random.nextGaussian() * 20, 0);
		}

		// Place a few blocks.
		in.pressKey(o -> o.keyHotbarSlots[1]);
		for (int i = 0; i < 5; i++) {
			look(random.nextGaussian() * 10, 0);
			in.pressMouse(GLFW.GLFW_MOUSE_BUTTON_RIGHT);
			look(0, 0);
		}

		// Inventory: move the cursor, click two slots, close.
		in.pressKey(o -> o.keyInventory);
		ctx.waitFor(mc -> mc.gui.screen() instanceof InventoryScreen, 40);
		for (int i = 0; i < 2; i++) {
			for (int t = 0; t < 10; t++) {
				in.moveCursor(random.nextGaussian() * 12, random.nextGaussian() * 8);
				ctx.waitTick();
			}
			in.pressMouse(GLFW.GLFW_MOUSE_BUTTON_LEFT);
		}
		in.pressKey(o -> o.keyInventory);
		ctx.waitTick();

		// Turn around over about a second; level the view again.
		for (int t = 0; t < 20; t++) {
			look(45 + random.nextGaussian() * 3, 0);
		}
		ctx.runOnClient(mc -> mc.player.setXRot(5));
	}

	private void look(double dx, double dy) {
		ctx.getInput().moveCursor(dx, dy);
		ctx.waitTick();
	}
}
