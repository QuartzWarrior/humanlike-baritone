package hb.executor.humanize;

import com.google.gson.JsonObject;

import java.io.IOException;
import java.nio.file.Path;

/**
 * Stage 1 meta-behavior parameters for one player (spec, Component B: meta behaviors), fitted
 * by the pipeline into hb-models/stage1/meta.json.
 *
 * @param hesitationMining    hesitations started per minute while mining
 * @param hesitationWalking   ... while walking
 * @param hesitationBuilding  ... while building
 * @param hesitationOther     ... otherwise
 * @param afterBreakP         chance a hesitation starts right after a block breaks (a task just finished)
 * @param hesitationMedianMs  how long one lasts (log-normal median, ms)
 * @param hesitationSigma     ... and the sigma of its log
 * @param glancePerMin        glances away per minute of standing still
 * @param glanceAngleMedian   how far a glance turns (log-normal median, degrees)
 * @param glanceAngleSigma    ... and the sigma of its log
 * @param glanceHoldMedianMs  how long the look away lasts (log-normal median, ms)
 * @param glanceHoldSigma     ... and the sigma of its log
 */
public record MetaModel(
	double hesitationMining, double hesitationWalking, double hesitationBuilding, double hesitationOther,
	double afterBreakP,
	double hesitationMedianMs, double hesitationSigma,
	double glancePerMin,
	double glanceAngleMedian, double glanceAngleSigma,
	double glanceHoldMedianMs, double glanceHoldSigma) {

	/** Generic values for a missing or partial file. */
	public static MetaModel defaults() {
		return new MetaModel(
			3.0, 1.0, 3.0, 1.0,
			0.05,
			700, 0.5,
			2.0,
			30, 0.5,
			800, 0.5);
	}

	public static MetaModel load(Path file) throws IOException {
		JsonObject j = ModelJson.read(file);
		MetaModel d = defaults();
		return new MetaModel(
			ModelJson.num(j, d.hesitationMining, "hesitation", "per_min", "mining"),
			ModelJson.num(j, d.hesitationWalking, "hesitation", "per_min", "walking"),
			ModelJson.num(j, d.hesitationBuilding, "hesitation", "per_min", "building"),
			ModelJson.num(j, d.hesitationOther, "hesitation", "per_min", "other"),
			ModelJson.num(j, d.afterBreakP, "hesitation", "after_break_p"),
			ModelJson.num(j, d.hesitationMedianMs, "hesitation", "duration_ms", "median"),
			ModelJson.num(j, d.hesitationSigma, "hesitation", "duration_ms", "sigma"),
			ModelJson.num(j, d.glancePerMin, "glance", "per_min"),
			ModelJson.num(j, d.glanceAngleMedian, "glance", "angle_deg", "median"),
			ModelJson.num(j, d.glanceAngleSigma, "glance", "angle_deg", "sigma"),
			ModelJson.num(j, d.glanceHoldMedianMs, "glance", "hold_ms", "median"),
			ModelJson.num(j, d.glanceHoldSigma, "glance", "hold_ms", "sigma"));
	}

	/** Hesitations per minute in a context: "mining", "walking", "building" or anything else. */
	public double hesitationPerMin(String context) {
		return switch (context) {
			case "mining" -> hesitationMining;
			case "walking" -> hesitationWalking;
			case "building" -> hesitationBuilding;
			default -> hesitationOther;
		};
	}
}
