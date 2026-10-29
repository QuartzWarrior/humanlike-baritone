package hb.common.mc;

import hb.proto.v1.BlockChange;
import hb.proto.v1.WorldCube;
import hb.proto.v1.WorldDelta;
import net.minecraft.client.Minecraft;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.core.BlockPos;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.LevelChunk;

import java.util.IdentityHashMap;
import java.util.Map;

/**
 * The 9x9x9 block cube centred on the player: a full {@link WorldCube} at segment start, then a
 * {@link WorldDelta} of changed positions each tick. When the player moves, positions entering the
 * cube count as changes, so a reader replaying cube + deltas always knows every block in view.
 */
public final class McWorldCube {
	public static final int SIZE = 9;
	private static final int HALF = SIZE / 2;
	private static final int VOLUME = SIZE * SIZE * SIZE;

	private BlockState[] states = new BlockState[VOLUME];
	private BlockState[] next = new BlockState[VOLUME];
	private final BlockPos.MutableBlockPos cursor = new BlockPos.MutableBlockPos();
	private int ox;
	private int oy;
	private int oz;
	private boolean valid;

	/** Snapshot around the player; also resets the baseline for {@link #delta()}. */
	public WorldCube full() {
		BlockPos feet = Minecraft.getInstance().player.blockPosition();
		ox = feet.getX() - HALF;
		oy = feet.getY() - HALF;
		oz = feet.getZ() - HALF;
		read(states, ox, oy, oz);
		valid = true;
		Map<BlockState, Integer> palette = new IdentityHashMap<>();
		WorldCube.Builder cube = WorldCube.newBuilder()
			.setOrigin(hb.proto.v1.BlockPos.newBuilder().setX(ox).setY(oy).setZ(oz))
			.setSize(SIZE);
		for (BlockState state : states) {
			Integer index = palette.get(state);
			if (index == null) {
				index = palette.size();
				palette.put(state, index);
				cube.addPalette(McProto.state(state));
			}
			cube.addIndices(index);
		}
		return cube.build();
	}

	/** Changes since the last full cube or delta; null if nothing changed. Call {@link #full()} first. */
	public WorldDelta delta() {
		if (!valid) {
			throw new IllegalStateException("no baseline cube");
		}
		BlockPos feet = Minecraft.getInstance().player.blockPosition();
		int nx = feet.getX() - HALF;
		int ny = feet.getY() - HALF;
		int nz = feet.getZ() - HALF;
		read(next, nx, ny, nz);
		WorldDelta.Builder delta = null;
		for (int dy = 0; dy < SIZE; dy++) {
			for (int dz = 0; dz < SIZE; dz++) {
				for (int dx = 0; dx < SIZE; dx++) {
					BlockState now = next[index(dx, dy, dz)];
					int px = nx + dx - ox;
					int py = ny + dy - oy;
					int pz = nz + dz - oz;
					boolean known = px >= 0 && px < SIZE && py >= 0 && py < SIZE && pz >= 0 && pz < SIZE;
					if (!known || states[index(px, py, pz)] != now) {
						if (delta == null) {
							delta = WorldDelta.newBuilder().setOrigin(hb.proto.v1.BlockPos.newBuilder().setX(nx).setY(ny).setZ(nz));
						}
						delta.addChanges(BlockChange.newBuilder()
							.setPos(hb.proto.v1.BlockPos.newBuilder().setX(nx + dx).setY(ny + dy).setZ(nz + dz))
							.setState(McProto.state(now)));
					}
				}
			}
		}
		BlockState[] swap = states;
		states = next;
		next = swap;
		ox = nx;
		oy = ny;
		oz = nz;
		return delta == null ? null : delta.build();
	}

	public void invalidate() {
		valid = false;
	}

	/**
	 * Reads column by column through the chunk instead of Level.getBlockState, which looks the chunk
	 * up again for every block; the cube touches at most four chunks.
	 */
	private void read(BlockState[] into, int x0, int y0, int z0) {
		ClientLevel level = Minecraft.getInstance().level;
		int minY = level.getMinY();
		int maxY = level.getMaxY();
		BlockState voidAir = Blocks.VOID_AIR.defaultBlockState();
		LevelChunk chunk = null;
		int chunkX = Integer.MIN_VALUE;
		int chunkZ = Integer.MIN_VALUE;
		for (int dz = 0; dz < SIZE; dz++) {
			int z = z0 + dz;
			for (int dx = 0; dx < SIZE; dx++) {
				int x = x0 + dx;
				if (chunk == null || x >> 4 != chunkX || z >> 4 != chunkZ) {
					chunkX = x >> 4;
					chunkZ = z >> 4;
					chunk = level.getChunk(chunkX, chunkZ);
				}
				for (int dy = 0; dy < SIZE; dy++) {
					int y = y0 + dy;
					into[index(dx, dy, dz)] = y < minY || y > maxY ? voidAir : chunk.getBlockState(cursor.set(x, y, z));
				}
			}
		}
	}

	private static int index(int dx, int dy, int dz) {
		return (dy * SIZE + dz) * SIZE + dx;
	}
}
