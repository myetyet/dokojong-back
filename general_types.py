from typing import Any, Callable, Literal


AnyMethod = Callable[..., Any]

Data = dict[str, Any]

DataType = Literal[
    "room.stage",
    "seat.status",
    "game.settings",
    "game.start",
    "game.status",
    "tiles.setup",
]

Stage = Literal["waiting", "gaming"]
