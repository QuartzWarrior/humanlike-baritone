package hb.common.mc.mixin;

import hb.common.mc.McHooks;
import net.minecraft.client.player.LocalPlayer;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Drops with the drop key. Drops from inventory screens arrive as THROW slot clicks instead. */
@Mixin(LocalPlayer.class)
abstract class LocalPlayerMixin {
	@Inject(method = "drop", at = @At("HEAD"))
	private void hb$beforeDrop(boolean all, CallbackInfoReturnable<Boolean> cir) {
		McHooks.beforeDrop((LocalPlayer) (Object) this, all);
	}

	@Inject(method = "drop", at = @At("RETURN"))
	private void hb$afterDrop(boolean all, CallbackInfoReturnable<Boolean> cir) {
		McHooks.afterDrop(cir.getReturnValueZ());
	}
}
