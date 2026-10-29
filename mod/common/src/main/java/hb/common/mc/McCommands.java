package hb.common.mc;

import com.mojang.brigadier.arguments.StringArgumentType;
import net.fabricmc.fabric.api.client.command.v2.ClientCommands;
import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.minecraft.client.Minecraft;
import net.minecraft.network.chat.Component;

import java.util.function.BiConsumer;
import java.util.function.Consumer;

/**
 * Client-side chat commands and chat feedback, so mods don't touch Brigadier or chat directly.
 * A command is a root word plus the rest of the line as one string: {@code /hb run mine iron_ore}
 * reaches the handler as {@code "run mine iron_ore"}. Client commands never reach the server.
 */
public final class McCommands {
	private McCommands() {
	}

	/** Registers {@code /root [rest...]}; the handler gets the rest ("" if none) and a reply sink. */
	public static void register(String root, BiConsumer<String, Consumer<String>> handler) {
		ClientCommandRegistrationCallback.EVENT.register((dispatcher, registryAccess) -> dispatcher.register(
			ClientCommands.literal(root)
				.executes(ctx -> {
					handler.accept("", McCommands::message);
					return 1;
				})
				.then(ClientCommands.argument("args", StringArgumentType.greedyString())
					.executes(ctx -> {
						handler.accept(StringArgumentType.getString(ctx, "args"), McCommands::message);
						return 1;
					}))));
	}

	/** A line in the player's chat, shown only to them. */
	public static void message(String text) {
		Minecraft mc = Minecraft.getInstance();
		mc.execute(() -> mc.gui.hud.getChat().addClientSystemMessage(Component.literal("[hb] " + text)));
	}
}
