# Humanlike Baritone

Baritone keeps deciding where to go and what to do. How it's done (mouse paths, key timing, mining
order, hesitations, mistakes) comes from models trained on your own recordings. The finish line: on
held-out clips, a discriminator can't tell bot from human better than 55% in any action set.

Target: Minecraft Java 26.2, Fabric (Mojang names, no remapping), Java 25. Singleplayer and servers
you own only.

## Layout

```
proto/hb/v1/hb.proto     the recording schema, single source of truth for Java and Python
proto/testdata/          cross-language golden records
mod/common/              hb-common: proto bindings, session I/O, and hb.common.mc, the only package
                         that touches Minecraft or Fabric API (adapter + mixins); jar-in-jar in both mods
mod/recorder/            hb-recorder Fabric mod: pause state machine, holdback, writer thread
mod/executor/            hb-executor Fabric mod; talks to Baritone only through PlannerBackend
mod/integration/         dev-only: full stack in one client, plus client game tests
baritone-fork/           git submodule: cabaletta/baritone 26.2 at v1.19.0; our diff is in HB_CHANGES.md
pipeline/                Python package hbpipe (uv, Python 3.12): session reader, verify, inspect
.github/workflows/ci.yml Gradle build + tests, client game test, Python tests
```

## Prerequisites

- JDK 25 (Temurin recommended). `./gradlew` can be launched with any Java 17+; the Gradle daemon
  itself runs on JDK 25 (`gradle/gradle-daemon-jvm.properties`), found locally or downloaded.
- [uv](https://docs.astral.sh/uv/) for the Python pipeline. It installs Python 3.12 itself.
- After cloning: `git submodule update --init`.

## Common commands

```sh
./gradlew build                                # both mods, the Baritone fork, Java tests
./gradlew :mod:integration:runClient           # play with recorder + executor + Baritone loaded
./gradlew :mod:integration:runClientGameTest   # automated: Phase 0 + Phase 1 gate tests in a real client
./gradlew :mod:integration:runClientGameTest -Phb.graphicsBackend=vulkan   # same, on the Vulkan renderer
./gradlew :mod:integration:runClientGameTest -Phb.benchMinutes=60          # recorder performance benchmark
./gradlew :mod:integration:runClientGameTest -Phb.baselineMinutes=20       # Phase 3: plain Baritone bot sessions
./gradlew :mod:common:syncPythonProto          # after editing hb.proto: regenerate Python bindings
./gradlew :mod:common:test -Phb.updateGolden=true   # after an intentional schema change

cd pipeline && uv sync && uv run pytest
uv run hbpipe inspect <.minecraft/hb-data>     # per-session size, rate, pauses, flags, recorder cost
uv run hbpipe verify  <.minecraft/hb-data>     # Java-to-Python round trip + privacy checks
uv run hbpipe label    <.minecraft/hb-data>    # convert new sessions to Parquet and label them
uv run hbpipe discover <.minecraft/hb-data>    # cluster the play no detector claims
uv run hbpipe review   <.minecraft/hb-data>    # browser tool: check labels, fix them, name clusters
uv run hbpipe report   <.minecraft/hb-data>    # Phase 2 gate numbers
uv run hbpipe train-activities <.minecraft/hb-data>   # after reviewing: fit the activity classifier
uv run hbpipe exclude  <.minecraft/hb-data> <session> --reason "wurst search on" [--from-tick N]
                                               # keep play out of training (no args: list)
uv run hbpipe discrim  <.minecraft/hb-data>    # Phase 3: fresh discriminators, per-set report
uv run hbpipe fit-models <.minecraft/hb-data>  # Phase 4: Stage 1 models into .minecraft/hb-models/stage1
```

Mod jars land in `mod/recorder/build/libs/` and `mod/executor/build/libs/`. The executor also needs
the fork jar, `build/baritone/runtime/baritone-fabric.jar`.

## Recording

Install hb-recorder (it carries hb-common) and play. Every world visit is one session in
`.minecraft/hb-data/sessions/<uuid>/`: `header.json` (settings, key map, hashed world id),
`chunk-NNNN.hbr.zst` (a new chunk every 5 minutes) and `stats.json` (written on exit; its absence
means the game crashed). Nothing is uploaded.

| Key | |
|---|---|
| F8 | pause / resume |
| F9 | discard the last 60 s |
| F7 | status and settings (hover the dot for the pause reason) |

A small dot in the top-left corner is red while recording and gray while paused. Recording pauses
by itself for every trigger in the build spec's table (idle 10 s with the tail trimmed to 3 s,
unfocused or minimized window, pause/chat/sign/book/options/death/loading/credits screens,
creative or spectator, a movement key held 30 s with no mouse input, Baritone or any other
automation) and resumes on the next real input, under a new segment id. It also pauses when the
view stops being the player's (freecam mods, or the camera on another entity), trimming back to the
first frame where the mouse stopped turning the player. FPS under 20 for over 2 s
or a server position correction flags the segment degraded instead.

