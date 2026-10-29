package hb.executor;

import hb.common.mc.McCommands;
import hb.common.mc.McEvents;
import hb.executor.humanize.Humanizer;
import hb.executor.planner.BaritonePlannerBackend;
import hb.executor.planner.PlannerBackend;
import hb.executor.record.BotRecorder;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.loader.api.FabricLoader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Set;

public final class HbExecutorClient implements ClientModInitializer {
	public static final String MOD_ID = "hb-executor";
	private static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

	private static PlannerBackend planner;
	private static BotRecorder botRecorder;
	private static Humanizer humanizer;
	private static HbController controller;

	@Override
	public void onInitializeClient() {
		String version = FabricLoader.getInstance().getModContainer(MOD_ID)
			.map(m -> m.getMetadata().getVersion().getFriendlyString()).orElse("dev");
		planner = new BaritonePlannerBackend();
		humanizer = new Humanizer(planner);
		// Humanized, the real keys are pressed and logged as such; plain Baritone holds its own.
		botRecorder = new BotRecorder(version, () -> humanizer.active() ? Set.of() : planner.heldBindings());
		controller = new HbController(planner, botRecorder, humanizer);
		McEvents.init();
		McEvents.onTick(now -> {
			controller.tick();
			botRecorder.onTick();
		});
		McEvents.onDisconnect(() -> {
			controller.finish("disconnected");
			botRecorder.stop("disconnected");
		});
		McEvents.onStopping(() -> {
			controller.finish("client stopping");
			botRecorder.stop("client stopping");
		});
		McCommands.register("hb", controller::handle);
		LOGGER.info("{} loaded with planner backend '{}'", MOD_ID, planner.name());
	}

	public static PlannerBackend planner() {
		return planner;
	}

	/** The /hb commands and the current run. */
	public static HbController controller() {
		return controller;
	}

	/** Records the bot's play as source = bot sessions; see {@link BotRecorder}. */
	public static BotRecorder botRecorder() {
		return botRecorder;
	}
}
