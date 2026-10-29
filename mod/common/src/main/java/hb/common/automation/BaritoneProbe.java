package hb.common.automation;

import baritone.api.BaritoneAPI;
import baritone.api.IBaritone;

/**
 * Reports Baritone as active while it is pathing or any of its processes (mine, goto, follow,
 * build, ...) is in control. Only load this class when the "baritone" mod is present.
 */
public final class BaritoneProbe {
	private BaritoneProbe() {
	}

	public static void register() {
		HbAutomation.addProbe("baritone", BaritoneProbe::isActive);
	}

	private static boolean isActive() {
		IBaritone baritone = BaritoneAPI.getProvider().getPrimaryBaritone();
		return baritone.getPathingBehavior().isPathing()
			|| baritone.getPathingControlManager().mostRecentInControl().isPresent();
	}
}
