package hb.executor.humanize;

import java.util.Random;

/**
 * Turns the planner's look target into raw mouse counts, frame by frame (spec, Component C: motor
 * output). A new target starts a {@link CameraPlan} from where the camera is; a target that moves
 * a little keeps the plan; a finished plan still off target gets another correction, and a
 * camera at rest trembles slightly. A block target counts as reached once the crosshair is on that
 * face of that block, anywhere on it, as a player stops there; the block's size on screen sets the
 * movement time and how widely the landing spreads. Counts are whole numbers, as a mouse sends, with the remainder
 * carried to the next frame.
 * <p>
 * The planner often flips between nearby candidate blocks from one tick to the next. A player picks
 * one and goes for it, so a block target keeps the camera until the planner stops asking for it
 * (or it's gone), and only then gives way to the planner's newer pick.
 */
public final class CameraController {
	/** The game, as the controller sees it; {@link hb.common.mc.McInput} in the mod, a fake in tests. */
	public interface View {
		/** Yaw and pitch in degrees, or null outside a world. */
		float[] rotation();

		double degreesPerCount();

		/** +1 or -1 per axis (inverted mouse). */
		int[] axisSigns();

		/** The block a look in that direction hits within reach, or null. */
		Aim aim(float yaw, float pitch);

		/** The block and face under the crosshair, or null. */
		Aim crosshair();
	}

	/** A block a look points at, the face it hits, and how wide the block looks in degrees. */
	public record Aim(long block, int face, double widthDeg) {
		boolean sameFace(Aim other) {
			return other != null && other.block == block && other.face == face;
		}
	}

	static final double NEW_TARGET_DEG = 15.0; // the target jumped this far from the plan's goal: a new target
	static final double REACT_DEG = 8.0; // ... and the camera is this far off it: react before moving
	static final int RECENT_AIMS = 20; // target updates a recently aimed block stays good for
	static final int SWITCH_UPDATES = 4; // a block target not asked for in this many updates gives way
	static final int ANTICIPATE_UPDATES = 10; // a new block target this soon after a break was already picked
	static final double BLOCK_WIDTH_DEG = 6.0; // a block target when the look hits no block
	static final double VISIBLE_PROBE = 0.4; // share of a width probed each way (landings spread a third of it)
	static final double MIN_FACE_DEG = 1.0; // the smallest face width aimed for
	static final double VIEW_WIDTH_DEG = 20.0; // a direction to face while moving
	static final double SETTLE_BLOCK_DEG = 1.5; // close enough to a block target to stop correcting
	static final double SETTLE_VIEW_DEG = 6.0;

	private final View view;
	private final Random rng;
	private CameraModel model = CameraModel.defaults();
	private double strength = 1.0;

	private boolean hasTarget;
	private double targetYaw, targetPitch;
	private boolean blockInteract;
	private Aim aim; // the block a block target points at, if any
	private final java.util.Map<Long, Long> recentAims = new java.util.HashMap<>(); // block -> update it was aimed at
	private long updates;
	private long goalAskedAt; // the update the block target was last asked for
	private long brokeAt = Long.MIN_VALUE / 2; // the update a block was last broken at
	private boolean held;
	private long stillUntilNs = Long.MIN_VALUE; // no motion at all before this (a click going out)

	private CameraPlan plan;
	private long planStartNs;
	private double planGoalYaw, planGoalPitch; // absolute, where the plan was aimed when made
	private static final long NO_BLOCK = Long.MIN_VALUE;
	private long planBlock = NO_BLOCK; // the block target the plan was made for
	private double planWidth; // how wide the plan took the target to be, degrees
	private double emittedYaw, emittedPitch;
	private double residualX, residualY;
	private final Creep creep = new Creep(); // the resting hand's drift, set up for creepPerCount
	private double creepPerCount = Double.NaN;
	private final double[] creepTurn = new double[2];

	public CameraController(View view, Random rng) {
		this.view = view;
		this.rng = rng;
	}

	public void configure(CameraModel model, double strength) {
		this.model = model;
		this.strength = strength;
		creepPerCount = Double.NaN;
	}

	/** The planner's wanted rotation, as of this tick. */
	public void setTarget(float yaw, float pitch, boolean blockInteract) {
		float clamped = (float) Math.max(-90, Math.min(90, pitch));
		Aim asked = blockInteract ? view.aim(yaw, clamped) : null;
		updates++;
		if (asked != null) {
			recentAims.put(asked.block(), updates);
			recentAims.values().removeIf(at -> updates - at > RECENT_AIMS);
		}
		if (hasTarget && this.blockInteract && blockInteract && !sameGoal(yaw, clamped, asked)
			&& updates - goalAskedAt <= SWITCH_UPDATES && stillThere()) {
			return; // keep going for the block already picked
		}
		this.hasTarget = true;
		this.targetYaw = yaw;
		this.targetPitch = clamped;
		this.blockInteract = blockInteract;
		this.aim = asked;
		this.goalAskedAt = updates;
	}

