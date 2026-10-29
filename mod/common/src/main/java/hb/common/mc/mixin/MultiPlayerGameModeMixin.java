package hb.common.mc.mixin;

import hb.common.mc.McHooks;
import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.phys.BlockHitResult;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/** Block-destroy progress and container slot clicks (26.2 renamed ClickType to ContainerInput). */
@Mixin(MultiPlayerGameMode.class)
abstract class MultiPlayerGameModeMixin {
	@Inject(method = "startDestroyBlock", at = @At("HEAD"))
	private void hb$beforeStartDestroy(BlockPos pos, Direction direction, CallbackInfoReturnable<Boolean> cir) {
		McHooks.beforeStartDestroy((MultiPlayerGameMode) (Object) this);
	}

	@Inject(method = "startDestroyBlock", at = @At("RETURN"))
	private void hb$afterStartDestroy(BlockPos pos, Direction direction, CallbackInfoReturnable<Boolean> cir) {
		if (cir.getReturnValueZ()) {
			McHooks.afterStartDestroy((MultiPlayerGameMode) (Object) this, pos, direction);
		}
	}

	@Inject(method = "stopDestroyBlock", at = @At("HEAD"))
	private void hb$stopDestroy(CallbackInfo ci) {
		McHooks.beforeStopDestroy((MultiPlayerGameMode) (Object) this);
	}

	@Inject(method = "useItemOn", at = @At("HEAD"))
	private void hb$useItemOn(LocalPlayer player, InteractionHand hand, BlockHitResult hit, CallbackInfoReturnable<InteractionResult> cir) {
		McHooks.useItemOn(hit);
	}

	@Inject(method = "handleContainerInput", at = @At("HEAD"))
	private void hb$containerInput(int containerId, int slotNum, int buttonNum, ContainerInput input, Player player, CallbackInfo ci) {
		McHooks.containerInput(containerId, slotNum, buttonNum, input, player);
	}
}
