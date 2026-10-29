package hb.integration;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.protobuf.util.JsonFormat;
import com.mojang.blaze3d.platform.Window;
import hb.common.automation.HbAutomation;
import hb.common.io.HbrReader;
import hb.common.mc.McUi;
import hb.executor.HbExecutorClient;
import hb.proto.v1.Envelope;
import hb.proto.v1.PauseReason;
import hb.proto.v1.SegmentFlag;
import hb.proto.v1.SessionHeader;
import hb.proto.v1.StateChange;
import hb.recorder.HbRecorderClient;
import hb.recorder.Recorder;
import hb.recorder.core.RecorderStateMachine.State;
import net.fabricmc.fabric.api.client.gametest.v1.FabricClientGameTest;
import net.fabricmc.fabric.api.client.gametest.v1.TestInput;
import net.fabricmc.fabric.api.client.gametest.v1.context.ClientGameTestContext;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestSingleplayerContext;
import net.minecraft.client.gui.screens.ChatScreen;
import net.minecraft.client.gui.screens.DeathScreen;
import net.minecraft.client.gui.screens.GenericMessageScreen;
import net.minecraft.client.gui.screens.PauseScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.WinScreen;
import net.minecraft.client.gui.screens.inventory.AbstractSignEditScreen;
import net.minecraft.client.gui.screens.inventory.AnvilScreen;
import net.minecraft.client.gui.screens.inventory.BookEditScreen;
import net.minecraft.client.gui.screens.options.OptionsScreen;
import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;
import org.lwjgl.glfw.GLFW;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.InputStream;
import java.lang.reflect.Field;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.EnumSet;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.function.Predicate;
import java.util.function.Supplier;
import java.util.stream.Stream;

/**
 * Phase 1 gate, in a real 26.2 client: every pause trigger from the build spec's table, the HUD dot,
 * and the privacy rules, then an audit of what actually reached disk.
 */
@SuppressWarnings("UnstableApiUsage")
public final class RecorderGateClientGameTest implements FabricClientGameTest {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-integration-test");
	private static final long S = 1_000_000_000L;
	private static final List<String> SECRETS = List.of("hunter2", "signsecret", "booksecret", "anvilsecret");

	private final List<long[]> typingWindows = new ArrayList<>();
	private ClientGameTestContext ctx;
	private TestSingleplayerContext sp;

