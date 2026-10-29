package hb.executor.humanize;

import hb.common.io.HbData;
import hb.common.mc.McHooks;
import hb.common.mc.McInput;
import hb.common.mc.McInputListener;
import hb.executor.decide.MiningOrder;
import hb.executor.decide.MiningOrderModel;
import hb.executor.planner.IntentListener;
import hb.executor.planner.PlannerBackend;
import hb.proto.v1.GameEvent;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Random;
import java.util.Set;
import java.util.stream.Stream;

/**
 * The per-tick pipeline between the planner's intent and the game (spec, Component C): takes the
 * planner's look target and keys, and sends real mouse counts and key presses through
 * {@link McInput}, shaped by the player's fitted models. While active, the planner applies none
 * of its own intent. Each tick:
 * <ol>
 *   <li>the context (mining, building, walking, other) and the safety filter's verdict: steady
 *       footing, no lava, fire or fatal drop close by, not sneaking at an edge;</li>
 *   <li>the meta layer may start a hesitation (everything held still) or, standing still, a glance
 *       away and back ({@link MetaLayer});</li>
 *   <li>the camera aims at the planner's block, or while walking a point ahead along the path,
 *       wandering and pitched like the player ({@link WalkStyle}, {@link CameraController});</li>
 *   <li>the keys: regrips, sprint starts and sprint-jumps worked in, then turned to the camera's
 *       actual facing and pressed with human timing ({@link KeyController}).</li>
 * </ol>
 * Not yet: the mining-order model and injected mistakes.
 */
public final class Humanizer implements IntentListener, McInputListener {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-humanizer");

	static final int CONTEXT_TICKS = 40; // an attack or use within the last 2 s sets the context
	static final int ATTACK_GRACE_TICKS = 4; // attack stays held this long across the planner's flips
	static final int SHIFT_TICKS = 3; // a block broke this recently: a sneaking player may stand up
	static final double LEAN_FRACTION = 0.35; // how far across a block's angular size the camera leans
	static final int HAZARD_RADIUS = 3; // blocks: no meta within 3 blocks of lava or a fatal drop
	static final int FATAL_DROP = 4;
	private static final int PATH_NODES = 12;
	private static final int OPEN_NODES = 6; // a straight, flat, clear stretch this long allows sprint-jumping
	static final int BLOCKED_TICKS = 10; // the planner's keys held this long without getting anywhere ...
	static final double BLOCKED_BLOCKS = 0.3; // ... (this far): blocked
	static final int DIRECT_TICKS = 40; // then the camera faces the planner's way at least this long
	static final int SNEAK_GRACE_TICKS = 10; // a player placing stays crouched between blocks this long
	static final int REFINE_TICKS = 10; // resting on a block target this long without the planner clicking: adjust
	static final int USE_HOLD_TICKS = 3; // a place click holds use at most this long: the game repeats it every 4
	static final int USE_CYCLE_TICKS = 12; // the planner still asking this long after a click: click again
	static final long CLICK_STILL_NS = 200_000_000L; // the camera holds still while a place click goes out (keys lag 0-2 ticks)
	static final int SNEAK_FIRST_TICKS = 2; // sneak down this long before a place click: the server hears of it first
	static final int CLOSE_MIN_TICKS = 6, CLOSE_MAX_TICKS = 16; // a container opened by mistake is closed after this long
	static final int AIM_GAP_TICKS = 4; // a block target the planner skips this long still holds a still player's camera
	/** -Dhb.humanizer.trace=true logs the planner's intent and the camera once a second (debugging). */
	private static final boolean TRACE = Boolean.getBoolean("hb.humanizer.trace");
	private static final boolean TICK_TRACE = Boolean.getBoolean("hb.humanizer.tickTrace");

