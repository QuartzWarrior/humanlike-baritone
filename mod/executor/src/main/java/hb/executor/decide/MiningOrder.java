package hb.executor.decide;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;

/**
 * Picks which block a build or clear job breaks next the way the player does (spec, Stage 1
 * decision models: mining order), out of the blocks the planner could break right now.
 * <p>
 * After each break the player's ranker scores the blocks they could go for next, from where they
 * stood and looked as it broke; the next time the planner asks, one of its candidates is drawn by
 * the ranker's softmax and kept until it's broken or out of reach, as a player finishes the block
 * they started. A player picks the next block while the current one is still breaking, so
 * {@link #predictNext} can draw it early, as if the current block were gone; that block is then
 * taken when the planner offers it. The job's first block, a step with none of the ranker's
 * candidates, and play without a fitted model go by the planner's own order (still kept until
 * done).
 * <p>
 * The ranker was trained on the job's volume: the blocks within 2 of the job's breaks. The planner
 * only offers blocks that are to go, so the volume here is the box of everything it has offered
 * in this job, grown by 2; outside it the world reads as unknown, as it did in training.
 */
public final class MiningOrder {
	static final int VOLUME_MARGIN = 2;
	public static final long NONE = Long.MIN_VALUE;

	/** The game, as the ranker needs it; McInput in the mod, a fake in tests. */
	public interface Game {
		/** The block id at (x, y, z), or null where it isn't loaded. */
		String block(int x, int y, int z);

		/** The player's feet (x, y, z), or null outside a world. */
		double[] stand();

		/** Yaw and pitch, degrees, or null outside a world. */
		float[] look();

		long key(int x, int y, int z);

		int[] unpack(long key);
	}

	private final Game game;
	private final Random rng;
	private MiningOrderModel model;
	private BreakFeatures.Job job = new BreakFeatures.Job();
	private List<BreakFeatures.Candidate> next; // the ranker's candidates for the coming choice
	private final Map<Long, double[]> nextFeatures = new HashMap<>();
	private long pick = NONE;
	private long planned = NONE; // drawn ahead, while the pick was breaking
	private boolean inJob; // the planner has offered blocks since the job began
	private int minX, minY, minZ, maxX, maxY, maxZ;

	public MiningOrder(Game game, Random rng) {
		this.game = game;
		this.rng = rng;
	}

	/** A new job (a new run): forgets everything; {@code model} null means the planner's order. */
	public void start(MiningOrderModel model) {
		this.model = model;
		job = new BreakFeatures.Job();
		next = null;
		nextFeatures.clear();
		pick = NONE;
		planned = NONE;
		inJob = false;
	}

	/** The block the job is breaking now (packed), or {@link #NONE}. */
	public long pick() {
		return pick;
	}

	/**
	 * Draws the block to break after {@code current} as if it were already broken, from where the
	 * player stands and looks now, and keeps it for the next choice; {@link #NONE} without a model
	 * or a candidate.
	 */
	public long predictNext(long current) {
		double[] stand = game.stand();
		float[] look = game.look();
		if (model == null || !inJob || stand == null || look == null) {
			return NONE;
		}
		int[] c = game.unpack(current);
		BreakFeatures.Job ahead = job.copy();
		ahead.broke(c[0], c[1], c[2], next);
		List<BreakFeatures.Candidate> cands = BreakFeatures.candidates(this::known, ahead, stand[0], stand[1], stand[2], look[0], look[1]);
		if (cands.isEmpty()) {
			return NONE;
		}
		double[][] rows = new double[cands.size()][];
		for (int i = 0; i < rows.length; i++) {
			rows[i] = cands.get(i).features();
		}
		BreakFeatures.Candidate drawn = cands.get(model.choose(rows, rng));
		planned = game.key(drawn.x(), drawn.y(), drawn.z());
		return planned;
	}

	/** The planner's candidates (packed positions, its own order, never empty): the index to break. */
	public int choose(long[] candidates) {
		for (long c : candidates) {
			grow(game.unpack(c));
		}
		for (int i = 0; i < candidates.length; i++) {
			if (candidates[i] == pick) {
				return i;
			}
		}
		for (int i = 0; i < candidates.length; i++) {
			if (candidates[i] == planned) {
				pick = planned;
				planned = NONE;
				return i;
			}
		}
		planned = NONE;
		int chosen = 0;
		if (model != null && !nextFeatures.isEmpty()) {
			List<double[]> rows = new ArrayList<>();
			List<Integer> index = new ArrayList<>();
			for (int i = 0; i < candidates.length; i++) {
				double[] f = nextFeatures.get(candidates[i]);
				if (f != null) {
					rows.add(f);
					index.add(i);
				}
			}
			if (!rows.isEmpty()) {
				chosen = index.get(model.choose(rows.toArray(new double[0][]), rng));
			}
		}
		pick = candidates[chosen];
		return chosen;
	}

	/**
	 * As {@link #choose(long[])}, but among the candidates {@code preferred} accepts when there
	 * are any (the job's extent still grows over all of them).
	 */
	public int choose(long[] candidates, java.util.function.LongPredicate preferred) {
		long[] near = java.util.Arrays.stream(candidates).filter(preferred).toArray();
		if (near.length == 0 || near.length == candidates.length) {
			return choose(candidates);
		}
		for (long c : candidates) {
			grow(game.unpack(c));
		}
		long chosen = near[choose(near)];
		for (int i = 0; i < candidates.length; i++) {
			if (candidates[i] == chosen) {
				return i;
			}
		}
		return 0; // not reached: chosen is one of them
	}

	/** A block broke at (x, y, z); counted only within a job the planner has offered blocks for. */
	public void broke(int x, int y, int z) {
		if (!inJob) {
			return;
		}
		job.broke(x, y, z, next);
		next = null;
		nextFeatures.clear();
		double[] stand = game.stand();
		float[] look = game.look();
		if (model == null || stand == null || look == null) {
			return;
		}
		next = BreakFeatures.candidates(this::known, job, stand[0], stand[1], stand[2], look[0], look[1]);
		for (BreakFeatures.Candidate c : next) {
			nextFeatures.put(game.key(c.x(), c.y(), c.z()), c.features());
		}
	}

	private String known(int x, int y, int z) {
		boolean inside = x >= minX - VOLUME_MARGIN && x <= maxX + VOLUME_MARGIN
			&& y >= minY - VOLUME_MARGIN && y <= maxY + VOLUME_MARGIN
			&& z >= minZ - VOLUME_MARGIN && z <= maxZ + VOLUME_MARGIN;
		return inside ? game.block(x, y, z) : null;
	}

	private void grow(int[] p) {
		if (!inJob) {
			minX = maxX = p[0];
			minY = maxY = p[1];
			minZ = maxZ = p[2];
			inJob = true;
			return;
		}
		minX = Math.min(minX, p[0]);
		maxX = Math.max(maxX, p[0]);
		minY = Math.min(minY, p[1]);
		maxY = Math.max(maxY, p[1]);
		minZ = Math.min(minZ, p[2]);
		maxZ = Math.max(maxZ, p[2]);
	}
}