	@Override
	public void runTest(ClientGameTestContext context) {
		if (RecorderBenchmarkClientGameTest.active() || BaselineRunsClientGameTest.active() || Phase4GateClientGameTest.active()
			|| BuildClientGameTest.active()) {
			return;
		}
		ctx = context;
		Path sessionDir;
		try (TestSingleplayerContext singleplayer = context.worldBuilder().create()) {
			sp = singleplayer;
			sp.getConnection().waitForChunksRender();
			sp.getServer().runCommand("difficulty peaceful");

			check("starts paused, resumes on the first real input", () -> {
				expect(State.PAUSED, null, 5);
				nudge();
				expect(State.RECORDING, null, 5);
			});
			sessionDir = onRecorder(Recorder::sessionDir);
			hudDotIs(Recorder.RED, "recording");

			check("idle 10 s pauses (tail trimmed to 3 s)", () -> {
				sleep(10.6);
				expect(State.PAUSED, PauseReason.PAUSE_REASON_IDLE, 3);
				hudDotIs(Recorder.GRAY, "paused");
				nudge();
				expect(State.RECORDING, null, 5);
			});

			check("window unfocused pauses immediately", () -> {
				nudge();
				setWindowFlag("focused", false);
				expect(State.PAUSED, PauseReason.PAUSE_REASON_UNFOCUSED, 3);
				setWindowFlag("focused", true);
				sleep(0.5);
				expect(State.PAUSED, PauseReason.PAUSE_REASON_UNFOCUSED, 3); // focus alone doesn't resume
				nudge();
				expect(State.RECORDING, null, 5);
			});

			check("window minimized pauses immediately", () -> {
				setWindowFlag("iconified", true);
				expect(State.PAUSED, PauseReason.PAUSE_REASON_UNFOCUSED, 3);
				setWindowFlag("iconified", false);
				nudge();
				expect(State.RECORDING, null, 5);
			});

			check("camera on another entity pauses (spectating, freecam-style mods)", () -> {
				sp.getServer().runCommand("execute at @p run summon armor_stand ~3 ~ ~ {NoGravity:1b,Tags:[\"hbcam\"]}");
				sleep(0.5);
				ctx.runOnClient(mc -> {
					for (net.minecraft.world.entity.Entity e : mc.level.entitiesForRendering()) {
						if (e instanceof net.minecraft.world.entity.decoration.ArmorStand) {
							mc.setCameraEntity(e);
						}
					}
				});
				expect(State.PAUSED, PauseReason.PAUSE_REASON_CAMERA_DETACHED, 3);
				ctx.runOnClient(mc -> mc.setCameraEntity(mc.player));
				nudge();
				expect(State.RECORDING, null, 5);
				sp.getServer().runCommand("kill @e[tag=hbcam]");
			});

			List<Supplier<Screen>> pauseScreens = List.of(
				() -> new PauseScreen(true),
				() -> new ChatScreen("", false),
				() -> new OptionsScreen(null, ctx.computeOnClient(mc -> mc.options), true),
				() -> new DeathScreen(Component.literal("test"), false, ctx.computeOnClient(mc -> mc.player)),
				() -> new GenericMessageScreen(Component.literal("Loading")),
				() -> new WinScreen(false, () -> { }));
			for (Supplier<Screen> screen : pauseScreens) {
				check("pause-list screen pauses and resumes on close", () -> {
					nudge();
					ctx.setScreen(screen);
					expect(State.PAUSED, PauseReason.PAUSE_REASON_SCREEN, 3);
					ctx.setScreen(() -> null);
					expect(State.RECORDING, null, 3);
				});
			}

			check("creative mode pauses", () -> {
				sp.getServer().runCommand("gamemode creative @a");
				expect(State.PAUSED, PauseReason.PAUSE_REASON_GAME_MODE, 5);
				sp.getServer().runCommand("gamemode survival @a");
				sleep(0.3);
				nudge();
				expect(State.RECORDING, null, 5);
			});

			check("F8 pauses until pressed again", () -> {
				input().pressKey(GLFW.GLFW_KEY_F8);
				expect(State.PAUSED, PauseReason.PAUSE_REASON_MANUAL, 3);
				nudge();
				sleep(0.5);
				expect(State.PAUSED, PauseReason.PAUSE_REASON_MANUAL, 3);
				input().pressKey(GLFW.GLFW_KEY_F8);
				expect(State.RECORDING, null, 3);
			});

			check("FPS under 20 for over 2 s keeps recording, flagged degraded", () -> {
				// Real load, not a lower FPS cap: the cap is a session-header setting, and changing it
				// rightly starts a new session. Stalling the client thread 80 ms per frame gives ~12 FPS.
				long end = System.nanoTime() + 5 * S;
				while (System.nanoTime() < end) {
					ctx.runOnClient(mc -> Thread.sleep(80));
					input().moveCursor(3, 0);
					ctx.waitTick();
				}
				expect(State.RECORDING, null, 3);
				sleep(1.5); // let the FPS counter recover before the next check
			});

			check("server position correction keeps recording, flagged degraded", () -> {
				nudge();
				sp.getServer().runCommand("tp @a ~ ~ ~0.5");
				sleep(0.5);
				expect(State.RECORDING, null, 3);
			});

			check("automation claim pauses; no human data while claimed", () -> {
				nudge();
				try (HbAutomation.Claim claim = HbAutomation.claim("integration-test")) {
					expect(State.PAUSED, PauseReason.PAUSE_REASON_AUTOMATION, 3);
					nudge(); // input during automation must not resume or be recorded
					sleep(1);
					expect(State.PAUSED, PauseReason.PAUSE_REASON_AUTOMATION, 3);
				}
				sleep(0.3);
				expect(State.PAUSED, PauseReason.PAUSE_REASON_AUTOMATION, 3); // resumes only on human input
				nudge();
				expect(State.RECORDING, null, 5);
			});

			check("Baritone pathing pauses", () -> {
				BlockPos at = ctx.computeOnClient(mc -> mc.player.blockPosition());
				String go = String.format(Locale.ROOT, "goto %d %d %d", at.getX() + 24, at.getY(), at.getZ());
				if (!ctx.computeOnClient(mc -> HbExecutorClient.planner().execute(go))) {
					throw new AssertionError("Baritone rejected: " + go);
				}
				expect(State.PAUSED, PauseReason.PAUSE_REASON_AUTOMATION, 10);
				sleep(1.5);
				ctx.runOnClient(mc -> HbExecutorClient.planner().cancel());
				ctx.waitFor(mc -> !HbExecutorClient.planner().isActive(), 100);
				sleep(0.3);
				nudge();
				expect(State.RECORDING, null, 5);
			});

			check("a movement key held 30 s with no mouse input pauses (suspect_afk)", () -> {
				BlockPos p = ctx.computeOnClient(mc -> mc.player.blockPosition());
				String box = String.format(Locale.ROOT, "%d %d %d %d %d %d", p.getX() - 1, p.getY() - 1, p.getZ() - 1, p.getX() + 1, p.getY() + 2, p.getZ() + 1);
				sp.getServer().runCommand("fill " + box + " glass hollow");
				sleep(0.5);
				nudge();
				input().holdKey(o -> o.keyUp);
				sleep(29);
				expect(State.RECORDING, null, 3);
				sleep(1.6);
				expect(State.PAUSED, PauseReason.PAUSE_REASON_STUCK_KEY, 3);
				input().releaseKey(o -> o.keyUp);
				nudge();
				expect(State.RECORDING, null, 5);
				sp.getServer().runCommand("fill " + box + " air replace glass");
				sp.getServer().runCommand(String.format(Locale.ROOT, "setblock %d %d %d grass_block", p.getX(), p.getY() - 1, p.getZ()));
			});

			check("chat: typed text never recorded", () -> {
				nudge();
				input().pressKey(o -> o.keyChat);
				ctx.waitForScreen(ChatScreen.class);
				type("hunter2");
				input().pressKey(GLFW.GLFW_KEY_ENTER);
				ctx.waitFor(mc -> mc.gui.screen() == null);
			});

			check("sign editing: typed text never recorded", () -> {
				sleep(1.7); // the chat's typing grace runs out, so the placement itself is recorded
				BlockPos ground = ctx.computeOnClient(mc -> mc.player.blockPosition().below().east(2));
				sp.getServer().runCommand("give @a oak_sign");
				sp.getServer().runCommand("give @a writable_book");
				sleep(0.3);
				input().pressKey(o -> o.keyHotbarSlots[0]);
				input().lookAt(ground);
				nudge();
				input().pressMouse(GLFW.GLFW_MOUSE_BUTTON_RIGHT);
				ctx.waitForScreen(AbstractSignEditScreen.class);
				expect(State.PAUSED, PauseReason.PAUSE_REASON_SCREEN, 3);
				type("signsecret");
				ctx.setScreen(() -> null);
			});

			check("book editing: typed text never recorded", () -> {
				input().pressKey(o -> o.keyHotbarSlots[1]);
				input().lookAt(-90, 0);
				nudge();
				input().pressMouse(GLFW.GLFW_MOUSE_BUTTON_RIGHT);
				ctx.waitForScreen(BookEditScreen.class);
				type("booksecret");
				ctx.setScreen(() -> null);
			});

			check("anvil rename field: typing pauses recording, including keys after the field closes", () -> {
				sleep(1.7); // the book's typing grace runs out
				BlockPos p = ctx.computeOnClient(mc -> mc.player.blockPosition());
				BlockPos anvil = p.west(2);
				sp.getServer().runCommand(String.format(Locale.ROOT, "setblock %d %d %d anvil", anvil.getX(), anvil.getY(), anvil.getZ()));
				sleep(0.3);
				input().lookAt(anvil);
				nudge();
				input().pressMouse(GLFW.GLFW_MOUSE_BUTTON_RIGHT);
				ctx.waitForScreen(AnvilScreen.class);
				expect(State.RECORDING, null, 3); // a container screen keeps recording...
				// ...until a key goes into its name field. The "e" in the text is also the inventory
				// key: it closes the anvil, and the letters after it land on game key bindings. The
				// pause must cover those too.
				type("anvilsecret");
				expect(State.PAUSED, PauseReason.PAUSE_REASON_TEXT_INPUT, 3);
				ctx.setScreen(() -> null);
				sleep(1.7);
				nudge();
				expect(State.RECORDING, null, 5);
			});
			nudge();
			context.takeScreenshot("hb-recorder-gate");
		}

		awaitClosed(sessionDir);
		writeProbe(sessionDir);
		audit(sessionDir);

		// F9 gets its own world: it wipes the last 60 s, which would erase the evidence above.
		Path discardDir;
		long f9At;
		try (TestSingleplayerContext singleplayer = context.worldBuilder().create()) {
			sp = singleplayer;
			sp.getConnection().waitForChunksRender();
			nudge();
			expect(State.RECORDING, null, 5);
			discardDir = onRecorder(Recorder::sessionDir);
			long f9 = check("F9 discards the last 60 s", () -> {
				for (int i = 0; i < 40; i++) { // 20 s, so the oldest records are already on disk
					nudge();
					sleep(0.5);
				}
				long at = onRecorder(Recorder::sessionTimeNs);
				input().pressKey(GLFW.GLFW_KEY_F9);
				sleep(0.2);
				expect(State.RECORDING, null, 3);
				for (int i = 0; i < 4; i++) {
					nudge();
					sleep(0.5);
				}
				return at;
			});
			f9At = f9;
		}
		awaitClosed(discardDir);
		auditDiscard(discardDir, f9At);
	}

