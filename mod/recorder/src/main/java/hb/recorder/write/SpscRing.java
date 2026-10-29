package hb.recorder.write;

import java.util.concurrent.atomic.AtomicLong;

/**
 * Bounded lock-free single-producer/single-consumer queue. The client thread offers, the writer
 * thread polls; neither ever blocks. A full ring makes {@link #offer} return false instead of
 * stalling the game.
 */
public final class SpscRing<T> {
	private final Object[] slots;
	private final int mask;
	private final AtomicLong head = new AtomicLong(); // next index to poll, written by the consumer
	private final AtomicLong tail = new AtomicLong(); // next index to fill, written by the producer
	private long headCache; // producer's last view of head

	public SpscRing(int capacityPowerOfTwo) {
		if (Integer.bitCount(capacityPowerOfTwo) != 1) {
			throw new IllegalArgumentException("capacity must be a power of two");
		}
		slots = new Object[capacityPowerOfTwo];
		mask = capacityPowerOfTwo - 1;
	}

	/** Producer only. */
	public boolean offer(T item) {
		long t = tail.getPlain();
		if (t - headCache >= slots.length) {
			headCache = head.getAcquire();
			if (t - headCache >= slots.length) {
				return false;
			}
		}
		slots[(int) (t & mask)] = item;
		tail.setRelease(t + 1);
		return true;
	}

	/** Consumer only. Returns null when empty. */
	@SuppressWarnings("unchecked")
	public T poll() {
		long h = head.getPlain();
		if (h >= tail.getAcquire()) {
			return null;
		}
		int i = (int) (h & mask);
		T item = (T) slots[i];
		slots[i] = null;
		head.setRelease(h + 1);
		return item;
	}

	public int size() {
		return (int) (tail.getAcquire() - head.getAcquire());
	}

	public int capacity() {
		return slots.length;
	}
}
