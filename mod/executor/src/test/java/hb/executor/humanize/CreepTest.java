package hb.executor.humanize;

import org.junit.jupiter.api.Test;

import java.util.Random;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CreepTest {
	private static final double SD = 0.065; // degrees a frame, as fitted for the player
	private static final double COUNT = 0.15; // degrees a count at sensitivity 0.5

	@Test
	void freshNoiseReversesAboutAsOftenAsNot() {
		double share = Creep.sameShare(0, SD, COUNT, 1);
		assertTrue(share > 0.3 && share < 0.6, "fresh noise keeps its way " + share);
	}

	@Test
	void thePersistenceFoundGivesThePlayersShare() {
		double phi = Creep.persistenceFor(0.86, SD, COUNT, 2);
		assertTrue(phi > 0 && phi < Creep.MAX_PHI, "phi " + phi);
		// Checked on other draws than the ones it was found with.
		double share = (Creep.sameShare(phi, SD, COUNT, 7) + Creep.sameShare(phi, SD, COUNT, 8)) / 2;
		assertEquals(0.86, share, 0.03);
	}

	@Test
	void theCreepKeepsThePlayersPerFrameSize() {
		Creep creep = new Creep();
		creep.configure(SD, 0.86, COUNT, 3);
		Random rng = new Random(4);
		double[] turn = new double[2];
		double sum = 0;
		int n = 200_000;
		for (int i = 0; i < n; i++) {
			creep.next(rng, turn);
			sum += turn[0] * turn[0] + turn[1] * turn[1];
		}
		assertEquals(SD, Math.sqrt(sum / (2 * n)), SD * 0.1);
	}

	@Test
	void noPersistenceWhereNoneIsNeeded() {
		assertEquals(0, Creep.persistenceFor(0.2, SD, COUNT, 5), "a share fresh noise already reaches");
		assertEquals(0, Creep.persistenceFor(0.86, 0, COUNT, 5), "no tremor at all");
		Creep still = new Creep();
		still.configure(0, 0.86, COUNT, 6);
		double[] turn = {1, 1};
		still.next(new Random(6), turn);
		assertEquals(0, turn[0]);
		assertEquals(0, turn[1]);
	}
}
