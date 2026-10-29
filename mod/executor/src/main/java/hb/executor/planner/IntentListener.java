package hb.executor.planner;

import java.util.Set;

/**
 * What the planner wants each tick, handed over instead of applied (the fork's IIntentSink, in
 * planner-neutral terms): a look target, and the key bindings it wants down.
 */
public interface IntentListener {
	/** The wanted rotation; {@code blockInteract} when it aims at a block to break or place. */
	void look(float yaw, float pitch, boolean blockInteract);

	/** Bindings (e.g. "key.forward", "key.attack") the planner wants down this tick. */
	void inputs(Set<String> bindings);
}
