package hb.executor.decide;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParseException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Random;
import java.util.Set;
import java.util.function.Consumer;

/**
 * The Stage 1 mining-order ranker for one player (spec, Component B: decision models), fitted by
 * the pipeline (hbpipe.models.decision.mining_order) into hb-models/stage1/mining_order.json: a
 * LightGBM lambdarank tree ensemble that scores every block the executor could break next
 * ({@link BreakFeatures}), and a temperature. The executor samples the next block with
 * probability proportional to exp(score / temperature), so it is as varied as the player rather
 * than always taking the top pick.
 *
 * <p>The trees are evaluated exactly as the pipeline's score() does, which matches LightGBM's raw
 * predict: numerical "<=" splits with LightGBM's missing-value rules. Categorical splits aren't
 * supported (the pipeline doesn't use them); a file with one is rejected like any other invalid
 * file, and the caller falls back to Baritone's own order.
 */
public final class MiningOrderModel {
	public static final String FILE = "mining_order.json";
	static final int SCHEMA = 1;
	/** LightGBM's kZeroThreshold as the pipeline uses it: |v| <= this counts as zero for missing_type "Zero". */
	static final double ZERO = 1e-35;

	private static final Logger LOGGER = LoggerFactory.getLogger("hb-mining-order");
	private static final byte MISSING_NONE = 0;
	private static final byte MISSING_ZERO = 1;
	private static final byte MISSING_NAN = 2;

	private final double temperature;
	// Each tree's root, then the split nodes flattened; a child (or root) below 0 is ~leaf index.
	private final int[] roots;
	private final int[] feature;
	private final double[] threshold;
	private final boolean[] defaultLeft;
	private final byte[] missing;
	private final int[] left;
	private final int[] right;
	private final double[] leafValue;

	private MiningOrderModel(double temperature, Builder b) {
		this.temperature = temperature;
		int n = b.feature.size();
		roots = b.roots.stream().mapToInt(Integer::intValue).toArray();
		feature = b.feature.stream().mapToInt(Integer::intValue).toArray();
		threshold = b.threshold.stream().mapToDouble(Double::doubleValue).toArray();
		defaultLeft = new boolean[n];
		missing = new byte[n];
		for (int i = 0; i < n; i++) {
			defaultLeft[i] = b.defaultLeft.get(i);
			missing[i] = b.missing.get(i);
		}
		left = b.left.stream().mapToInt(Integer::intValue).toArray();
		right = b.right.stream().mapToInt(Integer::intValue).toArray();
		leafValue = b.leaves.stream().mapToDouble(Double::doubleValue).toArray();
	}

	/** The fitted file, or null (logged) if it is missing or invalid: then Baritone's order is used. */
	public static MiningOrderModel load(Path file) {
		return load(file, why -> LOGGER.warn("No mining-order model, using Baritone's own order: {}", why));
	}

	/** The fitted file, or null if it is missing or invalid, with the reason given to {@code why}. */
	public static MiningOrderModel load(Path file, Consumer<String> why) {
		try {
			String text = Files.readString(file, StandardCharsets.UTF_8);
			JsonElement root = new Gson().fromJson(text, JsonElement.class);
			if (root == null || !root.isJsonObject()) {
				throw new Invalid("not a JSON object");
			}
			return parse(root.getAsJsonObject());
		} catch (NoSuchFileException e) {
			why.accept(file + " doesn't exist (hbpipe fit-models writes it)");
		} catch (IOException e) {
			why.accept("can't read " + file + ": " + e);
		} catch (JsonParseException e) {
			why.accept(file + " isn't valid JSON: " + e.getMessage());
		} catch (Invalid e) {
			why.accept(file + ": " + e.getMessage());
		} catch (RuntimeException e) {
			// A field of the wrong JSON type (Gson's getAs* throws these).
			why.accept(file + " is malformed: " + e);
		}
		return null;
	}