	/** Leaving the world ends the session; the writer closes it asynchronously. */
	private void awaitClosed(Path dir) {
		long deadline = System.nanoTime() + 20 * S;
		while (!Files.exists(dir.resolve("stats.json")) && System.nanoTime() < deadline) {
			ctx.waitTick();
		}
		require(Files.exists(dir.resolve("stats.json")), "stats.json written on close of " + dir.getFileName());
	}

	// --- Disk audit -----------------------------------------------------------------------------

	private void audit(Path dir) {
		try {
			List<Envelope> records = readAll(dir);
			LOGGER.info("auditing {} records in {}", records.size(), dir);

			// Nothing but state changes, F9 tombstones and boundary events (chat flag, death, respawn,
			// dimension change) while paused.
			boolean paused = true;
			Set<PauseReason> reasons = EnumSet.noneOf(PauseReason.class);
			Set<String> segments = new HashSet<>();
			int resumes = 0;
			for (Envelope e : records) {
				if (e.hasState()) {
					StateChange s = e.getState();
					paused = s.getState() != StateChange.S.RECORDING;
					if (s.getState() == StateChange.S.PAUSED) {
						reasons.add(s.getReasonCode());
					} else if (s.getState() == StateChange.S.RECORDING) {
						resumes++;
						require(segments.add(e.getSegmentId()), "segment id reused: " + e.getSegmentId());
					}
				} else if (paused && !e.hasDiscard() && !(e.hasEvent() && Recorder.isBoundaryEvent(e.getEvent()))) {
					throw new AssertionError("recorded while paused: " + e.getBodyCase() + " at t=" + e.getTNs());
				}
			}
			for (PauseReason r : List.of(PauseReason.PAUSE_REASON_IDLE, PauseReason.PAUSE_REASON_UNFOCUSED, PauseReason.PAUSE_REASON_SCREEN,
				PauseReason.PAUSE_REASON_GAME_MODE, PauseReason.PAUSE_REASON_STUCK_KEY, PauseReason.PAUSE_REASON_AUTOMATION, PauseReason.PAUSE_REASON_MANUAL, PauseReason.PAUSE_REASON_TEXT_INPUT, PauseReason.PAUSE_REASON_CAMERA_DETACHED)) {
				require(reasons.contains(r), "no " + r + " pause on disk");
			}
			require(records.stream().anyMatch(e -> e.hasState() && e.getState().getReason().contains("LevelLoadingScreen")),
				"the world-loading screen paused recording");
			LOGGER.info("{} segments, pause reasons on disk: {}", resumes, reasons);

			// Idle: the tail after last input + 3 s never reached disk.
			Envelope idle = records.stream().filter(e -> e.hasState() && e.getState().getReasonCode() == PauseReason.PAUSE_REASON_IDLE).findFirst().orElseThrow();
			long lastInput = records.stream().filter(e -> e.getSegmentId().equals(idle.getSegmentId()) && (e.hasMouse() || e.hasKey() || e.hasButton()))
				.mapToLong(Envelope::getTNs).max().orElseThrow();
			long kept = idle.getTNs() - lastInput;
			require(Math.abs(kept - 3 * S) < S / 10, "idle tail kept " + kept / 1e9 + " s, expected 3 s");
			require(records.stream().noneMatch(e -> e.getSegmentId().equals(idle.getSegmentId()) && e.getTNs() > idle.getTNs()), "idle tail trimmed");

			// Flags.
			List<SegmentFlag> flags = records.stream().filter(Envelope::hasFlag).map(Envelope::getFlag).toList();
			require(flags.stream().anyMatch(f -> f.getKind() == SegmentFlag.Kind.SUSPECT_AFK), "suspect_afk flag");
			require(flags.stream().anyMatch(f -> f.getKind() == SegmentFlag.Kind.DEGRADED && f.getReason().contains("fps")), "low-FPS degraded flag");
			require(flags.stream().anyMatch(f -> f.getKind() == SegmentFlag.Kind.DEGRADED && f.getReason().contains("position")), "position-correction degraded flag");

			// Privacy.
			for (Envelope e : records) {
				if (e.hasKey()) {
					require(!e.getKey().getBinding().isEmpty() || GUI_KEYS.contains(e.getKey().getKey()), "unbound non-GUI key " + e.getKey().getKey());
					for (long[] w : typingWindows) {
						require(e.getTNs() < w[0] || e.getTNs() > w[1], "key recorded while typing into a text field: " + e.getKey());
					}
				}
			}
			for (Path file : sessionFiles(dir)) {
				if (file.getFileName().toString().equals("privacy-probe.json")) {
					continue; // the test's own list of what it typed
				}
				byte[] data = file.toString().endsWith(".zst") ? decompress(file) : Files.readAllBytes(file);
				String text = new String(data, StandardCharsets.ISO_8859_1);
				for (String secret : SECRETS) {
					require(!text.contains(secret), "'" + secret + "' found in " + file.getFileName());
				}
			}
			require(records.stream().anyMatch(e -> e.hasEvent() && e.getEvent().hasChatSent() && !e.getEvent().getChatSent().getIsCommand()), "chat flag recorded");
			require(records.stream().anyMatch(e -> e.hasEvent() && e.getEvent().hasBlockPlaced() && e.getEvent().getBlockPlaced().getBlock().contains("sign")), "sign placement recorded");
			require(records.stream().anyMatch(e -> e.hasEvent() && e.getEvent().hasScreenOpened() && e.getEvent().getScreenOpened().getScreen().contains("AnvilScreen")), "anvil screen recorded");
			LOGGER.info("privacy: {} key records, {} typing windows, no secrets in {} files", records.stream().filter(Envelope::hasKey).count(), typingWindows.size(), sessionFiles(dir).size());

			// Header: hashed world id, settings and key order.
			SessionHeader.Builder header = SessionHeader.newBuilder();
			JsonFormat.parser().merge(Files.readString(dir.resolve("header.json")), header);
			require(header.getWorldIdHash().matches("[0-9a-f]{32}"), "world id is a salted hash");
			require(!Files.readString(dir.resolve("header.json")).contains("New World"), "no raw world name in the header");
			require(header.getKeyOrderCount() > 10 && header.getKeyOrderCount() <= 64, "key order recorded");
			LOGGER.info("graphics backend: {}", header.getGraphicsBackend());

			// Java side of the round trip: the mirror holds exactly the records on disk.
			List<String> mirror = Files.readAllLines(dir.resolve("mirror.jsonl"));
			require(mirror.size() == records.size(), "mirror has " + mirror.size() + " lines for " + records.size() + " records");
			for (int i = 0; i < mirror.size(); i++) {
				Envelope.Builder b = Envelope.newBuilder();
				JsonFormat.parser().merge(mirror.get(i), b);
				require(b.build().equals(records.get(i)), "mirror line " + i + " differs");
			}
			LOGGER.info("audit passed: {} records, {} segments", records.size(), resumes);
		} catch (AssertionError e) {
			throw e;
		} catch (Exception e) {
			throw new AssertionError("audit failed", e);
		}
	}

