package hb.executor;

import hb.common.mc.McHooks;
import hb.common.mc.McInput;
import hb.common.mc.McInputListener;
import hb.executor.humanize.Humanizer;
import hb.executor.planner.PlannerBackend;
import hb.executor.record.BotRecorder;
import hb.proto.v1.GameEvent;

import java.util.Locale;
import java.util.function.Consumer;

/**
 * The {@code /hb} chat commands and the lifecycle of one run (spec, Component C: controls).
 * <pre>
 *   /hb run &lt;planner command&gt;   e.g. /hb run mine iron_ore, /hb run goto 100 200
 *   /hb stop
 *   /hb strength &lt;0..1&gt;          0 = plain Baritone (for A/B tests), 1 = the full models
 *   /hb status
 * </pre>
 * A run records itself as a bot session and ends when the planner has had nothing to do for
 * {@link #IDLE_END_TICKS}. Runs only start, and only continue, in a world the player owns:
 * singleplayer, or a server where they have owner-level operator permission.
 *
 * <p>The watchdog (spec, Component C, step 7): progress is the player getting half a block to a
 * block other than the last few it made progress at (going up and down between two blocks, as a
 * pillar and a dig back down once did, is none; up or down only counts standing or on a ladder, out
 * of water: in it a player blocked by a wall bobs up and down, and a jump comes back down), or
 * breaking or placing a block somewhere other than the last few (one block placed and broken again
 * and again is none). After {@link #REPATH_TICKS} without it the planner plans afresh, unless attack
 * or use is held (a block takes a while to break); after {@link #STUCK_TICKS} the run ends as
 * "stuck", held keys or not (a build once held use on a block for minutes, placing nothing).
 */
public final class HbController {
	static final int IDLE_END_TICKS = 60;
	static final int REPATH_TICKS = 100; // 5 s
	static final int STUCK_TICKS = 600; // 30 s
	static final double PROGRESS_BLOCKS = 0.5;
	static final String NOT_OWNED = "Humanlike Baritone only runs in singleplayer, on a server on this computer "
		+ "(localhost), or on a server where you're an owner-level operator. Not starting.";

	private final PlannerBackend planner;
	private final BotRecorder recorder;
	private final Humanizer humanizer;
	private double strength = 1.0;
	private boolean running;
	private int idleTicks;
	private String command = "";
	private double[] progressAt;
	private int stillTicks;
	private int repaths;
	private String lastOutcome = "";
	static final int RECENT_WORK = 8; // block changes remembered: one at none of them is progress
	private boolean worked; // a block broken or placed somewhere new since the watchdog last looked
	private final long[] recentWork = new long[RECENT_WORK];
	private int recentWorkNext;
	private final long[] recentSpots = new long[RECENT_WORK]; // where the player was at the last progress
	private int recentSpotsNext;
	private final McInputListener work = new McInputListener() {
		@Override
		public void onGameEvent(long nanoTime, GameEvent event) {
			if (event.hasBlockBroken() || event.hasBlockPlaced()) {
				var p = event.hasBlockBroken() ? event.getBlockBroken().getPos() : event.getBlockPlaced().getPos();
				noteWork(McInput.blockKey(p.getX(), p.getY(), p.getZ()));
			}
		}
	};

	/** True, and remembered, if the player's block isn't one of the last few progress was made at. */
	private boolean newSpot(long block) {
		for (long k : recentSpots) {
			if (k == block) {
				return false;
			}
		}
		recentSpots[recentSpotsNext] = block;
		recentSpotsNext = (recentSpotsNext + 1) % RECENT_WORK;
		return true;
	}

	private void noteWork(long block) {
		for (long k : recentWork) {
			if (k == block) {
				return;
			}
		}
		recentWork[recentWorkNext] = block;
		recentWorkNext = (recentWorkNext + 1) % RECENT_WORK;
		worked = true;
	}

	public HbController(PlannerBackend planner, BotRecorder recorder, Humanizer humanizer) {
		this.planner = planner;
		this.recorder = recorder;
		this.humanizer = humanizer;
	}