	private boolean sameGoal(float yaw, float pitch, Aim asked) {
		return asked != null && aim != null ? asked.block() == aim.block()
			: Math.hypot(wrap(yaw - targetYaw), pitch - targetPitch) <= NEW_TARGET_DEG;
	}

	/** The picked block is still where the target looks (not broken, not hidden). */
	private boolean stillThere() {
		if (aim == null) {
			return true;
		}
		Aim now = view.aim((float) targetYaw, (float) targetPitch);
		return now != null && now.block() == aim.block();
	}

	/**
	 * A block just broke. While mining, a player picks the next block during the break, so a new
	 * block target right after it is moved to at once, without a reaction first.
	 */
	public void blockBroken() {
		brokeAt = updates;
	}

	/**
	 * A block was placed at {@code block}: what's there now is new, not the block the planner aimed
	 * at to break, and holding attack on it would break it again (wire placed where rock was just dug
	 * out went at once, round and round).
	 */
	public void blockPlaced(long block) {
		recentAims.remove(block);
	}

	/** The crosshair is on a block the planner aimed at within the last few target updates. */
	public boolean onRecentAim() {
		Aim on = view.crosshair();
		return on != null && recentAims.containsKey(on.block());
	}

	/**
	 * Held, the camera rests where it is (tremor only) whatever the target: a hesitation. Let go,
	 * it plans afresh from wherever it is then.
	 */
	public void hold(boolean held) {
		if (held && !this.held) {
			plan = null;
		}
		this.held = held;
	}

	/** No target: the camera rests where it is. */
	public void clearTarget() {
		hasTarget = false;
		plan = null;
		recentAims.clear();
	}

	/**
	 * The move to a block target has ended with the crosshair on the target's face. A click goes
	 * out a tick after the planner sees the crosshair on the face, and a camera still moving has
	 * left the face by then: a block placed against the wrong face faces the wrong way.
	 */
	public boolean landed(long nowNs) {
		float[] rot = view.rotation();
		return hasTarget && blockInteract && !held && plan != null && rot != null && plan.done((nowNs - planStartNs) / 1e6)
			&& settled(wrap(targetYaw - rot[0]), targetPitch - rot[1]);
	}

	/**
	 * Keeps the camera still until {@code untilNs}, tremor too: a click goes out on the game's next
	 * tick, at wherever the crosshair is by then, and a player holds still to place.
	 */
	public void still(long untilNs) {
		stillUntilNs = untilNs;
	}

	/**
	 * Resting on a block target, a small careful move onto the target point itself: from where the
	 * camera landed on the face, the click wouldn't do what the planner wants (a block placed there
	 * would face the wrong way), so the planner holds off, and a player adjusts.
	 */
	public void refine(long nowNs) {
		float[] rot = view.rotation();
		if (held || !hasTarget || !blockInteract || rot == null || plan != null && !plan.done((nowNs - planStartNs) / 1e6)) {
			return;
		}
		plan = CameraPlan.plan(wrap(targetYaw - rot[0]), targetPitch - rot[1], MIN_FACE_DEG, false, model, strength, rng);
		planWidth = MIN_FACE_DEG;
		planStartNs = nowNs;
		planGoalYaw = targetYaw;
		planGoalPitch = targetPitch;
		planBlock = aim != null ? aim.block() : NO_BLOCK;
		emittedYaw = 0;
		emittedPitch = 0;
	}

	/** How wide the current plan took its target to be, in degrees. */
	double planWidth() {
		return planWidth;
	}

	/** Degrees between the camera's yaw and the target's (0 without a target). */
	public double yawError() {
		float[] rot = view.rotation();
		return hasTarget && rot != null ? Math.abs(wrap(targetYaw - rot[0])) : 0;
	}

	/** One frame of mouse motion in counts: {@code out[0]} = dx, {@code out[1]} = dy. */
	public void frame(long nowNs, double[] out) {
		out[0] = 0;
		out[1] = 0;
		float[] rot = view.rotation();
		if (!hasTarget || rot == null) {
			return;
		}
		if (nowNs < stillUntilNs) {
			return;
		}
		if (held) {
			creep();
			emit(creepTurn[0], creepTurn[1], out);
			return;
		}
		double errYaw = wrap(targetYaw - rot[0]);
		double errPitch = targetPitch - rot[1];
		double ms = plan == null ? 0 : (nowNs - planStartNs) / 1e6;
		// A real jump, or another block (setTarget has already kept the planner's flips out), is a
		// new target, planned from scratch; a target drifting a little isn't.
		boolean newTarget = plan == null
			|| Math.hypot(wrap(targetYaw - planGoalYaw), targetPitch - planGoalPitch) > NEW_TARGET_DEG
			|| blockInteract && aim != null && aim.block() != planBlock;
		if (newTarget || plan.done(ms) && !settled(errYaw, errPitch)) {
			boolean anticipated = blockInteract && updates - brokeAt <= ANTICIPATE_UPDATES;
			boolean react = newTarget && !anticipated && Math.hypot(errYaw, errPitch) > REACT_DEG;
			double width = !blockInteract ? VIEW_WIDTH_DEG : aim != null ? visibleWidth(aim.widthDeg()) : BLOCK_WIDTH_DEG;
			plan = CameraPlan.plan(errYaw, errPitch, width, react, model, strength, rng);
			planWidth = width;
			planStartNs = nowNs;
			planGoalYaw = targetYaw;
			planGoalPitch = targetPitch;
			planBlock = aim != null ? aim.block() : NO_BLOCK;
			emittedYaw = 0;
			emittedPitch = 0;
			ms = 0;
		}
		double[] at = plan.offsetAt(ms);
		double dYaw = at[0] - emittedYaw;
		double dPitch = at[1] - emittedPitch;
		emittedYaw = at[0];
		emittedPitch = at[1];
		if (plan.done(ms)) {
			creep();
			dYaw += creepTurn[0];
			dPitch += creepTurn[1];
		}
		emit(dYaw, dPitch, out);
	}

