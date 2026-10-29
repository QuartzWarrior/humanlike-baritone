package hb.executor.humanize;

import com.google.gson.Gson;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** Reading the pipeline's Stage 1 model files (hb-models/stage1/*.json): a missing file or field means the default. */
final class ModelJson {
	private ModelJson() {
	}

	/** The file's top-level object, or an empty one if the file doesn't exist. */
	static JsonObject read(Path file) throws IOException {
		if (!Files.exists(file)) {
			return new JsonObject();
		}
		return new Gson().fromJson(Files.readString(file, StandardCharsets.UTF_8), JsonObject.class);
	}

	/** The number at {@code path} (nested keys), or {@code fallback}. */
	static double num(JsonObject j, double fallback, String... path) {
		JsonElement e = at(j, path);
		return e != null && e.isJsonPrimitive() ? e.getAsDouble() : fallback;
	}

	/** The array of numbers at {@code path}, or {@code fallback}. */
	static double[] nums(JsonObject j, double[] fallback, String... path) {
		JsonElement e = at(j, path);
		if (e == null || !e.isJsonArray() || e.getAsJsonArray().isEmpty()) {
			return fallback;
		}
		double[] out = new double[e.getAsJsonArray().size()];
		for (int i = 0; i < out.length; i++) {
			out[i] = e.getAsJsonArray().get(i).getAsDouble();
		}
		return out;
	}

	static JsonElement at(JsonObject j, String... path) {
		JsonElement e = j;
		for (String key : path) {
			if (e == null || !e.isJsonObject() || !e.getAsJsonObject().has(key) || e.getAsJsonObject().get(key).isJsonNull()) {
				return null;
			}
			e = e.getAsJsonObject().get(key);
		}
		return e;
	}
}
