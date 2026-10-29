package hb.recorder;

import hb.common.HbCommon;
import hb.common.automation.HbAutomation;
import hb.common.io.HbData;
import hb.common.mc.McInputListener;
import hb.common.mc.McKeys;
import hb.common.mc.McScreens;
import hb.common.mc.McSession;
import hb.common.mc.McState;
import hb.common.mc.McUi;
import hb.common.mc.McWorldCube;
import hb.proto.v1.Button;
import hb.proto.v1.Discard;
import hb.proto.v1.Envelope;
import hb.proto.v1.GameEvent;
import hb.proto.v1.GuiCursor;
import hb.proto.v1.Key;
import hb.proto.v1.MouseMove;
import hb.proto.v1.PauseReason;
import hb.proto.v1.Scroll;
import hb.proto.v1.SegmentFlag;
import hb.proto.v1.SessionHeader;
import hb.proto.v1.SlotClick;
import hb.proto.v1.Source;
import hb.proto.v1.StateChange;
import hb.proto.v1.WorldDelta;
import hb.recorder.core.Conditions;
import hb.recorder.core.PerfMeter;
import hb.recorder.core.RecorderStateMachine;
import hb.recorder.core.RecorderStateMachine.Input;
import hb.recorder.core.RecorderStateMachine.State;
import hb.recorder.write.SpscRing;
import hb.recorder.write.WriterThread;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.locks.LockSupport;

/**
 * The recorder on the client thread: turns hooks into records, runs the pause state machine, and
 * hands everything to the writer thread through a lock-free ring. One session per world visit;
 * a new segment every time recording resumes.
 */
public final class Recorder implements McInputListener, RecorderStateMachine.Listener {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-recorder");
	public static final long DISCARD_NS = 60 * RecorderStateMachine.SECOND_NS;
	private static final int RING_CAPACITY = 1 << 16;
	private static final int MAX_PENDING = 1024;
	private static final int SETTINGS_CHECK_TICKS = 20;
	private static final long TICK_NS = 50_000_000L;
	// Mouse frames in a row that move the mouse without turning the player before the camera counts
	// as detached (freecam). One frame can round to zero yaw; five in a row don't.
	private static final int DECOUPLED_FRAMES = 5;
	public static final int RED = 0xFFE53935;
	public static final int GRAY = 0xFF9E9E9E;

	private final RecorderConfig config;
	private final String version;
	private final boolean mirror;
	private final SpscRing<Object> ring = new SpscRing<>(RING_CAPACITY);
	private final WriterThread writer = new WriterThread(ring);
	private final McWorldCube cube = new McWorldCube();
	private final PerfMeter perf = new PerfMeter();
	// Where the per-tick time goes; reported in stats.json under main_thread_sections.
	private final PerfMeter conditionsPerf = new PerfMeter();
	private final PerfMeter cubePerf = new PerfMeter();
	private final PerfMeter tickStatePerf = new PerfMeter();
	private final PerfMeter inputPerf = new PerfMeter();
	private final PerfMeter hudPerf = new PerfMeter();
	private final PerfMeter[] meters = {perf, conditionsPerf, cubePerf, tickStatePerf, inputPerf, hudPerf};
	private final Thread clientThread = Thread.currentThread();
	private final List<Envelope.Builder> pending = new ArrayList<>();
	private final long[] heldSince = new long[McKeys.MAX_KEYS];

	private RecorderStateMachine sm;
	private boolean inSession;
	private volatile boolean enabled = true;
	private String sessionId = "";
	private long startNanoTime;
	private long tick;
	private int segments;
	private String segmentId = "";
	private boolean needCube;
	private McKeys.KeyOrder keyOrder;
	private long settings;

	// Per-session counters for stats.json.
	private long recordingTicks;
	private double fpsSum;
	private long ringDrops;
	private long automationDrops;
	private long offThreadDrops;
	private long recorded;
	private long sessionStartNs;
	private int decoupledFrames;
	private long decoupledSinceNs;
	private boolean warnedOffThread;

	public Recorder(RecorderConfig config, String version, boolean mirror) {
		this.config = config;
		this.version = version;
		this.mirror = mirror;
		writer.start();
		// A game crash ends in System.exit without the client-stopping event. This still commits
		// the holdback and closes the session; only a hard kill loses the last ~15 s.
		Runtime.getRuntime().addShutdownHook(new Thread(() -> writer.shutdown(3_000), "hb-recorder-exit"));
	}

