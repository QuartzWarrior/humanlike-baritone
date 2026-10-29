package hb.executor.decide;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Random;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Parity with the pipeline's score() and softmax on fixtures exported from synthetic jobs (the
 * pipeline test's generator; see export_mining_order_fixtures.py in the session scratchpad):
 * a small lambdarank model fitted by mining_order.fit, a regression model with "NaN" and "Zero"
 * missing types, and the generic prior.
 */
class MiningOrderModelTest {
	static Path resource(String name) {
		try {
			return Path.of(MiningOrderModelTest.class.getResource("/decide/" + name).toURI());
		} catch (URISyntaxException e) {
			throw new IllegalStateException(e);
		}
	}

	static JsonObject json(String name) throws IOException {
		return new Gson().fromJson(Files.readString(resource(name), StandardCharsets.UTF_8), JsonObject.class);
	}

	/** Feature rows; null stands for NaN (JSON has no NaN). */
	static double[][] rows(JsonArray a) {
		double[][] out = new double[a.size()][];
		for (int i = 0; i < out.length; i++) {
			JsonArray r = a.get(i).getAsJsonArray();
			out[i] = new double[r.size()];
			for (int k = 0; k < r.size(); k++) {
				JsonElement v = r.get(k);
				out[i][k] = v.isJsonNull() ? Double.NaN : v.getAsDouble();
			}
		}
		return out;
	}

	private static MiningOrderModel load(String name) {
		List<String> why = new ArrayList<>();
		MiningOrderModel m = MiningOrderModel.load(resource(name), why::add);
		assertNotNull(m, String.valueOf(why));
		return m;
	}

	private static void assertScoresMatch(String fixture) throws IOException {
		JsonObject f = json(fixture);
		MiningOrderModel model = load(f.get("model").getAsString());
		double[][] rows = rows(f.getAsJsonArray("rows"));
		JsonArray want = f.getAsJsonArray("scores");
		assertEquals(want.size(), rows.length);
		double[] got = model.scores(rows);
		for (int i = 0; i < rows.length; i++) {
			assertEquals(want.get(i).getAsDouble(), got[i], 1e-9, fixture + " row " + i);
		}
	}

	@Test
	void theFittedRankerScoresLikeThePipeline() throws IOException {
		assertScoresMatch("ranker_rows.json");
		MiningOrderModel model = load("mining_order_ranker.json");
		assertEquals(25, model.trees());
		assertEquals(json("mining_order_ranker.json").get("temperature").getAsDouble(), model.temperature());
	}

	@Test
	void missingValuesFollowLightGbm() throws IOException {
		assertScoresMatch("missing_rows.json"); // "NaN" and "Zero" missing types, both default sides
	}

	@Test
	void theGenericPriorScoresLikeThePipeline() throws IOException {
		assertScoresMatch("default_rows.json");
		MiningOrderModel prior = load("mining_order_default.json");
		assertEquals(2.0, prior.score(new double[BreakFeatures.COUNT]), 1e-12); // near the crosshair, close
		assertEquals(1.0, prior.temperature());
	}

	@Test
	void probabilitiesAreThePipelinesSoftmax() throws IOException {
		JsonObject f = json("ranker_rows.json");
		MiningOrderModel model = load("mining_order_ranker.json");
		double[][] rows = rows(f.getAsJsonArray("rows"));
		JsonArray steps = f.getAsJsonArray("steps");
		for (int s = 0; s < steps.size(); s++) {
			int a = steps.get(s).getAsJsonArray().get(0).getAsInt();
			int b = steps.get(s).getAsJsonArray().get(1).getAsInt();
			double[] p = model.probabilities(model.scores(Arrays.copyOfRange(rows, a, b)));
			JsonArray want = f.getAsJsonArray("probabilities").get(s).getAsJsonArray();
			double sum = 0;
			for (int i = 0; i < p.length; i++) {
				assertEquals(want.get(i).getAsDouble(), p[i], 1e-12, "step " + s + " candidate " + i);
				sum += p[i];
			}
			assertEquals(1.0, sum, 1e-12);
		}
	}

	@Test
	void choicesComeAsOftenAsTheirProbability() throws IOException {
		JsonObject f = json("ranker_rows.json");
		MiningOrderModel model = load("mining_order_ranker.json");
		JsonArray step = f.getAsJsonArray("steps").get(0).getAsJsonArray();
		double[][] rows = Arrays.copyOfRange(rows(f.getAsJsonArray("rows")), step.get(0).getAsInt(), step.get(1).getAsInt());
		double[] p = model.probabilities(model.scores(rows));
		int n = 200_000;
		int[] count = new int[rows.length];
		Random rng = new Random(7);
		for (int i = 0; i < n; i++) {
			count[model.choose(rows, rng)]++;
		}
		for (int i = 0; i < rows.length; i++) {
			double sd = Math.sqrt(n * p[i] * (1 - p[i]));
			assertEquals(n * p[i], count[i], 5 * sd + 1, "candidate " + i + " p " + p[i]);
		}
		assertEquals(-1, model.choose(new double[0][], rng));
	}

