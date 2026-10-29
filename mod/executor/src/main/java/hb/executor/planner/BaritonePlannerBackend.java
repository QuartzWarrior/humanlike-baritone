package hb.executor.planner;

import baritone.api.BaritoneAPI;
import baritone.api.IBaritone;
import baritone.api.Settings;
import baritone.api.pathing.path.IPathExecutor;
import baritone.api.utils.BetterBlockPos;
import baritone.api.utils.IInputOverrideHandler;
import baritone.api.utils.IIntentSink;
import baritone.api.utils.Rotation;
import hb.common.mc.McInput;
import baritone.api.utils.input.Input;

import java.util.EnumMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

public final class BaritonePlannerBackend implements PlannerBackend {
	private volatile BreakChooser breakChooser;
	private Set<Long> offered = Set.of(); // client thread: what the builder offered this tick
	private Set<Long> candidates = Set.of(); // ... as of the tick's inputs
	private static final int HUMAN_MOVEMENT_TIMEOUT = 400; // ticks; Baritone's default is 100
	private static final Map<Input, String> BINDINGS = new EnumMap<>(Map.of(
		Input.MOVE_FORWARD, "key.forward",
		Input.MOVE_BACK, "key.back",
		Input.MOVE_LEFT, "key.left",
		Input.MOVE_RIGHT, "key.right",
		Input.CLICK_LEFT, "key.attack",
		Input.CLICK_RIGHT, "key.use",
		Input.JUMP, "key.jump",
		Input.SNEAK, "key.sneak",
		Input.SPRINT, "key.sprint"));

	private Integer savedTimeout;

	@Override
	public String name() {
		return "baritone";
	}

	@Override
	public boolean isReady() {
		return baritone().getPlayerContext().player() != null;
	}

	@Override
	public boolean isActive() {
		IBaritone baritone = baritone();
		return baritone.getPathingBehavior().isPathing()
			|| baritone.getPathingControlManager().mostRecentInControl().isPresent();
	}

	@Override
	public boolean execute(String command) {
		return baritone().getCommandManager().execute(command);
	}

	@Override
	public void cancel() {
		baritone().getPathingBehavior().cancelEverything();
	}

	@Override
	public Set<String> heldBindings() {
		IInputOverrideHandler input = baritone().getInputOverrideHandler();
		Set<String> held = new HashSet<>();
		BINDINGS.forEach((in, binding) -> {
			if (input.isInputForcedDown(in)) {
				held.add(binding);
			}
		});
		return held;
	}

	@Override
	public void setIntentListener(IntentListener listener) {
		// The builder asks the chooser before the tick's inputs go out: what it offered this tick
		// is what breakCandidates() reports for the rest of the tick.
		baritone().setBreakChooser(listener == null ? null : candidates -> {
			long[] keys = candidates.stream().mapToLong(p -> McInput.blockKey(p.x, p.y, p.z)).toArray();
			offered = new HashSet<>();
			for (long k : keys) {
				offered.add(k);
			}
			BreakChooser c = breakChooser;
			return c == null ? -1 : c.choose(keys);
		});
		baritone().setIntentSink(listener == null ? null : new IIntentSink() {
			@Override
			public void look(Rotation target, boolean blockInteract) {
				listener.look(target.getYaw(), target.getPitch(), blockInteract);
			}

			@Override
			public void inputs(Set<Input> held) {
				Set<String> bindings = new HashSet<>();
				for (Input in : held) {
					String binding = BINDINGS.get(in);
					if (binding != null) {
						bindings.add(binding);
					}
				}
				candidates = offered;
				offered = Set.of();
				listener.inputs(bindings);
			}
		});
	}

	@Override
	public void setHumanPacing(boolean on) {
		Settings settings = BaritoneAPI.getSettings();
		if (on) {
			// A person turns before walking and reacts before clicking: give movements longer
			// before they count as stuck.
			savedTimeout = settings.movementTimeoutTicks.value;
			settings.movementTimeoutTicks.value = HUMAN_MOVEMENT_TIMEOUT;
		} else if (savedTimeout != null) {
			settings.movementTimeoutTicks.value = savedTimeout;
			savedTimeout = null;
		}
	}

	@Override
	public void setBreakChooser(BreakChooser chooser) {
		this.breakChooser = chooser;
	}

	@Override
	public Set<Long> breakCandidates() {
		return candidates;
	}

	@Override
	public String debugState() {
		var ctx = baritone().getPlayerContext();
		Rotation rot = ctx.playerRotations();
		var control = baritone().getPathingControlManager().mostRecentInControl();
		var pathing = baritone().getPathingBehavior();
		return "sees " + ctx.getSelectedBlock().map(p -> String.valueOf(p.asLong())).orElse("-")
			+ String.format(java.util.Locale.ROOT, " at %.1f/%.1f", rot.getYaw(), rot.getPitch())
			+ "; " + control.map(c -> c.displayName()).orElse("no process")
			+ (pathing.isPathing() ? ", pathing" : "") + (pathing.getInProgress().isPresent() ? ", calculating" : "")
			+ ", goal " + pathing.getGoal() + ", offered " + candidates.size() + movementState();
	}

	/** The path's current movement for traces, or, with none, where the feet are and if that's the goal. */
	private String movementState() {
		String moving = currentMovement();
		if (!moving.isEmpty()) {
			return moving;
		}
		var ctx = baritone().getPlayerContext();
		var goal = baritone().getPathingBehavior().getGoal();
		return "; no movement, feet " + ctx.playerFeet() + (goal != null && goal.isInGoal(ctx.playerFeet()) ? " (in the goal)" : "");
	}

	/** The path's current movement for traces: kind, from and to, where the feet are. */
	private String currentMovement() {
		IPathExecutor current = baritone().getPathingBehavior().getCurrent();
		if (current == null || current.getPosition() >= current.getPath().movements().size()) {
			return "";
		}
		var m = current.getPath().movements().get(current.getPosition());
		BetterBlockPos d = m.getDest();
		var player = baritone().getPlayerContext().player();
		return "; " + m.getClass().getSimpleName() + " " + m.getSrc() + " -> " + d
			+ " feet " + baritone().getPlayerContext().playerFeet() + (m.safeToCancel() ? "" : " (busy)")
			+ "; dest " + McInput.blockId(d.x, d.y, d.z) + " / " + McInput.blockId(d.x, d.y + 1, d.z)
			+ " on " + McInput.blockId(d.x, d.y - 1, d.z) + (player.horizontalCollision ? "; bumping" : "");
	}

	@Override
	public double[][] pathAhead(int max) {
		IPathExecutor current = baritone().getPathingBehavior().getCurrent();
		if (current == null) {
			return new double[0][];
		}
		List<BetterBlockPos> nodes = current.getPath().positions();
		int from = Math.min(current.getPosition() + 1, nodes.size());
		int to = Math.min(nodes.size(), from + max);
		double[][] out = new double[to - from][];
		for (int i = from; i < to; i++) {
			BetterBlockPos p = nodes.get(i);
			out[i - from] = new double[]{p.x + 0.5, p.y, p.z + 0.5};
		}
		return out;
	}

	@Override
	public boolean repath() {
		return baritone().getPathingBehavior().cancelSegmentIfSafe();
	}

	// Looked up on every call rather than cached: Baritone creates its primary instance during
	// client startup, after mod initializers have run.
	private static IBaritone baritone() {
		return BaritoneAPI.getProvider().getPrimaryBaritone();
	}
}