Mods that act for you (Wurst and other hack clients, auto-harvest or auto-trade mods, Litematica
easy-place) are invisible to the recorder, so whatever they do is recorded as you. Record in an
instance without them, or press F8 before turning one on.

A crash still closes the session (a shutdown hook writes out the 15 s holdback); a killed process
loses about the last 17 s, and the rest stays readable.

Privacy, enforced in `hb.common.mc` before data reaches the recorder: keys are recorded only if
bound or GUI keys (OS key-repeat events are dropped), never while a text field has focus, and typing into any text field pauses
recording until 1.5 s after the last keystroke (so keys that follow a field closing mid-word aren't
recorded either). Chat, sign, book and anvil text, item names and other players' names are never
read. World names and server addresses are stored only as salted per-install hashes.

## Labeling

`hbpipe label` converts each new session into Parquet tables under `hb-data/tables/<table>/`
(ticks, mouse, keys, buttons, gui, events, world, segments; all keyed by session, segment, tick
and t_ns) and an `index.sqlite` (sessions, segments, flags, labels, corrections), then labels it
offline in the spec's passes:

1. **Clean** (`label/clean.py`): fill tick gaps, decode flags and keys, derive speed,
   acceleration, yaw/pitch rate and jerk, ticks since landing, and, by replaying the recorded
   9x9x9 block cubes, headroom, the block underfoot and gaps below.
2. **Primitives** (`label/detectors/`): one small pure function per taxonomy set (move.*,
   look.*, mine.*, build.*, combat.*, inv.*, use.*, meta.*), each unit-tested on synthetic
   sequences.
3. **Activities** (`act.*`): 60 s windows scored by rules from break geometry and event mix.
   Once enough activity spans are checked in the review tool, `hbpipe train-activities` fits a
   LightGBM classifier on the same window features plus the rule scores, and the labeler lets it
   choose each window's main activity (`label/activity_model.py`).
4. **Decision traces** (`label/traces.py`): for each mining job, the starting volume and the
   ordered breaks with stance, look and tool, for the mining-order model.
5. **Discovery** (`hbpipe discover`): play no detector claims becomes `other.unlabeled`; it's
   embedded from motor statistics and clustered with HDBSCAN. Naming a cluster in the review tool
   makes it a new set that claims matching play on the next `hbpipe label --force`.

The review tool opens each span with a plain-English account of what happened (when, where to,
how fast, keys, camera, what was broken, placed or hit), so a span can be judged without
remembering it, next to a top-down path; height, camera, keys, events and the labels around it
are in charts below. Verdicts are stored apart from detector output and win over it
(`label/corrections.py`). The gate: 90% precision on 200 checked spans for every set with at least
200 spans (30 for sets read straight from one event or flag, like block breaks, eating or
trading, which only a pipeline bug could get wrong), and `other.unlabeled` under 15% of active
play.

**Excluded play** (`hbpipe exclude`, `exclusions.py`): play with a hack on that shows hidden
blocks (Search, X-Ray, ESP) follows information no player has, so it stays out of training. The
recordings, tables and labels keep it; the decision traces drop mining jobs that touch an
exclusion, and every later training reader goes through `exclusions.drop_excluded`.
`hbpipe report` ends with the excluded total and the hack-client menus and freecam pauses in play
that isn't excluded; `hbpipe exclude <session> --checked "freecam only"` records that a session's
were looked into and nothing more needs excluding. The recorder can't see which hacks are on, so the clean option is recording
in an instance without hack clients.

## How the pieces connect

- **Schema.** `hb.proto` compiles with one protoc (pinned in `gradle.properties`) into Java (at
  build time) and Python (committed under `pipeline/src/hbpipe/proto`, so the pipeline needs no
  JVM). A digest file makes pytest fail if the Python bindings are stale. Records are
  varint-length-delimited `Envelope`s, zstd-compressed in `.hbr.zst` chunks. `HbrGoldenTest` (Java)
  and `test_hbr.py` (Python) build the same records independently and must both match
  `proto/testdata/golden-v1.hbr` byte for byte.
- **Baritone fork.** cabaletta builds with Gradle 8 + Unimined, so it can't join this Gradle 9 +
  Loom build. The root `buildBaritoneFork` task runs the fork's own wrapper (Fabric only) and
  publishes two artifacts: the full jar for runtime, and an API-only jar (`baritone/api/**`) that is
  the only Baritone code the mods can compile against.
- **Mods.** hb-common is nested (jar-in-jar) in both mods, with protobuf-java, protobuf-java-util
  and zstd-jni nested inside it. hb-executor declares a dependency on the `baritone` mod;
  hb-recorder doesn't, but pauses whenever Baritone is pathing.
