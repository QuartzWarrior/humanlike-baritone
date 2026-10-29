package hb.common.mc;

import org.junit.jupiter.api.Test;

import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.net.UnknownHostException;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class McInputTest {
	private static InetSocketAddress at(String ip) throws UnknownHostException {
		return new InetSocketAddress(InetAddress.getByName(ip), 25565);
	}

	@Test
	void aServerOnThisComputerIsALoopbackAddress() throws UnknownHostException {
		assertTrue(McInput.isLoopback(at("127.0.0.1")));
		assertTrue(McInput.isLoopback(at("127.1.2.3")));
		assertTrue(McInput.isLoopback(at("::1")));
	}

	@Test
	void anyOtherAddressIsNot() throws UnknownHostException {
		assertFalse(McInput.isLoopback(at("192.168.1.20")), "a LAN address can be anyone's computer");
		assertFalse(McInput.isLoopback(at("10.0.0.5")));
		assertFalse(McInput.isLoopback(at("8.8.8.8")));
		assertFalse(McInput.isLoopback(InetSocketAddress.createUnresolved("localhost", 25565)), "only a resolved address counts");
		assertFalse(McInput.isLoopback(new SocketAddress() {}), "the integrated server's in-memory channel isn't a socket");
		assertFalse(McInput.isLoopback(null));
	}
}