	// --- Lifecycle ------------------------------------------------------------------------------

	private void startSession(long now) {
		long begin = System.nanoTime();
		sessionId = UUID.randomUUID().toString();
		startNanoTime = now;
		tick = 0;
		segments = 0;
		segmentId = "";
		recordingTicks = 0;
		fpsSum = 0;
		ringDrops = 0;
		automationDrops = 0;
		offThreadDrops = 0;
		recorded = 0;
		pending.clear();
		decoupledFrames = 0;
		warnedOffThread = false;
		Arrays.fill(heldSince, -1);
		for (PerfMeter meter : meters) {
			meter.reset();
		}
		keyOrder = McKeys.keyOrder();
		settings = McSession.settingsFingerprint();
		sm = new RecorderStateMachine(this);
		cube.invalidate();
		SessionHeader header = McSession.header(sessionId, Source.SOURCE_HUMAN, version, keyOrder);
		offerCommand(new WriterThread.Open(HbData.sessions(), header, startNanoTime, mirror));
		inSession = true;
		sessionStartNs = System.nanoTime() - begin;
		LOGGER.info("Session {} started", sessionId);
	}

	/** Ends the current session, if any: STOPPED record, then stats.json via the writer. */
	public void endSession(String why) {
		if (!inSession) {
			return;
		}
		long now = System.nanoTime();
		offer(envelope(now).setState(StateChange.newBuilder()
			.setState(StateChange.S.STOPPED)
			.setReason(why)
			.setReasonCode(why.equals("disconnected") ? PauseReason.PAUSE_REASON_DISCONNECTED : PauseReason.PAUSE_REASON_NONE)));
		sm.stop();
		inSession = false;
		pending.clear();
		offerCommand(new WriterThread.Close(stats(why)));
		LOGGER.info("Session {} ended ({}): {} records, main thread {} µs/tick mean, {} µs p99",
			sessionId, why, recorded, String.format(Locale.ROOT, "%.1f", perf.meanUs()), perf.quantileUs(0.99));
	}

	public void shutdown() {
		endSession("client stopping");
		writer.shutdown(10_000);
	}

	private Map<String, Object> stats(String why) {
		Map<String, Object> m = new LinkedHashMap<>();
		m.put("recorder_version", version);
		m.put("closed_by", why);
		m.put("duration_s", Math.round((System.nanoTime() - startNanoTime) / 1e7) / 100.0);
		m.put("ticks", tick);
		m.put("recording_ticks", recordingTicks);
		m.put("segments", segments);
		m.put("fps_mean", recordingTicks == 0 ? 0 : Math.round(fpsSum / recordingTicks * 10) / 10.0);
		m.put("main_thread_per_tick", perf.toMap());
		Map<String, Object> sections = new LinkedHashMap<>();
		sections.put("conditions", conditionsPerf.toMap());
		sections.put("world_cube", cubePerf.toMap());
		sections.put("tick_state", tickStatePerf.toMap());
		sections.put("input_hooks", inputPerf.toMap());
		sections.put("hud_dot", hudPerf.toMap());
		m.put("main_thread_sections", sections);
		m.put("session_start_ms", Math.round(sessionStartNs / 1e4) / 100.0);
		m.put("ring_drops", ringDrops);
		m.put("automation_drops", automationDrops);
		m.put("off_thread_drops", offThreadDrops);
		return m;
	}

	// --- Tick -----------------------------------------------------------------------------------

	public void onTick(long now, boolean pausePressed, boolean discardPressed) {
		if (!enabled) {
			endSession("recorder disabled");
			return;
		}
		// Session start (header, key map, salt) is a one-off per world visit, reported separately
		// as session_start_ms; the per-tick budget covers everything after it.
		if (!inSession) {
			startSession(now);
		}
		long start = perf.start();
		if (tick % SETTINGS_CHECK_TICKS == 0 && settings != McSession.settingsFingerprint()) {
			endSession("settings changed");
			startSession(now);
			start = perf.start();
		}
		long t = rel(now);
		HbAutomation.poll();
		if (pausePressed) {
			sm.toggleManual(t);
		}
		if (discardPressed) {
			discard(now);
		}
		long c = conditionsPerf.start();
		Conditions conditions = conditions(now);
		conditionsPerf.stop(c);
		sm.onTick(t, conditions);
		if (!sm.resumePending()) {
			pending.clear(); // still blocked: input buffered for a resume doesn't carry over
		}
		if (sm.state() == State.RECORDING) {
			captureTick(now);
		}
		tick++;
		perf.stop(start);
		for (PerfMeter meter : meters) {
			meter.endTick();
		}
	}

