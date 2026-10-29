package hb.executor.humanize;

import org.junit.jupiter.api.Test;

import java.util.Random;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MetaAndWalkTest {
	private static MetaModel meta(double miningPerMin, double afterBreak, double glancePerMin) {
		MetaModel d = MetaModel.defaults();
		return new MetaModel(miningPerMin, 0, 0, 0, afterBreak, d.hesitationMedianMs(), d.hesitationSigma(),
			glancePerMin, d.glanceAngleMedian(), d.glanceAngleSigma(), d.glanceHoldMedianMs(), d.glanceHoldSigma());
	}

	@Test
	void hesitationsComeAtTheFittedRateAndLastLikeOne() {
		MetaLayer layer = new MetaLayer(new Random(1));
		layer.configure(meta(6.0, 0, 0), 1.0);
		int starts = 0, ticksPaused = 0;
		MetaLayer.State last = MetaLayer.State.NONE;
		int minutes = 200;
		for (int t = 0; t < minutes * 1200; t++) {
			MetaLayer.State s = layer.tick("mining", true, false);
			if (s == MetaLayer.State.HESITATE) {
				ticksPaused++;
				if (last != MetaLayer.State.HESITATE) {
					starts++;
				}
			}
			last = s;
		}
		double perMin = starts / (double) minutes;
		assertEquals(6.0, perMin, 1.0);
		double meanTicks = ticksPaused / (double) starts;
		assertTrue(meanTicks >= MetaLayer.MIN_HESITATION_TICKS && meanTicks <= MetaLayer.MAX_HESITATION_TICKS);
	}

	@Test
	void theSafetyFilterHasTheLastWord() {
		MetaLayer layer = new MetaLayer(new Random(2));
		layer.configure(meta(1200.0, 1.0, 1200.0), 1.0); // would start at once
		assertEquals(MetaLayer.State.NONE, layer.tick("mining", false, true), "never starts when unsafe");
		assertEquals(MetaLayer.State.HESITATE, layer.tick("mining", true, false));
		assertEquals(MetaLayer.State.NONE, layer.tick("mining", false, false), "and stops the tick it isn't safe");
	}

	@Test
	void aBreakCanStartAHesitationAndGlancesNeedStandingStill() {
		MetaLayer layer = new MetaLayer(new Random(3));
		layer.configure(meta(0, 1.0, 0), 1.0);
		layer.blockBroken();
		assertEquals(MetaLayer.State.HESITATE, layer.tick("mining", true, false));

		MetaLayer glances = new MetaLayer(new Random(4));
		glances.configure(meta(0, 0, 1200.0), 1.0);
		assertEquals(MetaLayer.State.NONE, glances.tick("walking", true, false), "no glance while walking");
		assertEquals(MetaLayer.State.GLANCE, glances.tick("other", true, true));
		assertTrue(Math.abs(glances.glanceYaw()) > 1);
	}

	private static MovementModel movement(double doubleTap, double sprintJump, double holdMedian) {
		MovementModel d = MovementModel.defaults();
		return new MovementModel(holdMedian, 0.01, 3, 0.01, doubleTap, new double[]{0, 0, 1}, sprintJump,
			d.headingTheta(), d.headingSigma(), d.headingMean(), d.lookAheadMedian(), d.lookAheadSigma(),
			d.walkPitchMean(), d.walkPitchSd(), d.miningSneakShare(), d.miningSneakMedian(), d.miningSneakSigma(),
			d.miningStandSigma(), d.miningStepsPerMin(), d.miningStepMedian(), d.miningStepSigma(), d.miningStepKeys(),
			d.miningReachMedian(), d.miningReachQ90());
	}

	@Test
	void forwardIsLiftedForAMomentAfterTheFittedHold() {
		WalkStyle walk = new WalkStyle(new Random(5));
		walk.configure(movement(0, 0, 80), 1.0);
		Set<String> want = Set.of("key.forward");
		int held = 0;
		while (walk.keys(want, false, true, true, false).contains("key.forward")) {
			held++;
		}
		assertEquals(80, held, 3);
		int lifted = 0;
		while (!walk.keys(want, false, true, true, false).contains("key.forward")) {
			lifted++;
		}
		assertEquals(3, lifted, 1);
		// Not when it isn't safe (an edge, a ladder...).
		WalkStyle careful = new WalkStyle(new Random(6));
		careful.configure(movement(0, 0, 80), 1.0);
		for (int i = 0; i < 200; i++) {
			assertTrue(careful.keys(want, false, true, false, false).contains("key.forward"));
		}
	}

	@Test
	void aDoubleTapSprintTapsForwardInsteadOfTheKey() {
		WalkStyle walk = new WalkStyle(new Random(7));
		walk.configure(movement(1.0, 0, 1000), 1.0);
		Set<String> want = Set.of("key.forward", "key.sprint");
		StringBuilder taps = new StringBuilder();
		for (int i = 0; i < 6; i++) {
			Set<String> keys = walk.keys(want, false, true, true, false);
			assertFalse(keys.contains("key.sprint"));
			taps.append(keys.contains("key.forward") ? '1' : '0');
		}
		assertEquals("010111", taps.toString());
	}

	@Test
	void openGroundSprintsBecomeSprintJumpsOnTheFittedBeat() {
		WalkStyle walk = new WalkStyle(new Random(8));
		walk.configure(movement(0, 1.0, 1000), 1.0);
		Set<String> want = Set.of("key.forward", "key.sprint");
		// Landed: the re-jump comes 2 ticks later (the fitted pmf puts all its weight on 2).
		assertFalse(walk.keys(want, true, true, true, true).contains("key.jump"));
		assertFalse(walk.keys(want, true, true, true, true).contains("key.jump"));
		assertTrue(walk.keys(want, true, true, true, true).contains("key.jump"));
		// Not when the ground ahead isn't open.
		assertFalse(walk.keys(want, true, true, true, false).contains("key.jump"));
	}

	@Test
	void miningSneakMatchesTheSharesAndStandsUpOnlyAtABreak() {
		MovementModel d = MovementModel.defaults();
		MovementModel m = new MovementModel(d.forwardHoldMedian(), d.forwardHoldSigma(), d.regripMedian(), d.regripSigma(),
			d.doubleTapShare(), d.jumpAfterLanding(), d.sprintJumpShare(), d.headingTheta(), d.headingSigma(), d.headingMean(),
			d.lookAheadMedian(), d.lookAheadSigma(), d.walkPitchMean(), d.walkPitchSd(), 0.4, 60, 0.8, 0.8,
			d.miningStepsPerMin(), d.miningStepMedian(), d.miningStepSigma(), d.miningStepKeys(),
			d.miningReachMedian(), d.miningReachQ90());
		MiningSneak sneak = new MiningSneak(new Random(10));
		sneak.configure(m, 1.0);
		int sneaking = 0, n = 400_000;
		boolean was = false;
		for (int t = 0; t < n; t++) {
			boolean justBroke = t % 10 == 0; // a block every half second
			boolean now = sneak.tick(true, justBroke);
			if (was && !now) {
				assertTrue(justBroke, "stood up mid-block at tick " + t);
			}
			sneaking += now ? 1 : 0;
			was = now;
		}
		assertEquals(0.4, sneaking / (double) n, 0.04);
		assertFalse(sneak.tick(false, true), "not while not mining");
		MiningSneak never = new MiningSneak(new Random(11));
		never.configure(MovementModel.defaults(), 1.0); // a player who doesn't
		for (int t = 0; t < 1000; t++) {
			assertFalse(never.tick(true, true));
		}
	}

	@Test
	void miningStepsComeAtTheRateWithTheKeyMixAndStopWhenUnsafe() {
		MovementModel d = MovementModel.defaults();
		MovementModel m = new MovementModel(d.forwardHoldMedian(), d.forwardHoldSigma(), d.regripMedian(), d.regripSigma(),
			d.doubleTapShare(), d.jumpAfterLanding(), d.sprintJumpShare(), d.headingTheta(), d.headingSigma(), d.headingMean(),
			d.lookAheadMedian(), d.lookAheadSigma(), d.walkPitchMean(), d.walkPitchSd(), 0, 100, 1, 1,
			30, 6, 0.5, new double[]{0.6, 0.2, 0.1, 0.1}, d.miningReachMedian(), d.miningReachQ90());
		MiningSteps steps = new MiningSteps(new Random(12));
		steps.configure(m, 1.0);
		int started = 0, forward = 0, held = 0, n = 1200 * 200;
		String was = null;
		for (int t = 0; t < n; t++) {
			String key = steps.tick(true, k -> true);
			if (key != null && was == null) {
				started++;
				forward += key.equals("key.forward") ? 1 : 0;
			}
			held += key != null ? 1 : 0;
			was = key;
		}
		assertEquals(30.0, started / 200.0, 4.0); // a minute
		assertEquals(0.6, forward / (double) started, 0.05);
		assertTrue(held / (double) started <= MiningSteps.MAX_TICKS);
		assertEquals(6.0 * Math.exp(0.125), held / (double) started, 1.5); // log-normal mean
		// A step ends the tick it stops being safe, and none starts toward somewhere unsafe.
		MiningSteps careful = new MiningSteps(new Random(13));
		careful.configure(m, 1.0);
		for (int t = 0; t < 100_000; t++) {
			assertEquals(null, careful.tick(true, k -> false));
		}
		assertEquals(null, steps.tick(false, k -> true), "not while not mining in place");
	}

	@Test
	void theHeadingWandersButReturns() {
		WalkStyle walk = new WalkStyle(new Random(9));
		walk.configure(MovementModel.defaults(), 1.0);
		double sumSq = 0;
		for (int i = 0; i < 20_000; i++) {
			double h = walk.headingOffset();
			sumSq += h * h;
		}
		double sd = Math.sqrt(sumSq / 20_000);
		MovementModel m = MovementModel.defaults();
		assertEquals(m.headingSigma(), sd, m.headingSigma() * 0.35); // the fitted stationary spread
	}
}
