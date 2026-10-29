package hb.integration;

import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.sounds.SoundSource;

/**
 * Test clients play no sound: master volume 0, kept there every tick (set once at start, it came
 * back up: something during the test's setup sets the options again).
 */
public final class QuietClient implements ClientModInitializer {
	@Override
	public void onInitializeClient() {
		ClientTickEvents.END_CLIENT_TICK.register(mc -> {
			var master = mc.options.getSoundSourceOptionInstance(SoundSource.MASTER);
			if (master.get() != 0.0) {
				master.set(0.0);
			}
		});
	}
}
