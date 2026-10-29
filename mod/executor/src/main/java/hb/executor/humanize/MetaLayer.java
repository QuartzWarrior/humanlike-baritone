package hb.executor.humanize;

import java.util.Random;

/**
 * Hesitations and glances (spec, Component C, step 5: the meta layer; Component B: meta
 * behaviors). Each tick a hesitation starts with the player's fitted per-minute rate for the
 * current context (mining, walking, building, other), and right after a block breaks with the
 * fitted "task just finished" chance; while standing still, a glance away starts at the glance
 * rate. The safety filter has the last word: nothing starts unless {@code safe}, and anything
 * under way stops the tick it isn't (spec, step 6).
 *
 * <p>Mistakes (wrong slot, missed click...) aren't injected yet.
 */
public final class MetaLayer {
	public enum State {NONE, HESITATE, GLANCE}

	static final int TICK_MS = 50;
	static final int MIN_HESITATION_TICKS = 6; // 300 ms: shorter isn't a hesitation (meta.hesitate)
	static final int MAX_HESITATION_TICKS = 60; // 3 s: longer is a break, not a hesitation

	private final Random rng;
	private MetaModel model = MetaModel.defaults();
	private double strength = 1.0;
	private State state = State.NONE;
	private int ticksLeft;
	private double glanceYaw, glancePitch;
	private boolean afterBreak;

	public MetaLayer(Random rng) {
		this.rng = rng;
	}

	public void configure(MetaModel model, double strength) {
		this.model = model;
		this.strength = strength;
		state = State.NONE;
		afterBreak = false;
	}

	/** A block just broke: maybe pause, as after finishing a small task. */
	public void blockBroken() {
		if (rng.nextDouble() < model.afterBreakP() * strength) {
			afterBreak = true;
		}
	}

	/**
	 * One game tick. {@code context}: "mining", "walking", "building" or "other"; {@code safe}:
	 * steady footing and nothing dangerous near; {@code stationary}: not moving and not asked to.
	 */
	public State tick(String context, boolean safe, boolean stationary) {
		if (!safe) {
			state = State.NONE;
			afterBreak = false;
			return state;
		}
		if (state != State.NONE && --ticksLeft > 0) {
			return state;
		}
		state = State.NONE;
		if (afterBreak || rng.nextDouble() < model.hesitationPerMin(context) * strength / 1200.0) {
			afterBreak = false;
			state = State.HESITATE;
			double ms = CameraPlan.logNormal(model.hesitationMedianMs(), model.hesitationSigma(), rng);
			ticksLeft = (int) Math.max(MIN_HESITATION_TICKS, Math.min(MAX_HESITATION_TICKS, Math.round(ms / TICK_MS)));
		} else if (stationary && rng.nextDouble() < model.glancePerMin() * strength / 1200.0) {
			state = State.GLANCE;
			double angle = Math.min(120, CameraPlan.logNormal(model.glanceAngleMedian(), model.glanceAngleSigma(), rng));
			glanceYaw = rng.nextBoolean() ? angle : -angle;
			glancePitch = rng.nextGaussian() * angle / 4;
			double ms = CameraPlan.logNormal(model.glanceHoldMedianMs(), model.glanceHoldSigma(), rng);
			ticksLeft = (int) Math.max(4, Math.min(100, Math.round(ms / TICK_MS)));
		}
		return state;
	}

	public State state() {
		return state;
	}

	/** Where a glance looks, relative to where the camera was when it started (degrees). */
	public double glanceYaw() {
		return glanceYaw;
	}

	public double glancePitch() {
		return glancePitch;
	}
}
