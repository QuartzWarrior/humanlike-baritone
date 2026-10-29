package hb.executor.record;

import hb.common.HbCommon;
import hb.common.automation.HbAutomation;
import hb.common.io.HbData;
import hb.common.io.SessionWriter;
import hb.common.mc.McHooks;
import hb.common.mc.McInputListener;
import hb.common.mc.McKeys;
import hb.common.mc.McSession;
import hb.common.mc.McState;
import hb.common.mc.McWorldCube;
import hb.proto.v1.Button;
import hb.proto.v1.Envelope;
import hb.proto.v1.GameEvent;
import hb.proto.v1.GuiCursor;
import hb.proto.v1.Key;
import hb.proto.v1.MouseMove;
import hb.proto.v1.PauseReason;
import hb.proto.v1.Scroll;
import hb.proto.v1.SessionHeader;
import hb.proto.v1.SlotClick;
import hb.proto.v1.Source;
import hb.proto.v1.StateChange;
import hb.proto.v1.WorldDelta;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;

/**
 * Records what the bot does, in the recorder's format with source = bot (spec, Component C:
 * Controls). While a session is open it holds an automation claim, which pauses hb-recorder and
 * is the only way SessionWriter accepts a bot session, so bot records can't reach human data.
 *
 * <p>Each task (one planner command) is a segment: the world cube and tick state every tick,
 * plus every input and game event McHooks reports. Plain Baritone sends no raw input and holds no
 * key mapping; it drives the player through its own input override. Those held inputs go into
 * the tick's keys_down (alongside any real key), so bot and human ticks say the same thing about
 * which movement keys were down. The humanized executor injects real input, which lands here too.
 * Records are built on the client thread and written by one daemon thread that owns the
 * SessionWriter.
 */
public final class BotRecorder implements McInputListener {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-bot-recorder");

	private final String version;
	private final Supplier<Set<String>> plannerKeys;
	private final McWorldCube cube = new McWorldCube();

	private ExecutorService io;
	private HbAutomation.Claim claim;
	private SessionWriter writer;
	private McKeys.KeyOrder keyOrder;
	private String sessionId = "";
	private long startNanoTime;
	private long tick;
	private int segments;
	private String segmentId = "";
	private boolean inTask;
	private boolean needCube;
	private long taskTicks;
	private volatile String lastError;
	private double strength;

	/** {@code plannerKeys}: the bindings the planner holds through its own input override. */
	public BotRecorder(String version, Supplier<Set<String>> plannerKeys) {
		this.version = version;
		this.plannerKeys = plannerKeys;
	}

	public boolean inSession() {
		return claim != null;
	}

	public boolean inTask() {
		return inTask;
	}

	public String sessionId() {
		return sessionId;
	}

	public Path sessionDir() {
		return HbData.sessions().resolve(sessionId);
	}

	/** The last write failure, if any; a failed write ends nothing, but the session is incomplete. */
	public String lastError() {
		return lastError;
	}

	/**
	 * Opens a bot session (and the automation claim that comes with it). Client thread only.
	 * {@code strength} is the humanization strength driving it (0 = plain Baritone), kept in
	 * stats.json so the discriminator can tell humanized runs from plain ones.
	 */
	public void start(double strength) {
		if (inSession()) {
			return;
		}
		this.strength = strength;
		claim = HbAutomation.claim("hb-executor");
		sessionId = UUID.randomUUID().toString();
		startNanoTime = System.nanoTime();
		tick = 0;
		segments = 0;
		keyOrder = McKeys.keyOrder();
		SessionHeader header = McSession.header(sessionId, Source.SOURCE_BOT, version, keyOrder);
		io = Executors.newSingleThreadExecutor(r -> {
			Thread t = new Thread(r, "hb-bot-writer");
			t.setDaemon(true);
			return t;
		});
		HbAutomation.Claim c = claim;
		io.execute(() -> {
			try {
				writer = SessionWriter.open(HbData.sessions(), header, c, false);
			} catch (Exception e) {
				fail(e);
			}
		});
		McHooks.addListener(this);
		LOGGER.info("Bot session {} started", sessionId);
	}

	/** Starts a segment for one task, e.g. the planner command it runs. */
	public void beginTask(String task) {
		if (!inSession() || inTask) {
			return;
		}
		segments++;
		segmentId = String.format(Locale.ROOT, "seg-%04d", segments);
		inTask = true;
		taskTicks = 0;
		needCube = true;
		cube.invalidate();
		write(envelope(System.nanoTime()).setState(StateChange.newBuilder().setState(StateChange.S.RECORDING).setReason(task)));
	}