	private final PlannerBackend planner;
	private final Random rng = new Random();
	private final CameraController camera = new CameraController(new CameraController.View() {
		@Override
		public float[] rotation() {
			return McInput.rotation();
		}

		@Override
		public double degreesPerCount() {
			return McInput.degreesPerCount();
		}

		@Override
		public int[] axisSigns() {
			return McInput.axisSigns();
		}

		@Override
		public CameraController.Aim aim(float yaw, float pitch) {
			McInput.BlockAim a = McInput.blockAlong(yaw, pitch);
			return a == null ? null : new CameraController.Aim(a.block(), a.face(), a.widthDeg());
		}

		@Override
		public CameraController.Aim crosshair() {
			McInput.BlockAim a = McInput.crosshair();
			return a == null ? null : new CameraController.Aim(a.block(), a.face(), 0);
		}
	}, rng);
	private final KeyController keys = new KeyController(new KeyController.Keys() {
		@Override
		public void set(String binding, boolean down) {
			McInput.setKey(binding, down);
		}

		@Override
		public void click(String binding) {
			McInput.clickKey(binding);
		}
	}, rng);
	private final MetaLayer meta = new MetaLayer(rng);
	private final WalkStyle walk = new WalkStyle(rng);

	private boolean active;
	private long tick;
	private long lastAttack = Long.MIN_VALUE / 2, lastUse = Long.MIN_VALUE / 2;
	private long lastBreak = Long.MIN_VALUE / 2;
	private long lastBlockTarget = Long.MIN_VALUE / 2;
	private long lastPlannerSneak = Long.MIN_VALUE / 2;
	private int restingTicks; // on a block target, landed, without the planner clicking
	private int useTicks; // ticks into the current place click's cycle (0: none under way)
	private int sneakHeld; // ticks the sneak key has been down
	private int containerTicks; // ticks a container's screen has been open
	private int closeAfter;
	private final MiningSneak sneak = new MiningSneak(rng);
	private final MiningSteps steps = new MiningSteps(rng);
	private final MiningReach reach = new MiningReach();
	private long leanedFor = MiningOrder.NONE; // the block the camera last leaned past
	private final MiningOrder order = new MiningOrder(new MiningOrder.Game() {
		@Override
		public String block(int x, int y, int z) {
			return McInput.blockId(x, y, z);
		}

		@Override
		public double[] stand() {
			return McInput.position();
		}

		@Override
		public float[] look() {
			return McInput.rotation();
		}

		@Override
		public long key(int x, int y, int z) {
			return McInput.blockKey(x, y, z);
		}

		@Override
		public int[] unpack(long key) {
			return McInput.unpackBlock(key);
		}
	}, rng);
	private float plannerYaw;
	private boolean blockTargetThisTick;
	private float walkYaw, walkPitch;
	private boolean hasWalkTarget;
	private MetaLayer.State lastMeta = MetaLayer.State.NONE;
	private double[] blockedFrom; // where the planner's movement keys last got the player
	private int blockedTicks, directTicks;
	private float glanceFromYaw, glanceFromPitch;

	public Humanizer(PlannerBackend planner) {
		this.planner = planner;
	}

	public boolean active() {
		return active;
	}

	/** Takes over from the planner. {@code strength} in (0, 1] scales the models' variation. */
	public void start(double strength) {
		if (active) {
			return;
		}
		Path models = HbData.models().resolve("stage1");
		List<String> missing = Stream.of("camera.json", "movement.json", "meta.json", MiningOrderModel.FILE)
			.filter(f -> !Files.exists(models.resolve(f))).toList();
		if (missing.isEmpty()) {
			LOGGER.info("Humanizing with the models in {}", models);
		} else {
			LOGGER.warn("Missing {} in {}: generic human defaults stand in for a motor model, Baritone's own order "
				+ "for the mining order (hbpipe fit-models writes them)", missing, models);
		}
		camera.configure(load(() -> CameraModel.load(models.resolve("camera.json")), CameraModel.defaults(), "camera"), strength);
		MovementModel movement = load(() -> MovementModel.load(models.resolve("movement.json")), MovementModel.defaults(), "movement");
		walk.configure(movement, strength);
		sneak.configure(movement, strength);
		steps.configure(movement, strength);
		reach.configure(movement);
		leanedFor = MiningOrder.NONE;
		meta.configure(load(() -> MetaModel.load(models.resolve("meta.json")), MetaModel.defaults(), "meta"), strength);
		camera.clearTarget();
		camera.hold(false);
		hasWalkTarget = false;
		blockedFrom = null;
		blockedTicks = 0;
		directTicks = 0;
		blockTargetThisTick = false;
		lastMeta = MetaLayer.State.NONE;
		float[] rot = McInput.rotation();
		plannerYaw = rot == null ? 0 : rot[0];
		planner.setHumanPacing(true);
		planner.setIntentListener(this);
		planner.setBreakChooser(this::chooseBreak);
		Path ranker = models.resolve(MiningOrderModel.FILE);
		order.start(Files.exists(ranker) ? MiningOrderModel.load(ranker) : null);
		McInput.setMouseSource(camera::frame);
		McHooks.addListener(this);
		active = true;
	}

