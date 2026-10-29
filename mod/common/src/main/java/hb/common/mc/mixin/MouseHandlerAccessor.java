package hb.common.mc.mixin;

import net.minecraft.client.MouseHandler;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

@Mixin(MouseHandler.class)
public interface MouseHandlerAccessor {
	@Accessor("accumulatedDX")
	double hb$accumulatedDX();

	@Accessor("accumulatedDY")
	double hb$accumulatedDY();

	@Accessor("accumulatedDX")
	void hb$setAccumulatedDX(double value);

	@Accessor("accumulatedDY")
	void hb$setAccumulatedDY(double value);
}
