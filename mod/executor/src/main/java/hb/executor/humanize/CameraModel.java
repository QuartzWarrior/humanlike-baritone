package hb.executor.humanize;

import com.google.gson.Gson;
import com.google.gson.JsonObject;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Stage 1 camera parameters for one player (spec, Component B: camera), fitted by the pipeline
 * ({@code hbpipe fit-models}) into hb-models/stage1/camera.json. Degrees and milliseconds.
 *
 * <p>The defaults are generic human values for a missing or partial file, so the executor still
 * moves like a person, just not this one.
 */
public record CameraModel(
	double reactionMedianMs, double reactionSigma,
	double fittsA, double fittsB, double durationSigma,
	double peakAtMean, double peakAtSd,
	double gainMean, double gainSd,
	double correctionsMean, int correctionsMax,
	double correctionMedianMs, double correctionSigma,
	double correctionGapMedianMs, double correctionGapSigma,
	double landingSdYaw, double landingSdPitch,
	double landingFracYaw, double landingFracPitch,
	double curvatureSd, double tremorDeg, double tremorSameSign) {

	public static CameraModel defaults() {
		return new CameraModel(
			250, 0.35,
			80, 120, 0.25,
			0.45, 0.07,
			0.93, 0.08,
			0.8, 3,
			140, 0.4,
			60, 0.5,
			0.6, 0.4,
			0.15, 0.15,
			0.06, 0.02, 0.8);
	}

	/** The fitted file, field by field over the defaults; the defaults if the file is missing. */
	public static CameraModel load(Path file) throws IOException {
		CameraModel d = defaults();
		if (!Files.exists(file)) {
			return d;
		}
		JsonObject j = new Gson().fromJson(Files.readString(file, StandardCharsets.UTF_8), JsonObject.class);
		return new CameraModel(
			num(j, "reaction_ms", "median", d.reactionMedianMs), num(j, "reaction_ms", "sigma", d.reactionSigma),
			num(j, "fitts", "a_ms", d.fittsA), num(j, "fitts", "b_ms", d.fittsB), num(j, "duration_sigma", null, d.durationSigma),
			num(j, "peak_at", "mean", d.peakAtMean), num(j, "peak_at", "sd", d.peakAtSd),
			num(j, "primary_gain", "mean", d.gainMean), num(j, "primary_gain", "sd", d.gainSd),
			num(j, "corrections", "mean", d.correctionsMean), (int) num(j, "corrections", "max", d.correctionsMax),
			num(j, "correction_ms", "median", d.correctionMedianMs), num(j, "correction_ms", "sigma", d.correctionSigma),
			num(j, "correction_gap_ms", "median", d.correctionGapMedianMs), num(j, "correction_gap_ms", "sigma", d.correctionGapSigma),
			num(j, "landing_sd_deg", "yaw", d.landingSdYaw), num(j, "landing_sd_deg", "pitch", d.landingSdPitch),
			num(j, "landing_sd_frac", "yaw", d.landingFracYaw), num(j, "landing_sd_frac", "pitch", d.landingFracPitch),
			num(j, "curvature_sd", null, d.curvatureSd), num(j, "tremor_deg", null, d.tremorDeg),
			num(j, "tremor_same_sign", null, d.tremorSameSign));
	}

	private static double num(JsonObject j, String key, String sub, double fallback) {
		if (!j.has(key) || j.get(key).isJsonNull()) {
			return fallback;
		}
		if (sub == null) {
			return j.get(key).getAsDouble();
		}
		JsonObject o = j.getAsJsonObject(key);
		return o.has(sub) && !o.get(sub).isJsonNull() ? o.get(sub).getAsDouble() : fallback;
	}
}
