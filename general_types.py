from typing import Any, Callable, Literal


AnyMethod = Callable[..., Any]

Data = dict[str, Any]

DataType = Literal[
    "room.status",
    "seat.status",
    "game.settings",
    "game.status",
    "tiles.setup",
]
