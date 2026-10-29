package hb.recorder.write;

import hb.proto.v1.Envelope;

import java.io.IOException;
import java.util.ArrayDeque;
import java.util.Iterator;

/**
 * The in-memory holdback: records wait here for {@link #HOLDBACK_NS} before reaching disk, so an
 * idle tail can still be trimmed and an F9 discard usually never touches disk. Writer thread only.
 */
public final class CommitStage {
	public static final long HOLDBACK_NS = 15_000_000_000L;

	public interface Sink {
		void write(Envelope envelope) throws IOException;
	}

	private final ArrayDeque<Envelope> held = new ArrayDeque<>();
	private final long holdbackNs;
	private final Sink sink;
	private long lastCommittedTns = -1;
	private long trimmed;
	private long discarded;
	private long trimMissed;

	public CommitStage(Sink sink) {
		this(sink, HOLDBACK_NS);
	}

	public CommitStage(Sink sink, long holdbackNs) {
		this.sink = sink;
		this.holdbackNs = holdbackNs;
	}

	public void add(Envelope envelope) {
		held.addLast(envelope);
	}

	/** Drops held records newer than {@code afterNs} (the idle tail). */
	public void trimAfter(long afterNs) {
		if (lastCommittedTns > afterNs) {
			trimMissed++; // already on disk; only possible if the writer fell behind by over 5 s
		}
		for (Iterator<Envelope> it = held.descendingIterator(); it.hasNext(); ) {
			if (it.next().getTNs() > afterNs) {
				it.remove();
				trimmed++;
			}
		}
	}

	/**
	 * Drops held records in [fromNs, toNs]. If some of that span already reached disk, writes the
	 * tombstone so the pipeline drops it too.
	 */
	public void discard(long fromNs, long toNs, Envelope tombstone) throws IOException {
		for (Iterator<Envelope> it = held.iterator(); it.hasNext(); ) {
			long t = it.next().getTNs();
			if (t >= fromNs && t <= toNs) {
				it.remove();
				discarded++;
			}
		}
		while (!held.isEmpty() && held.peekFirst().getTNs() < fromNs) {
			commit(held.pollFirst());
		}
		if (lastCommittedTns >= fromNs) {
			commit(tombstone);
		}
	}

	/** Commits records older than the holdback, measured against {@code nowNs} (session time). */
	public void commitAged(long nowNs) throws IOException {
		while (!held.isEmpty() && held.peekFirst().getTNs() <= nowNs - holdbackNs) {
			commit(held.pollFirst());
		}
	}

	public void flush() throws IOException {
		while (!held.isEmpty()) {
			commit(held.pollFirst());
		}
	}

	/** Forgets held records without writing them (session closed after a write error). */
	public void clear() {
		held.clear();
	}

	private void commit(Envelope envelope) throws IOException {
		sink.write(envelope);
		lastCommittedTns = Math.max(lastCommittedTns, envelope.getTNs());
	}

	public int heldCount() {
		return held.size();
	}

	public long trimmed() {
		return trimmed;
	}

	public long discarded() {
		return discarded;
	}

	public long trimMissed() {
		return trimMissed;
	}
}
