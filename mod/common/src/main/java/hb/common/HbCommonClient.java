package hb.common;

import hb.common.automation.BaritoneProbe;
import hb.common.mc.McEvents;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.loader.api.FabricLoader;

/** Shared setup, once per client no matter which hb mods are installed. */
public final class HbCommonClient implements ClientModInitializer {
	@Override
	public void onInitializeClient() {
		McEvents.init();
		if (FabricLoader.getInstance().isModLoaded("baritone")) {
			BaritoneProbe.register(); // Baritone run by hand pauses the recorder too
		}
	}
}
