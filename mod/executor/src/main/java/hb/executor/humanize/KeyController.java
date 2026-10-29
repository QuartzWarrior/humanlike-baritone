package hb.executor.humanize;

import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;

/**
 * Presses and releases the real keys the planner wants (spec, Component C: motor output), tick by
 * tick.
 *
 * <p>The planner's movement keys are relative to the rotation it asked for, but the humanized
 * camera gets there late and lands near rather than on it. So the wanted movement is turned into
 * a direction in the world (the planner's yaw plus its forward/back/strafe keys) and pressed as
 * whichever of forward, back, left and right go that way from where the camera actually faces:
 * a player facing a little off the path walks forward and strafes, as people do, instead of
 * walking off course or stopping to turn.
 *
 * <p>Stage 1 timing, until the pipeline fits key timing from the player's data:
 * <ul>
 *   <li>a press lands 0-2 ticks after it's wanted, as a person reacts;</li>
 *   <li>releases, jump and sneak are immediate: a late release or jump walks off edges, and sneak
 *       is what keeps the player on them (the safety filter's veto, spec step 6).</li>
 * </ul>
 * Attack and use are pressed as clicks (the game counts the press) and held while wanted.
 */
public final class KeyController {
	/** The game's key mappings; {@link hb.common.mc.McInput} in the mod, a fake in tests. */
	public interface Keys {
		void set(String binding, boolean down);

		void click(String binding);
	}

	static final String FORWARD = "key.forward", BACK = "key.back", LEFT = "key.left", RIGHT = "key.right";
	static final String SPRINT = "key.sprint";
	static final List<String> MOVEMENT = List.of(FORWARD, BACK, LEFT, RIGHT);
	static final List<String> MANAGED = List.of(
		FORWARD, BACK, LEFT, RIGHT, "key.jump", "key.sneak", "key.sprint", "key.attack", "key.use");
	private static final Set<String> IMMEDIATE = Set.of("key.jump", "key.sneak");
	private static final Set<String> CLICKED = Set.of("key.attack", "key.use");
	/** A key goes down when the way to go is within 67.5 degrees of its direction (8 sectors). */
	static final double KEY_COS = Math.cos(Math.toRadians(67.5));
	private static final double[] PRESS_LAG = {0.55, 0.35, 0.10}; // P(0, 1, 2 ticks)

	private final Keys keys;
	private final Random rng;
	private final Map<String, Boolean> down = new HashMap<>();
	private final Map<String, Long> pressAt = new HashMap<>(); // binding -> tick the press lands
	private long tick;

	public KeyController(Keys keys, Random rng) {
		this.keys = keys;
		this.rng = rng;
	}

	/**
	 * One game tick: the bindings the planner wants down, the yaw its movement keys are relative
	 * to, the yaw the camera actually faces (degrees), and whether the player is sprinting.
	 */
	public void update(Set<String> wanted, double plannerYaw, double facingYaw, boolean sprinting) {
		tick++;
		Set<String> want = new HashSet<>(wanted);
		want.removeAll(MOVEMENT);
		want.addAll(movementKeys(wanted, plannerYaw, facingYaw));
		if (sprinting && !want.contains(SPRINT) && want.contains(FORWARD) && isDown(FORWARD)) {
			// Letting go of sprint doesn't stop a sprint; a player lifts forward for a moment.
			want.remove(FORWARD);
		}
		for (String binding : MANAGED) {
			boolean is = down.getOrDefault(binding, false);
			if (!want.contains(binding)) {
				pressAt.remove(binding);
				if (is) {
					apply(binding, false);
				}
				continue;
			}
			if (is) {
				continue;
			}
			long at = pressAt.computeIfAbsent(binding, b -> tick + (IMMEDIATE.contains(b) ? 0 : lag()));
			if (tick >= at) {
				pressAt.remove(binding);
				apply(binding, true);
			}
		}
	}

	/** The movement keys that go the planner's way from the camera's actual facing. */
	static Set<String> movementKeys(Set<String> wanted, double plannerYaw, double facingYaw) {
		int f = (wanted.contains(FORWARD) ? 1 : 0) - (wanted.contains(BACK) ? 1 : 0);
		int s = (wanted.contains(RIGHT) ? 1 : 0) - (wanted.contains(LEFT) ? 1 : 0);
		if (f == 0 && s == 0) {
			return Set.of();
		}
		// Yaw grows turning right, so strafing right heads 90 degrees clockwise of forward.
		double heading = plannerYaw + Math.toDegrees(Math.atan2(s, f));
		double rel = Math.toRadians(CameraController.wrap(heading - facingYaw));
		Set<String> out = new HashSet<>();
		if (Math.cos(rel) > KEY_COS) {
			out.add(FORWARD);
		} else if (Math.cos(rel) < -KEY_COS) {
			out.add(BACK);
		}
		if (Math.sin(rel) > KEY_COS) {
			out.add(RIGHT);
		} else if (Math.sin(rel) < -KEY_COS) {
			out.add(LEFT);
		}
		return out;
	}

	/** Lets go of everything this controller holds. */
	public void releaseAll() {
		for (String binding : MANAGED) {
			if (down.getOrDefault(binding, false)) {
				apply(binding, false);
			}
		}
		pressAt.clear();
	}

	public boolean isDown(String binding) {
		return down.getOrDefault(binding, false);
	}

	private void apply(String binding, boolean press) {
		if (press && CLICKED.contains(binding)) {
			keys.click(binding);
		}
		keys.set(binding, press);
		down.put(binding, press);
	}

	private int lag() {
		double r = rng.nextDouble();
		for (int i = 0; i < PRESS_LAG.length; i++) {
			r -= PRESS_LAG[i];
			if (r < 0) {
				return i;
			}
		}
		return PRESS_LAG.length - 1;
	}
}
