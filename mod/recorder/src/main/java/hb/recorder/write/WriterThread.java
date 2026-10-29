package hb.recorder.write;

import hb.common.io.SessionWriter;
import hb.proto.v1.Envelope;
import hb.proto.v1.SessionHeader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.locks.LockSupport;
import java.util.function.LongSupplier;

/**
 * Background thread that drains the capture ring: holds records for 15 s, serializes them,
 * compresses them into rolling chunks and writes stats.json when a session closes. Everything that
 * costs real time happens here, never on the client thread.
 *
 * <p>The ring carries {@link Envelope}s and the commands below, in capture order, so trims,
 * discards and session boundaries apply to exactly the records offered before them.
 */
public final class WriterThread {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-recorder");
	private static final long IDLE_PARK_NS = 2_000_000;

	public sealed interface Command {
	}

	/** Starts a session; {@code startNanoTime} is the System.nanoTime() that t_ns counts from. */
	public record Open(Path sessionsRoot, SessionHeader header, long startNanoTime, boolean mirror) implements Command {
	}

	public record Trim(long afterNs) implements Command {
	}

	public record Discard(long fromNs, long toNs, Envelope tombstone) implements Command {
	}

	/** Flushes the holdback and closes the session; {@code stats} is merged into stats.json. */
	public record Close(Map<String, Object> stats) implements Command {
	}

	private final SpscRing<Object> ring;
	private final LongSupplier nanoClock;
	private final Thread thread;
	private volatile boolean stopRequested;

	private SessionWriter session;
	private CommitStage stage;
	private long startNanoTime;
	private volatile long bytesWritten;
	private volatile long recordsWritten;
	private volatile String lastError;
	private volatile Path lastSessionDir;

	public WriterThread(SpscRing<Object> ring) {
		this(ring, System::nanoTime);
	}

	public WriterThread(SpscRing<Object> ring, LongSupplier nanoClock) {
		this.ring = ring;
		this.nanoClock = nanoClock;
		this.thread = new Thread(this::run, "hb-recorder-writer");
		this.thread.setDaemon(true);
		this.thread.setPriority(Thread.NORM_PRIORITY - 1);
	}

	public void start() {
		thread.start();
	}

	/** Drains what was offered so far, closes any open session and stops. */
	public void shutdown(long timeoutMs) {
		stopRequested = true;
		LockSupport.unpark(thread);
		try {
			thread.join(timeoutMs);
		} catch (InterruptedException e) {
			Thread.currentThread().interrupt();
		}
	}

	public long bytesWritten() {
		return bytesWritten;
	}

	/** Records of the current (or last) session that have left the holdback. */
	public long recordsWritten() {
		return recordsWritten;
	}

	public String lastError() {
		return lastError;
	}

	public Path lastSessionDir() {
		return lastSessionDir;
	}

	private void run() {
		while (true) {
			Object item = ring.poll();
			if (item == null) {
				if (stopRequested) {
					break;
				}
				commitAged();
				LockSupport.parkNanos(IDLE_PARK_NS);
				continue;
			}
			try {
				handle(item);
			} catch (IOException | RuntimeException e) {
				fail(e);
			}
		}
		if (session != null) {
			try {
				// Only reached when the game exits without the client-stopping event, i.e. a crash.
				handle(new Close(Map.of("closed_by", "exit without client stop")));
			} catch (IOException | RuntimeException e) {
				fail(e);
			}
		}
	}

	void handle(Object item) throws IOException {
		switch (item) {
			case Envelope envelope -> {
				if (stage != null) {
					stage.add(envelope);
				}
			}
			case Open open -> {
				if (session != null) {
					handle(new Close(Map.of("closed_by", "next session")));
				}
				session = SessionWriter.open(open.sessionsRoot(), open.header(), null, open.mirror());
				SessionWriter writer = session;
				stage = new CommitStage(writer::write);
				startNanoTime = open.startNanoTime();
				lastSessionDir = writer.dir();
			}
			case Trim trim -> {
				if (stage != null) {
					stage.trimAfter(trim.afterNs());
				}
			}
			case Discard discard -> {
				if (stage != null) {
					stage.discard(discard.fromNs(), discard.toNs(), discard.tombstone());
				}
			}
			case Close close -> {
				if (session != null) {
					stage.flush();
					Map<String, Object> stats = new LinkedHashMap<>(close.stats());
					stats.put("holdback_trimmed_records", stage.trimmed());
					stats.put("discarded_records", stage.discarded());
					stats.put("trim_missed", stage.trimMissed());
					session.close(stats);
					bytesWritten = session.bytesWritten();
					recordsWritten = session.records();
					session = null;
					stage = null;
				}
			}
			default -> throw new IllegalArgumentException("unknown ring item " + item.getClass());
		}
		if (session != null) {
			commitAged();
		}
	}

	private void commitAged() {
		if (stage == null) {
			return;
		}
		try {
			stage.commitAged(nanoClock.getAsLong() - startNanoTime);
			bytesWritten = session.bytesWritten();
			recordsWritten = session.records();
		} catch (IOException | RuntimeException e) {
			fail(e);
		}
	}

	private void fail(Exception e) {
		lastError = e.toString();
		LOGGER.error("hb-recorder writer failed; closing the session", e);
		if (stage != null) {
			stage.clear();
		}
		if (session != null) {
			try {
				session.close(Map.of("closed_by", "error", "error", e.toString()));
			} catch (IOException | RuntimeException ignored) {
				// already reporting the first failure
			}
		}
		session = null;
		stage = null;
	}
}
