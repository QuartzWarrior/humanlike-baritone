package hb.common.mc.mixin;

import net.minecraft.client.KeyMapping;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

/** One mapping's click count, so a click reaches that mapping only (KeyMapping.click(key) reaches every mapping on the key). */
@Mixin(KeyMapping.class)
public interface KeyMappingAccessor {
	@Accessor("clickCount")
	int hb$clickCount();

	@Accessor("clickCount")
	void hb$setClickCount(int value);
}