- **Recorder threads.** The client thread captures into a lock-free single-producer ring; a
  writer thread holds records for 15 s (so an idle tail or an F9 discard can still be dropped),
  then serializes, compresses and rolls chunks.
- **Human vs bot data.** `HbAutomation` (hb-common) knows when anything but the human drives the
  player. The recorder pauses and drops records while it's active, and `SessionWriter` opens a
  bot session only under a live automation claim and a human session only without one.

## Discriminator (Phase 3)

`hbpipe discrim` measures how human the bot looks (`pipeline/src/hbpipe/discrim/`). Bot sessions
come from hb-executor's `BotRecorder`: the recorder's format with source = bot, written under an
automation claim (so hb-recorder pauses) into the same `hb-data/sessions/`. Plain Baritone drives
the player through its own input override, so the bot recorder logs those held inputs as the
tick's keys; it sends no raw mouse frames. Bot sessions are labeled like human ones but stay out
of the review tool and the Phase 2 numbers.

- **Eval set** (`eval_set.py`): per action set, up to 10 s clips of the set's ticks, human
  (verdicts applied, excluded play out) and bot, balanced per set. For now the clips are
  unmatched: the bot runs the same kinds of task (`BaselineRunsClientGameTest`: travel, trees,
  area clearing, tunnels, ores, exploring, in two spots of a fresh world, one session per task)
  in another world, because the human recordings come from a server the bot can't run on.
- **Models**, trained from scratch on every run and split by session: gradient boosting over
  per-clip statistics (camera rates, jerk, snaps, heading error, speed, jump timing, key states,
  break timing), a 1D CNN over the per-tick streams, and a GRU over each mining job's breaks.
  Raw-mouse features are computed but left out until the bot sends real input.
- **Report**: accuracy and ROC AUC per set with 95% intervals from bootstrapping sessions, the
  five features that best give the bot away, Wasserstein/KS distances per feature, written to
  `hb-data/discrim/<run>/` and shown on the review tool's Discriminator page.

## Executor (Phase 4)

hb-executor drives Baritone through real input. The fork's `IIntentSink` (`baritone-fork/HB_CHANGES.md`
#2) hands over what Baritone wants each tick, the rotation and the inputs, instead of applying them;
`Humanizer` turns that into raw mouse counts injected into the game's per-frame mouse accumulator
and key presses through `KeyMapping` (`hb.common.mc.McInput`), so the game's own sensitivity,
attack and use logic do the rest. Nothing sets a rotation or sends a packet.

- **Camera** (`humanize/CameraPlan`, `CameraController`): a reaction delay, a primary
  submovement timed by Fitts' law whose speed peaks at the player's fitted point, corrective
  submovements, a landing spread that scales with the target's size on screen, a slight bow, and
  at rest a drift (`humanize/Creep`: per-frame motion of the player's size that keeps its
  direction as often as theirs does, `tremor_same_sign`, rather than fresh noise each frame,
  which reverses about every other move), all sent as whole mouse counts. A block target is reached once the crosshair is on that
  block (a raycast, not an angle). Baritone flips between nearby candidate blocks from tick to
  tick; the camera keeps going for the block it picked until Baritone stops asking for it (or it's
  broken), and attack stays held across the flips while the crosshair is on a block Baritone just
  asked for. Parameters come from `hb-models/stage1/camera.json` (`hbpipe fit-models`,
  `pipeline/src/hbpipe/models/motor/camera.py`).
- **Walking** (`humanize/WalkStyle`, `movement.json` from `models/motor/movement.py`): the camera
  faces a point along the path at the player's walking pitch, wandering off the way of travel as
  a fitted Ornstein-Uhlenbeck process; forward is lifted now and then (a regrip); sprints start by
  the key or a double tap and become sprint-jumps on open ground at the player's re-jump timing.
  Held up (Baritone's movement keys held for half a second without getting anywhere, say on a
  corner or in a one-wide gap), the camera turns to Baritone's own heading for a couple of
  seconds, as a player looks where they're trying to go.
- **Meta** (`humanize/MetaLayer`, `meta.json` from `models/meta.py`): hesitations at the player's
  rate per context (mining, building, walking, other) and after breaks, and glances away while
  standing still, all vetoed by a safety check (steady footing, no hazard nearby).
