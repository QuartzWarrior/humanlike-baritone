package hb.executor.humanize;

import java.util.Random;

/**
 * Sneaking while mining, as the player does (movement.json mining_sneak): stretches of sneaking
 * and of standing alternate while the player mines in place, each as long as the player's
 * (log-normal), with the standing stretches sized so the long-run share of sneaking is the
 * player's. A player shifts between blocks, not halfway through one, so standing up waits for a
 * break; crouching down can come at any time (it only makes a fall less likely).
 */
final class MiningSneak {
	private final Random rng;
	private double share;
	private double sneakMedian, sneakSigma, standMedian, standSigma;
	private boolean active;
	private boolean sneaking;
	private int left;

	MiningSneak(Random rng) {
		this.rng = rng;
	}

	void configure(MovementModel m, double strength) {
		share = Math.max(0, Math.min(1, m.miningSneakShare())) * Math.min(1, strength);
		sneakMedian = Math.max(1, m.miningSneakMedian());
		sneakSigma = m.miningSneakSigma();
		standSigma = m.miningStandSigma();
		// mean stand = mean sneak * (1 - share) / share, for log-normals mean = median * e^(sigma^2 / 2)
		double sneakMean = sneakMedian * Math.exp(sneakSigma * sneakSigma / 2);
		standMedian = share <= 0 ? Double.POSITIVE_INFINITY
			: sneakMean * (1 - share) / share / Math.exp(standSigma * standSigma / 2);
		active = false;
		sneaking = false;
	}

	/**
	 * One tick: whether the habit has the player sneaking now. {@code mining}: mining in place
	 * (a stretch of it starts the habit afresh); {@code justBroke}: a block broke a moment ago.
	 */
	boolean tick(boolean mining, boolean justBroke) {
		if (!mining || share <= 0) {
			active = false;
			sneaking = false;
			return false;
		}
		if (!active) {
			active = true;
			sneaking = rng.nextDouble() < share;
			left = draw(sneaking);
		}
		if (--left <= 0 && (!sneaking || justBroke)) {
			sneaking = !sneaking;
			left = draw(sneaking);
		}
		return sneaking;
	}

	private int draw(boolean sneak) {
		double median = sneak ? sneakMedian : standMedian;
		double sigma = sneak ? sneakSigma : standSigma;
		return (int) Math.max(1, Math.min(Integer.MAX_VALUE / 2, Math.round(CameraPlan.logNormal(median, sigma, rng))));
	}
}