	@Test
	void temperatureSetsHowVariedTheChoicesAre(@TempDir Path dir) throws IOException {
		JsonObject f = json("ranker_rows.json");
		JsonArray step = f.getAsJsonArray("steps").get(1).getAsJsonArray();
		double[][] rows = Arrays.copyOfRange(rows(f.getAsJsonArray("rows")), step.get(0).getAsInt(), step.get(1).getAsInt());
		JsonObject j = json("mining_order_ranker.json");
		j.addProperty("temperature", 1e-4);
		MiningOrderModel cold = write(dir.resolve("cold.json"), j);
		j.addProperty("temperature", 1e4);
		MiningOrderModel hot = write(dir.resolve("hot.json"), j);
		double[] s = cold.scores(rows);
		double top = Arrays.stream(s).max().orElseThrow();
		Random rng = new Random(8);
		for (int i = 0; i < 200; i++) {
			assertEquals(top, s[cold.choose(rows, rng)], "a cold softmax takes a top pick (trees tie often)");
		}
		for (double p : hot.probabilities(s)) {
			assertEquals(1.0 / rows.length, p, 0.01 / rows.length, "a hot one is uniform");
		}
	}

	private static MiningOrderModel write(Path file, JsonObject j) throws IOException {
		Files.writeString(file, j.toString(), StandardCharsets.UTF_8);
		return MiningOrderModel.load(file, why -> {
			throw new AssertionError(why);
		});
	}

	private static String rejected(Path dir, String text) throws IOException {
		Path file = dir.resolve("mining_order.json");
		Files.writeString(file, text, StandardCharsets.UTF_8);
		List<String> why = new ArrayList<>();
		assertNull(MiningOrderModel.load(file, why::add), text);
		assertEquals(1, why.size());
		return why.getFirst();
	}

	@Test
	void aMissingOrInvalidFileLoadsAsNullAndSaysWhy(@TempDir Path dir) throws IOException {
		List<String> why = new ArrayList<>();
		assertNull(MiningOrderModel.load(dir.resolve("nothing.json"), why::add));
		assertTrue(why.getFirst().contains("doesn't exist"), why.getFirst());

		JsonObject good = json("mining_order_default.json");
		assertTrue(rejected(dir, "{not json").contains("isn't valid JSON"));
		assertTrue(rejected(dir, "[1, 2]").contains("not a JSON object"));

		JsonObject j = good.deepCopy();
		j.addProperty("kind", "camera");
		assertTrue(rejected(dir, j.toString()).contains("kind"));

		j = good.deepCopy();
		j.addProperty("schema", 2);
		assertTrue(rejected(dir, j.toString()).contains("schema"));

		j = good.deepCopy();
		j.getAsJsonArray("features").set(3, new JsonPrimitive("dx_prev"));
		assertTrue(rejected(dir, j.toString()).contains("dx_prev"));

		for (double t : new double[]{0, -1}) {
			j = good.deepCopy();
			j.addProperty("temperature", t);
			assertTrue(rejected(dir, j.toString()).contains("temperature"));
		}
		j = good.deepCopy();
		j.remove("temperature");
		assertTrue(rejected(dir, j.toString()).contains("temperature"));

		j = good.deepCopy();
		j.add("trees", new JsonArray());
		assertTrue(rejected(dir, j.toString()).contains("no trees"));

		j = good.deepCopy();
		node(j, 2).addProperty("decision_type", "==");
		assertTrue(rejected(dir, j.toString()).contains("categorical"));

		j = good.deepCopy();
		node(j, 0).addProperty("missing_type", "Sometimes");
		assertTrue(rejected(dir, j.toString()).contains("missing_type"));

		j = good.deepCopy();
		node(j, 1).addProperty("split_feature", 20);
		assertTrue(rejected(dir, j.toString()).contains("feature 20"));

		j = good.deepCopy();
		node(j, 1).addProperty("threshold", "high");
		assertTrue(rejected(dir, j.toString()).contains("malformed"));
	}

	private static JsonObject node(JsonObject model, int tree) {
		return model.getAsJsonArray("trees").get(tree).getAsJsonObject().getAsJsonObject("tree_structure");
	}

	@Test
	void aModelInAnotherColumnOrderStillReadsBreakFeaturesRows(@TempDir Path dir) throws IOException {
		JsonObject j = json("mining_order_default.json");
		JsonArray names = j.getAsJsonArray("features");
		JsonArray reversed = new JsonArray();
		for (int i = names.size() - 1; i >= 0; i--) {
			reversed.add(names.get(i));
		}
		j.add("features", reversed);
		for (JsonElement t : j.getAsJsonArray("trees")) {
			JsonObject root = t.getAsJsonObject().getAsJsonObject("tree_structure");
			root.addProperty("split_feature", names.size() - 1 - root.get("split_feature").getAsInt());
		}
		MiningOrderModel m = write(dir.resolve("reversed.json"), j);
		MiningOrderModel same = load("mining_order_default.json");
		JsonObject f = json("default_rows.json");
		double[][] rows = rows(f.getAsJsonArray("rows"));
		for (double[] row : rows) {
			assertEquals(same.score(row), m.score(row));
		}
	}
}