	/** Gives control back: no more injected input, every held key released. */
	public void stop() {
		if (!active) {
			return;
		}
		McHooks.removeListener(this);
		planner.setBreakChooser(null);
		planner.setIntentListener(null);
		planner.setHumanPacing(false);
		McInput.setMouseSource(null);
		camera.clearTarget();
		keys.releaseAll();
		active = false;
	}

	/**
	 * The planner's wanted rotation. A block to break or place is the camera's target as given.
	 * A direction to walk isn't: the planner aims each step at the next block's centre, which
	 * swings about as a slower, human camera lags behind it, so while walking the camera looks
	 * along the path instead ({@link #inputs}). Either way the planner's yaw is the frame its
	 * movement keys mean, which the key controller translates to the camera's actual facing.
	 */
	@Override
	public void look(float yaw, float pitch, boolean blockInteract) {
		plannerYaw = yaw;
		if (blockInteract) {
			blockTargetThisTick = true;
			camera.setTarget(yaw, pitch, true);
		} else {
			walkYaw = yaw;
			walkPitch = pitch;
			hasWalkTarget = true;
		}
	}

	/** Once per game tick, after every look of the tick. */
	@Override
	public void inputs(Set<String> bindings) {
		float[] rot = McInput.rotation();
		if (rot == null) {
			return;
		}
		tick++;
		if (McInput.containerOpen()) {
			// A click on a chest or hopper that reached the server before its sneak did opens it;
			// the planner never means to (the builder places against them sneaking). A player
			// closes it again.
			if (containerTicks++ == 0) {
				closeAfter = CLOSE_MIN_TICKS + rng.nextInt(CLOSE_MAX_TICKS - CLOSE_MIN_TICKS + 1);
				if (TRACE) {
					LOGGER.info("trace: {} opened; closing it in {} ticks", McInput.screenName(), closeAfter);
				}
			} else if (containerTicks >= closeAfter) {
				McInput.closeContainer();
				containerTicks = 0;
			}
			keys.update(Set.of(), plannerYaw, rot[0], McInput.sprinting());
			return;
		}
		containerTicks = 0;
		if (bindings.contains("key.attack")) {
			lastAttack = tick;
		}
		if (bindings.contains("key.use")) {
			lastUse = tick;
		}
		if (bindings.contains("key.sneak")) {
			lastPlannerSneak = tick;
		}
		boolean moving = bindings.stream().anyMatch(KeyController.MOVEMENT::contains);
		String context = tick - lastAttack < CONTEXT_TICKS ? "mining"
			: tick - lastUse < CONTEXT_TICKS ? "building"
			: moving ? "walking" : "other";
		// The habit is for mining on solid ground: in water sneaking sinks the player the planner
		// is swimming up (jump), and nobody sneaks mid-jump.
		boolean sneaking = sneak.tick(context.equals("mining") && !moving && !bindings.contains("key.jump")
			&& McInput.steadyFooting(), tick - lastBreak <= SHIFT_TICKS);
		boolean footing = McInput.steadyFooting() && !bindings.contains("key.sneak");
		boolean safe = footing && !McInput.hazardNear(HAZARD_RADIUS, FATAL_DROP);
		// Steps check their own landing (stepIsSafe), so a drop elsewhere nearby doesn't stop them
		// (clearing a room over a cave, it always would); lava and fire still do.
		boolean stepsSafe = footing && !McInput.lavaOrFireNear(HAZARD_RADIUS);
		boolean blockTarget = blockTargetThisTick;
		blockTargetThisTick = false;
		if (blockTarget) {
			lastBlockTarget = tick;
		}
		// The planner can skip its block for a tick or two (a builder's pick flickers in and out of
		// reach). A player keeps aiming: turning to the path and back every other tick restarted the
		// camera's move each tick, before it got going, and the camera never moved (a build stuck).
		boolean aiming = blockTarget || !moving && tick - lastBlockTarget <= AIM_GAP_TICKS;
		// Standing still and not aiming at anything: the planner isn't waiting on the camera.
		boolean stationary = !moving && !aiming && McInput.speed() < 0.5;
		MetaLayer.State state = meta.tick(context, safe, stationary);
		boolean sprinting = McInput.sprinting();

		if (state == MetaLayer.State.HESITATE) {
			camera.hold(true);
			keys.update(sneaking ? Set.of("key.sneak") : Set.of(), plannerYaw, rot[0], sprinting);
			lastMeta = state;
			return;
		}
		camera.hold(false);
		if (state == MetaLayer.State.GLANCE) {
			if (lastMeta != MetaLayer.State.GLANCE) {
				glanceFromYaw = rot[0];
				glanceFromPitch = rot[1];
			}
			camera.setTarget((float) (glanceFromYaw + meta.glanceYaw()), (float) (glanceFromPitch + meta.glancePitch()), false);
		} else if (!aiming) {
			if (blocked(moving) && hasWalkTarget) {
				// Stuck on a corner or in a one-wide gap, a player looks where they're trying to go:
				// keys from a camera looking along the path ahead can head into the wall.
				camera.setTarget(walkYaw, walkPitch, false);
			} else {
				lookAlongPath(rot, moving);
			}
		}
		lastMeta = state;
		Set<String> want = walk.keys(bindings, sprinting, McInput.steadyFooting(), safe, sprinting && openAhead());
		if (!want.contains("key.attack") && !bindings.contains("key.use") && (onBreakCandidate()
			|| tick - lastAttack <= ATTACK_GRACE_TICKS && camera.onRecentAim())) {
			// The planner asks to attack only while the crosshair is on its pick; a player clearing
			// an area keeps attack held over any block that is to go, and across the planner's flips.
			want.add("key.attack");
		}
		long now = System.nanoTime();
		sneakHeld = keys.isDown("key.sneak") ? sneakHeld + 1 : 0;
		if (want.contains("key.use") && useTicks == 0 && (!camera.landed(now)
			|| bindings.contains("key.sneak") && sneakHeld < SNEAK_FIRST_TICKS)) {
			// A player places once the crosshair has come to rest on the face, and crouched already:
			// a click that goes out with the crouch reaches the server first, and opens what it hits.
			want.remove("key.use");
		} else if (want.contains("key.use")) {
			// One click, not use held down: held, the game places again every 4 ticks wherever the
			// crosshair has got to (a hopper on the hopper just placed). The planner asks until it
			// sees the block; still asking a while after the click, it missed, so click again.
			if (useTicks == 0) {
				camera.still(now + CLICK_STILL_NS);
			}
			if (useTicks >= USE_HOLD_TICKS) {
				want.remove("key.use");
			}
			useTicks = (useTicks + 1) % USE_CYCLE_TICKS;
		} else {
			useTicks = 0;
		}
		if (blockTarget && camera.landed(now) && !bindings.contains("key.use") && !bindings.contains("key.attack")) {
			if (++restingTicks >= REFINE_TICKS) {
				camera.refine(now);
				restingTicks = 0;
			}
		} else {
			restingTicks = 0;
		}
		if (sneaking) {
			want.add("key.sneak");
		}
		// The builder crouches only on the ticks it means to place, and its pick flickers: standing up
		// in between bobs the eye, so the crosshair jumps between blocks and never comes to rest on one.
		if (!moving && !bindings.contains("key.jump") && tick - lastPlannerSneak <= SNEAK_GRACE_TICKS) {
			want.add("key.sneak");
		}
		// A step about the work: only mining in place in a build or clear job, never while the
		// planner moves; its key is relative to where the camera faces.
		boolean inPlace = context.equals("mining") && !moving && stepsSafe && !planner.breakCandidates().isEmpty();
		String approach = inPlace ? approach(rot) : null;
		if (!inPlace) {
			reach.reset();
		}
		// approaching ends any step of the player's own under way
		String own = steps.tick(inPlace && approach == null, key -> stepIsSafe(rot[0], key));
		String step = approach != null ? approach : own;
		if (step != null) {
			want.add(step);
		}
		leanTowardNext(rot, want);
		keys.update(want, step != null ? rot[0] : plannerYaw, rot[0], sprinting);
		if (TICK_TRACE && !bindings.contains("key.attack") && blockTarget) {
			LOGGER.info("tick-trace: {} planner wants {}; pressing {}; {}; {}", tick, bindings, want,
				planner.debugState(), camera.describe(System.nanoTime()));
		}
		if (TRACE && tick % 20 == 0) {
			LOGGER.info("trace: planner wants {} yaw {}; pressing {}; {} {}; {}; {}{}", bindings, String.format(java.util.Locale.ROOT, "%.1f", plannerYaw),
				want, context, state, camera.describe(System.nanoTime()), planner.debugState(),
				McInput.screenName().isEmpty() ? "" : "; screen " + McInput.screenName());
			if (context.equals("mining")) {
				LOGGER.info("step-trace: in place {} (footing {}, lava {}, offered {}); pick {} approach {} step {}", inPlace,
					footing, McInput.lavaOrFireNear(HAZARD_RADIUS), planner.breakCandidates().size(),
					order.pick(), approach, step);
			}
		}
	}