	private Conditions conditions(long now) {
		long down = keyOrder.downMask();
		long stuckMask = keyOrder.stuckMask();
		long stuckSince = -1;
		for (int i = 0; i < McKeys.MAX_KEYS; i++) {
			if ((stuckMask & 1L << i) == 0) {
				continue;
			}
			if ((down & 1L << i) != 0) {
				if (heldSince[i] < 0) {
					heldSince[i] = rel(now);
				}
				stuckSince = stuckSince < 0 ? heldSince[i] : Math.min(stuckSince, heldSince[i]);
			} else {
				heldSince[i] = -1;
			}
		}
		var screen = McScreens.current();
		return new Conditions(
			McState.windowFocused(),
			McScreens.pausesRecording(screen),
			McScreens.name(screen),
			config.pauseInCreativeOrSpectator && McState.creativeOrSpectator(),
			McState.gameModeName(),
			HbAutomation.isActive(),
			HbAutomation.activeOwner(),
			keyOrder.anyDown(down),
			stuckSince,
			McState.fps(),
			decoupledFrames >= DECOUPLED_FRAMES || !McState.cameraOnPlayer());
	}

	private void captureTick(long now) {
		long c = cubePerf.start();
		if (needCube) {
			offer(envelope(now).setCube(cube.full()));
			needCube = false;
		} else {
			WorldDelta delta = cube.delta();
			if (delta != null) {
				offer(envelope(now).setDelta(delta));
			}
		}
		cubePerf.stop(c);
		long t = tickStatePerf.start();
		offer(envelope(now).setTickState(McState.tick(keyOrder.downMask())));
		tickStatePerf.stop(t);
		recordingTicks++;
		fpsSum += McState.fps();
	}

	/** F9: drop the last 60 s. Held records vanish; anything already on disk gets a tombstone. */
	private void discard(long now) {
		long to = rel(now);
		long from = Math.max(0, to - DISCARD_NS);
		Envelope tombstone = envelope(now).setDiscard(Discard.newBuilder().setFromTNs(from).setToTNs(to)).build();
		offerCommand(new WriterThread.Discard(from, to, tombstone));
		pending.clear();
		sm.newSegment(to);
		LOGGER.info("Discarded the last 60 s of session {}", sessionId);
	}

	// --- State machine output -------------------------------------------------------------------

	@Override
	public void resumed(long tNs) {
		segments++;
		segmentId = String.format(Locale.ROOT, "seg-%04d", segments);
		needCube = true;
		offer(envelopeAt(tNs, tick).setState(StateChange.newBuilder().setState(StateChange.S.RECORDING)));
		for (Envelope.Builder input : pending) {
			offer(input);
		}
		pending.clear();
	}

	@Override
	public void paused(long tNs, PauseReason reason, String detail, long trimAfterNs) {
		long atTick = tick;
		if (trimAfterNs >= 0) {
			offerCommand(new WriterThread.Trim(trimAfterNs));
			atTick = Math.max(0, tick - (rel(System.nanoTime()) - trimAfterNs) / TICK_NS);
		}
		offer(envelopeAt(tNs, atTick).setState(StateChange.newBuilder()
			.setState(StateChange.S.PAUSED).setReason(detail).setReasonCode(reason)));
	}

	@Override
	public void flagged(long tNs, SegmentFlag.Kind kind, String reason) {
		offer(envelopeAt(tNs, tick).setFlag(SegmentFlag.newBuilder().setKind(kind).setReason(reason)));
	}

	// --- Input hooks ----------------------------------------------------------------------------

	@Override
	public void onMouseTurn(long nanoTime, double dx, double dy, float dYaw, float dPitch) {
		if (!inSession) {
			return;
		}
		long start = perf.start();
		if (dx != 0) {
			// Freecam: the mouse turns a free camera while the player keeps still. Pause, and trim
			// back to the first such frame.
			if (dYaw == 0) {
				if (decoupledFrames++ == 0) {
					decoupledSinceNs = rel(nanoTime);
				}
				if (decoupledFrames == DECOUPLED_FRAMES) {
					sm.onCameraDetached(decoupledSinceNs);
				}
			} else {
				decoupledFrames = 0;
			}
		}
		sm.onInput(rel(nanoTime), Input.MOUSE, Math.hypot(dx, dy));
		record(envelope(nanoTime).setMouse(MouseMove.newBuilder().setDx(dx).setDy(dy).setDYaw(dYaw).setDPitch(dPitch)));
		perf.stop(start);
		inputPerf.stop(start);
	}

