package hb.recorder;

import hb.common.HbCommon;
import hb.common.mc.McEvents;
import hb.common.mc.McHooks;
import hb.common.mc.McKeys;
import hb.common.mc.McUi;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.loader.api.FabricLoader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class HbRecorderClient implements ClientModInitializer {
	public static final String MOD_ID = "hb-recorder";
	private static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);
	private static Recorder recorder;

	/** The running recorder; null before client init. */
	public static Recorder recorder() {
		return recorder;
	}

	@Override
	public void onInitializeClient() {
		String version = FabricLoader.getInstance().getModContainer(MOD_ID)
			.map(m -> m.getMetadata().getVersion().getFriendlyString()).orElse("dev");
		// -Dhb.recorder.mirror=true also writes every record as JSON (round-trip tests only).
		boolean mirror = Boolean.getBoolean("hb.recorder.mirror");
		recorder = new Recorder(RecorderConfig.load(), version, mirror);

		McKeys.Key pause = McKeys.register("key.hb-recorder.pause", McKeys.F8, MOD_ID, "recorder");
		McKeys.Key discard = McKeys.register("key.hb-recorder.discard", McKeys.F9, MOD_ID, "recorder");
		McKeys.Key status = McKeys.register("key.hb-recorder.status", McKeys.F7, MOD_ID, "recorder");

		McEvents.init();
		McHooks.addListener(recorder);
		McEvents.onTick(now -> {
			recorder.onTick(now, pause.consumeClick(), discard.consumeClick());
			if (status.consumeClick()) {
				McUi.openStatusScreen(recorder::statusModel);
			}
		});
		McEvents.onDisconnect(() -> recorder.endSession("disconnected"));
		McEvents.onStopping(recorder::shutdown);
		McUi.registerHudDot(MOD_ID, "status_dot", recorder::hudColor, recorder::hudCost);

		LOGGER.info("{} {} loaded (record schema v{}{})", MOD_ID, version, HbCommon.SCHEMA_VERSION, mirror ? ", JSON mirror on" : "");
	}
}