	public void handle(String args, Consumer<String> reply) {
		String[] parts = args.trim().split("\\s+", 2);
		String verb = parts[0].toLowerCase(Locale.ROOT);
		String rest = parts.length > 1 ? parts[1].trim() : "";
		switch (verb) {
			case "run" -> run(rest, reply);
			case "stop" -> {
				if (running) {
					finish("stopped");
					reply.accept("Stopped.");
				} else {
					reply.accept("Nothing is running.");
				}
			}
			case "strength" -> setStrength(rest, reply);
			case "status" -> reply.accept(running
				? String.format(Locale.ROOT, "Running '%s' at strength %.2f, recording bot session %s.", command, strength, recorder.sessionId())
				: String.format(Locale.ROOT, "Idle. Strength %.2f.", strength));
			default -> reply.accept("Usage: /hb run <baritone command> | stop | strength <0..1> | status");
		}
	}

	private void setStrength(String value, Consumer<String> reply) {
		double s;
		try {
			s = Double.parseDouble(value);
		} catch (NumberFormatException e) {
			s = -1;
		}
		if (s < 0 || s > 1) {
			reply.accept("Usage: /hb strength <0..1>");
			return;
		}
		strength = s;
		reply.accept(String.format(Locale.ROOT, "Humanization strength %.2f%s.%s", s,
			s == 0 ? " (plain Baritone)" : "", running ? " Applies from the next run." : ""));
	}

	private void run(String cmd, Consumer<String> reply) {
		if (cmd.isEmpty()) {
			reply.accept("Usage: /hb run <baritone command>, e.g. /hb run mine iron_ore");
			return;
		}
		if (!McInput.ownsWorld()) {
			reply.accept(NOT_OWNED);
			return;
		}
		if (running) {
			finish("replaced by a new run");
		}
		recorder.start(strength);
		recorder.beginTask(cmd);
		if (strength > 0) {
			humanizer.start(strength);
		}
		if (!planner.execute(cmd)) {
			finish("unknown command");
			reply.accept("Baritone did not recognize: " + cmd);
			return;
		}
		running = true;
		McHooks.addListener(work);
		worked = false;
		java.util.Arrays.fill(recentWork, Long.MIN_VALUE);
		java.util.Arrays.fill(recentSpots, Long.MIN_VALUE);
		idleTicks = 0;
		command = cmd;
		progressAt = McInput.position();
		stillTicks = 0;
		repaths = 0;
		reply.accept("Running: " + cmd + (strength > 0 ? "" : " (plain Baritone)"));
	}

	/** Once per client tick. */
	public void tick() {
		if (!running) {
			return;
		}
		if (!McInput.ownsWorld()) {
			finish("left an owned world");
			return;
		}
		idleTicks = planner.isActive() ? 0 : idleTicks + 1;
		if (idleTicks >= IDLE_END_TICKS) {
			finish("done");
			return;
		}
		watchdog();
	}

	private void watchdog() {
		double[] at = McInput.position();
		boolean working = McInput.keyDown("key.attack") || McInput.keyDown("key.use");
		double climbed = !McInput.supported() ? 0 : Math.abs(at == null || progressAt == null ? 0 : at[1] - progressAt[1]);
		boolean progress = worked;
		worked = false;
		boolean moved = at != null && progressAt != null
			&& Math.hypot(at[0] - progressAt[0], at[2] - progressAt[2]) + climbed >= PROGRESS_BLOCKS;
		if (moved) {
			// Up and down between the same two blocks (a pillar, a dig back down) is no progress
			progressAt = at;
			moved = newSpot(McInput.blockKey((int) Math.floor(at[0]), (int) Math.floor(at[1]), (int) Math.floor(at[2])));
		}
		if (at == null || progressAt == null || progress || moved) {
			progressAt = at;
			stillTicks = 0;
			return;
		}
		if (!planner.isActive()) {
			return;
		}
		stillTicks++;
		if (stillTicks >= STUCK_TICKS) {
			finish("stuck");
		} else if (stillTicks % REPATH_TICKS == 0 && !working && planner.repath()) {
			repaths++;
		}
	}

	/** Ends the run (also on disconnect): planner stopped, input released, session closed. */
	public void finish(String why) {
		if (running) {
			lastOutcome = why;
		}
		McHooks.removeListener(work);
		planner.cancel();
		humanizer.stop();
		recorder.stop(why);
		running = false;
		command = "";
	}

	public boolean running() {
		return running;
	}

	/** How the last run ended: "done", "stuck", "stopped", ... */
	public String lastOutcome() {
		return lastOutcome;
	}

	/** Times the watchdog made the planner plan afresh in the current or last run. */
	public int repaths() {
		return repaths;
	}
}
