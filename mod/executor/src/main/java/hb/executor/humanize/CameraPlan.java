package hb.executor.humanize;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.concurrent.ConcurrentHashMap;

/**
 * One camera movement toward a target, as a person makes it (spec, Component B: camera): a
 * reaction delay, a primary submovement that usually stops a little short (or long), then
 * corrective submovements with pauses between them, landing near rather than on the exact point.
 * Each submovement's speed rises and falls like a minimum-jerk movement (speed ~ t^2 (1-t)^2),
 * skewed so the peak comes at the player's fitted fraction of its duration (speed ~ t^a (1-t)^b
 * with a = 4p, b = 4(1 - p), which peaks at exactly p and is minimum jerk at p = 0.5), and it bows
 * slightly sideways.
 *
 * <p>Pure math, no game state: offsets are degrees of yaw and pitch from where the plan started.
 */
public final class CameraPlan {
	/** A submovement: start and duration (ms from the plan's start), its move, peak and bow. */
	record Sub(double startMs, double durMs, double dYaw, double dPitch, double peak, double bow) {
	}

	private static final double MIN_DISTANCE = 0.3; // degrees; less is a hold, not a movement
	private static final int GRID = 256;
	private static final Map<Integer, double[]> PROFILES = new ConcurrentHashMap<>();

	private final List<Sub> subs;
	private final double endMs;
	private final double goalYaw;
	private final double goalPitch;

	private CameraPlan(List<Sub> subs, double goalYaw, double goalPitch) {
		this.subs = subs;
		this.endMs = subs.isEmpty() ? 0 : subs.getLast().startMs() + subs.getLast().durMs();
		this.goalYaw = goalYaw;
		this.goalPitch = goalPitch;
	}

	/**
	 * A plan to turn by (dYaw, dPitch) degrees toward a target {@code widthDeg} wide. {@code react}
	 * adds a reaction delay first (a new target, not a nudge of the current one). {@code strength}
	 * in (0, 1] scales every random deviation; at 1 the movement is as varied as the player's.
	 */
	public static CameraPlan plan(double dYaw, double dPitch, double widthDeg, boolean react, CameraModel m, double strength, Random rng) {
		double distance = Math.hypot(dYaw, dPitch);
		if (distance < MIN_DISTANCE) {
			return new CameraPlan(List.of(), dYaw, dPitch);
		}
		double s = strength;
		double t = react ? logNormal(m.reactionMedianMs(), m.reactionSigma() * s, rng) : 0;
		double fitts = m.fittsA() + m.fittsB() * log2(distance / Math.max(widthDeg, 0.5) + 1);
		double duration = clamp(fitts * Math.exp(rng.nextGaussian() * m.durationSigma() * s), 60, 1500);
		double gain = clamp(m.gainMean() + rng.nextGaussian() * m.gainSd() * s, 0.6, 1.3);
		double ux = dYaw / distance;
		double uy = dPitch / distance;

		ArrayList<Sub> subs = new ArrayList<>();
		double py = dYaw * gain;
		double pp = dPitch * gain;
		subs.add(new Sub(t, duration, py, pp, peak(m, s, rng), rng.nextGaussian() * m.curvatureSd() * s));
		t += duration;

		// Where the crosshair finally settles: near the target, not on its exact point. The
		// spread scales with how big the target looks (the player's spread as a share of width).
		double endYaw = dYaw + rng.nextGaussian() * m.landingFracYaw() * widthDeg * s;
		double endPitch = dPitch + rng.nextGaussian() * m.landingFracPitch() * widthDeg * s;
		double errYaw = endYaw - py;
		double errPitch = endPitch - pp;
		int n = Math.min(poisson(m.correctionsMean(), rng), Math.max(1, m.correctionsMax()));
		if (Math.hypot(dYaw - py, dPitch - pp) > widthDeg / 2) {
			n = Math.max(n, 1); // the primary ended off the target: a correction is needed, not optional
		}
		for (int i = 0; i < n; i++) {
			// Every correction but the last covers most of what's left; the last lands.
			double part = i == n - 1 ? 1.0 : 0.6 + 0.35 * rng.nextDouble();
			double cy = errYaw * part;
			double cp = errPitch * part;
			t += logNormal(m.correctionGapMedianMs(), m.correctionGapSigma() * s, rng);
			double d = clamp(logNormal(m.correctionMedianMs(), m.correctionSigma() * s, rng), 30, 600);
			subs.add(new Sub(t, d, cy, cp, peak(m, s, rng), rng.nextGaussian() * m.curvatureSd() * s));
			t += d;
			errYaw -= cy;
			errPitch -= cp;
		}
		double reachedYaw = py, reachedPitch = pp;
		for (int i = 1; i < subs.size(); i++) {
			reachedYaw += subs.get(i).dYaw();
			reachedPitch += subs.get(i).dPitch();
		}
		return new CameraPlan(List.copyOf(subs), reachedYaw, reachedPitch);
	}

