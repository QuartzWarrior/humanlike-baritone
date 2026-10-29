package hb.executor.decide;

import org.junit.jupiter.api.Test;

import java.util.HashSet;
import java.util.Random;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MiningOrderTest {
	/** A 5x3x5 room of stone at x, z 0..4, y 64..66, everything else air; packed as x*10000+y*100+z. */
	private static final class Room implements MiningOrder.Game {
		final Set<Long> gone = new HashSet<>();
		double[] stand = {2.5, 63, -1.5};
		float[] look = {0f, 20f};

		@Override
		public String block(int x, int y, int z) {
			boolean stone = x >= 0 && x <= 4 && y >= 64 && y <= 66 && z >= 0 && z <= 4 && !gone.contains(key(x, y, z));
			return stone ? "minecraft:stone" : "minecraft:air";
		}

		@Override
		public double[] stand() {
			return stand;
		}

		@Override
		public float[] look() {
			return look;
		}

		@Override
		public long key(int x, int y, int z) {
			return x * 10000L + y * 100L + z;
		}

		@Override
		public int[] unpack(long key) {
			return new int[]{(int) (key / 10000), (int) (key / 100 % 100), (int) (key % 100)};
		}
	}

	private static long[] frontFace(Room room) {
		long[] out = new long[15];
		int i = 0;
		for (int x = 0; x <= 4; x++) {
			for (int y = 64; y <= 66; y++) {
				out[i++] = room.key(x, y, 0);
			}
		}
		return out;
	}

	@Test
	void withoutAModelThePlannersPickIsKeptUntilItsGone() {
		Room room = new Room();
		MiningOrder order = new MiningOrder(room, new Random(1));
		order.start(null);
		long[] offered = frontFace(room);
		assertEquals(0, order.choose(offered));
		long[] reordered = offered.clone();
		reordered[0] = offered[3];
		reordered[3] = offered[0];
		assertEquals(3, order.choose(reordered), "keeps going for the block it started on");
		long[] without = java.util.Arrays.copyOfRange(offered, 1, offered.length);
		assertEquals(0, order.choose(without), "then the planner's next pick");
	}

	@Test
	void picksAmongThePreferredBlocksWhenThereAreAny() {
		Room room = new Room();
		MiningOrder order = new MiningOrder(room, new Random(4));
		order.start(null);
		long[] offered = frontFace(room);
		long wanted = offered[7];
		assertEquals(7, order.choose(offered, c -> c == wanted), "the only preferred one, at its index among all");
		assertEquals(7, order.choose(offered, c -> c == wanted), "and kept");
		MiningOrder none = new MiningOrder(room, new Random(5));
		none.start(null);
		assertEquals(0, none.choose(offered, c -> false), "none preferred: as without a preference");
	}

	@Test
	void afterABreakTheRankerPicksAmongThePlannersBlocks() {
		MiningOrderModel model = MiningOrderModel.load(MiningOrderModelTest.resource("mining_order_ranker.json"));
		assertNotNull(model);
		Room room = new Room();
		MiningOrder order = new MiningOrder(room, new Random(2));
		order.start(model);
		long[] offered = frontFace(room);
		int first = order.choose(offered); // the job's first block: the planner's
		assertEquals(0, first);
		room.gone.add(offered[0]);
		order.broke(0, 64, 0);
		long[] left = java.util.Arrays.copyOfRange(offered, 1, offered.length);
		Set<Integer> picks = new HashSet<>();
		for (int seed = 0; seed < 40; seed++) {
			MiningOrder again = new MiningOrder(room, new Random(seed));
			again.start(model);
			again.choose(offered);
			again.broke(0, 64, 0);
			int pick = again.choose(left);
			assertEquals(pick, again.choose(left), "kept once picked");
			picks.add(pick);
		}
		// Drawn from the ranker's softmax, not always the planner's first.
		assertTrue(picks.size() > 1 || !picks.contains(0), "always the planner's pick: " + picks);
	}

	@Test
	void theBlockDrawnDuringABreakIsTheOneTakenAfterIt() {
		MiningOrderModel model = MiningOrderModel.load(MiningOrderModelTest.resource("mining_order_ranker.json"));
		Room room = new Room();
		MiningOrder order = new MiningOrder(room, new Random(4));
		order.start(model);
		long[] offered = frontFace(room);
		order.choose(offered);
		room.gone.add(offered[0]);
		order.broke(0, 64, 0);
		long[] left = java.util.Arrays.copyOfRange(offered, 1, offered.length);
		int current = order.choose(left);
		long next = order.predictNext(left[current]); // drawn while the current block breaks
		assertTrue(next != MiningOrder.NONE);
		long[] after = java.util.Arrays.stream(left).filter(k -> k != left[current]).toArray();
		int i = java.util.stream.IntStream.range(0, after.length).filter(k -> after[k] == next).findFirst().orElse(-1);
		if (i >= 0) { // offered by the planner: taken
			assertEquals(i, order.choose(after));
		}
		assertEquals(order.pick(), after[order.choose(after)], "and kept");
	}

	@Test
	void breaksOutsideAJobAreIgnored() {
		Room room = new Room();
		MiningOrder order = new MiningOrder(room, new Random(3));
		order.start(MiningOrderModel.load(MiningOrderModelTest.resource("mining_order_ranker.json")));
		order.broke(0, 64, 0); // walking through, before any build or clear job offered a block
		assertEquals(0, order.choose(frontFace(room)), "still the job's first block, the planner's");
	}
}
