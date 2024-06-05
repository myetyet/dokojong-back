from typing import Any, Callable, Literal


AnyMethod = Callable[..., Any]

Data = dict[str, Any]

DataType = Literal[
    "user.init",
    "seat.status",
    "game.settings", "game.status",
]

Stage = Literal["waiting", "gaming"]
