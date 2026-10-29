package hb.recorder.core;

import hb.proto.v1.PauseReason;
import hb.proto.v1.SegmentFlag;
import hb.recorder.core.RecorderStateMachine.Input;
import hb.recorder.core.RecorderStateMachine.State;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static hb.recorder.core.RecorderStateMachine.SECOND_NS;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RecorderStateMachineTest {
	private static final long TICK = 50_000_000L;
	private final List<String> log = new ArrayList<>();
	private final List<Long> resumes = new ArrayList<>();
	private final List<long[]> trims = new ArrayList<>();
	private final List<SegmentFlag.Kind> flags = new ArrayList<>();
	private RecorderStateMachine sm;
	private long now;

	@BeforeEach
	void setUp() {
		sm = new RecorderStateMachine(new RecorderStateMachine.Listener() {
			@Override
			public void resumed(long tNs) {
				resumes.add(tNs);
				log.add("resume");
			}

			@Override
			public void paused(long tNs, PauseReason reason, String detail, long trimAfterNs) {
				log.add("pause " + reason.name());
				if (trimAfterNs >= 0) {
					trims.add(new long[]{tNs, trimAfterNs});
				}
			}

			@Override
			public void flagged(long tNs, SegmentFlag.Kind kind, String reason) {
				flags.add(kind);
			}
		});
		now = 1000 * SECOND_NS;
	}

	private void tick(Conditions c) {
		now += TICK;
		sm.onTick(now, c);
	}

	/** Runs ticks for {@code seconds}, feeding a real mouse movement every tick if {@code active}. */
	private void play(double seconds, Conditions c, boolean active) {
		long end = now + (long) (seconds * SECOND_NS);
		while (now < end) {
			if (active) {
				sm.onInput(now + TICK / 2, Input.MOUSE, 3);
			}
			tick(c);
		}
	}

	private void startRecording() {
		sm.onInput(now + 1, Input.KEY, 1);
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void startsPausedAndResumesAtTheFirstRealInput() {
		assertEquals(State.PAUSED, sm.state());
		tick(Conditions.playing());
		assertEquals(State.PAUSED, sm.state(), "no input yet");
		long inputAt = now + 7;
		sm.onInput(inputAt, Input.BUTTON, 1);
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state());
		assertEquals(List.of(inputAt), resumes, "the segment starts at the input, not at the tick");
	}

	@Test
	void mouseMotionAtOrBelowHalfAPixelIsNotInput() {
		sm.onInput(now + 1, Input.MOUSE, 0.5);
		tick(Conditions.playing());
		assertEquals(State.PAUSED, sm.state());
		sm.onInput(now + 1, Input.MOUSE, 0.51);
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void idleTenSecondsPausesAndTrimsAllButTheFirstThree() {
		startRecording();
		play(5, Conditions.playing(), true);
		long lastInput = now - TICK / 2;
		play(9.9, Conditions.playing(), false);
		assertEquals(State.RECORDING, sm.state(), "9.9 s without input is not idle yet");
		play(0.2, Conditions.playing(), false);
		assertEquals(State.PAUSED, sm.state());
		assertEquals(PauseReason.PAUSE_REASON_IDLE, sm.reason());
		assertEquals(1, trims.size());
		assertEquals(lastInput + 3 * SECOND_NS, trims.get(0)[1], "keeps the first 3 s of the idle tail");
		assertEquals(trims.get(0)[1], trims.get(0)[0], "the pause is stamped at the trim point");
	}

	@Test
	void aHeldKeyIsNotIdle() {
		startRecording();
		play(20, Conditions.playing().withKeys(true, -1), false);
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void realInputAfterIdleResumesUnderANewSegment() {
		startRecording();
		play(11, Conditions.playing(), false);
		assertEquals(State.PAUSED, sm.state());
		sm.onInput(now + 5, Input.KEY, 1);
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state());
		assertEquals(2, resumes.size());
	}

	@Test
	void unfocusedPausesImmediatelyAndResumesOnlyOnInputAfterFocus() {
		startRecording();
		tick(Conditions.playing().withFocused(false));
		assertEquals(PauseReason.PAUSE_REASON_UNFOCUSED, sm.reason());
		sm.onInput(now + 1, Input.MOUSE, 10); // input while unfocused doesn't count
		tick(Conditions.playing().withFocused(false));
		tick(Conditions.playing());
		assertEquals(State.PAUSED, sm.state(), "focus alone doesn't resume");
		sm.onInput(now + 1, Input.BUTTON, 1);
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void pauseScreenPausesAndResumesWhenItCloses() {
		startRecording();
		play(1, Conditions.playing(), true);
		tick(Conditions.playing().withScreen("PauseScreen", true));
		assertEquals(PauseReason.PAUSE_REASON_SCREEN, sm.reason());
		sm.onInput(now + 1, Input.KEY, 1); // e.g. Escape closing the menu
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void aContainerScreenKeepsRecording() {
		startRecording();
		play(2, Conditions.playing().withScreen("InventoryScreen", false), true);
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void creativeOrSpectatorPauses() {
		startRecording();
		tick(Conditions.playing().withGameMode("creative", true));
		assertEquals(PauseReason.PAUSE_REASON_GAME_MODE, sm.reason());
		play(1, Conditions.playing().withGameMode("creative", true), true);
		assertEquals(State.PAUSED, sm.state());
		sm.onInput(now + 1, Input.KEY, 1);
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void stuckKeyFlagsSuspectAfkAndPausesUntilTheMouseMoves() {
		startRecording();
		long heldSince = now;
		Conditions stuck = Conditions.playing().withKeys(true, heldSince);
		play(29.9, stuck, false);
		assertEquals(State.RECORDING, sm.state());
		play(0.2, stuck, false);
		assertEquals(PauseReason.PAUSE_REASON_STUCK_KEY, sm.reason());
		assertEquals(List.of(SegmentFlag.Kind.SUSPECT_AFK), flags);

		sm.onInput(now + 1, Input.KEY, 1); // another key alone doesn't clear a stuck key
		tick(stuck);
		assertEquals(State.PAUSED, sm.state());
		sm.onInput(now + 1, Input.MOUSE, 4);
		tick(stuck);
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void automationPausesAndRecordingResumesOnHumanInputAfterIt() {
		startRecording();
		tick(Conditions.playing().withAutomation("baritone"));
		assertEquals(PauseReason.PAUSE_REASON_AUTOMATION, sm.reason());
		sm.onInput(now + 1, Input.MOUSE, 10);
		tick(Conditions.playing().withAutomation("baritone"));
		assertEquals(State.PAUSED, sm.state());
		tick(Conditions.playing());
		assertEquals(State.PAUSED, sm.state(), "release alone doesn't resume");
		sm.onInput(now + 1, Input.MOUSE, 10);
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void lowFpsOverTwoSecondsFlagsDegradedOnceAndKeepsRecording() {
		startRecording();
		play(1.5, Conditions.playing().withFps(15), true);
		play(0.5, Conditions.playing(), true);
		assertTrue(flags.isEmpty(), "a short dip is fine");
		play(2.2, Conditions.playing().withFps(15), true);
		assertEquals(List.of(SegmentFlag.Kind.DEGRADED), flags);
		play(5, Conditions.playing().withFps(15), true);
		assertEquals(1, flags.size(), "once per segment");
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void positionCorrectionFlagsDegradedOncePerSegment() {
		startRecording();
		sm.onPositionCorrected(now);
		sm.onPositionCorrected(now + 1);
		assertEquals(List.of(SegmentFlag.Kind.DEGRADED), flags);
		assertEquals(State.RECORDING, sm.state());
		sm.newSegment(now + 2);
		sm.onPositionCorrected(now + 3);
		assertEquals(2, flags.size());
	}

	@Test
	void manualPauseIgnoresInputUntilToggledAgain() {
		startRecording();
		sm.toggleManual(now);
		assertEquals(PauseReason.PAUSE_REASON_MANUAL, sm.reason());
		play(2, Conditions.playing(), true);
		assertEquals(State.PAUSED, sm.state());
		sm.toggleManual(now);
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void pauseReasonFollowsTheCurrentBlocker() {
		startRecording();
		tick(Conditions.playing().withScreen("ChatScreen", true));
		tick(Conditions.playing().withScreen("ChatScreen", true).withFocused(false));
		assertEquals(PauseReason.PAUSE_REASON_UNFOCUSED, sm.reason());
		tick(Conditions.playing());
		assertEquals(State.PAUSED, sm.state(), "unfocused needs input to resume, even though a screen started it");
	}

	@Test
	void typingIntoATextFieldPausesAtOnceAndForAGracePeriod() {
		startRecording();
		play(1, Conditions.playing().withScreen("AnvilScreen", false), true);
		sm.onTextInput(now);
		assertEquals(PauseReason.PAUSE_REASON_TEXT_INPUT, sm.reason(), "paused before the tick");
		play(1, Conditions.playing(), true); // keys right after the field closes stay unrecorded
		assertEquals(State.PAUSED, sm.state());
		assertEquals(PauseReason.PAUSE_REASON_TEXT_INPUT, sm.reason());
		play(0.6, Conditions.playing(), false);
		sm.onInput(now + 1, Input.KEY, 1);
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state(), "fresh input after the grace period resumes");
	}

	@Test
	void chatClosedRightAfterTypingWaitsOutTheGrace() {
		startRecording();
		tick(Conditions.playing().withScreen("ChatScreen", true));
		sm.onTextInput(now);
		tick(Conditions.playing()); // Enter sends and closes the chat
		assertEquals(PauseReason.PAUSE_REASON_TEXT_INPUT, sm.reason());
		play(1.6, Conditions.playing(), false);
		assertEquals(State.PAUSED, sm.state(), "no input after the grace yet");
		sm.onInput(now + 1, Input.MOUSE, 5);
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void detachedCameraPausesRetroactivelyAndResumesWhenTheViewIsThePlayersAgain() {
		startRecording();
		play(2, Conditions.playing(), true);
		long freecamFrom = now - TICK / 4;
		sm.onCameraDetached(freecamFrom);
		assertEquals(PauseReason.PAUSE_REASON_CAMERA_DETACHED, sm.reason());
		assertEquals(freecamFrom - 1, trims.get(0)[1], "drops the frames recorded before the detection");
		play(2, Conditions.playing().withCameraDetached(true), true);
		assertEquals(State.PAUSED, sm.state(), "mouse input with a detached camera doesn't resume");
		sm.onInput(now + 1, Input.MOUSE, 5);
		tick(Conditions.playing());
		assertEquals(State.RECORDING, sm.state());
	}

	@Test
	void stoppedIgnoresEverything() {
		startRecording();
		sm.stop();
		sm.onInput(now, Input.KEY, 1);
		tick(Conditions.playing());
		assertEquals(State.STOPPED, sm.state());
	}
}
