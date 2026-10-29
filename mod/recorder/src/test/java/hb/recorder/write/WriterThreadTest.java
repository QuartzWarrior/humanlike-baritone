package hb.recorder.write;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.protobuf.util.JsonFormat;
import hb.common.io.HbrReader;
import hb.proto.v1.Envelope;
import hb.proto.v1.SessionHeader;
import hb.proto.v1.Source;
import hb.proto.v1.Tick;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class WriterThreadTest {
	private static final long S = 1_000_000_000L;

	@Test
	void writesHeaderRollingChunksAndStats(@TempDir Path root) throws Exception {
		SpscRing<Object> ring = new SpscRing<>(1 << 16);
		AtomicLong clock = new AtomicLong();
		WriterThread writer = new WriterThread(ring, clock::get);
		writer.start();

		SessionHeader header = SessionHeader.newBuilder().setSchema(1).setSessionId("s-test").setSource(Source.SOURCE_HUMAN)
			.setGameVersion("26.2").addKeyOrder("key.forward").build();
		ring.offer(new WriterThread.Open(root, header, 0, true));
		int seconds = 12 * 60;
		for (int s = 0; s < seconds; s++) {
			ring.offer(Envelope.newBuilder().setSchema(1).setSegmentId("seg-0001").setTick(s * 20L).setTNs(s * S)
				.setTickState(Tick.newBuilder().setX(s).setY(64)).build());
		}
		ring.offer(new WriterThread.Trim((seconds - 8) * S)); // idle tail: drop the last 7 records
		ring.offer(new WriterThread.Close(Map.of("closed_by", "test")));
		writer.shutdown(30_000);

		Path dir = root.resolve("s-test");
		SessionHeader readBack = parseHeader(dir.resolve("header.json"));
		assertEquals(header, readBack);

		List<Path> chunks;
		try (Stream<Path> files = Files.list(dir)) {
			chunks = files.filter(p -> p.getFileName().toString().endsWith(".hbr.zst")).sorted().toList();
		}
		assertEquals(List.of("chunk-0000.hbr.zst", "chunk-0001.hbr.zst", "chunk-0002.hbr.zst"),
			chunks.stream().map(p -> p.getFileName().toString()).toList(), "a new chunk every 5 minutes of session time");

		List<Envelope> all = new ArrayList<>();
		for (Path chunk : chunks) {
			try (InputStream in = Files.newInputStream(chunk); HbrReader reader = HbrReader.zstd(in)) {
				all.addAll(reader.readAll());
			}
		}
		assertEquals(seconds - 7, all.size());
		for (int i = 0; i < all.size(); i++) {
			assertEquals(i * S, all.get(i).getTNs());
		}

		JsonObject stats = JsonParser.parseString(Files.readString(dir.resolve("stats.json"))).getAsJsonObject();
		assertEquals(seconds - 7, stats.get("records").getAsLong());
		assertEquals(3, stats.get("chunks").getAsInt());
		assertEquals(7, stats.get("holdback_trimmed_records").getAsLong());
		assertEquals("test", stats.get("closed_by").getAsString());
		assertTrue(stats.get("bytes").getAsLong() > 0);

		List<String> mirror = Files.readAllLines(dir.resolve("mirror.jsonl"), StandardCharsets.UTF_8);
		assertEquals(seconds - 7, mirror.size());
		Envelope.Builder first = Envelope.newBuilder();
		JsonFormat.parser().merge(mirror.get(0), first);
		assertEquals(all.get(0), first.build());
	}

	@Test
	void holdbackFollowsTheWallClockWhileTheRingIsQuiet(@TempDir Path root) throws Exception {
		SpscRing<Object> ring = new SpscRing<>(64);
		AtomicLong clock = new AtomicLong(1000 * S);
		WriterThread writer = new WriterThread(ring, clock::get);
		writer.start();
		ring.offer(new WriterThread.Open(root, SessionHeader.newBuilder().setSessionId("quiet").setSource(Source.SOURCE_HUMAN).build(), 1000 * S, false));
		ring.offer(Envelope.newBuilder().setTNs(0).setTickState(Tick.getDefaultInstance()).build());
		Thread.sleep(100);
		assertEquals(0, writer.recordsWritten(), "still held");
		clock.addAndGet(16 * S);
		long deadline = System.nanoTime() + 5 * S;
		while (writer.recordsWritten() == 0 && System.nanoTime() < deadline) {
			Thread.sleep(10);
		}
		assertEquals(1, writer.recordsWritten(), "written once 15 s passed, with no new records to push it out");
		writer.shutdown(10_000);
		assertTrue(Files.exists(root.resolve("quiet/chunk-0000.hbr.zst")));
		assertTrue(Files.exists(root.resolve("quiet/stats.json")), "shutdown closes the open session");
	}

	private static SessionHeader parseHeader(Path file) throws Exception {
		SessionHeader.Builder b = SessionHeader.newBuilder();
		JsonFormat.parser().merge(Files.readString(file, StandardCharsets.UTF_8), b);
		return b.build();
	}
}