	@Override
	public void onCursorMove(long nanoTime, double dx, double dy) {
		if (inSession) {
			sm.onInput(rel(nanoTime), Input.MOUSE, Math.hypot(dx, dy));
		}
	}

	@Override
	public void onGuiCursor(long nanoTime, double x, double y) {
		if (inSession) {
			long start = perf.start();
			record(envelope(nanoTime).setCursor(GuiCursor.newBuilder().setX(x).setY(y)));
			perf.stop(start);
			inputPerf.stop(start);
		}
	}

	@Override
	public void onMouseButton(long nanoTime, int button, boolean down, int mods, boolean inGui) {
		if (!inSession) {
			return;
		}
		long start = perf.start();
		sm.onInput(rel(nanoTime), Input.BUTTON, 1);
		record(envelope(nanoTime).setButton(Button.newBuilder().setButton(button).setDown(down).setMods(mods).setInGui(inGui)));
		perf.stop(start);
		inputPerf.stop(start);
	}

	@Override
	public void onScroll(long nanoTime, double dx, double dy) {
		if (!inSession) {
			return;
		}
		long start = perf.start();
		sm.onInput(rel(nanoTime), Input.SCROLL, 1);
		record(envelope(nanoTime).setScroll(Scroll.newBuilder().setDx(dx).setDy(dy)));
		perf.stop(start);
		inputPerf.stop(start);
	}

	@Override
	public void onKey(long nanoTime, int key, int scancode, int action, int mods, String binding, boolean guiKey) {
		if (!inSession) {
			return;
		}
		if (action == 2) {
			return; // OS key repeat while held: not an input, and Tick.keys_down already shows the hold
		}
		long start = perf.start();
		sm.onInput(rel(nanoTime), Input.KEY, 1);
		Key.Builder k = Key.newBuilder().setKey(key).setScancode(scancode).setDown(action != 0).setMods(mods);
		if (binding != null) {
			k.setBinding(binding);
		}
		record(envelope(nanoTime).setKey(k));
		perf.stop(start);
		inputPerf.stop(start);
	}

	@Override
	public void onPrivateKey(long nanoTime, boolean textInput) {
		if (!inSession) {
			return;
		}
		if (textInput) {
			sm.onTextInput(rel(nanoTime)); // pauses at once; nothing about the key is recorded
		} else {
			sm.onInput(rel(nanoTime), Input.KEY, 1); // an unbound key: activity only
		}
	}

	@Override
	public void onSlotClick(long nanoTime, SlotClick click) {
		if (inSession) {
			long start = perf.start();
			record(envelope(nanoTime).setSlot(click));
			perf.stop(start);
			inputPerf.stop(start);
		}
	}

	@Override
	public void onGameEvent(long nanoTime, GameEvent event) {
		if (!inSession) {
			return;
		}
		long start = perf.start();
		if (event.hasPositionCorrected()) {
			sm.onPositionCorrected(rel(nanoTime));
		}
		if (isBoundaryEvent(event)) {
			// These happen exactly at pause boundaries (chat screen, death screen, loading screen), so
			// they're kept while paused; none of them carries input or behaviour.
			if (!HbAutomation.isActive()) {
				offer(envelope(nanoTime).setEvent(event));
			}
		} else {
			record(envelope(nanoTime).setEvent(event));
		}
		perf.stop(start);
		inputPerf.stop(start);
	}

	/** Chat/command flag, death, respawn and dimension change: recorded even while paused. */
	public static boolean isBoundaryEvent(GameEvent event) {
		return switch (event.getKindCase()) {
			case CHAT_SENT, DEATH, RESPAWN, DIMENSION_CHANGE -> true;
			default -> false;
		};
	}

	// --- Records --------------------------------------------------------------------------------

	private long rel(long nanoTime) {
		return Math.max(0, nanoTime - startNanoTime);
	}

	private Envelope.Builder envelope(long nanoTime) {
		return envelopeAt(rel(nanoTime), tick);
	}

	private Envelope.Builder envelopeAt(long tNs, long atTick) {
		return Envelope.newBuilder().setSchema(HbCommon.SCHEMA_VERSION).setTick(atTick).setTNs(tNs);
	}

