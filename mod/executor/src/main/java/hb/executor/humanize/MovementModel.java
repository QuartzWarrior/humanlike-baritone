package hb.executor.humanize;

import com.google.gson.JsonObject;

import java.io.IOException;
import java.nio.file.Path;

/**
 * Stage 1 movement parameters for one player (spec, Component B: movement), fitted by the
 * pipeline into hb-models/stage1/movement.json. Ticks, degrees, blocks, seconds.
 *
 * @param forwardHoldMedian    forward is held this long per press (log-normal median, ticks)
 * @param forwardHoldSigma     ... and the sigma of its log
 * @param regripMedian         a brief lift of forward while moving lasts this long (ticks)
 * @param regripSigma          ... and the sigma of its log
 * @param doubleTapShare       share of sprints started by double-tapping forward (else the key)
 * @param jumpAfterLanding     probability of re-jumping 0, 1, 2... ticks after landing, sprint-jumping
 * @param sprintJumpShare      share of sprinting on open ground spent sprint-jumping
 * @param headingTheta         reversion rate per second of the camera's offset from the way of travel
 * @param headingSigma         its stationary spread (degrees): the sd it wanders with
 * @param headingMean          its bias, degrees
 * @param lookAheadMedian      how far along the path the camera points while walking (blocks)
 * @param lookAheadSigma       ... and the sigma of its log
 * @param walkPitchMean        camera pitch while walking (degrees, positive looks down)
 * @param walkPitchSd          ... and its spread
 * @param miningSneakShare     share of mining time spent sneaking
 * @param miningSneakMedian    a sneaking stretch while mining lasts this long (ticks)
 * @param miningSneakSigma     ... and the sigma of its log
 * @param miningStandSigma     sigma of the log of the stretches between (their median follows
 *                             from the share)
 * @param miningStepsPerMin    steps about the work per minute of mining in place
 * @param miningStepMedian     a step's movement key is held this long (ticks)
 * @param miningStepSigma      ... and the sigma of its log
 * @param miningStepKeys       shares of steps by forward, back, left, right
 * @param miningReachMedian    blocks broken while mining are this far from the eye (median, to the
 *                             block's nearest point)
 * @param miningReachQ90       ... and nine in ten within this
 */
public record MovementModel(
	double forwardHoldMedian, double forwardHoldSigma,
	double regripMedian, double regripSigma,
	double doubleTapShare,
	double[] jumpAfterLanding,
	double sprintJumpShare,
	double headingTheta, double headingSigma, double headingMean,
	double lookAheadMedian, double lookAheadSigma,
	double walkPitchMean, double walkPitchSd,
	double miningSneakShare, double miningSneakMedian, double miningSneakSigma, double miningStandSigma,
	double miningStepsPerMin, double miningStepMedian, double miningStepSigma, double[] miningStepKeys,
	double miningReachMedian, double miningReachQ90) {

	/** Generic values for a missing or partial file: a person, just not this one. */
	public static MovementModel defaults() {
		return new MovementModel(
			60, 0.8,
			3, 0.5,
			0.0,
			new double[]{0.15, 0.45, 0.25, 0.1, 0.05},
			0.0,
			3.0, 3.0, 0.0,
			4.0, 0.3,
			12.0, 6.0,
			0.0, 100, 1.0, 1.0,
			0.0, 6, 1.0, new double[]{0.4, 0.2, 0.2, 0.2},
			1.5, 2.7);
	}

	public static MovementModel load(Path file) throws IOException {
		JsonObject j = ModelJson.read(file);
		MovementModel d = defaults();
		double tapped = ModelJson.num(j, 0, "sprint_method", "double_tap");
		double total = tapped + ModelJson.num(j, 0, "sprint_method", "key") + ModelJson.num(j, 0, "sprint_method", "toggle");
		return new MovementModel(
			ModelJson.num(j, d.forwardHoldMedian, "hold_ticks", "forward", "median"),
			ModelJson.num(j, d.forwardHoldSigma, "hold_ticks", "forward", "sigma"),
			ModelJson.num(j, d.regripMedian, "regrip_gap_ticks", "median"),
			ModelJson.num(j, d.regripSigma, "regrip_gap_ticks", "sigma"),
			total > 0 ? tapped / total : d.doubleTapShare,
			ModelJson.nums(j, d.jumpAfterLanding, "jump_after_landing"),
			ModelJson.num(j, d.sprintJumpShare, "sprint_jump_share"),
			ModelJson.num(j, d.headingTheta, "heading", "theta_per_s"),
			ModelJson.num(j, d.headingSigma, "heading", "sigma_deg"),
			ModelJson.num(j, d.headingMean, "heading", "mean_deg"),
			ModelJson.num(j, d.lookAheadMedian, "look_ahead_blocks", "median"),
			ModelJson.num(j, d.lookAheadSigma, "look_ahead_blocks", "sigma"),
			ModelJson.num(j, d.walkPitchMean, "walk_pitch", "mean"),
			ModelJson.num(j, d.walkPitchSd, "walk_pitch", "sd"),
			ModelJson.num(j, d.miningSneakShare, "mining_sneak", "share"),
			ModelJson.num(j, d.miningSneakMedian, "mining_sneak", "bout_ticks", "median"),
			ModelJson.num(j, d.miningSneakSigma, "mining_sneak", "bout_ticks", "sigma"),
			ModelJson.num(j, d.miningStandSigma, "mining_sneak", "gap_ticks", "sigma"),
			ModelJson.num(j, d.miningStepsPerMin, "mining_steps", "per_min"),
			ModelJson.num(j, d.miningStepMedian, "mining_steps", "ticks", "median"),
			ModelJson.num(j, d.miningStepSigma, "mining_steps", "ticks", "sigma"),
			new double[]{
				ModelJson.num(j, d.miningStepKeys[0], "mining_steps", "keys", "forward"),
				ModelJson.num(j, d.miningStepKeys[1], "mining_steps", "keys", "back"),
				ModelJson.num(j, d.miningStepKeys[2], "mining_steps", "keys", "left"),
				ModelJson.num(j, d.miningStepKeys[3], "mining_steps", "keys", "right")},
			ModelJson.num(j, d.miningReachMedian, "mining_reach", "median"),
			ModelJson.num(j, d.miningReachQ90, "mining_reach", "q90"));
	}
}