	/**
	 * Resting on a block target, a small unhurried move to (yaw, pitch), a point the caller has
	 * checked is on the same block: a player breaking a block already drifts toward the next one.
	 * Ignored while a move is under way; the target stays as it was.
	 */
	public void lean(float yaw, float pitch, double widthDeg, long nowNs) {
		float[] rot = view.rotation();
		if (held || !hasTarget || !blockInteract || rot == null) {
			return;
		}
		if (plan != null && !plan.done((nowNs - planStartNs) / 1e6)) {
			return;
		}
		plan = CameraPlan.plan(wrap(yaw - rot[0]), pitch - rot[1], widthDeg, false, model, strength, rng);
		planStartNs = nowNs;
		planGoalYaw = targetYaw;
		planGoalPitch = targetPitch;
		planBlock = aim != null ? aim.block() : NO_BLOCK;
		emittedYaw = 0;
		emittedPitch = 0;
	}

	/** This frame's resting drift into {@link #creepTurn}, set up afresh when the sensitivity changes. */
	private void creep() {
		double perCount = view.degreesPerCount();
		if (perCount != creepPerCount) {
			creep.configure(model.tremorDeg() * strength, model.tremorSameSign(), perCount, rng.nextLong());
			creepPerCount = perCount;
		}
		creep.next(rng, creepTurn);
	}

	/** Degrees to whole mouse counts, carrying the remainder to the next frame. */
	private void emit(double dYaw, double dPitch, double[] out) {
		double perCount = view.degreesPerCount();
		int[] signs = view.axisSigns();
		double x = dYaw / perCount * signs[0] + residualX;
		double y = dPitch / perCount * signs[1] + residualY;
		out[0] = Math.rint(x);
		out[1] = Math.rint(y);
		residualX = x - out[0];
		residualY = y - out[1];
	}

	/**
	 * How wide the target face looks around the target point: the block's width, halved until looks
	 * {@link #VISIBLE_PROBE} of it off the point, each way, still land on that face. Often only part
	 * of a face shows (a block's side seen past the player's own feet, a top past the blocks around
	 * it), and a player aims for that part, more carefully, rather than anywhere over the block.
	 */
	private double visibleWidth(double width) {
		double w = width;
		for (; w > MIN_FACE_DEG; w /= 2) {
			double d = VISIBLE_PROBE * w;
			if (onFace(d, 0) && onFace(-d, 0) && onFace(0, d) && onFace(0, -d)) {
				return w;
			}
		}
		return MIN_FACE_DEG;
	}

	private boolean onFace(double dYaw, double dPitch) {
		return aim.sameFace(view.aim((float) (targetYaw + dYaw), (float) Math.max(-90, Math.min(90, targetPitch + dPitch))));
	}

	/** On target: a block target's block under the crosshair, else close enough in angle. */
	private boolean settled(double errYaw, double errPitch) {
		if (blockInteract && aim != null) {
			// The face too: placing needs the face to place against, and a player aims for it.
			return aim.sameFace(view.crosshair());
		}
		return Math.hypot(errYaw, errPitch) <= (blockInteract ? SETTLE_BLOCK_DEG : SETTLE_VIEW_DEG);
	}

	/** One line for traces: target, error, plan progress, aim and crosshair. */
	public String describe(long nowNs) {
		float[] rot = view.rotation();
		if (!hasTarget || rot == null) {
			return "no target";
		}
		double ms = plan == null ? 0 : (nowNs - planStartNs) / 1e6;
		return String.format(java.util.Locale.ROOT, "target %.1f/%.1f%s err %.1f/%.1f plan %s aim %s on %s%s",
			targetYaw, targetPitch, blockInteract ? " block" : "", wrap(targetYaw - rot[0]), targetPitch - rot[1],
			plan == null ? "none" : plan.done(ms) ? "done" : String.format(java.util.Locale.ROOT, "%.0f/%.0f ms", ms, plan.endMs()),
			aim, view.crosshair(), held ? " HELD" : "");
	}

	static double wrap(double deg) {
		double d = (deg + 180.0) % 360.0;
		return (d < 0 ? d + 360.0 : d) - 180.0;
	}
}
