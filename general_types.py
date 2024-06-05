from typing import Any, Callable, Literal


Data = dict[str, Any]

DataType = Literal[
    "user.init",
    "seat.status",
    "game.settings", "game.status",
]

Stage = Literal["waiting", "gaming"]
