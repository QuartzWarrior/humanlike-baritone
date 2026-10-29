package hb.common.automation;

import hb.common.io.SessionWriter;
import hb.proto.v1.Envelope;
import hb.proto.v1.SessionHeader;
import hb.proto.v1.Source;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicBoolean;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class HbAutomationTest {
	@AfterEach
	void reset() {
		HbAutomation.reset();
	}

	@Test
	void claimsMarkAutomationActiveUntilClosed() {
		assertFalse(HbAutomation.isActive());
		try (HbAutomation.Claim claim = HbAutomation.claim("hb-executor")) {
			assertTrue(HbAutomation.isActive());
			assertEquals("hb-executor", HbAutomation.activeOwner());
			assertTrue(claim.isOpen());
		}
		assertFalse(HbAutomation.isActive());
		assertNull(HbAutomation.activeOwner());
	}

	@Test
	void probesAreReadAtPollTime() {
		AtomicBoolean pathing = new AtomicBoolean();
		HbAutomation.addProbe("baritone", pathing::get);
		HbAutomation.poll();
		assertFalse(HbAutomation.isActive());
		pathing.set(true);
		assertFalse(HbAutomation.isActive(), "cached until the next poll");
		HbAutomation.poll();
		assertEquals("baritone", HbAutomation.activeOwner());
	}

	@Test
	void aThrowingProbeDoesNotBlockRecording() {
		HbAutomation.addProbe("broken", () -> {
			throw new IllegalStateException("no world");
		});
		HbAutomation.poll();
		assertFalse(HbAutomation.isActive());
	}

	@Test
	void humanSessionsCannotOpenUnderAClaimAndBotSessionsNeedOne(@TempDir Path root) throws Exception {
		SessionHeader human = SessionHeader.newBuilder().setSessionId("h").setSource(Source.SOURCE_HUMAN).build();
		SessionHeader bot = SessionHeader.newBuilder().setSessionId("b").setSource(Source.SOURCE_BOT).build();
		assertThrows(IllegalStateException.class, () -> SessionWriter.open(root, bot, null, false));
		HbAutomation.Claim claim = HbAutomation.claim("hb-executor");
		assertThrows(IllegalStateException.class, () -> SessionWriter.open(root, human, claim, false));

		SessionWriter botWriter = SessionWriter.open(root, bot, claim, false);
		assertDoesNotThrow(() -> botWriter.write(Envelope.newBuilder().setTNs(1).build()));
		claim.close();
		assertThrows(IllegalStateException.class, () -> botWriter.write(Envelope.newBuilder().setTNs(2).build()),
			"a released claim ends the bot session");
		botWriter.close();
	}
}
