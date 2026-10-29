package hb.recorder.core;

import hb.proto.v1.PauseReason;
import hb.proto.v1.SegmentFlag;

/**
 * RECORDING / PAUSED(reason) / STOPPED, driven by real input and per-tick {@link Conditions}.
 *
 * <p>Pause triggers (build spec, "Pause and AFK logic"):
 * <ul>
 *   <li>F8 (manual), automation active, window unfocused, a pause-list screen, creative/spectator:
 *       pause at once. Screens resume when they close; the others on the next real input.</li>
 *   <li>The camera stops following the mouse (freecam) or sits on another entity: pause, trimming
 *       back to the first decoupled frame; resume on input once the view is the player's again.</li>
 *   <li>Any keystroke into a text field: pause at once and stay paused until 1.5 s after the last
 *       one, so keys that follow a field closing mid-typing (the rest of the text, landing on game
 *       key bindings) are never recorded.</li>
 *   <li>No key, click or mouse movement above 0.5 px for 10 s: pause, trimming the idle tail but
 *       keeping its first 3 s.</li>
 *   <li>A movement, attack or use key held 30 s with no mouse input: flag the segment suspect_afk
 *       and pause until the mouse moves or the key is released.</li>
 *   <li>FPS under 20 for over 2 s, or a server position correction: keep recording, flag the segment
 *       degraded (once per segment and reason).</li>
 * </ul>
 * Every resume starts a new segment, timed at the input that caused it. All times are monotonic
 * nanoseconds; the class has no clock of its own, so tests drive it with synthetic times.
 */
public final class RecorderStateMachine {
	public static final long SECOND_NS = 1_000_000_000L;
	public static final long IDLE_NS = 10 * SECOND_NS;
	public static final long IDLE_KEEP_NS = 3 * SECOND_NS;
	public static final long STUCK_NS = 30 * SECOND_NS;
	public static final long LOW_FPS_NS = 2 * SECOND_NS;
	public static final long TEXT_GRACE_NS = 1_500_000_000L;
	public static final float LOW_FPS = 20;
	public static final double MOUSE_THRESHOLD_PX = 0.5;
	private static final long NEVER = Long.MIN_VALUE / 2;

	public enum State { RECORDING, PAUSED, STOPPED }

	public enum Input { KEY, BUTTON, SCROLL, MOUSE }

	/** Receives transitions; the recorder turns them into records. */
	public interface Listener {
		/** A new segment starts at {@code tNs}. */
		void resumed(long tNs);

		/**
		 * Recording paused at {@code tNs}. When {@code trimAfterNs} is set (not -1), records after it
		 * are idle tail and must be dropped; {@code tNs} equals it then.
		 */
		void paused(long tNs, PauseReason reason, String detail, long trimAfterNs);

		void flagged(long tNs, SegmentFlag.Kind kind, String reason);
	}

	private final Listener listener;
	private State state = State.PAUSED;
	private PauseReason reason = PauseReason.PAUSE_REASON_NONE;
	private String detail = "waiting for input";
	private boolean manual;
	private long lastActivityNs = NEVER;
	private long lastMouseNs = NEVER;
	private long pendingInputNs = -1;
	private long segmentStartNs = -1;
	private long lowFpsSinceNs = -1;
	private long textUntilNs = NEVER;
	private boolean flaggedLowFps;
	private boolean flaggedCorrection;

	public RecorderStateMachine(Listener listener) {
		this.listener = listener;
	}

	public State state() {
		return state;
	}

	public PauseReason reason() {
		return reason;
	}

	public String detail() {
		return detail;
	}

	public boolean manualPause() {
		return manual;
	}

	public long segmentStartNs() {
		return segmentStartNs;
	}

	/** True while paused with an input that may resume recording at the next tick. */
	public boolean resumePending() {
		return state == State.PAUSED && pendingInputNs >= 0;
	}

	/**
	 * Reports raw input. Mouse motion counts only above {@link #MOUSE_THRESHOLD_PX}.
	 *
	 * @return whether this counted as real input
	 */
	public boolean onInput(long tNs, Input kind, double magnitude) {
		if (state == State.STOPPED || kind == Input.MOUSE && magnitude <= MOUSE_THRESHOLD_PX) {
			return false;
		}
		lastActivityNs = Math.max(lastActivityNs, tNs);
		if (kind == Input.MOUSE) {
			lastMouseNs = Math.max(lastMouseNs, tNs);
		}
		if (state == State.PAUSED && pendingInputNs < 0) {
			pendingInputNs = tNs;
		}
		return true;
	}

	/**
	 * The mouse moved the camera but not the player since {@code sinceNs} (freecam). Pauses at once
	 * and drops what was recorded from that moment; {@link Conditions#cameraDetached()} keeps it
	 * paused until the view is the player's again.
	 */
	public void onCameraDetached(long sinceNs) {
		if (state == State.RECORDING) {
			long at = Math.max(sinceNs, segmentStartNs);
			pause(at, PauseReason.PAUSE_REASON_CAMERA_DETACHED, "camera detached from the player", at - 1);
		}
	}

	/** A keystroke typed into a text field: counts as activity, and stops recording at once. */
	public void onTextInput(long tNs) {
		if (state == State.STOPPED) {
			return;
		}
		lastActivityNs = Math.max(lastActivityNs, tNs);
		textUntilNs = Math.max(textUntilNs, tNs + TEXT_GRACE_NS);
		if (state == State.RECORDING) {
			pause(tNs, PauseReason.PAUSE_REASON_TEXT_INPUT, "typing in a text field", -1);
		}
	}