	/** F9: what was still held never reaches disk; what was already written gets a tombstone. */
	private void auditDiscard(Path dir, long f9At) {
		try {
			List<Envelope> records = readAll(dir);
			Envelope tomb = records.stream().filter(Envelope::hasDiscard).findFirst().orElseThrow(() -> new AssertionError("no F9 tombstone"));
			long from = tomb.getDiscard().getFromTNs();
			long to = tomb.getDiscard().getToTNs();
			require(from == Math.max(0, to - 60 * S), "F9 covers the last 60 s");
			require(to >= f9At && to - f9At < 2 * S, "tombstone stamped at the F9 press");
			int tombIndex = records.indexOf(tomb);
			List<Envelope> before = records.subList(0, tombIndex);
			require(before.stream().anyMatch(e -> e.getTNs() >= from && e.hasTickState()), "older records had reached disk (the tombstone covers them)");
			require(before.stream().noneMatch(e -> e.getTNs() > to - 14 * S && e.getTNs() <= to),
				"records from the 15 s holdback before F9 never reach disk");
			List<Envelope> after = records.subList(tombIndex + 1, records.size());
			require(after.stream().anyMatch(e -> e.hasState() && e.getState().getState() == StateChange.S.RECORDING), "a new segment starts after F9");
			require(after.stream().anyMatch(Envelope::hasTickState), "recording continues after F9");
			LOGGER.info("F9 audit passed: tombstone [{} s, {} s], {} records before it, {} after", from / 1e9, to / 1e9, before.size(), after.size());
		} catch (AssertionError e) {
			throw e;
		} catch (Exception e) {
			throw new AssertionError("F9 audit failed", e);
		}
	}

