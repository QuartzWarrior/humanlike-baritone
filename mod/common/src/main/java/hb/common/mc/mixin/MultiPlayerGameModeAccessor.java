package hb.common.mc.mixin;

import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.core.BlockPos;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

@Mixin(MultiPlayerGameMode.class)
public interface MultiPlayerGameModeAccessor {
	@Accessor("destroyBlockPos")
	BlockPos hb$destroyBlockPos();

	@Accessor("destroyProgress")
	float hb$destroyProgress();
}