	public void onTick(long tNs, Conditions c) {
		if (state == State.STOPPED) {
			return;
		}
		if (c.anyKeyDown()) {
			lastActivityNs = Math.max(lastActivityNs, tNs); // a held key is not idling
		}
		PauseReason blocking = blockingReason(tNs, c);
		boolean idle = tNs - lastActivityNs >= IDLE_NS;
		boolean stuck = c.stuckHeldSinceNs() >= 0 && tNs - c.stuckHeldSinceNs() >= STUCK_NS && tNs - lastMouseNs >= STUCK_NS;

		if (state == State.RECORDING) {
			if (blocking != null) {
				pause(tNs, blocking, blockingDetail(blocking, c), -1);
			} else if (stuck) {
				listener.flagged(tNs, SegmentFlag.Kind.SUSPECT_AFK, "key held 30 s with no mouse input");
				pause(tNs, PauseReason.PAUSE_REASON_STUCK_KEY, "key held 30 s with no mouse input", -1);
			} else if (idle) {
				long trimAfter = Math.max(lastActivityNs + IDLE_KEEP_NS, segmentStartNs);
				pause(trimAfter, PauseReason.PAUSE_REASON_IDLE, "no input for 10 s", trimAfter);
			} else {
				checkFps(tNs, c.fps());
			}
			return;
		}

		if (blocking != null) {
			if (blocking != reason) {
				pause(tNs, blocking, blockingDetail(blocking, c), -1); // track the current reason
			}
		} else if (!idle && !stuck) {
			boolean resumesWithoutInput = reason == PauseReason.PAUSE_REASON_SCREEN || reason == PauseReason.PAUSE_REASON_MANUAL;
			if (pendingInputNs >= 0 || resumesWithoutInput) {
				resume(pendingInputNs >= 0 ? pendingInputNs : tNs);
			}
		}
		pendingInputNs = -1; // input that arrived while still blocked doesn't carry over
	}

	/** F8. Pausing is immediate; resuming happens at the next tick if nothing else blocks. */
	public void toggleManual(long tNs) {
		manual = !manual;
		if (manual && state == State.RECORDING) {
			pause(tNs, PauseReason.PAUSE_REASON_MANUAL, "manual (F8)", -1);
		}
	}

	public void onPositionCorrected(long tNs) {
		if (state == State.RECORDING && !flaggedCorrection) {
			flaggedCorrection = true;
			listener.flagged(tNs, SegmentFlag.Kind.DEGRADED, "server position correction");
		}
	}

	/** Starts a fresh segment without pausing, e.g. after F9 discarded the current one's start. */
	public void newSegment(long tNs) {
		if (state == State.RECORDING) {
			resume(tNs);
		}
	}

	public void stop() {
		state = State.STOPPED;
	}

	private PauseReason blockingReason(long tNs, Conditions c) {
		if (manual) {
			return PauseReason.PAUSE_REASON_MANUAL;
		}
		if (c.automation()) {
			return PauseReason.PAUSE_REASON_AUTOMATION;
		}
		if (c.cameraDetached()) {
			return PauseReason.PAUSE_REASON_CAMERA_DETACHED;
		}
		if (!c.focused()) {
			return PauseReason.PAUSE_REASON_UNFOCUSED;
		}
		if (c.pauseScreen()) {
			return PauseReason.PAUSE_REASON_SCREEN;
		}
		if (tNs < textUntilNs) {
			return PauseReason.PAUSE_REASON_TEXT_INPUT;
		}
		if (c.gameModePaused()) {
			return PauseReason.PAUSE_REASON_GAME_MODE;
		}
		return null;
	}

	private static String blockingDetail(PauseReason r, Conditions c) {
		return switch (r) {
			case PAUSE_REASON_MANUAL -> "manual (F8)";
			case PAUSE_REASON_AUTOMATION -> "automation: " + c.automationOwner();
			case PAUSE_REASON_UNFOCUSED -> "window unfocused";
			case PAUSE_REASON_SCREEN -> "screen: " + c.screen();
			case PAUSE_REASON_GAME_MODE -> "game mode: " + c.gameMode();
			case PAUSE_REASON_TEXT_INPUT -> "typing in a text field";
			case PAUSE_REASON_CAMERA_DETACHED -> "camera detached from the player";
			default -> r.name();
		};
	}

	private void checkFps(long tNs, float fps) {
		if (fps > 0 && fps < LOW_FPS) {
			if (lowFpsSinceNs < 0) {
				lowFpsSinceNs = tNs;
			} else if (!flaggedLowFps && tNs - lowFpsSinceNs > LOW_FPS_NS) {
				flaggedLowFps = true;
				listener.flagged(tNs, SegmentFlag.Kind.DEGRADED, "fps under 20 for over 2 s");
			}
		} else {
			lowFpsSinceNs = -1;
		}
	}

	private void pause(long tNs, PauseReason why, String text, long trimAfterNs) {
		state = State.PAUSED;
		reason = why;
		detail = text;
		pendingInputNs = -1;
		listener.paused(tNs, why, text, trimAfterNs);
	}

	private void resume(long tNs) {
		state = State.RECORDING;
		reason = PauseReason.PAUSE_REASON_NONE;
		detail = "";
		segmentStartNs = tNs;
		lowFpsSinceNs = -1;
		flaggedLowFps = false;
		flaggedCorrection = false;
		listener.resumed(tNs);
	}
}