	/**
	 * The crosshair is on a block the build or clear job is to break, or, with attack already held
	 * during the job, on nothing at all. A player clearing an area holds attack from block to block:
	 * the game's 5-tick pause after each break runs out while it's held, and the next block starts
	 * the moment the pause ends. Releasing and pressing again instead starts the block at once and
	 * then waits the pause out on it. Never pressed afresh at nothing, though: a click on air is a
	 * miss, and a miss holds breaking off for 10 ticks.
	 */
	private boolean onBreakCandidate() {
		Set<Long> candidates = planner.breakCandidates();
		if (candidates.isEmpty()) {
			return false;
		}
		McInput.BlockAim on = McInput.crosshair();
		return on == null ? McInput.keyDown("key.attack") : candidates.contains(on.block());
	}

	/**
	 * Breaking the job's block, a player has already picked the next one and drifts toward it:
	 * once per block, the next is drawn ahead ({@link MiningOrder#predictNext}) and the camera
	 * leans across this block's face toward it, staying on this face, so the move after the break
	 * is short and the camera is seldom still while mining.
	 */
	private void leanTowardNext(float[] rot, Set<String> want) {
		long pick = order.pick();
		if (pick == MiningOrder.NONE || pick == leanedFor || !want.contains("key.attack")) {
			return;
		}
		McInput.BlockAim on = McInput.crosshair();
		if (on == null || on.block() != pick) {
			return;
		}
		leanedFor = pick;
		long next = order.predictNext(pick);
		if (TRACE) {
			LOGGER.info("lean-trace: on {} next {}", pick, next);
		}
		double[] eye = McInput.eye();
		McInput.BlockAim here = McInput.blockAlong(rot[0], rot[1]);
		if (next == MiningOrder.NONE || eye == null || here == null) {
			return;
		}
		int[] b = McInput.unpackBlock(next);
		double dx = b[0] + 0.5 - eye[0], dy = b[1] + 0.5 - eye[1], dz = b[2] + 0.5 - eye[2];
		double dYaw = CameraController.wrap(-Math.toDegrees(Math.atan2(dx, dz)) - rot[0]);
		double dPitch = -Math.toDegrees(Math.atan2(dy, Math.hypot(dx, dz))) - rot[1];
		double dist = Math.hypot(dYaw, dPitch);
		if (dist < 1) {
			return;
		}
		double step = Math.min(dist, LEAN_FRACTION * here.widthDeg());
		for (int i = 0; i < 3; i++, step /= 2) { // shorter until it stays on this face
			float yaw = (float) (rot[0] + dYaw / dist * step);
			float pitch = (float) (rot[1] + dPitch / dist * step);
			McInput.BlockAim lands = McInput.blockAlong(yaw, pitch);
			if (lands != null && lands.block() == pick && lands.face() == on.face()) {
				camera.lean(yaw, pitch, Math.max(1, step / 2), System.nanoTime());
				if (TRACE) {
					LOGGER.info("lean-trace: leaning {} deg", String.format(java.util.Locale.ROOT, "%.1f", step));
				}
				return;
			}
		}
		if (TRACE) {
			LOGGER.info("lean-trace: no lean stays on the face");
		}
	}

