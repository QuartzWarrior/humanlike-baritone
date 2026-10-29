package hb.integration;

import hb.common.io.HbrReader;
import hb.proto.v1.Envelope;
import hb.proto.v1.GameEvent;
import hb.proto.v1.ResultTaken;
import hb.recorder.HbRecorderClient;
import hb.recorder.core.RecorderStateMachine.State;
import net.fabricmc.fabric.api.client.gametest.v1.FabricClientGameTest;
import net.fabricmc.fabric.api.client.gametest.v1.context.ClientGameTestContext;
import net.fabricmc.fabric.api.client.gametest.v1.context.TestSingleplayerContext;
import net.minecraft.client.gui.screens.DeathScreen;
import net.minecraft.core.BlockPos;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.InventoryMenu;
import net.minecraft.world.level.block.Blocks;
import org.lwjgl.glfw.GLFW;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.function.Predicate;
import java.util.stream.Stream;

/**
 * Every discrete event the recorder captures, triggered through the game's real paths and then
 * found on disk: block broken and destroy start, block placed, item picked up and dropped, damage
 * taken, a sound, slot clicks with a crafting result, death, respawn and a dimension change.
 */
@SuppressWarnings("UnstableApiUsage")
public final class RecorderCaptureCoverageClientGameTest implements FabricClientGameTest {
	private static final Logger LOGGER = LoggerFactory.getLogger("hb-integration-test");
	private static final long S = 1_000_000_000L;
	private ClientGameTestContext ctx;

	@Override
	public void runTest(ClientGameTestContext context) {
		if (RecorderBenchmarkClientGameTest.active() || BaselineRunsClientGameTest.active() || Phase4GateClientGameTest.active()
			|| BuildClientGameTest.active()) {
			return;
		}
		ctx = context;
		Path dir;
		try (TestSingleplayerContext sp = context.worldBuilder().create()) {
			sp.getConnection().waitForChunksRender();
			nudge();
			awaitRecording();
			dir = ctx.computeOnClient(mc -> HbRecorderClient.recorder().sessionDir());

			BlockPos feet = ctx.computeOnClient(mc -> mc.player.blockPosition());
			sp.getServer().runCommand("give @a oak_log");
			sp.getServer().runCommand("give @a diamond_pickaxe");
			sp.getServer().runCommand("give @a dirt 4");
			sleep(0.3);

			// Break a block with the pickaxe (slot 2): destroy start, progress, block broken.
			BlockPos stone = feet.east(2).above();
			sp.getServer().runCommand(String.format(Locale.ROOT, "setblock %d %d %d stone", stone.getX(), stone.getY(), stone.getZ()));
			sleep(0.3);
			ctx.getInput().pressKey(o -> o.keyHotbarSlots[1]);
			ctx.getInput().lookAt(stone);
			nudge();
			ctx.getInput().holdMouse(GLFW.GLFW_MOUSE_BUTTON_LEFT);
			ctx.waitFor(mc -> mc.level.getBlockState(stone).is(Blocks.AIR), 200);
			ctx.getInput().releaseMouse(GLFW.GLFW_MOUSE_BUTTON_LEFT);

			// Place dirt (slot 3) on the ground.
			ctx.getInput().pressKey(o -> o.keyHotbarSlots[2]);
			ctx.getInput().lookAt(feet.west(2).below());
			nudge();
			ctx.getInput().pressMouse(GLFW.GLFW_MOUSE_BUTTON_RIGHT);

			// Pick up an item, then drop one with Q.
			sp.getServer().runCommand("execute at @p run summon item ~ ~ ~ {Item:{id:\"minecraft:diamond\",count:2}}");
			ctx.waitFor(mc -> mc.player.getInventory().countItem(net.minecraft.world.item.Items.DIAMOND) == 2, 100);
			int diamondSlot = ctx.computeOnClient(mc -> mc.player.getInventory().findSlotMatchingItem(new net.minecraft.world.item.ItemStack(net.minecraft.world.item.Items.DIAMOND)));
			ctx.getInput().pressKey(o -> o.keyHotbarSlots[diamondSlot]);
			nudge();
			ctx.getInput().pressKey(o -> o.keyDrop);

			// Damage and a hostile sound nearby.
			nudge();
			sp.getServer().runCommand("damage @p 3");
			sp.getServer().runCommand("execute at @p run playsound minecraft:entity.zombie.ambient hostile @a ~3 ~ ~ 1");
			sleep(0.5);

			// Craft planks in the 2x2 grid through the same call the inventory screen makes.
			nudge();
			ctx.runOnClient(mc -> {
				int log = InventoryMenu.USE_ROW_SLOT_START; // hotbar slot 1 holds the log
				mc.gameMode.handleContainerInput(InventoryMenu.CONTAINER_ID, log, 0, ContainerInput.PICKUP, mc.player);
				mc.gameMode.handleContainerInput(InventoryMenu.CONTAINER_ID, InventoryMenu.CRAFT_SLOT_START, 0, ContainerInput.PICKUP, mc.player);
			});
			ctx.waitFor(mc -> mc.player.inventoryMenu.getSlot(InventoryMenu.RESULT_SLOT).hasItem(), 100); // the server fills the result
			ctx.runOnClient(mc -> mc.gameMode.handleContainerInput(InventoryMenu.CONTAINER_ID, InventoryMenu.RESULT_SLOT, 0, ContainerInput.QUICK_MOVE, mc.player));
			sleep(0.5);

			// Die, respawn, then travel to the Nether.
			nudge();
			sp.getServer().runCommand("kill @p");
			ctx.waitForScreen(DeathScreen.class);
			sleep(1.2); // the respawn button enables after a moment
			ctx.clickScreenButton("deathScreen.respawn");
			ctx.waitFor(mc -> mc.gui.screen() == null && mc.player != null && !mc.player.isDeadOrDying(), 200);
			nudge();
			awaitRecording();
			sp.getServer().runCommand("execute in minecraft:the_nether run tp @p 0 128 0");
			ctx.waitFor(mc -> mc.level.dimension().identifier().getPath().equals("the_nether") && mc.gui.screen() == null, 400);
			sleep(0.5);
		}
		long deadline = System.nanoTime() + 20 * S;
		while (!Files.exists(dir.resolve("stats.json")) && System.nanoTime() < deadline) {
			ctx.waitTick();
		}
		audit(dir);
	}

