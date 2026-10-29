from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class SchemaVersion(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SCHEMA_VERSION_UNSPECIFIED: _ClassVar[SchemaVersion]
    SCHEMA_VERSION_1: _ClassVar[SchemaVersion]
    SCHEMA_VERSION_CURRENT: _ClassVar[SchemaVersion]

class Direction(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    DIRECTION_UNSPECIFIED: _ClassVar[Direction]
    DIRECTION_DOWN: _ClassVar[Direction]
    DIRECTION_UP: _ClassVar[Direction]
    DIRECTION_NORTH: _ClassVar[Direction]
    DIRECTION_SOUTH: _ClassVar[Direction]
    DIRECTION_WEST: _ClassVar[Direction]
    DIRECTION_EAST: _ClassVar[Direction]

class Hand(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    HAND_UNSPECIFIED: _ClassVar[Hand]
    HAND_MAIN: _ClassVar[Hand]
    HAND_OFF: _ClassVar[Hand]

class Source(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SOURCE_UNSPECIFIED: _ClassVar[Source]
    SOURCE_HUMAN: _ClassVar[Source]
    SOURCE_BOT: _ClassVar[Source]

class ClickType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    CLICK_TYPE_UNSPECIFIED: _ClassVar[ClickType]
    CLICK_TYPE_PICKUP: _ClassVar[ClickType]
    CLICK_TYPE_QUICK_MOVE: _ClassVar[ClickType]
    CLICK_TYPE_SWAP: _ClassVar[ClickType]
    CLICK_TYPE_CLONE: _ClassVar[ClickType]
    CLICK_TYPE_THROW: _ClassVar[ClickType]
    CLICK_TYPE_QUICK_CRAFT: _ClassVar[ClickType]
    CLICK_TYPE_PICKUP_ALL: _ClassVar[ClickType]

class TickFlag(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    TICK_FLAG_NONE: _ClassVar[TickFlag]
    TICK_FLAG_ON_GROUND: _ClassVar[TickFlag]
    TICK_FLAG_SPRINTING: _ClassVar[TickFlag]
    TICK_FLAG_CROUCHING: _ClassVar[TickFlag]
    TICK_FLAG_SWIMMING: _ClassVar[TickFlag]
    TICK_FLAG_GLIDING: _ClassVar[TickFlag]
    TICK_FLAG_H_COLLISION: _ClassVar[TickFlag]
    TICK_FLAG_V_COLLISION: _ClassVar[TickFlag]
    TICK_FLAG_V_COLLISION_UP: _ClassVar[TickFlag]
    TICK_FLAG_IN_WATER: _ClassVar[TickFlag]
    TICK_FLAG_IN_LAVA: _ClassVar[TickFlag]
    TICK_FLAG_ON_CLIMBABLE: _ClassVar[TickFlag]
    TICK_FLAG_PASSENGER: _ClassVar[TickFlag]
    TICK_FLAG_USING_ITEM: _ClassVar[TickFlag]
    TICK_FLAG_BLOCKING: _ClassVar[TickFlag]
    TICK_FLAG_EYES_IN_WATER: _ClassVar[TickFlag]

class PauseReason(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PAUSE_REASON_NONE: _ClassVar[PauseReason]
    PAUSE_REASON_IDLE: _ClassVar[PauseReason]
    PAUSE_REASON_UNFOCUSED: _ClassVar[PauseReason]
    PAUSE_REASON_SCREEN: _ClassVar[PauseReason]
    PAUSE_REASON_GAME_MODE: _ClassVar[PauseReason]
    PAUSE_REASON_STUCK_KEY: _ClassVar[PauseReason]
    PAUSE_REASON_AUTOMATION: _ClassVar[PauseReason]
    PAUSE_REASON_MANUAL: _ClassVar[PauseReason]
    PAUSE_REASON_DISCONNECTED: _ClassVar[PauseReason]
    PAUSE_REASON_TEXT_INPUT: _ClassVar[PauseReason]
    PAUSE_REASON_CAMERA_DETACHED: _ClassVar[PauseReason]
SCHEMA_VERSION_UNSPECIFIED: SchemaVersion
SCHEMA_VERSION_1: SchemaVersion
SCHEMA_VERSION_CURRENT: SchemaVersion
DIRECTION_UNSPECIFIED: Direction
DIRECTION_DOWN: Direction
DIRECTION_UP: Direction
DIRECTION_NORTH: Direction
DIRECTION_SOUTH: Direction
DIRECTION_WEST: Direction
DIRECTION_EAST: Direction
HAND_UNSPECIFIED: Hand
HAND_MAIN: Hand
HAND_OFF: Hand
SOURCE_UNSPECIFIED: Source
SOURCE_HUMAN: Source
SOURCE_BOT: Source
CLICK_TYPE_UNSPECIFIED: ClickType
CLICK_TYPE_PICKUP: ClickType
CLICK_TYPE_QUICK_MOVE: ClickType
CLICK_TYPE_SWAP: ClickType
CLICK_TYPE_CLONE: ClickType
CLICK_TYPE_THROW: ClickType
CLICK_TYPE_QUICK_CRAFT: ClickType
CLICK_TYPE_PICKUP_ALL: ClickType
TICK_FLAG_NONE: TickFlag
TICK_FLAG_ON_GROUND: TickFlag
TICK_FLAG_SPRINTING: TickFlag
TICK_FLAG_CROUCHING: TickFlag
TICK_FLAG_SWIMMING: TickFlag
TICK_FLAG_GLIDING: TickFlag
TICK_FLAG_H_COLLISION: TickFlag
TICK_FLAG_V_COLLISION: TickFlag
TICK_FLAG_V_COLLISION_UP: TickFlag
TICK_FLAG_IN_WATER: TickFlag
TICK_FLAG_IN_LAVA: TickFlag
TICK_FLAG_ON_CLIMBABLE: TickFlag
TICK_FLAG_PASSENGER: TickFlag
TICK_FLAG_USING_ITEM: TickFlag
TICK_FLAG_BLOCKING: TickFlag
TICK_FLAG_EYES_IN_WATER: TickFlag
PAUSE_REASON_NONE: PauseReason
PAUSE_REASON_IDLE: PauseReason
PAUSE_REASON_UNFOCUSED: PauseReason
PAUSE_REASON_SCREEN: PauseReason
PAUSE_REASON_GAME_MODE: PauseReason
PAUSE_REASON_STUCK_KEY: PauseReason
PAUSE_REASON_AUTOMATION: PauseReason
PAUSE_REASON_MANUAL: PauseReason
PAUSE_REASON_DISCONNECTED: PauseReason
PAUSE_REASON_TEXT_INPUT: PauseReason
PAUSE_REASON_CAMERA_DETACHED: PauseReason

class Envelope(_message.Message):
    __slots__ = ("schema", "segment_id", "tick", "t_ns", "tick_state", "mouse", "button", "key", "scroll", "cursor", "slot", "event", "cube", "delta", "state", "flag", "discard")
    SCHEMA_FIELD_NUMBER: _ClassVar[int]
    SEGMENT_ID_FIELD_NUMBER: _ClassVar[int]
    TICK_FIELD_NUMBER: _ClassVar[int]
    T_NS_FIELD_NUMBER: _ClassVar[int]
    TICK_STATE_FIELD_NUMBER: _ClassVar[int]
    MOUSE_FIELD_NUMBER: _ClassVar[int]
    BUTTON_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    SCROLL_FIELD_NUMBER: _ClassVar[int]
    CURSOR_FIELD_NUMBER: _ClassVar[int]
    SLOT_FIELD_NUMBER: _ClassVar[int]
    EVENT_FIELD_NUMBER: _ClassVar[int]
    CUBE_FIELD_NUMBER: _ClassVar[int]
    DELTA_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    FLAG_FIELD_NUMBER: _ClassVar[int]
    DISCARD_FIELD_NUMBER: _ClassVar[int]
    schema: int
    segment_id: str
    tick: int
    t_ns: int
    tick_state: Tick
    mouse: MouseMove
    button: Button
    key: Key
    scroll: Scroll
    cursor: GuiCursor
    slot: SlotClick
    event: GameEvent
    cube: WorldCube
    delta: WorldDelta
    state: StateChange
    flag: SegmentFlag
    discard: Discard
    def __init__(self, schema: _Optional[int] = ..., segment_id: _Optional[str] = ..., tick: _Optional[int] = ..., t_ns: _Optional[int] = ..., tick_state: _Optional[_Union[Tick, _Mapping]] = ..., mouse: _Optional[_Union[MouseMove, _Mapping]] = ..., button: _Optional[_Union[Button, _Mapping]] = ..., key: _Optional[_Union[Key, _Mapping]] = ..., scroll: _Optional[_Union[Scroll, _Mapping]] = ..., cursor: _Optional[_Union[GuiCursor, _Mapping]] = ..., slot: _Optional[_Union[SlotClick, _Mapping]] = ..., event: _Optional[_Union[GameEvent, _Mapping]] = ..., cube: _Optional[_Union[WorldCube, _Mapping]] = ..., delta: _Optional[_Union[WorldDelta, _Mapping]] = ..., state: _Optional[_Union[StateChange, _Mapping]] = ..., flag: _Optional[_Union[SegmentFlag, _Mapping]] = ..., discard: _Optional[_Union[Discard, _Mapping]] = ...) -> None: ...

class Vec3(_message.Message):
    __slots__ = ("x", "y", "z")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    Z_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    z: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., z: _Optional[float] = ...) -> None: ...

class BlockPos(_message.Message):
    __slots__ = ("x", "y", "z")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    Z_FIELD_NUMBER: _ClassVar[int]
    x: int
    y: int
    z: int
    def __init__(self, x: _Optional[int] = ..., y: _Optional[int] = ..., z: _Optional[int] = ...) -> None: ...

class Enchantment(_message.Message):
    __slots__ = ("id", "level")
    ID_FIELD_NUMBER: _ClassVar[int]
    LEVEL_FIELD_NUMBER: _ClassVar[int]
    id: str
    level: int
    def __init__(self, id: _Optional[str] = ..., level: _Optional[int] = ...) -> None: ...

class ItemStack(_message.Message):
    __slots__ = ("id", "count", "damage", "max_damage", "enchantments")
    ID_FIELD_NUMBER: _ClassVar[int]
    COUNT_FIELD_NUMBER: _ClassVar[int]
    DAMAGE_FIELD_NUMBER: _ClassVar[int]
    MAX_DAMAGE_FIELD_NUMBER: _ClassVar[int]
    ENCHANTMENTS_FIELD_NUMBER: _ClassVar[int]
    id: str
    count: int
    damage: int
    max_damage: int
    enchantments: _containers.RepeatedCompositeFieldContainer[Enchantment]
    def __init__(self, id: _Optional[str] = ..., count: _Optional[int] = ..., damage: _Optional[int] = ..., max_damage: _Optional[int] = ..., enchantments: _Optional[_Iterable[_Union[Enchantment, _Mapping]]] = ...) -> None: ...

class MouseMove(_message.Message):
    __slots__ = ("dx", "dy", "d_yaw", "d_pitch")
    DX_FIELD_NUMBER: _ClassVar[int]
    DY_FIELD_NUMBER: _ClassVar[int]
    D_YAW_FIELD_NUMBER: _ClassVar[int]
    D_PITCH_FIELD_NUMBER: _ClassVar[int]
    dx: float
    dy: float
    d_yaw: float
    d_pitch: float
    def __init__(self, dx: _Optional[float] = ..., dy: _Optional[float] = ..., d_yaw: _Optional[float] = ..., d_pitch: _Optional[float] = ...) -> None: ...

class Button(_message.Message):
    __slots__ = ("button", "down", "mods", "in_gui")
    BUTTON_FIELD_NUMBER: _ClassVar[int]
    DOWN_FIELD_NUMBER: _ClassVar[int]
    MODS_FIELD_NUMBER: _ClassVar[int]
    IN_GUI_FIELD_NUMBER: _ClassVar[int]
    button: int
    down: bool
    mods: int
    in_gui: bool
    def __init__(self, button: _Optional[int] = ..., down: _Optional[bool] = ..., mods: _Optional[int] = ..., in_gui: _Optional[bool] = ...) -> None: ...

class Key(_message.Message):
    __slots__ = ("key", "scancode", "down", "binding", "mods", "repeat")
    KEY_FIELD_NUMBER: _ClassVar[int]
    SCANCODE_FIELD_NUMBER: _ClassVar[int]
    DOWN_FIELD_NUMBER: _ClassVar[int]
    BINDING_FIELD_NUMBER: _ClassVar[int]
    MODS_FIELD_NUMBER: _ClassVar[int]
    REPEAT_FIELD_NUMBER: _ClassVar[int]
    key: int
    scancode: int
    down: bool
    binding: str
    mods: int
    repeat: bool
    def __init__(self, key: _Optional[int] = ..., scancode: _Optional[int] = ..., down: _Optional[bool] = ..., binding: _Optional[str] = ..., mods: _Optional[int] = ..., repeat: _Optional[bool] = ...) -> None: ...

class Scroll(_message.Message):
    __slots__ = ("dx", "dy")
    DX_FIELD_NUMBER: _ClassVar[int]
    DY_FIELD_NUMBER: _ClassVar[int]
    dx: float
    dy: float
    def __init__(self, dx: _Optional[float] = ..., dy: _Optional[float] = ...) -> None: ...

class GuiCursor(_message.Message):
    __slots__ = ("x", "y")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ...) -> None: ...

class SlotClick(_message.Message):
    __slots__ = ("slot_id", "button", "click_type", "slot_item", "carried", "menu", "container_id")
    SLOT_ID_FIELD_NUMBER: _ClassVar[int]
    BUTTON_FIELD_NUMBER: _ClassVar[int]
    CLICK_TYPE_FIELD_NUMBER: _ClassVar[int]
    SLOT_ITEM_FIELD_NUMBER: _ClassVar[int]
    CARRIED_FIELD_NUMBER: _ClassVar[int]
    MENU_FIELD_NUMBER: _ClassVar[int]
    CONTAINER_ID_FIELD_NUMBER: _ClassVar[int]
    slot_id: int
    button: int
    click_type: ClickType
    slot_item: ItemStack
    carried: ItemStack
    menu: str
    container_id: int
    def __init__(self, slot_id: _Optional[int] = ..., button: _Optional[int] = ..., click_type: _Optional[_Union[ClickType, str]] = ..., slot_item: _Optional[_Union[ItemStack, _Mapping]] = ..., carried: _Optional[_Union[ItemStack, _Mapping]] = ..., menu: _Optional[str] = ..., container_id: _Optional[int] = ...) -> None: ...

class Tick(_message.Message):
    __slots__ = ("x", "y", "z", "vx", "vy", "vz", "yaw", "pitch", "flags", "keys_down", "slot", "held_item", "hit", "destroy_progress", "attack_cooldown", "health", "food", "fps", "entities", "saturation", "air", "fall_distance", "effects", "main_hand", "off_hand", "armor", "breaking_pos", "context")
    X_FIELD_NUMBER: _ClassVar[int]
    Y_FIELD_NUMBER: _ClassVar[int]
    Z_FIELD_NUMBER: _ClassVar[int]
    VX_FIELD_NUMBER: _ClassVar[int]
    VY_FIELD_NUMBER: _ClassVar[int]
    VZ_FIELD_NUMBER: _ClassVar[int]
    YAW_FIELD_NUMBER: _ClassVar[int]
    PITCH_FIELD_NUMBER: _ClassVar[int]
    FLAGS_FIELD_NUMBER: _ClassVar[int]
    KEYS_DOWN_FIELD_NUMBER: _ClassVar[int]
    SLOT_FIELD_NUMBER: _ClassVar[int]
    HELD_ITEM_FIELD_NUMBER: _ClassVar[int]
    HIT_FIELD_NUMBER: _ClassVar[int]
    DESTROY_PROGRESS_FIELD_NUMBER: _ClassVar[int]
    ATTACK_COOLDOWN_FIELD_NUMBER: _ClassVar[int]
    HEALTH_FIELD_NUMBER: _ClassVar[int]
    FOOD_FIELD_NUMBER: _ClassVar[int]
    FPS_FIELD_NUMBER: _ClassVar[int]
    ENTITIES_FIELD_NUMBER: _ClassVar[int]
    SATURATION_FIELD_NUMBER: _ClassVar[int]
    AIR_FIELD_NUMBER: _ClassVar[int]
    FALL_DISTANCE_FIELD_NUMBER: _ClassVar[int]
    EFFECTS_FIELD_NUMBER: _ClassVar[int]
    MAIN_HAND_FIELD_NUMBER: _ClassVar[int]
    OFF_HAND_FIELD_NUMBER: _ClassVar[int]
    ARMOR_FIELD_NUMBER: _ClassVar[int]
    BREAKING_POS_FIELD_NUMBER: _ClassVar[int]
    CONTEXT_FIELD_NUMBER: _ClassVar[int]
    x: float
    y: float
    z: float
    vx: float
    vy: float
    vz: float
    yaw: float
    pitch: float
    flags: int
    keys_down: int
    slot: int
    held_item: str
    hit: HitResult
    destroy_progress: float
    attack_cooldown: float
    health: float
    food: int
    fps: float
    entities: _containers.RepeatedCompositeFieldContainer[NearbyEntity]
    saturation: float
    air: int
    fall_distance: float
    effects: _containers.RepeatedCompositeFieldContainer[Effect]
    main_hand: ItemStack
    off_hand: ItemStack
    armor: _containers.RepeatedCompositeFieldContainer[ItemStack]
    breaking_pos: BlockPos
    context: TickContext
    def __init__(self, x: _Optional[float] = ..., y: _Optional[float] = ..., z: _Optional[float] = ..., vx: _Optional[float] = ..., vy: _Optional[float] = ..., vz: _Optional[float] = ..., yaw: _Optional[float] = ..., pitch: _Optional[float] = ..., flags: _Optional[int] = ..., keys_down: _Optional[int] = ..., slot: _Optional[int] = ..., held_item: _Optional[str] = ..., hit: _Optional[_Union[HitResult, _Mapping]] = ..., destroy_progress: _Optional[float] = ..., attack_cooldown: _Optional[float] = ..., health: _Optional[float] = ..., food: _Optional[int] = ..., fps: _Optional[float] = ..., entities: _Optional[_Iterable[_Union[NearbyEntity, _Mapping]]] = ..., saturation: _Optional[float] = ..., air: _Optional[int] = ..., fall_distance: _Optional[float] = ..., effects: _Optional[_Iterable[_Union[Effect, _Mapping]]] = ..., main_hand: _Optional[_Union[ItemStack, _Mapping]] = ..., off_hand: _Optional[_Union[ItemStack, _Mapping]] = ..., armor: _Optional[_Iterable[_Union[ItemStack, _Mapping]]] = ..., breaking_pos: _Optional[_Union[BlockPos, _Mapping]] = ..., context: _Optional[_Union[TickContext, _Mapping]] = ...) -> None: ...

class HitResult(_message.Message):
    __slots__ = ("type", "block_pos", "face", "location", "entity_id", "entity_type", "block")
    class Type(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        MISS: _ClassVar[HitResult.Type]
        BLOCK: _ClassVar[HitResult.Type]
        ENTITY: _ClassVar[HitResult.Type]
    MISS: HitResult.Type
    BLOCK: HitResult.Type
    ENTITY: HitResult.Type
    TYPE_FIELD_NUMBER: _ClassVar[int]
    BLOCK_POS_FIELD_NUMBER: _ClassVar[int]
    FACE_FIELD_NUMBER: _ClassVar[int]
    LOCATION_FIELD_NUMBER: _ClassVar[int]
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    ENTITY_TYPE_FIELD_NUMBER: _ClassVar[int]
    BLOCK_FIELD_NUMBER: _ClassVar[int]
    type: HitResult.Type
    block_pos: BlockPos
    face: Direction
    location: Vec3
    entity_id: int
    entity_type: str
    block: str
    def __init__(self, type: _Optional[_Union[HitResult.Type, str]] = ..., block_pos: _Optional[_Union[BlockPos, _Mapping]] = ..., face: _Optional[_Union[Direction, str]] = ..., location: _Optional[_Union[Vec3, _Mapping]] = ..., entity_id: _Optional[int] = ..., entity_type: _Optional[str] = ..., block: _Optional[str] = ...) -> None: ...

class NearbyEntity(_message.Message):
    __slots__ = ("id", "type", "dx", "dy", "dz", "vx", "vy", "vz", "health", "yaw")
    ID_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    DX_FIELD_NUMBER: _ClassVar[int]
    DY_FIELD_NUMBER: _ClassVar[int]
    DZ_FIELD_NUMBER: _ClassVar[int]
    VX_FIELD_NUMBER: _ClassVar[int]
    VY_FIELD_NUMBER: _ClassVar[int]
    VZ_FIELD_NUMBER: _ClassVar[int]
    HEALTH_FIELD_NUMBER: _ClassVar[int]
    YAW_FIELD_NUMBER: _ClassVar[int]
    id: int
    type: str
    dx: float
    dy: float
    dz: float
    vx: float
    vy: float
    vz: float
    health: float
    yaw: float
    def __init__(self, id: _Optional[int] = ..., type: _Optional[str] = ..., dx: _Optional[float] = ..., dy: _Optional[float] = ..., dz: _Optional[float] = ..., vx: _Optional[float] = ..., vy: _Optional[float] = ..., vz: _Optional[float] = ..., health: _Optional[float] = ..., yaw: _Optional[float] = ...) -> None: ...

class Effect(_message.Message):
    __slots__ = ("id", "amplifier", "duration_ticks")
    ID_FIELD_NUMBER: _ClassVar[int]
    AMPLIFIER_FIELD_NUMBER: _ClassVar[int]
    DURATION_TICKS_FIELD_NUMBER: _ClassVar[int]
    id: str
    amplifier: int
    duration_ticks: int
    def __init__(self, id: _Optional[str] = ..., amplifier: _Optional[int] = ..., duration_ticks: _Optional[int] = ...) -> None: ...

class TickContext(_message.Message):
    __slots__ = ("dimension", "biome", "block_light", "sky_light", "day_time", "screen", "ping_ms")
    DIMENSION_FIELD_NUMBER: _ClassVar[int]
    BIOME_FIELD_NUMBER: _ClassVar[int]
    BLOCK_LIGHT_FIELD_NUMBER: _ClassVar[int]
    SKY_LIGHT_FIELD_NUMBER: _ClassVar[int]
    DAY_TIME_FIELD_NUMBER: _ClassVar[int]
    SCREEN_FIELD_NUMBER: _ClassVar[int]
    PING_MS_FIELD_NUMBER: _ClassVar[int]
    dimension: str
    biome: str
    block_light: int
    sky_light: int
    day_time: int
    screen: str
    ping_ms: int
    def __init__(self, dimension: _Optional[str] = ..., biome: _Optional[str] = ..., block_light: _Optional[int] = ..., sky_light: _Optional[int] = ..., day_time: _Optional[int] = ..., screen: _Optional[str] = ..., ping_ms: _Optional[int] = ...) -> None: ...

class GameEvent(_message.Message):
    __slots__ = ("block_broken", "block_placed", "item_picked_up", "item_dropped", "result_taken", "damage_dealt", "damage_taken", "death", "respawn", "dimension_change", "screen_opened", "screen_closed", "chat_sent", "destroy_started", "destroy_aborted", "position_corrected", "sound_heard", "interaction")
    BLOCK_BROKEN_FIELD_NUMBER: _ClassVar[int]
    BLOCK_PLACED_FIELD_NUMBER: _ClassVar[int]
    ITEM_PICKED_UP_FIELD_NUMBER: _ClassVar[int]
    ITEM_DROPPED_FIELD_NUMBER: _ClassVar[int]
    RESULT_TAKEN_FIELD_NUMBER: _ClassVar[int]
    DAMAGE_DEALT_FIELD_NUMBER: _ClassVar[int]
    DAMAGE_TAKEN_FIELD_NUMBER: _ClassVar[int]
    DEATH_FIELD_NUMBER: _ClassVar[int]
    RESPAWN_FIELD_NUMBER: _ClassVar[int]
    DIMENSION_CHANGE_FIELD_NUMBER: _ClassVar[int]
    SCREEN_OPENED_FIELD_NUMBER: _ClassVar[int]
    SCREEN_CLOSED_FIELD_NUMBER: _ClassVar[int]
    CHAT_SENT_FIELD_NUMBER: _ClassVar[int]
    DESTROY_STARTED_FIELD_NUMBER: _ClassVar[int]
    DESTROY_ABORTED_FIELD_NUMBER: _ClassVar[int]
    POSITION_CORRECTED_FIELD_NUMBER: _ClassVar[int]
    SOUND_HEARD_FIELD_NUMBER: _ClassVar[int]
    INTERACTION_FIELD_NUMBER: _ClassVar[int]
    block_broken: BlockBroken
    block_placed: BlockPlaced
    item_picked_up: ItemPickedUp
    item_dropped: ItemDropped
    result_taken: ResultTaken
    damage_dealt: DamageDealt
    damage_taken: DamageTaken
    death: Death
    respawn: Respawn
    dimension_change: DimensionChange
    screen_opened: ScreenOpened
    screen_closed: ScreenClosed
    chat_sent: ChatSent
    destroy_started: DestroyStarted
    destroy_aborted: DestroyAborted
    position_corrected: PositionCorrected
    sound_heard: SoundHeard
    interaction: Interaction
    def __init__(self, block_broken: _Optional[_Union[BlockBroken, _Mapping]] = ..., block_placed: _Optional[_Union[BlockPlaced, _Mapping]] = ..., item_picked_up: _Optional[_Union[ItemPickedUp, _Mapping]] = ..., item_dropped: _Optional[_Union[ItemDropped, _Mapping]] = ..., result_taken: _Optional[_Union[ResultTaken, _Mapping]] = ..., damage_dealt: _Optional[_Union[DamageDealt, _Mapping]] = ..., damage_taken: _Optional[_Union[DamageTaken, _Mapping]] = ..., death: _Optional[_Union[Death, _Mapping]] = ..., respawn: _Optional[_Union[Respawn, _Mapping]] = ..., dimension_change: _Optional[_Union[DimensionChange, _Mapping]] = ..., screen_opened: _Optional[_Union[ScreenOpened, _Mapping]] = ..., screen_closed: _Optional[_Union[ScreenClosed, _Mapping]] = ..., chat_sent: _Optional[_Union[ChatSent, _Mapping]] = ..., destroy_started: _Optional[_Union[DestroyStarted, _Mapping]] = ..., destroy_aborted: _Optional[_Union[DestroyAborted, _Mapping]] = ..., position_corrected: _Optional[_Union[PositionCorrected, _Mapping]] = ..., sound_heard: _Optional[_Union[SoundHeard, _Mapping]] = ..., interaction: _Optional[_Union[Interaction, _Mapping]] = ...) -> None: ...

class BlockBroken(_message.Message):
    __slots__ = ("pos", "block", "tool", "ticks_taken", "face")
    POS_FIELD_NUMBER: _ClassVar[int]
    BLOCK_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    TICKS_TAKEN_FIELD_NUMBER: _ClassVar[int]
    FACE_FIELD_NUMBER: _ClassVar[int]
    pos: BlockPos
    block: str
    tool: str
    ticks_taken: int
    face: Direction
    def __init__(self, pos: _Optional[_Union[BlockPos, _Mapping]] = ..., block: _Optional[str] = ..., tool: _Optional[str] = ..., ticks_taken: _Optional[int] = ..., face: _Optional[_Union[Direction, str]] = ...) -> None: ...

class BlockPlaced(_message.Message):
    __slots__ = ("pos", "clicked_face", "block", "clicked_pos", "hand", "hit")
    POS_FIELD_NUMBER: _ClassVar[int]
    CLICKED_FACE_FIELD_NUMBER: _ClassVar[int]
    BLOCK_FIELD_NUMBER: _ClassVar[int]
    CLICKED_POS_FIELD_NUMBER: _ClassVar[int]
    HAND_FIELD_NUMBER: _ClassVar[int]
    HIT_FIELD_NUMBER: _ClassVar[int]
    pos: BlockPos
    clicked_face: Direction
    block: str
    clicked_pos: BlockPos
    hand: Hand
    hit: Vec3
    def __init__(self, pos: _Optional[_Union[BlockPos, _Mapping]] = ..., clicked_face: _Optional[_Union[Direction, str]] = ..., block: _Optional[str] = ..., clicked_pos: _Optional[_Union[BlockPos, _Mapping]] = ..., hand: _Optional[_Union[Hand, str]] = ..., hit: _Optional[_Union[Vec3, _Mapping]] = ...) -> None: ...

class ItemPickedUp(_message.Message):
    __slots__ = ("item",)
    ITEM_FIELD_NUMBER: _ClassVar[int]
    item: ItemStack
    def __init__(self, item: _Optional[_Union[ItemStack, _Mapping]] = ...) -> None: ...

class ItemDropped(_message.Message):
    __slots__ = ("item", "whole_stack")
    ITEM_FIELD_NUMBER: _ClassVar[int]
    WHOLE_STACK_FIELD_NUMBER: _ClassVar[int]
    item: ItemStack
    whole_stack: bool
    def __init__(self, item: _Optional[_Union[ItemStack, _Mapping]] = ..., whole_stack: _Optional[bool] = ...) -> None: ...

class ResultTaken(_message.Message):
    __slots__ = ("kind", "result", "menu", "quick_move")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        OTHER: _ClassVar[ResultTaken.Kind]
        CRAFT: _ClassVar[ResultTaken.Kind]
        SMELT: _ClassVar[ResultTaken.Kind]
        TRADE: _ClassVar[ResultTaken.Kind]
        SMITH: _ClassVar[ResultTaken.Kind]
        STONECUT: _ClassVar[ResultTaken.Kind]
        ENCHANT: _ClassVar[ResultTaken.Kind]
        ANVIL: _ClassVar[ResultTaken.Kind]
        BREW: _ClassVar[ResultTaken.Kind]
    OTHER: ResultTaken.Kind
    CRAFT: ResultTaken.Kind
    SMELT: ResultTaken.Kind
    TRADE: ResultTaken.Kind
    SMITH: ResultTaken.Kind
    STONECUT: ResultTaken.Kind
    ENCHANT: ResultTaken.Kind
    ANVIL: ResultTaken.Kind
    BREW: ResultTaken.Kind
    KIND_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    MENU_FIELD_NUMBER: _ClassVar[int]
    QUICK_MOVE_FIELD_NUMBER: _ClassVar[int]
    kind: ResultTaken.Kind
    result: ItemStack
    menu: str
    quick_move: bool
    def __init__(self, kind: _Optional[_Union[ResultTaken.Kind, str]] = ..., result: _Optional[_Union[ItemStack, _Mapping]] = ..., menu: _Optional[str] = ..., quick_move: _Optional[bool] = ...) -> None: ...

class DamageDealt(_message.Message):
    __slots__ = ("entity_id", "entity_type", "amount", "critical", "sprinting", "attack_cooldown")
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    ENTITY_TYPE_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    CRITICAL_FIELD_NUMBER: _ClassVar[int]
    SPRINTING_FIELD_NUMBER: _ClassVar[int]
    ATTACK_COOLDOWN_FIELD_NUMBER: _ClassVar[int]
    entity_id: int
    entity_type: str
    amount: float
    critical: bool
    sprinting: bool
    attack_cooldown: float
    def __init__(self, entity_id: _Optional[int] = ..., entity_type: _Optional[str] = ..., amount: _Optional[float] = ..., critical: _Optional[bool] = ..., sprinting: _Optional[bool] = ..., attack_cooldown: _Optional[float] = ...) -> None: ...

class DamageTaken(_message.Message):
    __slots__ = ("amount", "source", "attacker_id", "attacker_type")
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    ATTACKER_ID_FIELD_NUMBER: _ClassVar[int]
    ATTACKER_TYPE_FIELD_NUMBER: _ClassVar[int]
    amount: float
    source: str
    attacker_id: int
    attacker_type: str
    def __init__(self, amount: _Optional[float] = ..., source: _Optional[str] = ..., attacker_id: _Optional[int] = ..., attacker_type: _Optional[str] = ...) -> None: ...

class Death(_message.Message):
    __slots__ = ("cause",)
    CAUSE_FIELD_NUMBER: _ClassVar[int]
    cause: str
    def __init__(self, cause: _Optional[str] = ...) -> None: ...

class Respawn(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class DimensionChange(_message.Message):
    __slots__ = ("from_dimension", "to_dimension")
    FROM_DIMENSION_FIELD_NUMBER: _ClassVar[int]
    TO_DIMENSION_FIELD_NUMBER: _ClassVar[int]
    from_dimension: str
    to_dimension: str
    def __init__(self, from_dimension: _Optional[str] = ..., to_dimension: _Optional[str] = ...) -> None: ...

class ScreenOpened(_message.Message):
    __slots__ = ("screen", "menu", "gui_width", "gui_height")
    SCREEN_FIELD_NUMBER: _ClassVar[int]
    MENU_FIELD_NUMBER: _ClassVar[int]
    GUI_WIDTH_FIELD_NUMBER: _ClassVar[int]
    GUI_HEIGHT_FIELD_NUMBER: _ClassVar[int]
    screen: str
    menu: str
    gui_width: int
    gui_height: int
    def __init__(self, screen: _Optional[str] = ..., menu: _Optional[str] = ..., gui_width: _Optional[int] = ..., gui_height: _Optional[int] = ...) -> None: ...

class ScreenClosed(_message.Message):
    __slots__ = ("screen",)
    SCREEN_FIELD_NUMBER: _ClassVar[int]
    screen: str
    def __init__(self, screen: _Optional[str] = ...) -> None: ...

class ChatSent(_message.Message):
    __slots__ = ("is_command",)
    IS_COMMAND_FIELD_NUMBER: _ClassVar[int]
    is_command: bool
    def __init__(self, is_command: _Optional[bool] = ...) -> None: ...

class DestroyStarted(_message.Message):
    __slots__ = ("pos", "face", "block", "tool")
    POS_FIELD_NUMBER: _ClassVar[int]
    FACE_FIELD_NUMBER: _ClassVar[int]
    BLOCK_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    pos: BlockPos
    face: Direction
    block: str
    tool: str
    def __init__(self, pos: _Optional[_Union[BlockPos, _Mapping]] = ..., face: _Optional[_Union[Direction, str]] = ..., block: _Optional[str] = ..., tool: _Optional[str] = ...) -> None: ...

class DestroyAborted(_message.Message):
    __slots__ = ("pos", "progress")
    POS_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    pos: BlockPos
    progress: float
    def __init__(self, pos: _Optional[_Union[BlockPos, _Mapping]] = ..., progress: _Optional[float] = ...) -> None: ...

class PositionCorrected(_message.Message):
    __slots__ = ("before", "after")
    BEFORE_FIELD_NUMBER: _ClassVar[int]
    AFTER_FIELD_NUMBER: _ClassVar[int]
    before: Vec3
    after: Vec3
    def __init__(self, before: _Optional[_Union[Vec3, _Mapping]] = ..., after: _Optional[_Union[Vec3, _Mapping]] = ...) -> None: ...

class SoundHeard(_message.Message):
    __slots__ = ("sound", "category", "dx", "dy", "dz", "volume")
    SOUND_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    DX_FIELD_NUMBER: _ClassVar[int]
    DY_FIELD_NUMBER: _ClassVar[int]
    DZ_FIELD_NUMBER: _ClassVar[int]
    VOLUME_FIELD_NUMBER: _ClassVar[int]
    sound: str
    category: str
    dx: float
    dy: float
    dz: float
    volume: float
    def __init__(self, sound: _Optional[str] = ..., category: _Optional[str] = ..., dx: _Optional[float] = ..., dy: _Optional[float] = ..., dz: _Optional[float] = ..., volume: _Optional[float] = ...) -> None: ...

class Interaction(_message.Message):
    __slots__ = ("kind", "hand", "pos", "face", "entity_id", "entity_type", "item", "block")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        UNKNOWN: _ClassVar[Interaction.Kind]
        ATTACK_BLOCK: _ClassVar[Interaction.Kind]
        USE_BLOCK: _ClassVar[Interaction.Kind]
        USE_ITEM: _ClassVar[Interaction.Kind]
        ATTACK_ENTITY: _ClassVar[Interaction.Kind]
        USE_ENTITY: _ClassVar[Interaction.Kind]
    UNKNOWN: Interaction.Kind
    ATTACK_BLOCK: Interaction.Kind
    USE_BLOCK: Interaction.Kind
    USE_ITEM: Interaction.Kind
    ATTACK_ENTITY: Interaction.Kind
    USE_ENTITY: Interaction.Kind
    KIND_FIELD_NUMBER: _ClassVar[int]
    HAND_FIELD_NUMBER: _ClassVar[int]
    POS_FIELD_NUMBER: _ClassVar[int]
    FACE_FIELD_NUMBER: _ClassVar[int]
    ENTITY_ID_FIELD_NUMBER: _ClassVar[int]
    ENTITY_TYPE_FIELD_NUMBER: _ClassVar[int]
    ITEM_FIELD_NUMBER: _ClassVar[int]
    BLOCK_FIELD_NUMBER: _ClassVar[int]
    kind: Interaction.Kind
    hand: Hand
    pos: BlockPos
    face: Direction
    entity_id: int
    entity_type: str
    item: str
    block: str
    def __init__(self, kind: _Optional[_Union[Interaction.Kind, str]] = ..., hand: _Optional[_Union[Hand, str]] = ..., pos: _Optional[_Union[BlockPos, _Mapping]] = ..., face: _Optional[_Union[Direction, str]] = ..., entity_id: _Optional[int] = ..., entity_type: _Optional[str] = ..., item: _Optional[str] = ..., block: _Optional[str] = ...) -> None: ...

class WorldCube(_message.Message):
    __slots__ = ("origin", "size", "palette", "indices")
    ORIGIN_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    PALETTE_FIELD_NUMBER: _ClassVar[int]
    INDICES_FIELD_NUMBER: _ClassVar[int]
    origin: BlockPos
    size: int
    palette: _containers.RepeatedScalarFieldContainer[str]
    indices: _containers.RepeatedScalarFieldContainer[int]
    def __init__(self, origin: _Optional[_Union[BlockPos, _Mapping]] = ..., size: _Optional[int] = ..., palette: _Optional[_Iterable[str]] = ..., indices: _Optional[_Iterable[int]] = ...) -> None: ...

class WorldDelta(_message.Message):
    __slots__ = ("origin", "changes")
    ORIGIN_FIELD_NUMBER: _ClassVar[int]
    CHANGES_FIELD_NUMBER: _ClassVar[int]
    origin: BlockPos
    changes: _containers.RepeatedCompositeFieldContainer[BlockChange]
    def __init__(self, origin: _Optional[_Union[BlockPos, _Mapping]] = ..., changes: _Optional[_Iterable[_Union[BlockChange, _Mapping]]] = ...) -> None: ...

class BlockChange(_message.Message):
    __slots__ = ("pos", "state")
    POS_FIELD_NUMBER: _ClassVar[int]
    STATE_FIELD_NUMBER: _ClassVar[int]
    pos: BlockPos
    state: str
    def __init__(self, pos: _Optional[_Union[BlockPos, _Mapping]] = ..., state: _Optional[str] = ...) -> None: ...

class StateChange(_message.Message):
    __slots__ = ("state", "reason", "reason_code")
    class S(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        RECORDING: _ClassVar[StateChange.S]
        PAUSED: _ClassVar[StateChange.S]
        STOPPED: _ClassVar[StateChange.S]
    RECORDING: StateChange.S
    PAUSED: StateChange.S
    STOPPED: StateChange.S
    STATE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    REASON_CODE_FIELD_NUMBER: _ClassVar[int]
    state: StateChange.S
    reason: str
    reason_code: PauseReason
    def __init__(self, state: _Optional[_Union[StateChange.S, str]] = ..., reason: _Optional[str] = ..., reason_code: _Optional[_Union[PauseReason, str]] = ...) -> None: ...

class SegmentFlag(_message.Message):
    __slots__ = ("kind", "reason")
    class Kind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
        __slots__ = ()
        UNKNOWN: _ClassVar[SegmentFlag.Kind]
        DEGRADED: _ClassVar[SegmentFlag.Kind]
        SUSPECT_AFK: _ClassVar[SegmentFlag.Kind]
    UNKNOWN: SegmentFlag.Kind
    DEGRADED: SegmentFlag.Kind
    SUSPECT_AFK: SegmentFlag.Kind
    KIND_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    kind: SegmentFlag.Kind
    reason: str
    def __init__(self, kind: _Optional[_Union[SegmentFlag.Kind, str]] = ..., reason: _Optional[str] = ...) -> None: ...

class Discard(_message.Message):
    __slots__ = ("from_t_ns", "to_t_ns")
    FROM_T_NS_FIELD_NUMBER: _ClassVar[int]
    TO_T_NS_FIELD_NUMBER: _ClassVar[int]
    from_t_ns: int
    to_t_ns: int
    def __init__(self, from_t_ns: _Optional[int] = ..., to_t_ns: _Optional[int] = ...) -> None: ...

class ModInfo(_message.Message):
    __slots__ = ("id", "version")
    ID_FIELD_NUMBER: _ClassVar[int]
    VERSION_FIELD_NUMBER: _ClassVar[int]
    id: str
    version: str
    def __init__(self, id: _Optional[str] = ..., version: _Optional[str] = ...) -> None: ...

class KeyBinding(_message.Message):
    __slots__ = ("name", "key", "category")
    NAME_FIELD_NUMBER: _ClassVar[int]
    KEY_FIELD_NUMBER: _ClassVar[int]
    CATEGORY_FIELD_NUMBER: _ClassVar[int]
    name: str
    key: str
    category: str
    def __init__(self, name: _Optional[str] = ..., key: _Optional[str] = ..., category: _Optional[str] = ...) -> None: ...

class SessionHeader(_message.Message):
    __slots__ = ("schema", "session_id", "source", "started_unix_ms", "game_version", "recorder_version", "mods", "fov", "mouse_sensitivity", "raw_mouse_input", "invert_y", "gui_scale", "render_distance", "fps_cap", "toggle_sprint", "toggle_crouch", "auto_jump", "keybinds", "key_order", "game_mode", "difficulty", "world_id_hash", "multiplayer", "invert_x", "toggle_attack", "toggle_use", "mouse_wheel_sensitivity", "discrete_mouse_scroll", "graphics_backend")
    SCHEMA_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    STARTED_UNIX_MS_FIELD_NUMBER: _ClassVar[int]
    GAME_VERSION_FIELD_NUMBER: _ClassVar[int]
    RECORDER_VERSION_FIELD_NUMBER: _ClassVar[int]
    MODS_FIELD_NUMBER: _ClassVar[int]
    FOV_FIELD_NUMBER: _ClassVar[int]
    MOUSE_SENSITIVITY_FIELD_NUMBER: _ClassVar[int]
    RAW_MOUSE_INPUT_FIELD_NUMBER: _ClassVar[int]
    INVERT_Y_FIELD_NUMBER: _ClassVar[int]
    GUI_SCALE_FIELD_NUMBER: _ClassVar[int]
    RENDER_DISTANCE_FIELD_NUMBER: _ClassVar[int]
    FPS_CAP_FIELD_NUMBER: _ClassVar[int]
    TOGGLE_SPRINT_FIELD_NUMBER: _ClassVar[int]
    TOGGLE_CROUCH_FIELD_NUMBER: _ClassVar[int]
    AUTO_JUMP_FIELD_NUMBER: _ClassVar[int]
    KEYBINDS_FIELD_NUMBER: _ClassVar[int]
    KEY_ORDER_FIELD_NUMBER: _ClassVar[int]
    GAME_MODE_FIELD_NUMBER: _ClassVar[int]
    DIFFICULTY_FIELD_NUMBER: _ClassVar[int]
    WORLD_ID_HASH_FIELD_NUMBER: _ClassVar[int]
    MULTIPLAYER_FIELD_NUMBER: _ClassVar[int]
    INVERT_X_FIELD_NUMBER: _ClassVar[int]
    TOGGLE_ATTACK_FIELD_NUMBER: _ClassVar[int]
    TOGGLE_USE_FIELD_NUMBER: _ClassVar[int]
    MOUSE_WHEEL_SENSITIVITY_FIELD_NUMBER: _ClassVar[int]
    DISCRETE_MOUSE_SCROLL_FIELD_NUMBER: _ClassVar[int]
    GRAPHICS_BACKEND_FIELD_NUMBER: _ClassVar[int]
    schema: int
    session_id: str
    source: Source
    started_unix_ms: int
    game_version: str
    recorder_version: str
    mods: _containers.RepeatedCompositeFieldContainer[ModInfo]
    fov: float
    mouse_sensitivity: float
    raw_mouse_input: bool
    invert_y: bool
    gui_scale: int
    render_distance: int
    fps_cap: int
    toggle_sprint: bool
    toggle_crouch: bool
    auto_jump: bool
    keybinds: _containers.RepeatedCompositeFieldContainer[KeyBinding]
    key_order: _containers.RepeatedScalarFieldContainer[str]
    game_mode: str
    difficulty: str
    world_id_hash: str
    multiplayer: bool
    invert_x: bool
    toggle_attack: bool
    toggle_use: bool
    mouse_wheel_sensitivity: float
    discrete_mouse_scroll: bool
    graphics_backend: str
    def __init__(self, schema: _Optional[int] = ..., session_id: _Optional[str] = ..., source: _Optional[_Union[Source, str]] = ..., started_unix_ms: _Optional[int] = ..., game_version: _Optional[str] = ..., recorder_version: _Optional[str] = ..., mods: _Optional[_Iterable[_Union[ModInfo, _Mapping]]] = ..., fov: _Optional[float] = ..., mouse_sensitivity: _Optional[float] = ..., raw_mouse_input: _Optional[bool] = ..., invert_y: _Optional[bool] = ..., gui_scale: _Optional[int] = ..., render_distance: _Optional[int] = ..., fps_cap: _Optional[int] = ..., toggle_sprint: _Optional[bool] = ..., toggle_crouch: _Optional[bool] = ..., auto_jump: _Optional[bool] = ..., keybinds: _Optional[_Iterable[_Union[KeyBinding, _Mapping]]] = ..., key_order: _Optional[_Iterable[str]] = ..., game_mode: _Optional[str] = ..., difficulty: _Optional[str] = ..., world_id_hash: _Optional[str] = ..., multiplayer: _Optional[bool] = ..., invert_x: _Optional[bool] = ..., toggle_attack: _Optional[bool] = ..., toggle_use: _Optional[bool] = ..., mouse_wheel_sensitivity: _Optional[float] = ..., discrete_mouse_scroll: _Optional[bool] = ..., graphics_backend: _Optional[str] = ...) -> None: ...
