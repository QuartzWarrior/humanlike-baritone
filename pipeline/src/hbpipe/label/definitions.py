"""What each label set means, for the review tool: what it covers and how it is detected.

From the build spec's action taxonomy; the activity lines summarize detectors/activities.py.
"""

DEFINITIONS: dict[str, tuple[str, str]] = {
    "move.walk": (
        "Walking, no sprint",
        "Move key held, not sprinting, ground speed ~4.3 m/s",
    ),
    "move.sprint": (
        "Plain sprinting, no jumps",
        "Sprinting, on ground for 10+ ticks",
    ),
    "move.sprint_jump": (
        "Sprint-jumping in the open",
        "Sprinting with repeated jumps, no ceiling within 3 blocks",
    ),
    "move.head_hitter": (
        "Sprint-jumping under a 2-high ceiling, head bonking for speed",
        "Upward vertical collision on most jumps, solid block 2 above feet",
    ),
    "move.strafe": (
        "Side movement, diagonal strafing, A/D corrections",
        "Left/right key held with or without forward",
    ),
    "move.sneak": (
        "Crouch walking, edge safety",
        "Sneak held while moving",
    ),
    "move.step_up": (
        "Climbing 1-block rises, stairs, slabs",
        "Y increases by 0.5–1 while moving",
    ),
    "move.parkour": (
        "Jumping gaps, landing on narrow blocks",
        "Air gap under jump path, landing on a different block column",
    ),
    "move.climb": (
        "Ladders, vines, scaffolding",
        "On-climbable flag",
    ),
    "move.swim": (
        "Swimming, sprint-swimming, surfacing",
        "In-water flag",
    ),
    "move.fall": (
        "Falls, water-bucket clutches",
        "Fall distance over 4",
    ),
    "move.glide": (
        "Elytra flight, rocket timing",
        "Gliding flag",
    ),
    "move.ride": (
        "Boats, horses, minecarts, striders",
        "Vehicle present",
    ),
    "look.acquire": (
        "Moving the crosshair onto a target block or entity",
        "Mouse motion that ends with a new target held 3+ ticks",
    ),
    "look.travel": (
        "Camera while moving: leading turns, pitch drift",
        "Mouse input during movement with no target change",
    ),
    "look.idle_scan": (
        "Looking around, glancing at scenery or mobs",
        "Mouse motion while stationary with no action following",
    ),
    "look.react": (
        "Snapping toward a sound, hit, or mob",
        "Large fast turn within 500 ms of damage, sound event, or hostile entering view",
    ),
    "mine.block": (
        "Breaking one block: tool pick, face, hold, release",
        "Destroy start to block-broken event",
    ),
    "mine.strip": (
        "Strip mining: 1×2 tunnel, branch spacing, torch cadence, ore detours",
        "Long straight run of breaks at constant Y with periodic side branches",
    ),
    "mine.tunnel": (
        "Tunneling toward a destination (1×2, 2×2, 3×3)",
        "Breaks advancing in one direction, width/height consistent",
    ),
    "mine.area_clear": (
        "Hollowing out a region: next-block choice and overall pattern",
        "Breaks filling a bounded 3D volume",
    ),
    "mine.vein": (
        "Following an ore vein",
        "Consecutive breaks of the same ore, adjacent positions",
    ),
    "mine.staircase": (
        "Digging stairs up or down",
        "Breaks stepping 1 up/down per block forward",
    ),
    "mine.dig_down": (
        "Digging straight down (1×1 or 2×1)",
        "Breaks directly under feet in sequence",
    ),
    "mine.tree": (
        "Chopping trees: log order, reaching the top, leaves",
        "Log breaks in a vertical cluster",
    ),
    "mine.falling": (
        "Handling gravel and sand columns",
        "Break followed by a falling block refill",
    ),
    "mine.clear_path": (
        "Breaking obstacles while traveling",
        "Isolated breaks between movement segments",
    ),
    "build.place": (
        "Placing one block: face choice, timing",
        "Block-placed event",
    ),
    "build.bridge": (
        "Bridging (sneak, speed, diagonal variants)",
        "Placements under or beside feet over air while moving backward or sideways",
    ),
    "build.pillar": (
        "Jump-placing straight up",
        "Jump plus placement under feet in sequence",
    ),
    "build.fill": (
        "Walls, floors, and fills: placement order",
        "Placements filling a plane or volume",
    ),
    "build.light": (
        "Torch and lantern placement cadence",
        "Light-source placements, distance since last",
    ),
    "build.undo": (
        "Breaking a block you just placed (misplacement fix)",
        "Break within 5 s of placing at the same pos",
    ),
    "combat.melee": (
        "Click timing vs cooldown, crit jumps, sprint resets, strafing",
        "Attack-entity events",
    ),
    "combat.ranged": (
        "Bow, crossbow, trident: draw time, aim lead",
        "Use held with ranged item then release",
    ),
    "combat.shield": (
        "Blocking timing",
        "Use held with shield while hostile near",
    ),
    "combat.retreat": (
        "Backing off, eating, pillaring away",
        "Moving away from hostile after damage",
    ),
    "inv.hotbar": (
        "Slot switching: number keys vs scroll, timing before use",
        "Selected slot change",
    ),
    "inv.organize": (
        "Moving, shift-clicking, drag-splitting, sorting",
        "Slot clicks in player inventory",
    ),
    "inv.craft": (
        "Crafting: recipe book vs manual grid",
        "Craft result taken",
    ),
    "inv.container": (
        "Chests, furnaces, shulkers: looting and depositing",
        "Container screen open",
    ),
    "inv.eat": (
        "When and how long to eat",
        "Use held with food",
    ),
    "inv.equip": (
        "Armor, offhand swaps, tool swaps",
        "Equipment change",
    ),
    "inv.glance": (
        "Opening the inventory just to look",
        "Inventory opened and closed with no clicks",
    ),
    "use.mechanism": (
        "Doors, trapdoors, gates, buttons, levers",
        "Use on those block types",
    ),
    "use.bucket": (
        "Placing and collecting water and lava",
        "Bucket use events",
    ),
    "use.farm": (
        "Tilling, planting, harvesting, bone meal, breeding",
        "Use/break on crops, farmland, animals",
    ),
    "use.trade": (
        "Villager trading",
        "Merchant screen open",
    ),
    "use.misc": (
        "Beds, portals, fishing, enchanting, anvils, brewing, lighting fires",
        "Use on the matching block or item",
    ),
    "meta.hesitate": (
        "Short pauses under 3 s, mid-task stops",
        "Input gap 300 ms–3 s during an activity",
    ),
    "meta.fidget": (
        "Crouch spam, hotbar scrolling, camera wiggles with no purpose",
        "Repeated input with no world effect",
    ),
    "meta.mistake": (
        "Wrong slot, missed click, overshot path, misplaced block",
        "Action undone or corrected within 3 s",
    ),
    "meta.switch": (
        "Transitions between activities",
        "Activity label boundary",
    ),
    "act.travel": (
        "Going somewhere: a mostly straight path with few stops and no other work",
        "20+ m covered, net distance at least 70% of the path",
    ),
    "act.strip_mine": (
        "Strip mining: a main tunnel at one level with side branches",
        "20+ breaks in corridors on one floor, with parallel corridors or branches off the main",
    ),
    "act.area_clear": (
        "Clearing out an area: digging a ring around it, hollowing the inside",
        "A ring of breaks around an undug 4x4+ interior, a filled volume, or hollowing beside "
        "existing digging",
    ),
    "act.tunnel": (
        "Tunneling toward somewhere: one straight corridor with a steady cross-section",
        "75%+ of breaks in one straight corridor up to 3 wide and 8+ long, no branches",
    ),
    "act.tree_farm": (
        "Chopping trees",
        "4+ log breaks stacked vertically, logs and leaves at least half of all breaks",
    ),
    "act.build": (
        "Building: placing blocks that stay",
        "12+ kept placements, at least one per 3 s, at least as many as breaks",
    ),
    "act.fight": (
        "Fighting mobs or players",
        "2+ hits on hostile targets plus damage taken, a third hit, or a hostile nearby",
    ),
    "act.explore": (
        "Wandering and looking around new ground",
        "30+ m that goes nowhere, spread over 8+ areas, while looking around",
    ),
    "act.farm": (
        "Farming: harvesting, planting, tilling, breeding",
        "4+ farm actions making up half the work in the window",
    ),
    "act.manage_inventory": (
        "Sorting the inventory or chests",
        "2+ slot clicks in the inventory or a chest, without crafting or trading",
    ),
    "act.smelt_craft": (
        "Smelting and crafting at furnaces and crafting tables",
        "a crafted or smelted result taken, or 2+ clicks in those screens",
    ),
    "mine.dig_up": (
        "Digging straight up (shafts), often between pillar jumps",
        "3+ breaks above the head in sequence, climbing at least 2 blocks",
    ),
}