	/**
	 * A step toward the block being mined when it's further off than the player mines from
	 * ({@link MiningReach}), if stepping that way is safe.
	 */
	private String approach(float[] rot) {
		long block = order.pick();
		if (block == MiningOrder.NONE) {
			McInput.BlockAim on = McInput.crosshair();
			if (on == null) {
				return null;
			}
			block = on.block();
		}
		double[] at = McInput.position(), eye = McInput.eye();
		if (at == null || eye == null) {
			return null;
		}
		String key = reach.key(at, eye, McInput.unpackBlock(block), rot[0]);
		if (key == null || !stepIsSafe(rot[0], key, true)) {
			return null;
		}
		reach.stepped();
		return key;
	}

	/**
	 * Stepping that way from where the player stands lands on solid ground with room to stand
	 * (feet and head clear), checked half a block and nearly a block ahead; with {@code drop}, a
	 * block lower is fine too (walking up to the work, a player steps down off what's left of it).
	 */
	private static boolean stepIsSafe(float yaw, String key) {
		return stepIsSafe(yaw, key, false);
	}

	private static boolean stepIsSafe(float yaw, String key, boolean drop) {
		double[] at = McInput.position();
		if (at == null) {
			return false;
		}
		double r = Math.toRadians(yaw);
		double fx = -Math.sin(r), fz = Math.cos(r); // forward; left is (fz, -fx)
		double[] d = switch (key) {
			case "key.forward" -> new double[]{fx, fz};
			case "key.back" -> new double[]{-fx, -fz};
			case "key.left" -> new double[]{fz, -fx};
			default -> new double[]{-fz, fx};
		};
		int y = (int) Math.floor(at[1] + 1e-3);
		for (double dist : new double[]{0.5, 0.9}) {
			int x = (int) Math.floor(at[0] + d[0] * dist), z = (int) Math.floor(at[2] + d[1] * dist);
			if (!McInput.passable(x, y, z) || !McInput.passable(x, y + 1, z)
				|| McInput.passable(x, y - 1, z) && !(drop && !McInput.passable(x, y - 2, z))) {
				return false;
			}
		}
		return true;
	}