	/** Degrees of (yaw, pitch) turned since the plan started, at {@code ms} after its start. */
	public double[] offsetAt(double ms) {
		double yaw = 0, pitch = 0;
		for (Sub sub : subs) {
			if (ms <= sub.startMs()) {
				break;
			}
			double u = Math.min(1.0, (ms - sub.startMs()) / sub.durMs());
			double along = along(sub.peak(), u);
			double side = sub.bow() * Math.sin(Math.PI * along); // bows out, comes back by the end
			yaw += sub.dYaw() * along - sub.dPitch() * side;
			pitch += sub.dPitch() * along + sub.dYaw() * side;
		}
		return new double[]{yaw, pitch};
	}

	public boolean done(double ms) {
		return ms >= endMs;
	}

	public double endMs() {
		return endMs;
	}

	/** Where the plan ends up, relative to its start. */
	public double[] goal() {
		return new double[]{goalYaw, goalPitch};
	}

	List<Sub> subs() {
		return subs;
	}

	/** Fraction of a submovement covered at normalized time u in [0, 1], peaking in speed at {@code peak}. */
	static double along(double peak, double u) {
		double[] cdf = PROFILES.computeIfAbsent((int) Math.round(peak * 100), k -> profile(k / 100.0));
		double x = Math.max(0, Math.min(1, u)) * GRID;
		int i = (int) Math.min(GRID - 1, Math.floor(x));
		return cdf[i] + (cdf[i + 1] - cdf[i]) * (x - i);
	}

	/** Cumulative share of the movement on a grid, for speed ~ t^a (1-t)^b peaking at {@code peak}. */
	private static double[] profile(double peak) {
		double a = 4 * peak, b = 4 * (1 - peak);
		double[] cdf = new double[GRID + 1];
		double sum = 0;
		for (int i = 1; i <= GRID; i++) {
			double t = (i - 0.5) / GRID;
			sum += Math.pow(t, a) * Math.pow(1 - t, b);
			cdf[i] = sum;
		}
		for (int i = 1; i <= GRID; i++) {
			cdf[i] /= sum;
		}
		return cdf;
	}

	/** Where this submovement's speed peaks, as a fraction of its duration. */
	private static double peak(CameraModel m, double s, Random rng) {
		return clamp(m.peakAtMean() + rng.nextGaussian() * m.peakAtSd() * s, 0.25, 0.75);
	}

	static double logNormal(double median, double sigma, Random rng) {
		return median * Math.exp(rng.nextGaussian() * sigma);
	}

	static int poisson(double mean, Random rng) {
		double l = Math.exp(-mean), p = 1;
		int k = 0;
		do {
			k++;
			p *= rng.nextDouble();
		} while (p > l);
		return k - 1;
	}

	private static double log2(double v) {
		return Math.log(v) / Math.log(2);
	}

	private static double clamp(double v, double lo, double hi) {
		return Math.max(lo, Math.min(hi, v));
	}
}
