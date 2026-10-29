package hb.recorder;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import net.fabricmc.loader.api.FabricLoader;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** config/hb-recorder.json. Edited from the status screen (F7). */
public final class RecorderConfig {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-recorder");
	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

	/** Build spec: creative and spectator pause recording, configurable. */
	public boolean pauseInCreativeOrSpectator = true;
	public boolean showHudDot = true;

	private static Path file() {
		return FabricLoader.getInstance().getConfigDir().resolve("hb-recorder.json");
	}

	public static RecorderConfig load() {
		Path file = file();
		if (Files.exists(file)) {
			try {
				RecorderConfig config = GSON.fromJson(Files.readString(file, StandardCharsets.UTF_8), RecorderConfig.class);
				if (config != null) {
					return config;
				}
			} catch (IOException | RuntimeException e) {
				LOGGER.warn("Couldn't read {}; using defaults", file, e);
			}
		}
		RecorderConfig config = new RecorderConfig();
		config.save();
		return config;
	}

	public void save() {
		try {
			Files.createDirectories(file().getParent());
			Files.writeString(file(), GSON.toJson(this) + "\n", StandardCharsets.UTF_8);
		} catch (IOException e) {
			LOGGER.warn("Couldn't write {}", file(), e);
		}
	}
}