	private static final Set<Integer> GUI_KEYS = Set.of(256, 257, 335, 258, 262, 263, 264, 265, 340, 344, 341, 345, 342, 346);

	private static List<Envelope> readAll(Path dir) throws Exception {
		List<Envelope> all = new ArrayList<>();
		for (Path chunk : sessionFiles(dir).stream().filter(p -> p.toString().endsWith(".hbr.zst")).sorted().toList()) {
			try (InputStream in = Files.newInputStream(chunk); HbrReader reader = HbrReader.zstd(in)) {
				all.addAll(reader.readAll());
			}
		}
		return all;
	}

	private static byte[] decompress(Path chunk) throws Exception {
		try (InputStream in = new com.github.luben.zstd.ZstdInputStream(Files.newInputStream(chunk))) {
			return in.readAllBytes();
		}
	}

	private static List<Path> sessionFiles(Path dir) throws Exception {
		try (Stream<Path> files = Files.list(dir)) {
			return files.filter(Files::isRegularFile).toList();
		}
	}

	/** Typing windows and secrets, so `hbpipe verify` can repeat the privacy check in Python. */
	private void writeProbe(Path dir) {
		JsonObject probe = new JsonObject();
		JsonArray windows = new JsonArray();
		for (long[] w : typingWindows) {
			JsonArray pair = new JsonArray();
			pair.add(w[0]);
			pair.add(w[1]);
			windows.add(pair);
		}
		probe.add("windows", windows);
		JsonArray secrets = new JsonArray();
		SECRETS.forEach(secrets::add);
		probe.add("secrets", secrets);
		try {
			Files.writeString(dir.resolve("privacy-probe.json"), probe + "\n");
		} catch (Exception e) {
			throw new AssertionError(e);
		}
	}