	/**
	 * Which block a build or clear job breaks next: see {@link MiningOrder}. Out of those within
	 * the player's reach when there are any ({@link MiningReach}): the planner offers everything in
	 * the game's reach, and a player breaks what's in the way before what's beyond it.
	 */
	private int chooseBreak(long[] candidates) {
		double[] eye = McInput.eye();
		if (eye == null) {
			return order.choose(candidates);
		}
		return order.choose(candidates, c -> reach.near(eye, McInput.unpackBlock(c)));
	}

	@Override
	public void onGameEvent(long nanoTime, GameEvent event) {
		if (event.hasBlockBroken()) {
			meta.blockBroken();
			camera.blockBroken();
			lastBreak = tick;
			var pos = event.getBlockBroken().getPos();
			order.broke(pos.getX(), pos.getY(), pos.getZ());
		}
		if (event.hasBlockPlaced()) {
			var pos = event.getBlockPlaced().getPos();
			camera.blockPlaced(McInput.blockKey(pos.getX(), pos.getY(), pos.getZ()));
		}
	}

	/**
	 * The planner has held movement keys for {@link #BLOCKED_TICKS} without the player getting
	 * {@link #BLOCKED_BLOCKS} from where they were, or did within the last {@link #DIRECT_TICKS}.
	 */
	private boolean blocked(boolean moving) {
		double[] at = McInput.position();
		if (!moving || at == null) {
			blockedFrom = null;
			blockedTicks = 0;
		} else if (blockedFrom == null || Math.hypot(at[0] - blockedFrom[0], at[2] - blockedFrom[2]) + Math.abs(at[1] - blockedFrom[1]) >= BLOCKED_BLOCKS) {
			blockedFrom = at;
			blockedTicks = 0;
		} else if (++blockedTicks >= BLOCKED_TICKS) {
			directTicks = DIRECT_TICKS;
		}
		if (directTicks > 0) {
			directTicks--;
			return true;
		}
		return false;
	}

