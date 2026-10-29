package hb.common.mc;

import hb.proto.v1.GameEvent;
import hb.proto.v1.SlotClick;

/**
 * Raw input and discrete game events, as neutral values. Called on the client thread from
 * hb.common.mc mixins and Fabric events; times are {@link System#nanoTime()} at the hook.
 *
 * <p>Privacy is enforced here, before anything reaches a listener: key events typed into a text
 * field, and keys that are neither bound nor GUI keys, arrive only as {@link #onPrivateKey} with no
 * key identity; chat and command sends arrive as a flag without their text.
 */
public interface McInputListener {
	/** Mouse motion for one frame while the mouse turns the player. */
	default void onMouseTurn(long nanoTime, double dx, double dy, float dYaw, float dPitch) {
	}

	/** Mouse motion for one frame while a screen has the cursor (not turning the player). */
	default void onCursorMove(long nanoTime, double dx, double dy) {
	}

	/** Cursor position in GUI-scaled coordinates, each frame it moved while a container screen is open. */
	default void onGuiCursor(long nanoTime, double x, double y) {
	}

	default void onMouseButton(long nanoTime, int button, boolean down, int mods, boolean inGui) {
	}

	default void onScroll(long nanoTime, double dx, double dy) {
	}

	/**
	 * A key event outside text fields.
	 *
	 * @param binding name of the key mapping bound to this key, or null
	 * @param guiKey  Escape, Enter, Tab, arrows or a modifier key
	 * @param action  GLFW action: 0 release, 1 press, 2 repeat
	 */
	default void onKey(long nanoTime, int key, int scancode, int action, int mods, String binding, boolean guiKey) {
	}

	/**
	 * A key event that must not be stored. Deliberately carries nothing but the time.
	 *
	 * @param textInput typed into a text field (recording must stop), rather than an unbound key
	 */
	default void onPrivateKey(long nanoTime, boolean textInput) {
	}

	default void onSlotClick(long nanoTime, SlotClick click) {
	}

	default void onGameEvent(long nanoTime, GameEvent event) {
	}
}
