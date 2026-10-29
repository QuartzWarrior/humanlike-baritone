package hb.common.automation;

import java.util.List;
import java.util.Objects;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.BooleanSupplier;

/**
 * The one place that knows whether something other than the human is driving the player.
 *
 * <p>hb-executor holds a {@link Claim} for as long as it sends inputs; probes report automation we
 * don't control (Baritone run by hand, other bots). hb-recorder pauses and refuses to buffer records
 * while {@link #isActive()} is true, and {@code SessionWriter} will only open a bot session under a
 * live claim. Together these keep bot input out of human data in code, not only by convention.
 *
 * <p>Claims are thread-safe. Probes are evaluated on the client thread by {@link #poll()} once per
 * tick and the result is cached, so {@link #isActive()} is cheap enough to call from input hooks.
 */
public final class HbAutomation {
	private static final List<Claim> CLAIMS = new CopyOnWriteArrayList<>();
	private static final List<Probe> PROBES = new CopyOnWriteArrayList<>();
	private static volatile String probedOwner;

	private HbAutomation() {
	}

	/** Marks automation as active until the returned claim is closed. */
	public static Claim claim(String owner) {
		Claim claim = new Claim(Objects.requireNonNull(owner));
		CLAIMS.add(claim);
		return claim;
	}

	/** Registers a check for automation that doesn't take a claim, such as Baritone run by hand. */
	public static void addProbe(String owner, BooleanSupplier active) {
		PROBES.add(new Probe(owner, active));
	}

	/** Re-evaluates the probes. Call once per client tick, before reading {@link #isActive()}. */
	public static void poll() {
		String owner = null;
		for (Probe probe : PROBES) {
			boolean active;
			try {
				active = probe.active.getAsBoolean();
			} catch (RuntimeException e) {
				active = false; // a broken probe must not block recording forever
			}
			if (active) {
				owner = probe.owner;
				break;
			}
		}
		probedOwner = owner;
	}

	public static boolean isActive() {
		return !CLAIMS.isEmpty() || probedOwner != null;
	}

	/** Who is driving: the oldest claim's owner, else the first active probe, else null. */
	public static String activeOwner() {
		for (Claim claim : CLAIMS) {
			return claim.owner;
		}
		return probedOwner;
	}

	/** Test hook: drops all claims and probes. */
	public static void reset() {
		CLAIMS.clear();
		PROBES.clear();
		probedOwner = null;
	}

	public static final class Claim implements AutoCloseable {
		private final String owner;
		private volatile boolean open = true;

		private Claim(String owner) {
			this.owner = owner;
		}

		public String owner() {
			return owner;
		}

		public boolean isOpen() {
			return open;
		}

		@Override
		public void close() {
			open = false;
			CLAIMS.remove(this);
		}
	}

	private record Probe(String owner, BooleanSupplier active) {
	}
}
