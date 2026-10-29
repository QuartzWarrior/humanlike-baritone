package hb.executor.humanize;

/**
 * Steps toward the block being mined when it's further off than the player mines from
 * (movement.json mining_reach): the planner breaks anything in the game's reach, so left to itself
 * it stands still at the edge of reach, where a camera that lands near rather than on a block
 * often finds nothing under the crosshair. A player walks up to the work instead. The key is
 * relative to where the camera faces; none when the block is near enough, or mostly above or
 * below (walking wouldn't bring it nearer), or after {@link #MAX_TICKS} ticks of approaching
 * ({@link #stepped}) without getting near.
 */
final class MiningReach {
	static final double MIN_ACROSS = 0.5; // blocks between the feet and the block, horizontally
	static final int MAX_TICKS = 40;

	private double reach = MovementModel.defaults().miningReachQ90();
	private int approaching;

	void configure(MovementModel m) {
		reach = m.miningReachQ90();
		approaching = 0;
	}

	/**
	 * The movement key that takes the player toward the block at {@code block} (x, y, z), or null.
	 * {@code feet} and {@code eye} are positions, {@code yaw} the camera's (degrees).
	 */
	String key(double[] feet, double[] eye, int[] block, float yaw) {
		if (near(eye, block)) {
			approaching = 0;
			return null;
		}
		if (Math.hypot(gap(feet[0], block[0]), gap(feet[2], block[2])) < MIN_ACROSS || approaching >= MAX_TICKS) {
			return null;
		}
		double bearing = -Math.toDegrees(Math.atan2(block[0] + 0.5 - feet[0], block[2] + 0.5 - feet[2]));
		double rel = CameraController.wrap(bearing - yaw); // yaw grows turning right
		if (Math.abs(rel) <= 45) {
			return "key.forward";
		}
		if (Math.abs(rel) >= 135) {
			return "key.back";
		}
		return rel > 0 ? "key.right" : "key.left";
	}

	/** The block at {@code block} is within the player's reach from {@code eye}. */
	boolean near(double[] eye, int[] block) {
		double gx = gap(eye[0], block[0]), gy = gap(eye[1], block[1]), gz = gap(eye[2], block[2]);
		return Math.sqrt(gx * gx + gy * gy + gz * gz) <= reach;
	}

	/** The key from {@link #key} was pressed: a tick of approaching. */
	void stepped() {
		approaching++;
	}

	/** Not mining in place: the next approach starts afresh. */
	void reset() {
		approaching = 0;
	}

	/** Distance from p to the span [lo, lo + 1] (0 inside it). */
	private static double gap(double p, int lo) {
		return Math.max(0, Math.max(lo - p, p - (lo + 1)));
	}
}
