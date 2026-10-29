package hb.executor.humanize;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Random;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class KeyControllerTest {
	private final Set<String> held = new HashSet<>();
	private final List<String> clicks = new ArrayList<>();
	private final KeyController keys = new KeyController(new KeyController.Keys() {
		@Override
		public void set(String binding, boolean down) {
			if (down) {
				held.add(binding);
			} else {
				held.remove(binding);
			}
		}

		@Override
		public void click(String binding) {
			clicks.add(binding);
		}
	}, new Random(7));

	@Test
	void pressesComeWithinTwoTicksAndReleasesAtOnce() {
		int ticks = 0;
		while (!held.contains("key.forward")) {
			keys.update(Set.of("key.forward"), 0, 0, false);
			ticks++;
			assertTrue(ticks <= 3, "a press waits at most 2 ticks");
		}
		keys.update(Set.of(), 0, 0, false);
		assertFalse(held.contains("key.forward"), "a release is immediate");
	}

	@Test
	void jumpAndSneakAreNeverLate() {
		keys.update(Set.of("key.jump", "key.sneak"), 0, 0, false);
		assertTrue(held.containsAll(Set.of("key.jump", "key.sneak")));
	}

	@Test
	void movementGoesThePlannersWayFromWhereTheCameraFaces() {
		Set<String> forward = Set.of("key.forward");
		// Facing where the planner faces: just forward.
		assertEquals(Set.of("key.forward"), KeyController.movementKeys(forward, 0, 0));
		// Planner faces south (0), camera faces east (-90): south is to the camera's right.
		assertEquals(Set.of("key.right"), KeyController.movementKeys(forward, 0, -90));
		// 40 degrees off: forward and a strafe, as a player walking slightly sideways does.
		assertEquals(Set.of("key.forward", "key.left"), KeyController.movementKeys(forward, 0, 40));
		// Turned right round: back.
		assertEquals(Set.of("key.back"), KeyController.movementKeys(forward, 170, -10));
		// The planner's own strafe turns too, and nothing wanted is nothing pressed.
		assertEquals(Set.of("key.forward"), KeyController.movementKeys(Set.of("key.right"), 0, 90));
		assertEquals(Set.of(), KeyController.movementKeys(Set.of("key.jump"), 0, 90));
		// Wrapping: -179 and 179 are 2 degrees apart.
		assertEquals(Set.of("key.forward"), KeyController.movementKeys(forward, -179, 179));
	}

	@Test
	void theTranslatedKeysArePressed() {
		for (int i = 0; i < 5; i++) {
			keys.update(Set.of("key.forward", "key.sprint"), 0, -90, false);
		}
		assertTrue(held.contains("key.right") && !held.contains("key.forward"));
		assertTrue(held.contains("key.sprint"));
	}

	@Test
	void aSprintEndsByLiftingForwardForATick() {
		for (int i = 0; i < 5; i++) {
			keys.update(Set.of("key.forward", "key.sprint"), 0, 0, false);
		}
		keys.update(Set.of("key.forward"), 0, 0, true); // sprint no longer wanted, still sprinting
		assertFalse(held.contains("key.forward"));
		assertFalse(held.contains("key.sprint"));
		for (int i = 0; i < 4; i++) {
			keys.update(Set.of("key.forward"), 0, 0, false);
		}
		assertTrue(held.contains("key.forward"), "and walks on");
	}

	@Test
	void attackIsAClickThenAHold() {
		for (int i = 0; i < 5; i++) {
			keys.update(Set.of("key.attack"), 0, 0, false);
		}
		assertEquals(List.of("key.attack"), clicks);
		assertTrue(held.contains("key.attack"));
		keys.releaseAll();
		assertTrue(held.isEmpty());
	}
}