	// --- Helpers --------------------------------------------------------------------------------

	private interface Step {
		void run() throws Exception;
	}

	private interface ValueStep<T> {
		T run() throws Exception;
	}

	private void check(String what, Step step) {
		check(what, () -> {
			step.run();
			return null;
		});
	}

	private <T> T check(String what, ValueStep<T> step) {
		LOGGER.info("check: {}", what);
		try {
			return step.run();
		} catch (AssertionError | RuntimeException e) {
			throw new AssertionError(what + ": " + e.getMessage(), e);
		} catch (Exception e) {
			throw new AssertionError(what, e);
		}
	}

	private static void require(boolean ok, String what) {
		if (!ok) {
			throw new AssertionError(what);
		}
	}

	private TestInput input() {
		return ctx.getInput();
	}

	private <T> T onRecorder(java.util.function.Function<Recorder, T> f) {
		return ctx.computeOnClient(mc -> f.apply(HbRecorderClient.recorder()));
	}

	private void expect(State state, PauseReason reason, double withinSeconds) {
		Predicate<Recorder> ok = r -> r.state() == state && (reason == null || r.pauseReason() == reason);
		long deadline = System.nanoTime() + (long) (withinSeconds * S);
		while (!onRecorder(ok::test)) {
			if (System.nanoTime() > deadline) {
				throw new AssertionError("expected " + state + (reason == null ? "" : " " + reason) + " within " + withinSeconds
					+ " s, recorder is " + onRecorder(Recorder::state) + " " + onRecorder(Recorder::pauseReason));
			}
			ctx.waitTick();
		}
	}

