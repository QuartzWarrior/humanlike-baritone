package hb.common.mc.mixin;

import hb.common.mc.McHooks;
import net.minecraft.client.MouseHandler;
import net.minecraft.client.input.MouseButtonInfo;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Raw mouse input. handleAccumulatedMovement runs once per frame: before it, accumulatedDX/DY hold
 * the frame's raw motion (pre-sensitivity); after it, the player has turned by the game's own math.
 */
@Mixin(MouseHandler.class)
abstract class MouseHandlerMixin {
	@Inject(method = "handleAccumulatedMovement", at = @At("HEAD"))
	private void hb$beforeMovement(CallbackInfo ci) {
		McHooks.beforeAccumulatedMovement((MouseHandler) (Object) this);
	}

	@Inject(method = "handleAccumulatedMovement", at = @At("TAIL"))
	private void hb$afterMovement(CallbackInfo ci) {
		McHooks.afterAccumulatedMovement();
	}

	@Inject(method = "onButton", at = @At("HEAD"))
	private void hb$onButton(long handle, MouseButtonInfo button, int action, CallbackInfo ci) {
		McHooks.mouseButton(button.button(), action, button.modifiers());
	}

	@Inject(method = "onScroll", at = @At("HEAD"))
	private void hb$onScroll(long handle, double xoffset, double yoffset, CallbackInfo ci) {
		McHooks.scroll(xoffset, yoffset);
	}
}
