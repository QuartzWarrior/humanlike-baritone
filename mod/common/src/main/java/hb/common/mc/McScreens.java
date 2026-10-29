package hb.common.mc;

import hb.common.mc.mixin.AbstractRecipeBookScreenAccessor;
import hb.common.mc.mixin.CreativeModeInventoryScreenAccessor;
import hb.common.mc.mixin.RecipeBookComponentAccessor;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.components.AbstractTextAreaWidget;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.events.ContainerEventHandler;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.ChatScreen;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.CreditsAndAttributionScreen;
import net.minecraft.client.gui.screens.DeathScreen;
import net.minecraft.client.gui.screens.DisconnectedScreen;
import net.minecraft.client.gui.screens.GenericMessageScreen;
import net.minecraft.client.gui.screens.GenericWaitingScreen;
import net.minecraft.client.gui.screens.LevelLoadingScreen;
import net.minecraft.client.gui.screens.PauseScreen;
import net.minecraft.client.gui.screens.ProgressScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.WinScreen;
import net.minecraft.client.gui.screens.debug.DebugOptionsScreen;
import net.minecraft.client.gui.screens.debug.GameModeSwitcherScreen;
import net.minecraft.client.gui.screens.dialog.DialogScreen;
import net.minecraft.client.gui.screens.inventory.AbstractCommandBlockEditScreen;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.client.gui.screens.inventory.AbstractRecipeBookScreen;
import net.minecraft.client.gui.screens.inventory.AbstractSignEditScreen;
import net.minecraft.client.gui.screens.inventory.BookEditScreen;
import net.minecraft.client.gui.screens.inventory.BookSignScreen;
import net.minecraft.client.gui.screens.inventory.CreativeModeInventoryScreen;
import net.minecraft.client.gui.screens.inventory.JigsawBlockEditScreen;
import net.minecraft.client.gui.screens.inventory.StructureBlockEditScreen;
import net.minecraft.client.gui.screens.inventory.TestBlockEditScreen;
import net.minecraft.client.gui.screens.inventory.TestInstanceBlockEditScreen;
import net.minecraft.client.gui.screens.options.OptionsScreen;
import net.minecraft.client.gui.screens.options.OptionsSubScreen;

import java.util.List;

/** Which screens pause recording, and whether typing would go into a text field. */
public final class McScreens {
	/** Marker for hb's own screens; they pause recording like the options screens do. */
	public interface PausesRecording {
	}

	// Build spec pause list: pause menu, chat and command line, sign/book editing, options, death,
	// loading screens, credits. Text-editing block screens and server dialogs (which can hold text
	// inputs) are added for privacy.
	private static final List<Class<?>> PAUSE_SCREENS = List.of(
		PauseScreen.class,
		ChatScreen.class,
		AbstractSignEditScreen.class,
		BookEditScreen.class,
		BookSignScreen.class,
		OptionsScreen.class,
		OptionsSubScreen.class,
		DebugOptionsScreen.class,
		GameModeSwitcherScreen.class,
		DeathScreen.class,
		LevelLoadingScreen.class,
		GenericMessageScreen.class,
		GenericWaitingScreen.class,
		ProgressScreen.class,
		ConnectScreen.class,
		DisconnectedScreen.class,
		WinScreen.class,
		CreditsAndAttributionScreen.class,
		AbstractCommandBlockEditScreen.class,
		StructureBlockEditScreen.class,
		JigsawBlockEditScreen.class,
		TestBlockEditScreen.class,
		TestInstanceBlockEditScreen.class,
		DialogScreen.class,
		PausesRecording.class
	);

	// Screens whose whole purpose is typing text, even before a field is focused.
	private static final List<Class<?>> TEXT_SCREENS = List.of(
		ChatScreen.class,
		AbstractSignEditScreen.class,
		BookEditScreen.class,
		BookSignScreen.class,
		AbstractCommandBlockEditScreen.class,
		StructureBlockEditScreen.class,
		JigsawBlockEditScreen.class,
		TestBlockEditScreen.class,
		TestInstanceBlockEditScreen.class
	);

	private McScreens() {
	}

	public static Screen current() {
		return Minecraft.getInstance().gui.screen();
	}

	/** A pause-list screen is open, or a loading overlay covers the game. */
	public static boolean pausesRecording(Screen screen) {
		if (Minecraft.getInstance().gui.overlay() != null) {
			return true;
		}
		return screen != null && isAny(screen, PAUSE_SCREENS);
	}

	public static boolean isContainer(Screen screen) {
		return screen instanceof AbstractContainerScreen<?>;
	}

	public static String name(Screen screen) {
		return screen == null ? "" : screen.getClass().getName();
	}

	/**
	 * Whether key presses right now would be typed into a text field: a text-editing screen, any
	 * focused edit box in the screen's focus chain, or the recipe-book or creative search box.
	 */
	public static boolean textInputActive(Screen screen) {
		if (screen == null) {
			return false;
		}
		if (isAny(screen, TEXT_SCREENS)) {
			return true;
		}
		if (screen instanceof CreativeModeInventoryScreen creative) {
			EditBox search = ((CreativeModeInventoryScreenAccessor) creative).hb$searchBox();
			if (search != null && search.isFocused() && search.isVisible()) {
				return true;
			}
		}
		if (screen instanceof AbstractRecipeBookScreen<?> withBook) {
			EditBox search = ((RecipeBookComponentAccessor) ((AbstractRecipeBookScreenAccessor) withBook).hb$recipeBookComponent()).hb$searchBox();
			if (search != null && search.isFocused()) {
				return true;
			}
		}
		GuiEventListener focused = screen.getFocused();
		for (int depth = 0; focused != null && depth < 16; depth++) {
			if (focused instanceof EditBox || focused instanceof AbstractTextAreaWidget) {
				return true;
			}
			focused = focused instanceof ContainerEventHandler container ? container.getFocused() : null;
		}
		return false;
	}

	private static boolean isAny(Object o, List<Class<?>> types) {
		for (Class<?> type : types) {
			if (type.isInstance(o)) {
				return true;
			}
		}
		return false;
	}
}
