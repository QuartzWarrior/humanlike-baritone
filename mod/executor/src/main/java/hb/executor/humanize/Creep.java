package hb.executor.humanize;

import java.util.Random;

/**
 * A resting hand's slow creep (camera.json tremor_deg and tremor_same_sign). Per axis, each frame's
 * turn follows a persistent drift, this frame's being {@code phi} times the last one's plus fresh
 * noise, with the player's per-frame sd. Fresh noise alone becomes whole mouse counts that reverse
 * as often as not; a player's creep mostly keeps going the way it was going, so {@code phi} is the
 * one that gives the player's share of turns that go the same way as the one before, in whole
 * counts at the game's current mouse sensitivity.
 */
final class Creep {
	static final double MAX_PHI = 0.995;
	private static final int CALIBRATION_FRAMES = 20_000;
	private static final int BISECTIONS = 18;

	private double sd;
	private double phi;
	private double yaw, pitch;

	/** {@code sdDeg}: the per-frame sd; {@code sameSign}: the player's share; a count's degrees. */
	void configure(double sdDeg, double sameSign, double degreesPerCount, long seed) {
		sd = Math.max(0, sdDeg);
		phi = persistenceFor(sameSign, sd, degreesPerCount, seed);
		yaw = 0;
		pitch = 0;
	}

	double phi() {
		return phi;
	}

	/** This frame's turn in degrees: {@code out[0]} yaw, {@code out[1]} pitch. */
	void next(Random rng, double[] out) {
		double fresh = sd * Math.sqrt(1 - phi * phi);
		yaw = phi * yaw + fresh * rng.nextGaussian();
		pitch = phi * pitch + fresh * rng.nextGaussian();
		out[0] = yaw;
		out[1] = pitch;
	}

	/**
	 * The persistence giving {@code sameSign}: bisection over simulated frames, all with the same
	 * random draws so the share only moves with {@code phi}. 0 (fresh noise) when the share is
	 * already reached without any.
	 */
	static double persistenceFor(double sameSign, double sdDeg, double degreesPerCount, long seed) {
		if (sdDeg <= 0 || degreesPerCount <= 0 || sameSign <= sameShare(0, sdDeg, degreesPerCount, seed)) {
			return 0;
		}
		double lo = 0, hi = MAX_PHI;
		for (int i = 0; i < BISECTIONS; i++) {
			double mid = (lo + hi) / 2;
			if (sameShare(mid, sdDeg, degreesPerCount, seed) < sameSign) {
				lo = mid;
			} else {
				hi = mid;
			}
		}
		return (lo + hi) / 2;
	}

	/**
	 * Of the frames that turn by whole counts (rounding as {@link CameraController} does, the rest
	 * carried over), the share turning the same way as the turn before, on one axis.
	 */
	static double sameShare(double phi, double sdDeg, double degreesPerCount, long seed) {
		Random rng = new Random(seed);
		double fresh = sdDeg * Math.sqrt(1 - phi * phi);
		double v = 0, residual = 0;
		int last = 0, same = 0, pairs = 0;
		for (int i = 0; i < CALIBRATION_FRAMES; i++) {
			v = phi * v + fresh * rng.nextGaussian();
			double x = v / degreesPerCount + residual;
			double count = Math.rint(x);
			residual = x - count;
			if (count != 0) {
				int sign = count > 0 ? 1 : -1;
				if (last != 0) {
					pairs++;
					same += sign == last ? 1 : 0;
				}
				last = sign;
			}
		}
		return pairs == 0 ? 0.5 : (double) same / pairs;
	}
}
