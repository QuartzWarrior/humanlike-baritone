package hb.executor.decide;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Parity with the pipeline's candidate features (mining_order._job) on synthetic jobs from the
 * pipeline test's generator: a deepslate pocket with other blocks sprinkled in (as full block
 * states), a few never-seen positions, the stand jittered between breaks. Each job is replayed
 * break by break through {@link BreakFeatures.Job}, so the history features (along_sweep,
 * skipped_before) are checked too.
 */
class BreakFeaturesTest {
	/** The fixture's volume: block states on a box, null where the recorder never saw. */
	private record Box(int[] origin, int[] size, String[] palette, int[] cells) implements BreakFeatures.World {
		static Box of(JsonObject v) {
			JsonArray p = v.getAsJsonArray("palette");
			String[] palette = new String[p.size()];
			for (int i = 0; i < palette.length; i++) {
				palette[i] = p.get(i).getAsString();
			}
			JsonArray c = v.getAsJsonArray("cells");
			int[] cells = new int[c.size()];
			for (int i = 0; i < cells.length; i++) {
				cells[i] = c.get(i).getAsInt();
			}
			return new Box(ints(v.getAsJsonArray("origin")), ints(v.getAsJsonArray("size")), palette, cells);
		}

		@Override
		public String block(int x, int y, int z) {
			int i = x - origin[0];
			int j = y - origin[1];
			int k = z - origin[2];
			if (i < 0 || j < 0 || k < 0 || i >= size[0] || j >= size[1] || k >= size[2]) {
				return null;
			}
			int cell = cells[(i * size[1] + j) * size[2] + k];
			return cell < 0 ? null : palette[cell];
		}
	}

	private static int[] ints(JsonArray a) {
		int[] out = new int[a.size()];
		for (int i = 0; i < out.length; i++) {
			out[i] = a.get(i).getAsInt();
		}
		return out;
	}

	private static double[] doubles(JsonArray a) {
		double[] out = new double[a.size()];
		for (int i = 0; i < out.length; i++) {
			out[i] = a.get(i).getAsDouble();
		}
		return out;
	}

	@Test
	void namesAreTheModelsFeatures() throws IOException {
		JsonObject f = MiningOrderModelTest.json("break_features.json");
		JsonArray names = f.getAsJsonArray("features");
		assertEquals(BreakFeatures.COUNT, names.size());
		for (int i = 0; i < names.size(); i++) {
			assertEquals(names.get(i).getAsString(), BreakFeatures.NAMES.get(i));
		}
		assertEquals(f.get("eye_height").getAsDouble(), BreakFeatures.EYE_HEIGHT);
		assertEquals(f.get("reach").getAsDouble(), BreakFeatures.REACH);
	}

	@Test
	void blockCategoriesMatchThePipelines() throws IOException {
		JsonArray table = MiningOrderModelTest.json("break_features.json").getAsJsonArray("blocks");
		assertTrue(table.size() > 150);
		for (JsonElement e : table) {
			JsonArray r = e.getAsJsonArray();
			String id = BreakFeatures.blockId(r.get(0).getAsString());
			assertEquals(r.get(1).getAsBoolean(), BreakFeatures.passable(id), "passable " + r.get(0));
			assertEquals(r.get(2).getAsBoolean(), BreakFeatures.falls(id), "falls " + r.get(0));
			assertEquals(r.get(3).getAsInt(), BreakFeatures.blockClass(id), "block_class " + r.get(0));
		}
	}

	@Test
	void featuresMatchThePipelineBreakByBreak() throws IOException {
		JsonObject f = MiningOrderModelTest.json("break_features.json");
		int compared = 0;
		int j = 0;
		for (JsonElement je : f.getAsJsonArray("jobs")) {
			JsonObject job = je.getAsJsonObject();
			Box world = Box.of(job.getAsJsonObject("volume"));
			JsonArray breaks = job.getAsJsonArray("breaks");
			JsonObject want = job.getAsJsonObject("candidates");
			BreakFeatures.Job state = new BreakFeatures.Job();
			for (int k = 0; k < breaks.size(); k++) {
				int[] pos = ints(breaks.get(k).getAsJsonObject().getAsJsonArray("pos"));
				List<BreakFeatures.Candidate> cands = null;
				if (k > 0) {
					// Chosen from where the player stood and looked at the previous break.
					JsonObject prev = breaks.get(k - 1).getAsJsonObject();
					double[] stand = doubles(prev.getAsJsonArray("stand"));
					cands = BreakFeatures.candidates(world, state, stand[0], stand[1], stand[2],
						prev.get("yaw").getAsDouble(), prev.get("pitch").getAsDouble());
					if (want.has(String.valueOf(k))) {
						assertSameCandidates(want.getAsJsonArray(String.valueOf(k)), cands, "job " + j + " step " + k);
						compared++;
					}
				}
				state.broke(pos[0], pos[1], pos[2], cands);
			}
			j++;
		}
		assertEquals(12, compared);
	}

	private static void assertSameCandidates(JsonArray want, List<BreakFeatures.Candidate> got, String where) {
		assertEquals(want.size(), got.size(), where + " candidate count");
		for (int i = 0; i < want.size(); i++) {
			JsonArray w = want.get(i).getAsJsonArray();
			BreakFeatures.Candidate c = got.get(i);
			String at = where + " (" + c.x() + ", " + c.y() + ", " + c.z() + ")";
			assertEquals(w.get(0).getAsInt(), c.x(), at);
			assertEquals(w.get(1).getAsInt(), c.y(), at);
			assertEquals(w.get(2).getAsInt(), c.z(), at);
			for (int k = 0; k < BreakFeatures.COUNT; k++) {
				JsonElement v = w.get(3 + k);
				double expected = v.isJsonNull() ? Double.NaN : v.getAsDouble();
				// assertEquals(double, double, delta) treats NaN == NaN as equal
				assertEquals(expected, c.features()[k], 1e-9, at + " " + BreakFeatures.NAMES.get(k));
			}
		}
	}