	static MiningOrderModel parse(JsonObject j) throws Invalid {
		if (!"mining_order".equals(str(j, "kind"))) {
			throw new Invalid("kind is " + str(j, "kind") + ", not mining_order");
		}
		if (!j.has("schema") || j.get("schema").getAsInt() != SCHEMA) {
			throw new Invalid("schema " + j.get("schema") + " isn't the " + SCHEMA + " this executor reads");
		}
		// The model's own column order, mapped to BreakFeatures' (the same today; a reorder still loads).
		JsonArray names = array(j, "features");
		int[] column = new int[names.size()];
		Set<String> seen = new HashSet<>();
		for (int i = 0; i < column.length; i++) {
			String name = names.get(i).getAsString();
			column[i] = BreakFeatures.NAMES.indexOf(name);
			if (column[i] < 0) {
				throw new Invalid("feature " + name + " isn't one the executor computes");
			}
			if (!seen.add(name)) {
				throw new Invalid("feature " + name + " is listed twice");
			}
		}
		if (!j.has("temperature") || !j.get("temperature").isJsonPrimitive()) {
			throw new Invalid("no temperature");
		}
		double temperature = j.get("temperature").getAsDouble();
		if (!(temperature > 0) || Double.isInfinite(temperature)) {
			throw new Invalid("temperature " + temperature + " isn't a positive number");
		}
		JsonArray trees = array(j, "trees");
		if (trees.isEmpty()) {
			throw new Invalid("no trees");
		}
		Builder b = new Builder(column);
		for (int t = 0; t < trees.size(); t++) {
			JsonElement tree = trees.get(t);
			if (!tree.isJsonObject() || !tree.getAsJsonObject().has("tree_structure")) {
				throw new Invalid("tree " + t + " has no tree_structure");
			}
			b.roots.add(b.node(tree.getAsJsonObject().getAsJsonObject("tree_structure"), t));
		}
		return new MiningOrderModel(temperature, b);
	}

	public double temperature() {
		return temperature;
	}

	public int trees() {
		return roots.length;
	}

	/** The raw score of one candidate (features in {@link BreakFeatures#NAMES} order): the sum of its leaves. */
	public double score(double[] row) {
		if (row.length != BreakFeatures.COUNT) {
			throw new IllegalArgumentException("a row has " + BreakFeatures.COUNT + " features, not " + row.length);
		}
		double total = 0.0;
		for (int node : roots) {
			while (node >= 0) {
				node = goesLeft(node, row[feature[node]]) ? left[node] : right[node];
			}
			total += leafValue[~node];
		}
		return total;
	}

	public double[] scores(double[][] rows) {
		double[] out = new double[rows.length];
		for (int i = 0; i < rows.length; i++) {
			out[i] = score(rows[i]);
		}
		return out;
	}

	/** LightGBM's NumericalDecision, as the pipeline's _goes_left writes it. */
	private boolean goesLeft(int node, double v) {
		byte m = missing[node];
		if (Double.isNaN(v) && m != MISSING_NAN) {
			v = 0.0;
		}
		if (m == MISSING_ZERO && v >= -ZERO && v <= ZERO || m == MISSING_NAN && Double.isNaN(v)) {
			return defaultLeft[node];
		}
		return v <= threshold[node];
	}

	/**
	 * Each candidate's chance of being chosen: softmax(score / temperature) over the step's
	 * candidates, computed as the pipeline's _log_probs (max-shifted log-sum-exp).
	 */
	public double[] probabilities(double[] scores) {
		int n = scores.length;
		double[] z = new double[n];
		double max = Double.NEGATIVE_INFINITY;
		for (int i = 0; i < n; i++) {
			z[i] = scores[i] / temperature;
			max = Math.max(max, z[i]);
		}
		double sum = 0.0;
		for (int i = 0; i < n; i++) {
			sum += Math.exp(z[i] - max);
		}
		double log = Math.log(sum);
		double[] p = new double[n];
		for (int i = 0; i < n; i++) {
			p[i] = Math.exp(z[i] - max - log);
		}
		return p;
	}

