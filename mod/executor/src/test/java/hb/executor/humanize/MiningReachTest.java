package hb.executor.humanize;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MiningReachTest {
	// Standing at (0.5, 64, 0.5), eye 1.62 up; yaw 0 faces +z (south), and east (+x) is on the left.
	private static final double[] FEET = {0.5, 64, 0.5};
	private static final double[] EYE = {0.5, 65.62, 0.5};

	private static MiningReach reach() {
		MiningReach r = new MiningReach();
		r.configure(MovementModel.defaults()); // nine in ten within 2.7 blocks
		return r;
	}

	@Test
	void walksUpToABlockBeyondThePlayersReach() {
		MiningReach r = reach();
		assertEquals("key.forward", r.key(FEET, EYE, new int[]{0, 65, 4}, 0), "3.5 blocks ahead");
		assertEquals("key.left", r.key(FEET, EYE, new int[]{4, 65, 0}, 0));
		assertEquals("key.right", r.key(FEET, EYE, new int[]{-4, 65, 0}, 0));
		assertEquals("key.back", r.key(FEET, EYE, new int[]{0, 65, -4}, 0));
		assertEquals("key.forward", r.key(FEET, EYE, new int[]{4, 65, 0}, -90), "facing east");
	}

	@Test
	void standsWhereTheBlockIsNearEnoughOrWalkingWouldNotHelp() {
		MiningReach r = reach();
		assertNull(r.key(FEET, EYE, new int[]{0, 65, 2}, 0), "1.5 blocks ahead");
		assertNull(r.key(FEET, EYE, new int[]{0, 70, 0}, 0), "overhead");
		assertNull(r.key(FEET, EYE, new int[]{0, 60, 0}, 0), "underfoot");
	}

	@Test
	void nearIsWithinThePlayersReachOfTheBlocksNearestPoint() {
		MiningReach r = reach();
		assertTrue(r.near(EYE, new int[]{0, 65, 2}), "1.5 blocks");
		assertTrue(r.near(EYE, new int[]{0, 68, 0}), "overhead, 1.38 blocks up");
		assertFalse(r.near(EYE, new int[]{0, 65, 4}), "3.5 blocks");
		assertFalse(r.near(EYE, new int[]{3, 68, 3}), "2.5 across each way and 1.4 up");
	}

	@Test
	void givesUpAfterApproachingForAWhileUntilTheBlockIsNear() {
		MiningReach r = reach();
		int[] far = {0, 65, 4};
		for (int i = 0; i < MiningReach.MAX_TICKS; i++) {
			assertEquals("key.forward", r.key(FEET, EYE, far, 0));
			r.stepped();
		}
		assertNull(r.key(FEET, EYE, far, 0), "something keeps the player from getting nearer");
		assertNull(r.key(FEET, EYE, new int[]{0, 65, 2}, 0));
		assertEquals("key.forward", r.key(FEET, EYE, far, 0), "a near block starts the count afresh");
		r.reset();
		assertEquals("key.forward", r.key(FEET, EYE, far, 0));
		for (int i = 0; i < 2 * MiningReach.MAX_TICKS; i++) {
			assertEquals("key.forward", r.key(FEET, EYE, far, 0), "steps not taken don't count");
		}
	}
}