	/** A small world for hand-made checks: a map of states, null elsewhere. */
	private static BreakFeatures.World world(Map<List<Integer>, String> blocks) {
		return (x, y, z) -> blocks.get(List.of(x, y, z));
	}

	@Test
	void conventionsAreMinecrafts() {
		// Standing on stone at y 64, a pocket of air around the eye, a stone wall at z 3.
		Map<List<Integer>, String> blocks = new HashMap<>();
		for (int x = -3; x <= 3; x++) {
			for (int y = 63; y <= 67; y++) {
				for (int z = -3; z <= 3; z++) {
					boolean wall = y == 63 || Math.abs(x) == 3 || Math.abs(z) == 3 || y == 67;
					blocks.put(List.of(x, y, z), wall ? "minecraft:stone" : "minecraft:air");
				}
			}
		}
		BreakFeatures.World w = world(blocks);
		BreakFeatures.Job job = new BreakFeatures.Job();
		assertThrows(IllegalStateException.class, () -> BreakFeatures.candidates(w, job, 0.5, 64, 0.5, 0, 0));
		job.broke(0, 65, 3, null); // a hole in the wall ahead at eye level: now air
		double eyeY = 64 + BreakFeatures.EYE_HEIGHT;

		// Yaw 0 looks along +z: the wall block at z 3, eye level, is straight ahead.
		double[] ahead = BreakFeatures.features(w, job, 0.5, 64, 0.5, 0, 0, 0, 66, 3);
		assertEquals(66.5 - eyeY, ahead[4], 1e-12);
		assertTrue(ahead[1] < 20 && Math.abs(ahead[2]) < 1e-9, "ahead: angle " + ahead[1]);
		assertTrue(ahead[3] < 0, "above the eye: look up, negative pitch");
		assertEquals(1.0, ahead[11]); // same column as the previous break, one up
		// Yaw 90 looks along -x: the wall at x -3 is ahead; the one at x 3 is behind.
		assertTrue(BreakFeatures.features(w, job, 0.5, 64, 0.5, 90, 0, -3, 65, 0)[1] < 20);
		assertTrue(BreakFeatures.features(w, job, 0.5, 64, 0.5, 90, 0, 3, 65, 0)[1] > 160);
		// Facing +z, a block to the -x side is to the right: dyaw positive.
		assertTrue(BreakFeatures.features(w, job, 0.5, 64, 0.5, 0, 0, -3, 65, 1)[2] > 0);
		// Pitch +90 looks straight down, at the block under the feet.
		double[] below = BreakFeatures.features(w, job, 0.5, 64, 0.5, 0, 90, 0, 63, 0);
		assertTrue(below[1] < 1e-6, "angle " + below[1]);
		assertEquals(1.0, below[14]);
		assertEquals(1.0, below[15]);
		assertEquals(BreakFeatures.STONE, below[16]);
		// The broken block and the air aren't candidates; a block outside the known volume isn't either.
		assertNull(BreakFeatures.features(w, job, 0.5, 64, 0.5, 0, 0, 0, 65, 3));
		assertNull(BreakFeatures.features(w, job, 0.5, 64, 0.5, 0, 0, 0, 65, 2));
		assertNull(BreakFeatures.features(w, job, 0.5, 64, 0.5, 0, 0, 0, 62, 0));
		// Every candidate is solid, open and in reach.
		for (BreakFeatures.Candidate c : BreakFeatures.candidates(w, job, 0.5, 64, 0.5, 0, 0)) {
			assertTrue(c.features()[0] <= BreakFeatures.REACH && c.features()[13] >= 1);
		}
	}

	@Test
	void theJobRemembersSkippedBlocksAndTheSweep() {
		Map<List<Integer>, String> blocks = new HashMap<>();
		for (int x = -4; x <= 4; x++) {
			for (int y = 63; y <= 66; y++) {
				for (int z = -4; z <= 4; z++) {
					blocks.put(List.of(x, y, z), y >= 64 && y <= 65 && x == 0 && z == 0 ? "air" : "deepslate");
				}
			}
		}
		BreakFeatures.World w = world(blocks);
		BreakFeatures.Job job = new BreakFeatures.Job();
		job.broke(1, 64, 0, null); // first break: +x of the stand
		List<BreakFeatures.Candidate> step = BreakFeatures.candidates(w, job, 0.5, 64, 0.5, -90, 0);
		job.broke(2, 64, 0, step); // went on along +x, passing over (1, 65, 0) and the others next to (1, 64, 0)
		double[] passed = BreakFeatures.features(w, job, 0.5, 64, 0.5, -90, 0, 1, 65, 0);
		assertEquals(1.0, passed[19], "skipped_before");
		double[] onward = BreakFeatures.features(w, job, 0.5, 64, 0.5, -90, 0, 3, 64, 0);
		double[] back = BreakFeatures.features(w, job, 0.5, 64, 0.5, -90, 0, -1, 64, 0);
		assertEquals(1.0, onward[7], "along_sweep continues +x");
		assertEquals(-1.0, back[7], "along_sweep reverses");
		assertEquals(0.0, back[19]);
	}
}
