package hb.common.mc;

import com.mojang.blaze3d.systems.RenderSystem;
import hb.common.HbCommon;
import hb.common.io.HbData;
import hb.proto.v1.KeyBinding;
import hb.proto.v1.ModInfo;
import hb.proto.v1.SessionHeader;
import hb.proto.v1.Source;
import net.fabricmc.loader.api.FabricLoader;
import net.fabricmc.loader.api.ModContainer;
import net.minecraft.SharedConstants;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Options;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.level.storage.LevelResource;

/** The session header: versions, mods, input settings, key map, and a hashed world or server id. */
public final class McSession {
	private McSession() {
	}

	public static SessionHeader header(String sessionId, Source source, String recorderVersion, McKeys.KeyOrder keyOrder) {
		Minecraft mc = Minecraft.getInstance();
		Options o = mc.options;
		SessionHeader.Builder h = SessionHeader.newBuilder()
			.setSchema(HbCommon.SCHEMA_VERSION)
			.setSessionId(sessionId)
			.setSource(source)
			.setStartedUnixMs(System.currentTimeMillis())
			.setGameVersion(SharedConstants.getCurrentVersion().name())
			.setRecorderVersion(recorderVersion);
		for (ModContainer mod : FabricLoader.getInstance().getAllMods()) {
			h.addMods(ModInfo.newBuilder().setId(mod.getMetadata().getId()).setVersion(mod.getMetadata().getVersion().getFriendlyString()));
		}
		settings(h, mc, o);
		for (KeyMapping mapping : o.keyMappings) {
			h.addKeybinds(KeyBinding.newBuilder()
				.setName(mapping.getName())
				.setKey(mapping.saveString())
				.setCategory(mapping.getCategory().id().toString()));
		}
		h.addAllKeyOrder(keyOrder.names());
		h.setGameMode(McState.gameMode().getName());
		if (mc.level != null) {
			h.setDifficulty(mc.level.getDifficulty().getSerializedName());
		}
		h.setMultiplayer(!mc.isLocalServer());
		h.setWorldIdHash(worldIdHash(mc));
		try {
			h.setGraphicsBackend(RenderSystem.getDevice().getDeviceInfo().backendName());
		} catch (RuntimeException e) {
			h.setGraphicsBackend("unknown");
		}
		return h.build();
	}

	/**
	 * A hash of the settings and key map that change how input turns into motion. When it changes
	 * mid-session, the recorder starts a new session so every header stays true for its whole session.
	 * Cheap enough to check every second.
	 */
	public static long settingsFingerprint() {
		Minecraft mc = Minecraft.getInstance();
		Options o = mc.options;
		long h = 17;
		h = 31 * h + o.fov().get();
		h = 31 * h + Double.hashCode(o.sensitivity().get());
		h = 31 * h + Double.hashCode(o.mouseWheelSensitivity().get());
		h = 31 * h + mc.getWindow().getGuiScale();
		h = 31 * h + o.renderDistance().get();
		h = 31 * h + o.framerateLimit().get();
		boolean[] flags = {
			o.rawMouseInput().get(), o.invertMouseY().get(), o.invertMouseX().get(), o.toggleSprint().get(), o.toggleCrouch().get(),
			o.toggleAttack().get(), o.toggleUse().get(), o.autoJump().get(), o.discreteMouseScroll().get()
		};
		for (boolean flag : flags) {
			h = 31 * h + (flag ? 1 : 0);
		}
		for (KeyMapping mapping : o.keyMappings) {
			h = 31 * h + mapping.saveString().hashCode();
		}
		return h;
	}

	private static void settings(SessionHeader.Builder h, Minecraft mc, Options o) {
		h.setFov(o.fov().get())
			.setMouseSensitivity(o.sensitivity().get())
			.setRawMouseInput(o.rawMouseInput().get())
			.setInvertY(o.invertMouseY().get())
			.setInvertX(o.invertMouseX().get())
			.setGuiScale(mc.getWindow().getGuiScale())
			.setRenderDistance(o.renderDistance().get())
			.setFpsCap(o.framerateLimit().get())
			.setToggleSprint(o.toggleSprint().get())
			.setToggleCrouch(o.toggleCrouch().get())
			.setToggleAttack(o.toggleAttack().get())
			.setToggleUse(o.toggleUse().get())
			.setAutoJump(o.autoJump().get())
			.setMouseWheelSensitivity(o.mouseWheelSensitivity().get())
			.setDiscreteMouseScroll(o.discreteMouseScroll().get());
	}

	/** Salted hash of the singleplayer save folder or the server address; never the raw value. */
	private static String worldIdHash(Minecraft mc) {
		MinecraftServer server = mc.getSingleplayerServer();
		if (server != null) {
			return HbData.hash("world", server.getWorldPath(LevelResource.ROOT).toAbsolutePath().normalize().getFileName().toString());
		}
		ServerData data = mc.getCurrentServer();
		return HbData.hash("server", data == null ? "" : data.ip);
	}
}