	/**
	 * Input and events: written while recording, buffered while a resume is pending, otherwise
	 * dropped. Nothing is kept while automation drives the player.
	 */
	private void record(Envelope.Builder builder) {
		if (HbAutomation.isActive()) {
			automationDrops++;
			return;
		}
		if (sm.state() == State.RECORDING) {
			offer(builder);
		} else if (sm.resumePending() && pending.size() < MAX_PENDING) {
			pending.add(builder);
		}
	}

	private void offer(Envelope.Builder builder) {
		if (Thread.currentThread() != clientThread) {
			offThreadDrops++; // the ring has one producer: the client thread
			if (!warnedOffThread) {
				warnedOffThread = true;
				LOGGER.warn("Dropped a {} record offered from thread '{}'; only the client thread may record. Caller:",
					builder.getBodyCase(), Thread.currentThread().getName(), new Throwable("off-thread record"));
			}
			return;
		}
		if (ring.offer(builder.setSegmentId(segmentId).build())) {
			recorded++;
		} else {
			ringDrops++;
		}
	}

	/** Session commands must not be lost; wait briefly for the writer if the ring is ever full. */
	private void offerCommand(WriterThread.Command command) {
		for (int i = 0; i < 1000 && !ring.offer(command); i++) {
			LockSupport.parkNanos(100_000);
		}
	}

	// --- Status ---------------------------------------------------------------------------------

	/** Turns capture off entirely (ending the session) or back on (a new session starts next tick). */
	public void setEnabled(boolean value) {
		enabled = value;
	}

	/** Time spent drawing the HUD dot this frame; counted in the per-tick total. */
	public void hudCost(long ns) {
		if (inSession) {
			perf.add(ns);
			hudPerf.add(ns);
		}
	}

	public int hudColor() {
		if (!config.showHudDot || !inSession) {
			return 0;
		}
		return sm.state() == State.RECORDING ? RED : GRAY;
	}

	public boolean inSession() {
		return inSession;
	}

	public State state() {
		return inSession ? sm.state() : State.STOPPED;
	}

	public PauseReason pauseReason() {
		return inSession ? sm.reason() : PauseReason.PAUSE_REASON_NONE;
	}

	public String segmentId() {
		return segmentId;
	}

	/** Session-relative time, as written in t_ns. */
	public long sessionTimeNs() {
		return inSession ? rel(System.nanoTime()) : -1;
	}

	public Path sessionDir() {
		return inSession ? HbData.sessions().resolve(sessionId) : writer.lastSessionDir();
	}

	public PerfMeter perf() {
		return perf;
	}

	public McUi.StatusModel statusModel() {
		boolean recording = state() == State.RECORDING;
		String tooltip = !inSession ? "Not in a world"
			: recording ? "Recording (" + segmentId + ")"
			: "Paused: " + sm.detail();
		List<String> lines = new ArrayList<>();
		lines.add("Session: " + (inSession ? sessionId : "none"));
		lines.add(String.format(Locale.ROOT, "Recorded: %.1f min in %d segments", recordingTicks / 20.0 / 60.0, segments));
		lines.add(String.format(Locale.ROOT, "Written: %.2f MB (hb-data/sessions)", writer.bytesWritten() / 1e6));
		lines.add(String.format(Locale.ROOT, "Main thread: %.1f µs/tick mean, %.0f µs p99", perf.meanUs(), perf.quantileUs(0.99)));
		lines.add("F8 pause/resume, F9 discard the last 60 s");
		if (writer.lastError() != null) {
			lines.add("Write error: " + writer.lastError());
		}
		List<McUi.Toggle> toggles = List.of(
			new McUi.Toggle("Pause in creative/spectator", () -> config.pauseInCreativeOrSpectator, v -> {
				config.pauseInCreativeOrSpectator = v;
				config.save();
			}),
			new McUi.Toggle("Show HUD dot", () -> config.showHudDot, v -> {
				config.showHudDot = v;
				config.save();
			}));
		int color = !inSession ? GRAY : recording ? RED : GRAY;
		return new StatusModelRecord("Humanlike Baritone recorder", color, tooltip, lines, toggles);
	}

	private record StatusModelRecord(String title, int dotColor, String dotTooltip, List<String> lines, List<McUi.Toggle> toggles) implements McUi.StatusModel {
	}
}
