package hb.common.io;

import com.github.luben.zstd.ZstdInputStream;
import hb.proto.v1.Envelope;

import java.io.BufferedInputStream;
import java.io.Closeable;
import java.io.IOException;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.List;

/** Reads the stream written by {@link HbrWriter}. */
public final class HbrReader implements Closeable {
	private final InputStream in;

	private HbrReader(InputStream in) {
		this.in = in;
	}

	public static HbrReader raw(InputStream source) {
		return new HbrReader(new BufferedInputStream(source));
	}

	public static HbrReader zstd(InputStream source) throws IOException {
		return new HbrReader(new ZstdInputStream(new BufferedInputStream(source)));
	}

	/** Returns the next record, or null at the end of the stream. */
	public Envelope read() throws IOException {
		return Envelope.parseDelimitedFrom(in);
	}

	public List<Envelope> readAll() throws IOException {
		List<Envelope> envelopes = new ArrayList<>();
		for (Envelope envelope = read(); envelope != null; envelope = read()) {
			envelopes.add(envelope);
		}
		return envelopes;
	}

	@Override
	public void close() throws IOException {
		in.close();
	}
}