- **Mining order** (`decide/MiningOrder`, `MiningOrderModel`, `BreakFeatures`;
  `mining_order.json` from `models/decision/mining_order.py`): in build and clear jobs (area
  clears, tunnels) the fork's `IBreakChooser` (`HB_CHANGES.md` #4) offers every block Baritone
  could break from where the player stands, and the pick is made among those within the player's
  reach (`mining_reach`) when there are any, so what's in the way goes before what's beyond it;
  after each break the player's LightGBM ranker
  (ported to Java, checked against Python to 1e-9) scores the next candidates, one is drawn by
  its softmax and kept until broken. Attack stays held from block to block over blocks the job
  is to break (and, once held, over nothing), as the player holds it: released and pressed again,
  each block would also sit out the game's 5-tick pause after a break. While a block breaks, the
  next one is drawn already and the camera leans across the block's face toward it, so the move
  after the break is short.
- **Sneaking while mining** (`humanize/MiningSneak`, `mining_sneak` in `movement.json`): stretches
  of sneaking and standing alternate while mining in place at the player's share and stretch
  lengths; standing up waits for a break, and Baritone's echo of the crouch is dropped.
- **Steps while mining** (`humanize/MiningSteps`, `mining_steps` in `movement.json`): mining in
  place in a build or clear job, the player's short steps (forward, back, aside, by their mix,
  rate and length; bursts over 40 ticks are walks and left to Baritone), each only where it lands
  on solid ground with room to stand, re-checked every tick of the step. And when the block being
  mined is further off than the player mines from (`humanize/MiningReach`, `mining_reach` in
  `movement.json`: how far from the eye the player's broken blocks were; past the 90th percentile),
  a step toward it: Baritone breaks anything within the game's reach, so left alone it works from
  the edge of reach, where a humanized aim often lands on nothing.
- **Models**: `hbpipe fit-models <hb-data>` writes all four to `hb-models/stage1/`
  (`--models camera,meta,movement,mining_order` picks some). A missing file means generic human
  defaults (Baritone's own order for mining), and the log says which models were loaded.
- **Keys** (`humanize/KeyController`): Baritone's movement keys mean "relative to the rotation I
  asked for"; they're turned into the forward/back/strafe keys that go the same way from where
  the camera actually faces, so a lagging camera means strafing, not walking off course. Presses
  land 0-2 ticks late; releases, jump and sneak are immediate (safety). Sprint is the real sprint
  key (fork change #2 hands Baritone's sprint decision over as an input), and a sprint ends by
  lifting forward for a tick. With toggle sprint, sneak, attack or use on in the player's options,
  a key is pressed only to flip it to the state wanted (a toggle key flips on every press and
  ignores releases).
- **Controls**: `/hb run <baritone command>`, `/hb stop`, `/hb strength <0..1>` (0 = plain
  Baritone), `/hb status`. Every run is recorded as a bot session.
- **Gate** (`Phase4GateClientGameTest`): travel, a strip-mine tunnel, clearing a room and chopping
  trees in deepslate and with the player's own kit (Efficiency V diamond tools, from their
  recorded break times), each run as a bot session; `-Phb.gateScale=small` for short versions, `-Phb.gateTasks=`
  for some of them, `-Phb.gateFirst=N` to start at run N+1's ground (chunks of a long gate, or one
  failed run again), `-Phb.gateStrength=0` for plain Baritone on the same ground, `-Phb.trace=true`
  for a once-a-second trace (with Baritone's current movement). The test client's run directory
  is wiped on every run, so it reads the models from `build/hb-models` (`-Phb.models=DIR`) and the
  bot sessions are copied to `build/bot-sessions/<run>/` afterwards. Copied into hb-data/sessions,
  `hbpipe discrim <hb-data> --bot humanized --bot-since "2026-10-27 18:40"` judges just the bot
  sessions recorded since a given build; `--bot-until` ends the window, to judge an earlier build
  against newer human data.
- **Where it runs**: singleplayer, a server on this computer (the connection's remote address is
  loopback: 127.0.0.0/8 or ::1; a LAN address doesn't count), or a server where the player has
  owner-level operator permission (`McInput.ownsWorld`); anywhere else `/hb run` refuses, and a
  run that leaves such a world stops.

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Scaffold: monorepo, Gradle on 26.2, fork building, proto for Java + Python, CI | gate passed locally; CI needs a GitHub remote and a pushed fork |
| 1 | Recorder: full capture, pause/AFK state machine, HUD indicator, privacy, rolling chunks | gate passed locally (OpenGL and Vulkan) |
| 2 | Conversion and labeling: Parquet, detectors, activities, traces, discovery, review tool | in progress |
| 3 | Discriminator and baseline: eval set, bot recording + baseline runs, three models, dashboard | in progress (unmatched clips for now) |
| 4 | Executor with Stage 1 models: fork hook, real input, camera and key timing, controls, server lock | in progress: all Stage 1 models wired; full gate 71/80 tasks (travel 20/20; the misses are lava, an aquifer and gravel in natural deepslate, which plain Baritone fails too, and harness setup bugs since fixed); discriminator above 75% in most movement sets |
| 5-6 | Stage 2 policy, final eval | not started |
