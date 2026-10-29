package hb.common.io;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.protobuf.util.JsonFormat;
import hb.common.automation.HbAutomation;
import hb.proto.v1.Envelope;
import hb.proto.v1.SessionHeader;
import hb.proto.v1.Source;

import java.io.BufferedWriter;
import java.io.Closeable;
import java.io.FilterOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.EnumMap;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Writes one session directory:
 * <pre>
 *   sessions/&lt;session-id&gt;/header.json         SessionHeader, proto3 JSON mapping
 *   sessions/&lt;session-id&gt;/chunk-0000.hbr.zst  records, a new chunk every 5 minutes of session time
 *   sessions/&lt;session-id&gt;/stats.json          written on close; its absence marks a crashed session
 *   sessions/&lt;session-id&gt;/mirror.jsonl        optional, every record as JSON (round-trip tests)
 * </pre>
 * Not thread-safe: one writer thread owns it. Rolling chunks bounds what a crash can lose to the
 * open chunk.
 */
public final class SessionWriter implements Closeable {
	public static final long CHUNK_NS = 5L * 60 * 1_000_000_000;
	public static final int ZSTD_LEVEL = 3;
	private static final Gson GSON = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();

	private final Path dir;
	private final SessionHeader header;
	private final HbAutomation.Claim botClaim;
	private final BufferedWriter mirror;
	private final JsonFormat.Printer mirrorPrinter = JsonFormat.printer().omittingInsignificantWhitespace();
	private final Map<Envelope.BodyCase, Long> recordsByType = new EnumMap<>(Envelope.BodyCase.class);

	private HbrWriter chunk;
	private CountingOutputStream chunkBytes;
	private int chunkIndex = -1;
	private long chunkStartTns;
	private long closedChunkBytes;
	private long rawBytes;
	private long firstTns = -1;
	private long lastTns = -1;

	private SessionWriter(Path dir, SessionHeader header, HbAutomation.Claim botClaim, boolean mirror) throws IOException {
		this.dir = dir;
		this.header = header;
		this.botClaim = botClaim;
		this.mirror = mirror ? Files.newBufferedWriter(dir.resolve("mirror.jsonl"), StandardCharsets.UTF_8) : null;
	}

	/**
	 * Creates sessions/&lt;header.session_id&gt;/ under {@code sessionsRoot} and writes header.json.
	 * Bot sessions need a live automation claim; human sessions must not have one.
	 */
	public static SessionWriter open(Path sessionsRoot, SessionHeader header, HbAutomation.Claim botClaim, boolean mirror) throws IOException {
		boolean bot = header.getSource() == Source.SOURCE_BOT;
		if (header.getSource() != Source.SOURCE_HUMAN && !bot) {
			throw new IllegalArgumentException("session source must be human or bot");
		}
		if (bot != (botClaim != null)) {
			throw new IllegalStateException(bot ? "bot sessions need an automation claim" : "human sessions can't be written under an automation claim");
		}
		Path dir = sessionsRoot.resolve(header.getSessionId());
		Files.createDirectories(dir);
		String json = JsonFormat.printer().alwaysPrintFieldsWithNoPresence().print(header);
		Files.writeString(dir.resolve("header.json"), json + "\n", StandardCharsets.UTF_8);
		return new SessionWriter(dir, header, botClaim, mirror);
	}

	public Path dir() {
		return dir;
	}

	public void write(Envelope envelope) throws IOException {
		if (botClaim != null && !botClaim.isOpen()) {
			throw new IllegalStateException("automation claim released; bot session can't take more records");
		}
		long t = envelope.getTNs();
		if (chunk == null || t - chunkStartTns >= CHUNK_NS) {
			rollChunk(t);
		}
		chunk.write(envelope);
		rawBytes += envelope.getSerializedSize();
		recordsByType.merge(envelope.getBodyCase(), 1L, Long::sum);
		if (firstTns < 0) {
			firstTns = t;
		}
		lastTns = Math.max(lastTns, t);
		if (mirror != null) {
			mirror.write(mirrorPrinter.print(envelope));
			mirror.newLine();
		}
	}

	private void rollChunk(long t) throws IOException {
		closeChunk();
		chunkIndex++;
		chunkStartTns = t;
		Path file = dir.resolve(String.format(Locale.ROOT, "chunk-%04d.hbr.zst", chunkIndex));
		chunkBytes = new CountingOutputStream(Files.newOutputStream(file));
		chunk = HbrWriter.zstd(chunkBytes, ZSTD_LEVEL);
	}

	private void closeChunk() throws IOException {
		if (chunk != null) {
			chunk.close();
			closedChunkBytes += chunkBytes.count;
			chunk = null;
		}
	}

	/** Compressed bytes on disk so far, including the open chunk's flushed part. */
	public long bytesWritten() {
		return closedChunkBytes + (chunkBytes != null && chunk != null ? chunkBytes.count : 0);
	}

	public long records() {
		return recordsByType.values().stream().mapToLong(Long::longValue).sum();
	}

	@Override
	public void close() throws IOException {
		close(Map.of());
	}

	/** Closes the open chunk and writes stats.json, merging in the caller's stats. */
	public void close(Map<String, ?> extraStats) throws IOException {
		closeChunk();
		if (mirror != null) {
			mirror.close();
		}
		Map<String, Object> stats = new LinkedHashMap<>();
		stats.put("session_id", header.getSessionId());
		stats.put("source", header.getSource().name());
		stats.put("chunks", chunkIndex + 1);
		stats.put("bytes", closedChunkBytes);
		stats.put("raw_bytes", rawBytes);
		stats.put("records", records());
		Map<String, Long> byType = new LinkedHashMap<>();
		recordsByType.forEach((type, n) -> byType.put(type.name().toLowerCase(Locale.ROOT), n));
		stats.put("records_by_type", byType);
		stats.put("first_t_ns", Math.max(firstTns, 0));
		stats.put("last_t_ns", Math.max(lastTns, 0));
		stats.putAll(extraStats);
		Files.writeString(dir.resolve("stats.json"), GSON.toJson(stats) + "\n", StandardCharsets.UTF_8);
	}

	private static final class CountingOutputStream extends FilterOutputStream {
		long count;

		CountingOutputStream(OutputStream out) {
			super(out);
		}

		@Override
		public void write(int b) throws IOException {
			out.write(b);
			count++;
		}

		@Override
		public void write(byte[] b, int off, int len) throws IOException {
			out.write(b, off, len);
			count += len;
		}
	}
}