	/** Ends the task's segment; {@code outcome} says how (done, timed out, ...). */
	public void endTask(String outcome) {
		if (!inTask) {
			return;
		}
		write(envelope(System.nanoTime()).setState(StateChange.newBuilder()
			.setState(StateChange.S.PAUSED).setReason(outcome).setReasonCode(PauseReason.PAUSE_REASON_NONE)));
		inTask = false;
	}

	/** Once per client tick. */
	public void onTick() {
		if (!inSession()) {
			return;
		}
		if (inTask && McState.inWorld()) {
			long now = System.nanoTime();
			if (needCube) {
				write(envelope(now).setCube(cube.full()));
				needCube = false;
			} else {
				WorldDelta delta = cube.delta();
				if (delta != null) {
					write(envelope(now).setDelta(delta));
				}
			}
			write(envelope(now).setTickState(McState.tick(keyOrder.downMask() | keyOrder.maskOf(plannerKeys.get()))));
			taskTicks++;
		}
		tick++;
	}

	/** Ends the session, writes stats.json and releases the claim once the writer is done. */
	public void stop(String why) {
		if (!inSession()) {
			return;
		}
		endTask(why);
		write(envelope(System.nanoTime()).setState(StateChange.newBuilder().setState(StateChange.S.STOPPED).setReason(why)));
		McHooks.removeListener(this);
		Map<String, Object> stats = new LinkedHashMap<>();
		stats.put("recorder_version", version);
		stats.put("closed_by", why);
		stats.put("duration_s", Math.round((System.nanoTime() - startNanoTime) / 1e7) / 100.0);
		stats.put("ticks", tick);
		stats.put("segments", segments);
		stats.put("humanization_strength", strength);
		HbAutomation.Claim c = claim;
		io.execute(() -> {
			try {
				if (writer != null) {
					writer.close(stats);
				}
			} catch (Exception e) {
				fail(e);
			} finally {
				writer = null;
				c.close();
			}
		});
		io.shutdown();
		try {
			if (!io.awaitTermination(10, TimeUnit.SECONDS)) {
				LOGGER.warn("Bot session {} still writing after 10 s", sessionId);
			}
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		}
		claim = null;
		LOGGER.info("Bot session {} ended ({}), {} segments", sessionId, why, segments);
	}

	// --- Input and events -----------------------------------------------------------------------

	@Override
	public void onMouseTurn(long nanoTime, double dx, double dy, float dYaw, float dPitch) {
		if (inTask) {
			write(envelope(nanoTime).setMouse(MouseMove.newBuilder().setDx(dx).setDy(dy).setDYaw(dYaw).setDPitch(dPitch)));
		}
	}

	@Override
	public void onGuiCursor(long nanoTime, double x, double y) {
		if (inTask) {
			write(envelope(nanoTime).setCursor(GuiCursor.newBuilder().setX(x).setY(y)));
		}
	}

	@Override
	public void onMouseButton(long nanoTime, int button, boolean down, int mods, boolean inGui) {
		if (inTask) {
			write(envelope(nanoTime).setButton(Button.newBuilder().setButton(button).setDown(down).setMods(mods).setInGui(inGui)));
		}
	}

	@Override
	public void onScroll(long nanoTime, double dx, double dy) {
		if (inTask) {
			write(envelope(nanoTime).setScroll(Scroll.newBuilder().setDx(dx).setDy(dy)));
		}
	}

	@Override
	public void onKey(long nanoTime, int key, int scancode, int action, int mods, String binding, boolean guiKey) {
		if (!inTask || action == 2) {
			return; // OS key repeat: not an input (as in hb-recorder)
		}
		Key.Builder k = Key.newBuilder().setKey(key).setScancode(scancode).setDown(action != 0).setMods(mods);
		if (binding != null) {
			k.setBinding(binding);
		}
		write(envelope(nanoTime).setKey(k));
	}

	@Override
	public void onSlotClick(long nanoTime, SlotClick click) {
		if (inTask) {
			write(envelope(nanoTime).setSlot(click));
		}
	}

	@Override
	public void onGameEvent(long nanoTime, GameEvent event) {
		if (inTask) {
			write(envelope(nanoTime).setEvent(event));
		}
	}

	// --- Records --------------------------------------------------------------------------------

	private Envelope.Builder envelope(long nanoTime) {
		return Envelope.newBuilder().setSchema(HbCommon.SCHEMA_VERSION).setTick(tick)
			.setTNs(Math.max(0, nanoTime - startNanoTime)).setSegmentId(segmentId);
	}

	private void write(Envelope.Builder builder) {
		Envelope envelope = builder.build();
		io.execute(() -> {
			try {
				if (writer != null) {
					writer.write(envelope);
				}
			} catch (Exception e) {
				fail(e);
			}
		});
	}

	private void fail(Exception e) {
		if (lastError == null) {
			LOGGER.error("Bot session {} write failed", sessionId, e);
		}
		lastError = e.toString();
	}
}
