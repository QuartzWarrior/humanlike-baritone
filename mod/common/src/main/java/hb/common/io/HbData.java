package hb.common.io;

import net.fabricmc.loader.api.FabricLoader;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.HexFormat;

/**
 * .minecraft/hb-data, where every recording lives. Nothing in hb uploads it. Also owns the
 * per-install salt used to hash world names, server addresses and anything else that could identify
 * a place or a person.
 */
public final class HbData {
	private static byte[] salt;

	private HbData() {
	}

	public static Path root() {
		return FabricLoader.getInstance().getGameDir().resolve("hb-data");
	}

	public static Path sessions() {
		return root().resolve("sessions");
	}

	/**
	 * .minecraft/hb-models: model parameters the pipeline fits (Stage 1 JSON, Stage 2 ONNX). The
	 * dev client's game tests point {@code hb.models.dir} elsewhere, as their run directory is
	 * wiped on every run.
	 */
	public static Path models() {
		String dir = System.getProperty("hb.models.dir", "");
		return dir.isBlank() ? FabricLoader.getInstance().getGameDir().resolve("hb-models") : Path.of(dir);
	}

	/** Salted SHA-256 (first 16 bytes, hex). The salt is random per install and never leaves it. */
	public static synchronized String hash(String kind, String value) {
		try {
			MessageDigest sha = MessageDigest.getInstance("SHA-256");
			sha.update(salt());
			sha.update((kind + "\0" + value).getBytes(StandardCharsets.UTF_8));
			return HexFormat.of().formatHex(sha.digest(), 0, 16);
		} catch (NoSuchAlgorithmException e) {
			throw new IllegalStateException(e);
		}
	}

	private static byte[] salt() {
		if (salt == null) {
			Path file = root().resolve("install-salt");
			try {
				if (Files.exists(file)) {
					salt = HexFormat.of().parseHex(Files.readString(file, StandardCharsets.UTF_8).strip());
				} else {
					salt = new byte[32];
					new SecureRandom().nextBytes(salt);
					Files.createDirectories(root());
					Files.writeString(file, HexFormat.of().formatHex(salt) + "\n", StandardCharsets.UTF_8);
				}
			} catch (IOException e) {
				throw new UncheckedIOException(e);
			}
		}
		return salt;
	}
}