	/** Samples one of the step's candidates by {@link #probabilities}; -1 if there are none. */
	public int choose(double[][] rows, Random rng) {
		if (rows.length == 0) {
			return -1;
		}
		double[] p = probabilities(scores(rows));
		double total = 0.0;
		int best = 0;
		for (int i = 0; i < p.length; i++) {
			total += p[i];
			if (p[i] > p[best]) {
				best = i;
			}
		}
		double u = rng.nextDouble() * total;
		double acc = 0.0;
		for (int i = 0; i < p.length; i++) {
			acc += p[i];
			if (u < acc) {
				return i;
			}
		}
		return best; // rounding left u at the very top
	}

	private static String str(JsonObject j, String key) {
		JsonElement e = j.get(key);
		return e != null && e.isJsonPrimitive() ? e.getAsString() : null;
	}

	private static JsonArray array(JsonObject j, String key) throws Invalid {
		JsonElement e = j.get(key);
		if (e == null || !e.isJsonArray()) {
			throw new Invalid("no " + key + " list");
		}
		return e.getAsJsonArray();
	}

	/** A file that parses but isn't a mining-order model this executor can evaluate. */
	static final class Invalid extends Exception {
		Invalid(String message) {
			super(message);
		}
	}

	/** Flattens LightGBM's dump_model trees ("tree_structure" nodes) into the arrays above. */
	private static final class Builder {
		final int[] column;
		final List<Integer> roots = new ArrayList<>();
		final List<Integer> feature = new ArrayList<>();
		final List<Double> threshold = new ArrayList<>();
		final List<Boolean> defaultLeft = new ArrayList<>();
		final List<Byte> missing = new ArrayList<>();
		final List<Integer> left = new ArrayList<>();
		final List<Integer> right = new ArrayList<>();
		final List<Double> leaves = new ArrayList<>();

		Builder(int[] column) {
			this.column = column;
		}

		/** Adds the subtree at {@code n}; returns its node index, or ~leaf index for a leaf. */
		int node(JsonObject n, int tree) throws Invalid {
			if (n.has("leaf_value")) {
				leaves.add(n.get("leaf_value").getAsDouble());
				return ~(leaves.size() - 1);
			}
			String decision = str(n, "decision_type");
			if (!"<=".equals(decision)) {
				throw new Invalid("tree " + tree + " has a \"" + decision + "\" split; only numerical \"<=\" splits are"
					+ " supported (\"==\" is a categorical split, which the pipeline doesn't fit)");
			}
			int f = n.get("split_feature").getAsInt();
			if (f < 0 || f >= column.length) {
				throw new Invalid("tree " + tree + " splits on feature " + f + " of " + column.length);
			}
			byte m = switch (String.valueOf(str(n, "missing_type"))) {
				case "None" -> MISSING_NONE;
				case "Zero" -> MISSING_ZERO;
				case "NaN" -> MISSING_NAN;
				default -> throw new Invalid("tree " + tree + " has missing_type " + str(n, "missing_type"));
			};
			int i = feature.size();
			feature.add(column[f]);
			threshold.add(n.get("threshold").getAsDouble());
			defaultLeft.add(n.get("default_left").getAsBoolean());
			missing.add(m);
			left.add(0);
			right.add(0);
			left.set(i, node(child(n, "left_child", tree), tree));
			right.set(i, node(child(n, "right_child", tree), tree));
			return i;
		}

		private static JsonObject child(JsonObject n, String key, int tree) throws Invalid {
			JsonElement c = n.get(key);
			if (c == null || !c.isJsonObject()) {
				throw new Invalid("tree " + tree + " has a split without its " + key);
			}
			return c.getAsJsonObject();
		}
	}
}
