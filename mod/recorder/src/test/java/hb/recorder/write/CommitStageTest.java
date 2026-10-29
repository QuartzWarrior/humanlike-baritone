package hb.recorder.write;

import hb.proto.v1.Discard;
import hb.proto.v1.Envelope;
import hb.proto.v1.MouseMove;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CommitStageTest {
	private static final long S = 1_000_000_000L;
	private final List<Envelope> disk = new ArrayList<>();
	private final CommitStage stage = new CommitStage(disk::add);

	private static Envelope at(double seconds) {
		return Envelope.newBuilder().setTNs((long) (seconds * S)).setMouse(MouseMove.newBuilder().setDx(seconds)).build();
	}

	private List<Double> diskSeconds() {
		return disk.stream().map(e -> e.getTNs() / (double) S).toList();
	}

	@Test
	void recordsReachDiskOnlyAfterFifteenSeconds() throws Exception {
		for (int s = 0; s <= 20; s++) {
			stage.add(at(s));
		}
		stage.commitAged(20 * S);
		assertEquals(List.of(0.0, 1.0, 2.0, 3.0, 4.0, 5.0), diskSeconds());
		assertEquals(15, stage.heldCount());
	}

	@Test
	void idleTrimDropsTheHeldTail() throws Exception {
		for (int s = 0; s <= 10; s++) {
			stage.add(at(s));
		}
		stage.trimAfter(3 * S); // last input at 0 s, pause at 10 s: keep 0-3 s
		stage.flush();
		assertEquals(List.of(0.0, 1.0, 2.0, 3.0), diskSeconds());
		assertEquals(7, stage.trimmed());
		assertEquals(0, stage.trimMissed());
	}

	@Test
	void discardOfHeldRecordsLeavesNoTrace() throws Exception {
		for (int s = 100; s <= 110; s++) {
			stage.add(at(s));
		}
		stage.discard(50 * S, 110 * S, tombstone(50, 110));
		stage.flush();
		assertTrue(disk.isEmpty(), "everything discarded was still in memory, so no tombstone is needed");
		assertEquals(11, stage.discarded());
	}

	@Test
	void discardReachingIntoDiskWritesATombstone() throws Exception {
		for (int s = 0; s <= 70; s++) {
			stage.add(at(s));
		}
		stage.commitAged(70 * S); // 0-55 s on disk
		stage.discard(10 * S, 70 * S, tombstone(10, 70));
		stage.flush();
		Envelope last = disk.get(disk.size() - 1);
		assertTrue(last.hasDiscard());
		assertEquals(10 * S, last.getDiscard().getFromTNs());
		assertEquals(56, disk.size() - 1, "0-55 s were already written; 56-70 s never reach disk");
	}

	private static Envelope tombstone(long from, long to) {
		return Envelope.newBuilder().setTNs(to * S).setDiscard(Discard.newBuilder().setFromTNs(from * S).setToTNs(to * S)).build();
	}
}
