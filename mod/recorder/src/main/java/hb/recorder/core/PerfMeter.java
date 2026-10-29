package hb.recorder.core;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Main-thread cost of the recorder per client tick: every hook's time between two tick boundaries
 * is summed and bucketed at 1 µs resolution. Client thread only.
 */
public final class PerfMeter {
	private static final int MAX_US = 5000;

	private final long[] buckets = new long[MAX_US + 1];
	private long accumNs;
	private long ticks;
	private long totalNs;
	private long maxNs;

	public long start() {
		return System.nanoTime();
	}

	public void stop(long startNs) {
		accumNs += System.nanoTime() - startNs;
	}

	/** Adds time measured elsewhere (e.g. the HUD draw) to the current tick. */
	public void add(long ns) {
		accumNs += ns;
	}

	/** Closes the current tick's measurement. */
	public void endTick() {
		ticks++;
		totalNs += accumNs;
		maxNs = Math.max(maxNs, accumNs);
		buckets[(int) Math.min(accumNs / 1000, MAX_US)]++;
		accumNs = 0;
	}

	public void reset() {
		Arrays.fill(buckets, 0);
		accumNs = 0;
		ticks = 0;
		totalNs = 0;
		maxNs = 0;
	}

	public long ticks() {
		return ticks;
	}

	public double meanUs() {
		return ticks == 0 ? 0 : totalNs / 1000.0 / ticks;
	}

	/** Upper bound of the bucket holding quantile {@code q} (0-1), in µs. */
	public double quantileUs(double q) {
		if (ticks == 0) {
			return 0;
		}
		long rank = (long) Math.ceil(q * ticks);
		long seen = 0;
		for (int us = 0; us <= MAX_US; us++) {
			seen += buckets[us];
			if (seen >= rank) {
				return us + 1;
			}
		}
		return MAX_US;
	}

	public double maxUs() {
		return maxNs / 1000.0;
	}

	public Map<String, Object> toMap() {
		Map<String, Object> m = new LinkedHashMap<>();
		m.put("ticks", ticks);
		m.put("mean_us", round(meanUs()));
		m.put("p50_us", quantileUs(0.50));
		m.put("p99_us", quantileUs(0.99));
		m.put("p999_us", quantileUs(0.999));
		m.put("max_us", round(maxUs()));
		return m;
	}

	private static double round(double v) {
		return Math.round(v * 100) / 100.0;
	}
}