	/**
	 * While walking, faces a point the player's look-ahead distance along the path, offset by the
	 * player's wander off the way of travel, at their walking pitch; else the planner's direction.
	 */
	private void lookAlongPath(float[] rot, boolean moving) {
		double[] at = McInput.position();
		double[] point = at == null ? null : pointAhead(at, planner.pathAhead(PATH_NODES), walk.lookAheadBlocks());
		if (point != null) {
			double yaw = -Math.toDegrees(Math.atan2(point[0] - at[0], point[2] - at[2])) + walk.headingOffset();
			camera.setTarget((float) yaw, moving ? (float) walk.walkPitch() : rot[1], false);
		} else if (hasWalkTarget) {
			camera.setTarget(walkYaw, walkPitch, false);
		}
	}

	/** The first path node at least {@code ahead} blocks away horizontally, else the last one. */
	static double[] pointAhead(double[] at, double[][] path, double ahead) {
		for (double[] node : path) {
			if (Math.hypot(node[0] - at[0], node[2] - at[2]) >= ahead) {
				return node;
			}
		}
		return path.length > 0 && Math.hypot(path[path.length - 1][0] - at[0], path[path.length - 1][2] - at[2]) > 1.0
			? path[path.length - 1] : null;
	}

	/**
	 * The next OPEN_NODES path nodes run straight on one level, with two clear blocks over each
	 * (a sprint-jump's head clearance): room to sprint-jump.
	 */
	private boolean openAhead() {
		double[][] path = planner.pathAhead(OPEN_NODES);
		if (path.length < OPEN_NODES) {
			return false;
		}
		double dx = path[1][0] - path[0][0], dz = path[1][2] - path[0][2];
		for (int i = 0; i < path.length; i++) {
			double[] n = path[i];
			if (n[1] != path[0][1] || i > 0 && (n[0] - path[i - 1][0] != dx || n[2] - path[i - 1][2] != dz)) {
				return false;
			}
			int x = (int) Math.floor(n[0]), y = (int) Math.round(n[1]), z = (int) Math.floor(n[2]);
			if (!McInput.passable(x, y + 2, z) || !McInput.passable(x, y + 3, z) || McInput.passable(x, y - 1, z)) {
				return false;
			}
		}
		return true;
	}

	private interface Loader<T> {
		T load() throws IOException;
	}

	private static <T> T load(Loader<T> loader, T fallback, String name) {
		try {
			return loader.load();
		} catch (IOException | RuntimeException e) {
			LOGGER.warn("Couldn't read the {} model; using defaults", name, e);
			return fallback;
		}
	}
}
