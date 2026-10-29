package hb.common.mc;

import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;

import java.util.List;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import java.util.function.IntSupplier;
import java.util.function.LongConsumer;
import java.util.function.Supplier;

/**
 * hb's on-screen bits. Drawing goes only through Fabric's HUD API and vanilla screen widgets, so it
 * works on both 26.2 renderers (OpenGL and Vulkan).
 */
public final class McUi {
	public static final int DOT_SIZE = 4;
	public static final int DOT_X = 3;
	public static final int DOT_Y = 3;

	private McUi() {
	}

	/**
	 * A small dot in the top-left corner; {@code color} returns ARGB, or 0 to hide it. The time each
	 * frame's draw takes is reported to {@code costNs}, so it counts against the caller's budget.
	 */
	public static void registerHudDot(String namespace, String path, IntSupplier color, LongConsumer costNs) {
		HudElementRegistry.addLast(Identifier.fromNamespaceAndPath(namespace, path), (graphics, delta) -> {
			long start = System.nanoTime();
			int argb = color.getAsInt();
			if (argb != 0) {
				drawDot(graphics, DOT_X, DOT_Y, argb);
			}
			costNs.accept(System.nanoTime() - start);
		});
	}

	private static void drawDot(GuiGraphicsExtractor graphics, int x, int y, int argb) {
		graphics.fill(x - 1, y - 1, x + DOT_SIZE + 1, y + DOT_SIZE + 1, 0x80000000);
		graphics.fill(x, y, x + DOT_SIZE, y + DOT_SIZE, argb);
	}

	/** What the status screen shows; implemented by the mod, free of Minecraft types. */
	public interface StatusModel {
		String title();

		/** ARGB of the state dot. */
		int dotColor();

		/** Shown when hovering the dot, e.g. "Paused: screen: ...PauseScreen". */
		String dotTooltip();

		List<String> lines();

		List<Toggle> toggles();
	}

	public record Toggle(String label, BooleanSupplier get, Consumer<Boolean> set) {
	}

	public static void openStatusScreen(Supplier<StatusModel> model) {
		Minecraft mc = Minecraft.getInstance();
		mc.gui.setScreen(new StatusScreen(mc.gui.screen(), model));
	}

	private static final class StatusScreen extends Screen implements McScreens.PausesRecording {
		private final Screen parent;
		private final Supplier<StatusModel> model;

		StatusScreen(Screen parent, Supplier<StatusModel> model) {
			super(Component.literal(model.get().title()));
			this.parent = parent;
			this.model = model;
		}

		@Override
		protected void init() {
			List<Toggle> toggles = model.get().toggles();
			int y = height - 36 - 24 * toggles.size();
			for (Toggle toggle : toggles) {
				Button button = Button.builder(label(toggle), b -> {
					toggle.set().accept(!toggle.get().getAsBoolean());
					b.setMessage(label(toggle));
				}).bounds(width / 2 - 100, y, 200, 20).build();
				addRenderableWidget(button);
				y += 24;
			}
			addRenderableWidget(Button.builder(Component.translatable("gui.done"), b -> onClose()).bounds(width / 2 - 100, y + 4, 200, 20).build());
		}

		private static Component label(Toggle toggle) {
			return Component.literal(toggle.label() + ": " + (toggle.get().getAsBoolean() ? "ON" : "OFF"));
		}

		@Override
		public void extractRenderState(GuiGraphicsExtractor graphics, int mouseX, int mouseY, float a) {
			super.extractRenderState(graphics, mouseX, mouseY, a);
			StatusModel m = model.get();
			int x = width / 2 - 100;
			int y = 30;
			graphics.text(font, title, x, 12, 0xFFFFFFFF);
			int dotX = x;
			int dotY = y + 2;
			drawDot(graphics, dotX, dotY, m.dotColor());
			graphics.text(font, m.dotTooltip(), x + 10, y, 0xFFE0E0E0);
			if (mouseX >= dotX - 2 && mouseX <= dotX + DOT_SIZE + 2 && mouseY >= dotY - 2 && mouseY <= dotY + DOT_SIZE + 2) {
				graphics.setTooltipForNextFrame(Component.literal(m.dotTooltip()), mouseX, mouseY);
			}
			y += 16;
			for (String line : m.lines()) {
				graphics.text(font, line, x, y, 0xFFA0A0A0);
				y += 11;
			}
		}

		@Override
		public void onClose() {
			minecraft.gui.setScreen(parent);
		}
	}
}
