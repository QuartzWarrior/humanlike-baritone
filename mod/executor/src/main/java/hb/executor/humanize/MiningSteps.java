package hb.executor.humanize;

import java.util.Random;
import java.util.function.Predicate;

/**
 * Steps about the work while mining in place, as the player takes them (movement.json
 * mining_steps): a player clearing an area rarely stands still, shuffling a step forward, back or
 * aside between blocks. Steps come at the player's rate, last the player's time (log-normal,
 * capped: longer is a walk, which the planner does), and use the player's mix of keys, relative to
 * where the camera faces. The caller says which way is safe to step, and is asked again every tick
 * of a step: a step ends the moment it isn't.
 */
final class MiningSteps {
	static final String[] KEYS = {"key.forward", "key.back", "key.left", "key.right"};
	static final int MAX_TICKS = 20;

	private final Random rng;
	private double perTick;
	private double median, sigma;
	private double[] shares = {0.4, 0.2, 0.2, 0.2};
	private String key;
	private int left;

	MiningSteps(Random rng) {
		this.rng = rng;
	}

	void configure(MovementModel m, double strength) {
		median = Math.max(1, m.miningStepMedian());
		sigma = m.miningStepSigma();
		// The rate is per minute of mining, steps included; a step only starts between steps, so
		// the chance a tick between them starts one is higher by the share of time spent stepping.
		double rate = Math.max(0, m.miningStepsPerMin()) / 1200 * Math.min(1, strength);
		double meanTicks = Math.min(MAX_TICKS, median * Math.exp(sigma * sigma / 2));
		perTick = rate / Math.max(0.05, 1 - rate * meanTicks);
		shares = m.miningStepKeys();
		left = 0;
	}

	/**
	 * One tick: the movement key to hold now, or null. {@code mining}: mining in place; {@code safe}:
	 * whether stepping that way (a key in {@link #KEYS}) is safe now.
	 */
	String tick(boolean mining, Predicate<String> safe) {
		if (!mining) {
			left = 0;
			return null;
		}
		if (left > 0) {
			if (!safe.test(key)) {
				left = 0;
				return null;
			}
			left--;
			return key;
		}
		if (rng.nextDouble() >= perTick) {
			return null;
		}
		key = pick();
		if (!safe.test(key)) {
			return null; // no step this time, as a player wouldn't walk into a wall or off a drop
		}
		left = (int) Math.max(1, Math.min(MAX_TICKS, Math.round(CameraPlan.logNormal(median, sigma, rng)))) - 1;
		return key;
	}

	private String pick() {
		double total = 0;
		for (double s : shares) {
			total += Math.max(0, s);
		}
		double r = rng.nextDouble() * total;
		for (int i = 0; i < KEYS.length; i++) {
			r -= Math.max(0, shares[i]);
			if (r < 0) {
				return KEYS[i];
			}
		}
		return KEYS[0];
	}
}
