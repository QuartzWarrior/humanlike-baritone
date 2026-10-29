package hb.executor.humanize;

import java.util.HashSet;
import java.util.Random;
import java.util.Set;

/**
 * How this player walks (spec, Component B: movement; Component C, step 2: picking the set), on
 * top of the keys and look target the planner gives:
 * <ul>
 *   <li>the camera wanders off the way of travel as a mean-reverting (Ornstein-Uhlenbeck)
 *       process, points a fitted distance ahead along the path, and sits at the player's walking
 *       pitch, re-picked every few seconds;</li>
 *   <li>forward is held for the player's typical stretch, then lifted for a moment (a regrip);</li>
 *   <li>a sprint starts by double-tapping forward as often as the player does it, else by the key;</li>
 *   <li>on open, flat, straight ground with room overhead, a sprint becomes a sprint-jump as often
 *       as the player sprint-jumps, re-jumping the fitted number of ticks after each landing.</li>
 * </ul>
 * Regrips and sprint-jumps only happen when the caller says it's safe.
 */
public final class WalkStyle {
	static final double DT = 0.05; // seconds per tick
	static final String FORWARD = "key.forward", SPRINT = "key.sprint", JUMP = "key.jump";
	static final double MIN_LOOK_AHEAD = 3.0;
	/**
	 * The fitted forward holds pool every on-foot stretch, mostly shuffling about while mining
	 * (about a second, then a half-second stop); on a long walk that would stop the player every
	 * second. Until key timing is fitted per set, a regrip waits at least MIN_REGRIP_HOLD and lasts
	 * at most MAX_REGRIP_GAP.
	 */
	static final int MIN_REGRIP_HOLD = 60, MAX_REGRIP_GAP = 6;

	private final Random rng;
	private MovementModel model = MovementModel.defaults();
	private double strength = 1.0;

	private double heading;
	private double pitch;
	private int pitchTicks;
	private double lookAhead;
	private int lookAheadTicks;

	private int forwardHeld;
	private int holdLimit;
	private int regripLeft;

	private boolean sprintWanted;
	private boolean tapping; // this sprint is started by double-tapping
	private int tapPhase;

	private boolean sprintJumping;
	private boolean decidedJumping;
	private int groundTicks;
	private int jumpAt;

	public WalkStyle(Random rng) {
		this.rng = rng;
	}

	public void configure(MovementModel model, double strength) {
		this.model = model;
		this.strength = strength;
		heading = model.headingMean();
		pitchTicks = 0;
		lookAheadTicks = 0;
		forwardHeld = 0;
		holdLimit = sampleHold();
		regripLeft = 0;
		sprintWanted = false;
		sprintJumping = false;
		decidedJumping = false;
	}

	/**
	 * One tick of the camera's offset from the way of travel (degrees, positive = right): the exact
	 * Ornstein-Uhlenbeck step, wandering about the mean with the fitted stationary spread.
	 */
	public double headingOffset() {
		double decay = Math.exp(-model.headingTheta() * DT);
		heading = model.headingMean() + (heading - model.headingMean()) * decay
			+ model.headingSigma() * strength * Math.sqrt(1 - decay * decay) * rng.nextGaussian();
		heading = Math.max(-45, Math.min(45, heading));
		return heading;
	}

	/** The walking pitch to hold, re-picked every 3-6 s (degrees). */
	public double walkPitch() {
		if (pitchTicks-- <= 0) {
			pitch = Math.max(-30, Math.min(60, model.walkPitchMean() + rng.nextGaussian() * model.walkPitchSd() * strength));
			pitchTicks = 60 + rng.nextInt(61);
		}
		return pitch;
	}

	/**
	 * How far along the path to look (blocks), re-picked every 2-5 s. Never under MIN_LOOK_AHEAD:
	 * the player's fit is mostly "follows the path", which at under a few blocks means aiming at
	 * the next step's centre, the swinging the path look-ahead is there to avoid; the heading
	 * wander supplies the following.
	 */
	public double lookAheadBlocks() {
		if (lookAheadTicks-- <= 0) {
			lookAhead = Math.max(MIN_LOOK_AHEAD, Math.min(12, CameraPlan.logNormal(model.lookAheadMedian(), model.lookAheadSigma() * strength, rng)));
			lookAheadTicks = 40 + rng.nextInt(61);
		}
		return lookAhead;
	}

	/**
	 * The keys to press this tick: the planner's, with regrips, double-tap sprint starts and
	 * sprint-jumps worked in. {@code open}: the path ahead is flat, straight and clear overhead.
	 */
	public Set<String> keys(Set<String> wanted, boolean sprinting, boolean onGround, boolean safe, boolean open) {
		Set<String> out = new HashSet<>(wanted);
		boolean forward = wanted.contains(FORWARD);

		// Regrip: after a stretch of holding forward, lift it for a moment.
		if (regripLeft > 0) {
			regripLeft--;
			out.remove(FORWARD);
			out.remove(SPRINT);
		} else if (forward && safe && onGround && ++forwardHeld >= holdLimit) {
			forwardHeld = 0;
			holdLimit = sampleHold();
			regripLeft = (int) Math.max(1, Math.min(MAX_REGRIP_GAP, Math.round(CameraPlan.logNormal(model.regripMedian(), model.regripSigma(), rng))));
			out.remove(FORWARD);
			out.remove(SPRINT);
		} else if (!forward) {
			forwardHeld = 0;
		}

		// Sprint start: by the key, or by double-tapping forward.
		boolean wantsSprint = wanted.contains(SPRINT) && forward;
		if (wantsSprint && !sprintWanted) {
			tapping = rng.nextDouble() < model.doubleTapShare();
			tapPhase = 0;
		}
		sprintWanted = wantsSprint;
		if (wantsSprint && tapping) {
			out.remove(SPRINT);
			if (!sprinting && onGround && tapPhase < 4) {
				if (tapPhase % 2 == 0) {
					out.remove(FORWARD); // off, on, off, on: two presses inside the game's 7-tick window
				}
				tapPhase++;
			}
		}

		// Sprint-jumping on open ground.
		if (!(sprinting && open && safe)) {
			sprintJumping = false;
			decidedJumping = false;
			groundTicks = 0;
		} else {
			if (!decidedJumping) {
				decidedJumping = true;
				sprintJumping = rng.nextDouble() < model.sprintJumpShare() * strength;
				jumpAt = sampleJumpDelay();
			}
			if (sprintJumping) {
				if (!onGround) {
					groundTicks = 0;
				} else if (groundTicks++ >= jumpAt) {
					out.add(JUMP);
					groundTicks = 0;
					jumpAt = sampleJumpDelay();
				}
			}
		}
		return out;
	}

	boolean sprintJumping() {
		return sprintJumping;
	}

	private int sampleHold() {
		return (int) Math.max(MIN_REGRIP_HOLD, Math.round(CameraPlan.logNormal(model.forwardHoldMedian(), model.forwardHoldSigma(), rng)));
	}

	private int sampleJumpDelay() {
		double[] pmf = model.jumpAfterLanding();
		double total = 0;
		for (double p : pmf) {
			total += p;
		}
		double r = rng.nextDouble() * total;
		for (int i = 0; i < pmf.length; i++) {
			r -= pmf[i];
			if (r < 0) {
				return i;
			}
		}
		return pmf.length - 1;
	}
}
