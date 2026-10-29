package hb.executor.humanize;

import org.junit.jupiter.api.Test;

import java.util.Random;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CameraTest {
	private static final CameraModel MODEL = CameraModel.defaults();

	@Test
	void aPlanEndsNearTheTargetAndStaysThere() {
		Random rng = new Random(1);
		double sum = 0;
		for (int i = 0; i < 200; i++) {
			CameraPlan plan = CameraPlan.plan(60, -10, 6, true, MODEL, 1.0, rng);
			double[] end = plan.offsetAt(plan.endMs());
			assertEquals(plan.goal()[0], end[0], 1e-9);
			assertEquals(plan.offsetAt(plan.endMs() + 500)[0], end[0], 1e-9); // done means held
			// Either a correction landed it near the point, or the primary was already on the
			// 6-degree target and the small miss was left, as people leave it.
			double miss = Math.hypot(end[0] - 60, end[1] + 10);
			assertTrue(miss < Math.max(3.0, 4 * MODEL.landingFracYaw() * 6) + 1e-6, "missed by " + miss);
			sum += end[0] - 60;
		}
		assertEquals(0, sum / 200, 0.5); // no big bias to one side
	}

	@Test
	void reactionComesFirstAndTheBigMoveComesBeforeTheCorrections() {
		Random rng = new Random(2);
		CameraPlan plan = CameraPlan.plan(90, 0, 20, true, MODEL, 1.0, rng);
		CameraPlan.Sub primary = plan.subs().getFirst();
		assertTrue(primary.startMs() > 0, "a new target is reacted to first");
		assertEquals(0, plan.offsetAt(primary.startMs())[0], 1e-9);
		assertTrue(Math.abs(primary.dYaw()) > 0.6 * 90, "the primary covers most of the way");
		CameraPlan nudge = CameraPlan.plan(2, 0, 20, false, MODEL, 1.0, rng);
		assertEquals(0, nudge.subs().getFirst().startMs(), "a nudge isn't reacted to");
		assertTrue(CameraPlan.plan(0.1, 0, 6, true, MODEL, 1.0, rng).subs().isEmpty());
	}

	@Test
	void peakSpeedLandsAtTheFittedFraction() {
		CameraModel m = new CameraModel(250, 0.35, 80, 120, 0.25, 0.35, 0.0, 0.93, 0.08, 0.8, 3, 140, 0.4, 60, 0.5,
			0.6, 0.4, 0.15, 0.15, 0.0, 0.02, 0.8);
		CameraPlan plan = CameraPlan.plan(40, 0, 6, false, m, 1.0, new Random(3));
		CameraPlan.Sub sub = plan.subs().getFirst();
		double best = 0, at = 0;
		for (int i = 1; i < 1000; i++) {
			double t = sub.durMs() * i / 1000.0;
			double v = plan.offsetAt(t + 0.5)[0] - plan.offsetAt(t - 0.5)[0];
			if (v > best) {
				best = v;
				at = t / sub.durMs();
			}
		}
		assertEquals(0.35, at, 0.03);
	}

	/** A camera that turns by what the controller sends, and a block on screen when given one. */
	private static final class Camera implements CameraController.View {
		static final double PER_COUNT = 0.15;
		final float[] rot = {10f, 0f};
		CameraController.Aim block; // the target block, spanning +-halfWidth around the target
		double halfWidth;
		float targetYaw, targetPitch;
		java.util.List<float[]> blocks = new java.util.ArrayList<>(); // or several: {yaw, pitch, id}, halfWidth each

		CameraController.Aim blockAt(float yaw, float pitch) {
			for (float[] b : blocks) {
				if (Math.abs(CameraController.wrap(yaw - b[0])) <= halfWidth && Math.abs(pitch - b[1]) <= halfWidth) {
					return new CameraController.Aim((long) b[2], 2, 2 * halfWidth);
				}
			}
			return null;
		}
		long clockNs; // runs on across calls, as frame time does

		@Override
		public float[] rotation() {
			return rot;
		}

		@Override
		public double degreesPerCount() {
			return PER_COUNT;
		}

		@Override
		public int[] axisSigns() {
			return new int[]{1, 1};
		}

		@Override
		public CameraController.Aim aim(float yaw, float pitch) {
			return blocks.isEmpty() ? block : blockAt(yaw, pitch);
		}

		@Override
		public CameraController.Aim crosshair() {
			if (!blocks.isEmpty()) {
				CameraController.Aim on = blockAt(rot[0], rot[1]);
				return on == null ? null : new CameraController.Aim(on.block(), on.face(), 0);
			}
			boolean on = block != null && Math.abs(CameraController.wrap(rot[0] - targetYaw)) <= halfWidth
				&& Math.abs(rot[1] - targetPitch) <= halfWidth;
			return on ? new CameraController.Aim(block.block(), block.face(), 0) : null;
		}

		/** Runs the controller for {@code seconds} at 120 frames a second; returns the counts sent. */
		double run(CameraController c, double seconds) {
			double[] out = new double[2];
			double sent = 0;
			long frameNs = 1_000_000_000L / 120;
			for (long end = clockNs + (long) (seconds * 1e9); clockNs < end; clockNs += frameNs) {
				c.frame(clockNs, out);
				assertEquals(Math.rint(out[0]), out[0]);
				assertEquals(Math.rint(out[1]), out[1]);
				rot[0] += (float) (out[0] * PER_COUNT);
				rot[1] += (float) (out[1] * PER_COUNT);
				sent += Math.abs(out[0]) + Math.abs(out[1]);
			}
			return sent;
		}
	}

	@Test
	void theControllerTurnsTheCameraOntoTheTargetInWholeCounts() {
		Camera cam = new Camera(); // no block in view: settles by angle
		CameraController c = new CameraController(cam, new Random(4));
		c.setTarget(-80f, 25f, true); // 90 degrees left and down a bit
		cam.run(c, 3);
		assertTrue(c.yawError() < CameraController.SETTLE_BLOCK_DEG, "yaw still off by " + c.yawError());
		assertEquals(25, cam.rot[1], CameraController.SETTLE_BLOCK_DEG + 0.5);
	}

	@Test
	void onABlockTheCrosshairStopsAnywhereOnIt() {
		Camera cam = new Camera();
		cam.block = new CameraController.Aim(42L, 2, 20.0); // a block 20 degrees wide
		cam.halfWidth = 10;
		cam.targetYaw = -80f;
		cam.targetPitch = 25f;
		CameraController c = new CameraController(cam, new Random(5));
		CameraModel d = CameraModel.defaults();
		c.configure(new CameraModel(d.reactionMedianMs(), d.reactionSigma(), d.fittsA(), d.fittsB(), d.durationSigma(),
			d.peakAtMean(), d.peakAtSd(), d.gainMean(), d.gainSd(), d.correctionsMean(), d.correctionsMax(),
			d.correctionMedianMs(), d.correctionSigma(), d.correctionGapMedianMs(), d.correctionGapSigma(),
			d.landingSdYaw(), d.landingSdPitch(), d.landingFracYaw(), d.landingFracPitch(), d.curvatureSd(), 0.0, d.tremorSameSign()), 1.0);
		c.setTarget(cam.targetYaw, cam.targetPitch, true);
		cam.run(c, 3);
		assertTrue(cam.block.sameFace(cam.crosshair()), "ends on the block's face");
		// Once on it, the camera rests (tremor off here): no chasing the exact centre.
		double after = cam.run(c, 2);
		assertEquals(0, after, "kept correcting on the block");
	}

	@Test
	void landedOnlyOnceTheMoveHasEndedOnTheFace() {
		Camera cam = new Camera();
		cam.block = new CameraController.Aim(42L, 2, 20.0);
		cam.halfWidth = 10;
		cam.targetYaw = -80f;
		cam.targetPitch = 25f;
		CameraController c = new CameraController(cam, new Random(9));
		c.setTarget(cam.targetYaw, cam.targetPitch, true);
		assertFalse(c.landed(cam.clockNs), "not before the move");
		boolean crossedBeforeLanding = false;
		double[] out = new double[2];
		for (int frame = 0; frame < 360 && !c.landed(cam.clockNs); frame++, cam.clockNs += 1_000_000_000L / 120) {
			c.frame(cam.clockNs, out);
			cam.rot[0] += (float) (out[0] * Camera.PER_COUNT);
			cam.rot[1] += (float) (out[1] * Camera.PER_COUNT);
			crossedBeforeLanding |= cam.block.sameFace(cam.crosshair()) && !c.landed(cam.clockNs);
		}
		assertTrue(c.landed(cam.clockNs), "landed within 3 s");
		assertTrue(cam.block.sameFace(cam.crosshair()));
		assertTrue(crossedBeforeLanding, "the crosshair is on the face a while before the move ends");
		c.setTarget(cam.targetYaw + 60, cam.targetPitch, false);
		assertFalse(c.landed(cam.clockNs), "a direction to face isn't a block to place against");
	}

	@Test
	void aBlockPlacedWhereOneWasAimedAtIsNoLongerARecentAim() {
		Camera cam = new Camera();
		cam.block = new CameraController.Aim(42L, 2, 20.0);
		cam.halfWidth = 10;
		cam.targetYaw = -80f;
		cam.targetPitch = 25f;
		CameraController c = new CameraController(cam, new Random(10));
		c.setTarget(cam.targetYaw, cam.targetPitch, true);
		cam.run(c, 3);
		assertTrue(c.onRecentAim(), "on the block just aimed at");
		c.blockPlaced(42L);
		assertFalse(c.onRecentAim(), "a block placed there since is another block");
	}

	@Test
	void aFaceMostlyHiddenIsAimedForWhereItShows() {
		// Block 7 spans yaw -90..-70, but block 8, nearer, hides all of it right of -84: a strip 6
		// degrees wide shows, a third of the block, and the target point is in the middle of it.
		int landed = 0;
		for (int seed = 0; seed < 20; seed++) {
			Camera cam = new Camera();
			cam.halfWidth = 10;
			cam.blocks.add(new float[]{-74f, 25f, 8});
			cam.blocks.add(new float[]{-80f, 25f, 7});
			CameraController c = new CameraController(cam, new Random(seed));
			c.setTarget(-87f, 25f, true);
			cam.run(c, 0.02);
			assertTrue(c.planWidth() <= 6, "aims for the strip, not the block: " + c.planWidth());
			cam.run(c, 1.5);
			CameraController.Aim on = cam.crosshair();
			landed += on != null && on.block() == 7 ? 1 : 0;
		}
		assertTrue(landed >= 18, "on the strip that shows after 1.5 s in " + landed + " of 20");
	}

	@Test
	void aPlannerFlippingBetweenTwoBlocksStillGetsTheCameraToOne() {
		Camera cam = new Camera();
		cam.halfWidth = 5;
		cam.blocks.add(new float[]{-45f, 1f, 1}); // 27 degrees apart, as Baritone's builder flips
		cam.blocks.add(new float[]{-72f, 12f, 2});
		CameraController c = new CameraController(cam, new Random(6));
		double[] out = new double[2];
		long frameNs = 1_000_000_000L / 120;
		for (int tick = 0; tick < 40; tick++) { // 2 s of game ticks, the pick alternating every tick
			float[] pick = cam.blocks.get(tick % 2);
			c.setTarget(pick[0], pick[1], true);
			for (int f = 0; f < 6; f++, cam.clockNs += frameNs) {
				c.frame(cam.clockNs, out);
				cam.rot[0] += (float) (out[0] * Camera.PER_COUNT);
				cam.rot[1] += (float) (out[1] * Camera.PER_COUNT);
			}
		}
		CameraController.Aim on = cam.crosshair();
		assertTrue(on != null && on.block() == 1, "ends on the first pick, not stuck between: " + on);
		// Once the planner stops asking for it (broken, say), the other block takes over.
		cam.blocks.removeFirst();
		for (int tick = 0; tick < 40; tick++) {
			float[] pick = cam.blocks.getFirst();
			c.setTarget(pick[0], pick[1], true);
			cam.run(c, 0.05);
		}
		on = cam.crosshair();
		assertTrue(on != null && on.block() == 2, "moved on to the other block: " + on);
	}

	@Test
	void theBlockAfterABreakIsMovedToWithoutAReaction() {
		Camera cam = new Camera();
		cam.halfWidth = 5;
		cam.blocks.add(new float[]{10f, 0f, 1});
		cam.blocks.add(new float[]{-30f, 20f, 2});
		CameraController c = new CameraController(cam, new Random(7));
		c.setTarget(10f, 0f, true); // already on the first block
		cam.run(c, 0.5);
		cam.blocks.removeFirst(); // it breaks; the next was picked meanwhile
		c.blockBroken();
		c.setTarget(-30f, 20f, true);
		double before = cam.rot[0];
		cam.run(c, 0.08); // a reaction alone would take ~200 ms
		assertTrue(Math.abs(cam.rot[0] - before) > 1, "moving within 80 ms, no reaction first");
	}

	@Test
	void aLeanMovesAcrossTheBlockAndStaysOnIt() {
		Camera cam = new Camera();
		cam.halfWidth = 8;
		cam.blocks.add(new float[]{10f, 0f, 1});
		CameraController c = new CameraController(cam, new Random(8));
		c.setTarget(10f, 0f, true);
		cam.run(c, 1.0);
		float startYaw = cam.rot[0];
		c.lean(startYaw - 5f, cam.rot[1], 1.5, cam.clockNs);
		cam.run(c, 1.0);
		assertTrue(cam.rot[0] < startYaw - 2, "leaned toward the next block: " + startYaw + " -> " + cam.rot[0]);
		CameraController.Aim on = cam.crosshair();
		assertTrue(on != null && on.block() == 1, "still on the block");
	}

	@Test
	void wrapTakesTheShortWayRound() {
		assertEquals(-20, CameraController.wrap(340), 1e-9);
		assertEquals(20, CameraController.wrap(-340), 1e-9);
		assertEquals(-180, CameraController.wrap(180), 1e-9);
	}
}