	private void audit(Path dir) {
		List<Envelope> records = new ArrayList<>();
		try (Stream<Path> files = Files.list(dir)) {
			for (Path chunk : files.filter(p -> p.toString().endsWith(".hbr.zst")).sorted().toList()) {
				try (InputStream in = Files.newInputStream(chunk); HbrReader reader = HbrReader.zstd(in)) {
					records.addAll(reader.readAll());
				}
			}
		} catch (Exception e) {
			throw new AssertionError(e);
		}
		List<GameEvent> events = records.stream().filter(Envelope::hasEvent).map(Envelope::getEvent).toList();
		LOGGER.info("coverage: {} records, {} events", records.size(), events.size());
		expect(events, "destroy started on stone", e -> e.hasDestroyStarted() && e.getDestroyStarted().getBlock().equals("minecraft:stone"));
		expect(events, "stone broken with the pickaxe, ticks counted", e -> e.hasBlockBroken() && e.getBlockBroken().getBlock().equals("minecraft:stone")
			&& e.getBlockBroken().getTool().equals("minecraft:diamond_pickaxe") && e.getBlockBroken().getTicksTaken() > 0);
		expect(events, "dirt placed", e -> e.hasBlockPlaced() && e.getBlockPlaced().getBlock().equals("minecraft:dirt"));
		expect(events, "diamonds picked up", e -> e.hasItemPickedUp() && e.getItemPickedUp().getItem().getId().equals("minecraft:diamond"));
		expect(events, "a diamond dropped", e -> e.hasItemDropped() && e.getItemDropped().getItem().getId().equals("minecraft:diamond"));
		expect(events, "3 damage taken", e -> e.hasDamageTaken() && Math.abs(e.getDamageTaken().getAmount() - 3) < 0.01);
		expect(events, "zombie sound heard", e -> e.hasSoundHeard() && e.getSoundHeard().getSound().equals("minecraft:entity.zombie.ambient")
			&& e.getSoundHeard().getCategory().equals("hostile"));
		expect(events, "planks crafted", e -> e.hasResultTaken() && e.getResultTaken().getKind() == ResultTaken.Kind.CRAFT
			&& e.getResultTaken().getResult().getId().equals("minecraft:oak_planks") && e.getResultTaken().getQuickMove());
		expect(events, "death", GameEvent::hasDeath);
		expect(events, "respawn", GameEvent::hasRespawn);
		expect(events, "overworld to nether", e -> e.hasDimensionChange() && e.getDimensionChange().getToDimension().equals("minecraft:the_nether"));
		expect(events, "death screen opened", e -> e.hasScreenOpened() && e.getScreenOpened().getScreen().endsWith("DeathScreen"));
		long slotClicks = records.stream().filter(Envelope::hasSlot).count();
		if (slotClicks < 3) {
			throw new AssertionError("expected 3 slot clicks, found " + slotClicks);
		}
		LOGGER.info("coverage audit passed: {} event kinds", events.stream().map(GameEvent::getKindCase).distinct().count());
	}

	private static void expect(List<GameEvent> events, String what, Predicate<GameEvent> match) {
		if (events.stream().noneMatch(match)) {
			throw new AssertionError("not on disk: " + what);
		}
	}

	private void awaitRecording() {
		long deadline = System.nanoTime() + 3 * S;
		while (ctx.computeOnClient(mc -> HbRecorderClient.recorder().state()) != State.RECORDING) {
			if (System.nanoTime() > deadline) {
				throw new AssertionError("recorder not recording: " + ctx.computeOnClient(mc -> HbRecorderClient.recorder().pauseReason()));
			}
			ctx.waitTick();
		}
	}

	private void nudge() {
		ctx.getInput().moveCursor(12, 0);
		ctx.waitTick();
		ctx.getInput().moveCursor(-12, 0);
		ctx.waitTick();
	}

	private void sleep(double seconds) {
		long end = System.nanoTime() + (long) (seconds * S);
		while (System.nanoTime() < end) {
			ctx.waitTick();
		}
	}
}
