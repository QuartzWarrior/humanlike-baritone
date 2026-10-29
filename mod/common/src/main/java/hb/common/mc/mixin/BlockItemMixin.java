package hb.common.mc.mixin;

import hb.common.mc.McHooks;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.context.BlockPlaceContext;
import net.minecraft.world.level.block.state.BlockState;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Block placement as the client predicts it. No Fabric event reports a successful placement with
 * its final position; placeBlock receives the adjusted context. McHooks ignores the integrated
 * server's calls, which run on its own thread.
 */
@Mixin(BlockItem.class)
abstract class BlockItemMixin {
	@Inject(method = "placeBlock", at = @At("RETURN"))
	private void hb$placed(BlockPlaceContext context, BlockState state, CallbackInfoReturnable<Boolean> cir) {
		if (cir.getReturnValueZ()) {
			McHooks.blockPlaced(context);
		}
	}
}