	/** Real mouse motion through the game's own input path (above the 0.5 px threshold). */
	private void nudge() {
		input().moveCursor(12, 0);
		ctx.waitTick();
		input().moveCursor(-12, 0);
		ctx.waitTick();
	}

	/** Waits in wall-clock time; the recorder's thresholds are wall-clock too. */
	private void sleep(double seconds) {
		long end = System.nanoTime() + (long) (seconds * S);
		while (System.nanoTime() < end) {
			ctx.waitTick();
		}
	}

	/** Presses each letter's key (what the privacy filter must drop) and types its character. */
	private void type(String text) {
		long from = onRecorder(Recorder::sessionTimeNs);
		for (char c : text.toCharArray()) {
			int key = Character.isLetter(c) ? Character.toUpperCase(c) : c;
			input().pressKey(key);
			input().typeChar(c);
		}
		long to = onRecorder(Recorder::sessionTimeNs);
		typingWindows.add(new long[]{from, to});
	}

	/** Window.focused / iconified, as the GLFW callbacks would set them (the test harness swallows real focus events). */
	private void setWindowFlag(String field, boolean value) {
		try {
			ctx.runOnClient(mc -> {
				Field f = Window.class.getDeclaredField(field);
				f.setAccessible(true);
				f.setBoolean(mc.getWindow(), value);
			});
		} catch (ReflectiveOperationException e) {
			throw new AssertionError(e);
		}
		ctx.waitTick();
	}

	/** The HUD dot is drawn through Fabric's HUD API; check its pixels in a real screenshot. */
	private void hudDotIs(int argb, String state) {
		Path shot = ctx.takeScreenshot("hb-hud-" + state);
		int scale = ctx.computeOnClient(mc -> mc.getWindow().getGuiScale());
		try {
			BufferedImage image = ImageIO.read(shot.toFile());
			int x = (McUi.DOT_X + McUi.DOT_SIZE / 2) * scale;
			int y = (McUi.DOT_Y + McUi.DOT_SIZE / 2) * scale;
			int rgb = image.getRGB(x, y) & 0xFFFFFF;
			int want = argb & 0xFFFFFF;
			int diff = Math.abs((rgb >> 16) - (want >> 16)) + Math.abs((rgb >> 8 & 0xFF) - (want >> 8 & 0xFF)) + Math.abs((rgb & 0xFF) - (want & 0xFF));
			require(diff < 24, String.format(Locale.ROOT, "HUD dot while %s is #%06X, expected #%06X", state, rgb, want));
		} catch (java.io.IOException e) {
			throw new AssertionError(e);
		}
	}
}
