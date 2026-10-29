package hb.executor.planner;

import java.util.Set;

/**
 * The executor's only view of the path planner. All Baritone calls go through an implementation
 * of this interface, so the planner source (cabaletta, the Meteor fork, ...) can be swapped
 * without touching the rest of the executor.
 */
public interface PlannerBackend {
	/** Short identifier for logs. */
	String name();

	/** True once the planner is bound to a live player, i.e. the client is in a world. */
	boolean isReady();

	/** True while the planner is pathing or one of its processes is in control. */
	boolean isActive();

	/**
	 * Runs a planner command without its chat prefix, e.g. {@code "mine diamond_ore"}.
	 *
	 * @return false if the planner did not recognize the command
	 */
	boolean execute(String command);

	/** Stops all pathing and planner processes. */
	void cancel();

	/**
	 * Key bindings (e.g. {@code "key.forward"}) the planner is holding through its own input
	 * override rather than the game's key mappings. Plain Baritone moves the player this way, so
	 * no real key is ever down; the bot recorder logs these as the bot's keys.
	 */
	java.util.Set<String> heldBindings();

	/**
	 * Hands the planner's look and input intent to {@code listener} instead of applying it, or with
	 * null lets the planner drive the player itself again.
	 */
	void setIntentListener(IntentListener listener);

	/** Loosens the planner's movement timeouts for human pacing (slower turns, reaction delays). */
	void setHumanPacing(boolean on);

	/**
	 * The path ahead: the centers (x + 0.5, y, z + 0.5) of up to {@code max} nodes, from the one
	 * the player is moving to onward; empty when the planner has no path.
	 */
	double[][] pathAhead(int max);

	/**
	 * Picks the block a build or clear job breaks next, out of the blocks the planner could break
	 * right now (packed block positions, {@link hb.common.mc.McInput#blockKey}, in the planner's own
	 * order): the index to break, or -1 for the planner's own pick.
	 */
	interface BreakChooser {
		int choose(long[] candidates);
	}

	/** Lets {@code chooser} pick the next block in build and clear jobs; null for the planner's order. */
	default void setBreakChooser(BreakChooser chooser) {
	}

	/** The blocks a build or clear job could break this tick (packed positions); empty otherwise. */
	default Set<Long> breakCandidates() {
		return Set.of();
	}

	/** What the planner itself thinks it is looking at, for traces. */
	default String debugState() {
		return "";
	}

	/** Drops the current path, if that's safe now, so the planner plans afresh from here. */
	boolean repath();
}
