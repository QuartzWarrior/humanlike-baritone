package hb.common.io;

import hb.common.HbCommon;
import hb.proto.v1.Envelope;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * Java half of the cross-language contract test; pipeline/tests/test_hbr.py is the Python half.
 * After an intentional schema change, regenerate with
 * {@code ./gradlew :mod:common:test -Phb.updateGolden=true}.
 */
class HbrGoldenTest {
	private static final Path TESTDATA = Path.of(System.getProperty("hb.testdata", "../../proto/testdata"));
	private static final Path GOLDEN_RAW = TESTDATA.resolve("golden-v1.hbr");
	private static final Path GOLDEN_ZSTD = TESTDATA.resolve("golden-v1.hbr.zst");
	private static final int ZSTD_LEVEL = 3;

	@Test
	void serializationMatchesGolden() throws IOException {
		byte[] actual = raw(GoldenSamples.envelopes());
		if (Boolean.getBoolean("hb.updateGolden")) {
			Files.createDirectories(TESTDATA);
			Files.write(GOLDEN_RAW, actual);
			Files.write(GOLDEN_ZSTD, zstd(GoldenSamples.envelopes()));
		}
		assertArrayEquals(Files.readAllBytes(GOLDEN_RAW), actual,
			"serialized records differ from " + GOLDEN_RAW + "; rerun with -Phb.updateGolden=true if the schema change is intentional");
	}

	@Test
	void committedZstdGoldenDecodes() throws IOException {
		try (InputStream in = Files.newInputStream(GOLDEN_ZSTD); HbrReader reader = HbrReader.zstd(in)) {
			assertEquals(GoldenSamples.envelopes(), reader.readAll());
		}
	}

	@Test
	void rawAndZstdRoundTrip() throws IOException {
		List<Envelope> envelopes = GoldenSamples.envelopes();
		try (HbrReader reader = HbrReader.raw(new ByteArrayInputStream(raw(envelopes)))) {
			assertEquals(envelopes, reader.readAll());
		}
		try (HbrReader reader = HbrReader.zstd(new ByteArrayInputStream(zstd(envelopes)))) {
			assertEquals(envelopes, reader.readAll());
		}
	}

	@Test
	void everyRecordCarriesTheCurrentSchema() {
		assertEquals(1, HbCommon.SCHEMA_VERSION);
		GoldenSamples.envelopes().forEach(e -> assertEquals(HbCommon.SCHEMA_VERSION, e.getSchema()));
	}

	private static byte[] raw(List<Envelope> envelopes) throws IOException {
		ByteArrayOutputStream bytes = new ByteArrayOutputStream();
		try (HbrWriter writer = HbrWriter.raw(bytes)) {
			for (Envelope envelope : envelopes) {
				writer.write(envelope);
			}
		}
		return bytes.toByteArray();
	}

	private static byte[] zstd(List<Envelope> envelopes) throws IOException {
		ByteArrayOutputStream bytes = new ByteArrayOutputStream();
		try (HbrWriter writer = HbrWriter.zstd(bytes, ZSTD_LEVEL)) {
			for (Envelope envelope : envelopes) {
				writer.write(envelope);
			}
		}
		return bytes.toByteArray();
	}
}
