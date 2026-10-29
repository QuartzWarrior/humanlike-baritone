package hb.recorder.core;

/**
 * What the game looks like this tick, as far as pausing is concerned. Built by the recorder from
 * hb.common.mc each client tick; plain data so the state machine can be tested without Minecraft.
 *
 * @param focused          window focused and not minimized
 * @param pauseScreen      a screen from the pause list is open (pause menu, chat, sign/book editing,
 *                         options, death, loading, credits)
 * @param screen           current screen class, for the pause reason
 * @param gameModePaused   creative or spectator, when the config pauses for them
 * @param gameMode         current game mode name
 * @param automation       Baritone, hb-executor or another bot is driving
 * @param automationOwner  who, for the pause reason
 * @param anyKeyDown       any key mapping or mouse button is held
 * @param stuckHeldSinceNs earliest time a movement/attack/use key has been held continuously, -1 if none
 * @param fps              frames in the last second
 * @param cameraDetached   the view isn't the player's: freecam, or the camera on another entity
 */
public record Conditions(
	boolean focused,
	boolean pauseScreen,
	String screen,
	boolean gameModePaused,
	String gameMode,
	boolean automation,
	String automationOwner,
	boolean anyKeyDown,
	long stuckHeldSinceNs,
	float fps,
	boolean cameraDetached
) {
	/** Plain survival play: nothing that would pause. */
	public static Conditions playing() {
		return new Conditions(true, false, "", false, "survival", false, null, false, -1, 60, false);
	}

	public Conditions withFocused(boolean value) {
		return new Conditions(value, pauseScreen, screen, gameModePaused, gameMode, automation, automationOwner, anyKeyDown, stuckHeldSinceNs, fps, cameraDetached);
	}

	public Conditions withScreen(String value, boolean pauses) {
		return new Conditions(focused, pauses, value, gameModePaused, gameMode, automation, automationOwner, anyKeyDown, stuckHeldSinceNs, fps, cameraDetached);
	}

	public Conditions withGameMode(String value, boolean pauses) {
		return new Conditions(focused, pauseScreen, screen, pauses, value, automation, automationOwner, anyKeyDown, stuckHeldSinceNs, fps, cameraDetached);
	}

	public Conditions withAutomation(String owner) {
		return new Conditions(focused, pauseScreen, screen, gameModePaused, gameMode, owner != null, owner, anyKeyDown, stuckHeldSinceNs, fps, cameraDetached);
	}

	public Conditions withKeys(boolean anyDown, long stuckSinceNs) {
		return new Conditions(focused, pauseScreen, screen, gameModePaused, gameMode, automation, automationOwner, anyDown, stuckSinceNs, fps, cameraDetached);
	}

	public Conditions withCameraDetached(boolean value) {
		return new Conditions(focused, pauseScreen, screen, gameModePaused, gameMode, automation, automationOwner, anyKeyDown, stuckHeldSinceNs, fps, value);
	}

	public Conditions withFps(float value) {
		return new Conditions(focused, pauseScreen, screen, gameModePaused, gameMode, automation, automationOwner, anyKeyDown, stuckHeldSinceNs, value, cameraDetached);
	}
}
