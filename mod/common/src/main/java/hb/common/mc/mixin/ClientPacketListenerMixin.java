package hb.common.mc.mixin;

import hb.common.mc.McHooks;
import hb.common.mc.McState;
import net.minecraft.client.multiplayer.ClientPacketListener;
import net.minecraft.network.protocol.game.ClientboundDamageEventPacket;
import net.minecraft.network.protocol.game.ClientboundPlayerPositionPacket;
import net.minecraft.network.protocol.game.ClientboundSoundEntityPacket;
import net.minecraft.network.protocol.game.ClientboundSoundPacket;
import net.minecraft.network.protocol.game.ClientboundTakeItemEntityPacket;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/**
 * Server-driven events that have no Fabric event: item pickups, position corrections (teleport
 * packets), damage sources and world sounds. Each hook sits after ensureRunningOnSameThread, so it
 * runs once on the client thread instead of first on the network thread.
 */
@Mixin(ClientPacketListener.class)
abstract class ClientPacketListenerMixin {
	private static final String ON_CLIENT_THREAD = "Lnet/minecraft/network/protocol/PacketUtils;ensureRunningOnSameThread(Lnet/minecraft/network/protocol/Packet;Lnet/minecraft/network/PacketListener;Lnet/minecraft/network/PacketProcessor;)V";

	@Inject(method = "handleTakeItemEntity", at = @At(value = "INVOKE", target = ON_CLIENT_THREAD, shift = At.Shift.AFTER))
	private void hb$takeItem(ClientboundTakeItemEntityPacket packet, CallbackInfo ci) {
		McHooks.itemTaken(packet.getItemId(), packet.getPlayerId(), packet.getAmount());
	}

	@Inject(method = "handleMovePlayer", at = @At(value = "INVOKE", target = ON_CLIENT_THREAD, shift = At.Shift.AFTER))
	private void hb$beforeMovePlayer(ClientboundPlayerPositionPacket packet, CallbackInfo ci) {
		McHooks.beforePositionPacket();
	}

	@Inject(method = "handleMovePlayer", at = @At("TAIL"))
	private void hb$afterMovePlayer(ClientboundPlayerPositionPacket packet, CallbackInfo ci) {
		McHooks.afterPositionPacket();
	}

	@Inject(method = "handleDamageEvent", at = @At(value = "INVOKE", target = ON_CLIENT_THREAD, shift = At.Shift.AFTER))
	private void hb$damage(ClientboundDamageEventPacket packet, CallbackInfo ci) {
		McState.onDamageEvent(packet.entityId(), packet.sourceType(), packet.sourceCauseId());
	}

	@Inject(method = "handleSoundEvent", at = @At(value = "INVOKE", target = ON_CLIENT_THREAD, shift = At.Shift.AFTER))
	private void hb$sound(ClientboundSoundPacket packet, CallbackInfo ci) {
		McHooks.sound(packet.getSound(), packet.getSource(), packet.getX(), packet.getY(), packet.getZ(), packet.getVolume());
	}

	@Inject(method = "handleSoundEntityEvent", at = @At(value = "INVOKE", target = ON_CLIENT_THREAD, shift = At.Shift.AFTER))
	private void hb$entitySound(ClientboundSoundEntityPacket packet, CallbackInfo ci) {
		McHooks.entitySound(packet.getId(), packet.getSound(), packet.getSource(), packet.getVolume());
	}
}
