package hb.common.mc.mixin;

import hb.common.mc.McHooks;
import net.minecraft.client.KeyboardHandler;
import net.minecraft.client.input.KeyEvent;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Key events, before the game routes them; McHooks strips key identity inside text fields. */
@Mixin(KeyboardHandler.class)
abstract class KeyboardHandlerMixin {
	@Inject(method = "keyPress", at = @At("HEAD"))
	private void hb$onKey(long handle, int action, KeyEvent event, CallbackInfo ci) {
		McHooks.key(action, event);
	}
}
