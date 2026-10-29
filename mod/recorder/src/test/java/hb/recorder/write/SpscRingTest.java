package hb.recorder.write;

import org.junit.jupiter.api.Test;

import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SpscRingTest {
	@Test
	void fifoAndBounded() {
		SpscRing<Integer> ring = new SpscRing<>(4);
		for (int i = 0; i < 4; i++) {
			assertTrue(ring.offer(i));
		}
		assertFalse(ring.offer(4), "full ring rejects instead of blocking");
		assertEquals(0, ring.poll());
		assertTrue(ring.offer(4));
		for (int i = 1; i <= 4; i++) {
			assertEquals(i, ring.poll());
		}
		assertNull(ring.poll());
	}

	@Test
	void oneProducerOneConsumerKeepOrder() throws Exception {
		SpscRing<Long> ring = new SpscRing<>(1024);
		long n = 2_000_000;
		AtomicReference<String> failure = new AtomicReference<>();
		Thread consumer = new Thread(() -> {
			long expected = 0;
			while (expected < n) {
				Long v = ring.poll();
				if (v == null) {
					Thread.onSpinWait();
					continue;
				}
				if (v != expected) {
					failure.set("expected " + expected + " got " + v);
					return;
				}
				expected++;
			}
		});
		consumer.start();
		for (long i = 0; i < n; ) {
			if (ring.offer(i)) {
				i++;
			} else {
				Thread.onSpinWait();
			}
		}
		consumer.join(30_000);
		assertNull(failure.get(), failure.get());
		assertEquals(0, ring.size());
	}
}
