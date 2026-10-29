package hb.common.mc;

import com.mojang.blaze3d.platform.InputConstants;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.MouseHandler;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.resources.Identifier;
import org.lwjgl.glfw.GLFW;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArraySet;

/** Key mappings: which binding a key belongs to, the Tick.keys_down bit order, and hb's own keys. */
public final class McKeys {
	public static final int MAX_KEYS = 64;
	public static final int F7 = GLFW.GLFW_KEY_F7;
	public static final int F8 = GLFW.GLFW_KEY_F8;
	public static final int F9 = GLFW.GLFW_KEY_F9;

	// Keys that matter in GUIs even when unbound: Escape, Enter, Tab, arrows and modifiers.
	private static final Set<Integer> GUI_KEYS = Set.of(
		GLFW.GLFW_KEY_ESCAPE, GLFW.GLFW_KEY_ENTER, GLFW.GLFW_KEY_KP_ENTER, GLFW.GLFW_KEY_TAB,
		GLFW.GLFW_KEY_LEFT, GLFW.GLFW_KEY_RIGHT, GLFW.GLFW_KEY_UP, GLFW.GLFW_KEY_DOWN,
		GLFW.GLFW_KEY_LEFT_SHIFT, GLFW.GLFW_KEY_RIGHT_SHIFT, GLFW.GLFW_KEY_LEFT_CONTROL, GLFW.GLFW_KEY_RIGHT_CONTROL,
		GLFW.GLFW_KEY_LEFT_ALT, GLFW.GLFW_KEY_RIGHT_ALT
	);

	// Keys a stuck-key or weighted-key AFK setup holds down.
	private static final Set<String> STUCK_CANDIDATES = Set.of(
		"key.forward", "key.back", "key.left", "key.right", "key.jump", "key.sneak", "key.attack", "key.use"
	);

	private static final Set<KeyMapping> OWN = new CopyOnWriteArraySet<>();
	private static final Map<Identifier, KeyMapping.Category> CATEGORIES = new ConcurrentHashMap<>();

	private McKeys() {
	}

	/** Name of the first non-debug key mapping bound to this key, or null. */
	public static String bindingFor(KeyEvent event) {
		for (KeyMapping mapping : Minecraft.getInstance().options.keyMappings) {
			if (mapping.getCategory() != KeyMapping.Category.DEBUG && !mapping.isUnbound() && mapping.matches(event)) {
				return mapping.getName();
			}
		}
		return null;
	}

	public static boolean isGuiKey(int key) {
		return GUI_KEYS.contains(key);
	}

	/** Registers one of hb's own keys (excluded from {@link KeyOrder}). */
	public static Key register(String name, int glfwKey, String categoryNamespace, String categoryPath) {
		// Category.register throws on a second registration of the same id.
		KeyMapping.Category category = CATEGORIES.computeIfAbsent(Identifier.fromNamespaceAndPath(categoryNamespace, categoryPath), KeyMapping.Category::register);
		KeyMapping mapping = KeyMappingHelper.registerKeyMapping(new KeyMapping(name, InputConstants.Type.KEYSYM, glfwKey, category));
		OWN.add(mapping);
		return new Key(mapping);
	}

	/** A key mapping hb registered; poll it once per tick. */
	public static final class Key {
		private final KeyMapping mapping;

		private Key(KeyMapping mapping) {
			this.mapping = mapping;
		}

		public boolean consumeClick() {
			return mapping.consumeClick();
		}

		public String boundKeyName() {
			return mapping.getTranslatedKeyMessage().getString();
		}
	}

	/** The current non-debug key mappings, at most 64, in the game's own order. */
	public static KeyOrder keyOrder() {
		List<KeyMapping> mappings = new ArrayList<>();
		for (KeyMapping mapping : Minecraft.getInstance().options.keyMappings) {
			if (mapping.getCategory() != KeyMapping.Category.DEBUG && !OWN.contains(mapping) && mappings.size() < MAX_KEYS) {
				mappings.add(mapping);
			}
		}
		return new KeyOrder(mappings);
	}

	/** Bit positions for Tick.keys_down; SessionHeader.key_order lists the same names. */
	public static final class KeyOrder {
		private final KeyMapping[] mappings;
		private final List<String> names;
		private final long stuckMask;

		private KeyOrder(List<KeyMapping> list) {
			mappings = list.toArray(KeyMapping[]::new);
			List<String> n = new ArrayList<>(mappings.length);
			long stuck = 0;
			for (int i = 0; i < mappings.length; i++) {
				n.add(mappings[i].getName());
				if (STUCK_CANDIDATES.contains(mappings[i].getName())) {
					stuck |= 1L << i;
				}
			}
			names = List.copyOf(n);
			stuckMask = stuck;
		}

		public List<String> names() {
			return names;
		}

		/** Bits of the keys a stuck-key AFK setup would hold (movement, jump, sneak, attack, use). */
		public long stuckMask() {
			return stuckMask;
		}

		public long downMask() {
			long mask = 0;
			for (int i = 0; i < mappings.length; i++) {
				if (mappings[i].isDown()) {
					mask |= 1L << i;
				}
			}
			return mask;
		}

		/** Bits of the named mappings (e.g. "key.forward"); names not in this order are ignored. */
		public long maskOf(java.util.Collection<String> bindingNames) {
			long mask = 0;
			for (String name : bindingNames) {
				int i = names.indexOf(name);
				if (i >= 0) {
					mask |= 1L << i;
				}
			}
			return mask;
		}

		/** Any mapped key or any mouse button is held. */
		public boolean anyDown(long downMask) {
			MouseHandler mouse = Minecraft.getInstance().mouseHandler;
			return downMask != 0 || mouse.isLeftPressed() || mouse.isRightPressed() || mouse.isMiddlePressed();
		}
	}
}
