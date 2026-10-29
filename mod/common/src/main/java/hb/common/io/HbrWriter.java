package hb.common.io;

import com.github.luben.zstd.ZstdOutputStream;
import hb.proto.v1.Envelope;

import java.io.BufferedOutputStream;
import java.io.Closeable;
import java.io.IOException;
import java.io.OutputStream;

/**
 * Writes an .hbr stream: {@link Envelope} records, each prefixed with its varint32 length, either
 * raw or zstd-compressed (.hbr.zst). hbpipe.convert.hbr reads the same framing.
 */
public final class HbrWriter implements Closeable {
	private final OutputStream out;

	private HbrWriter(OutputStream out) {
		this.out = out;
	}

	public static HbrWriter raw(OutputStream sink) {
		return new HbrWriter(new BufferedOutputStream(sink));
	}

	public static HbrWriter zstd(OutputStream sink, int level) throws IOException {
		return new HbrWriter(new ZstdOutputStream(new BufferedOutputStream(sink), level));
	}

	public void write(Envelope envelope) throws IOException {
		envelope.writeDelimitedTo(out);
	}

	@Override
	public void close() throws IOException {
		out.close();
	}
}
