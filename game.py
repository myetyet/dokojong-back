import asyncio
import inspect
from collections import deque
from functools import partial
from typing_extensions import Self

from fastapi.websockets import WebSocket

from general_types import AnyMethod, Data, DataType
from user import User


class Room:
    # init
    def __init__(self, id: str, seat_number: int = 2) -> None:  # change to 4 for release
        self.id = id
        self.gaming = False
        self.order_issuer = 0
        self.users: dict[str, User] = {}
        self.seats: list[User | None] = [None for _ in range(seat_number)]
        self.operator: User | None = None
        self.settings = {"quick_game": True}
        # following members will be initialized when game starts
        self.leader: User
        self.last_message: DataType
        self.doors_opened: list[bool]
        self.player_active: list[bool]
        self.player_scores: list[list[int]]
        self.player_tiles: list[list[bool | None]]
        self.player_dogs: list[int]


    # Decorators
    def with_lock(func: AnyMethod):
        """Run async methods with an async lock."""
        lock = asyncio.Lock()
        async def decorator(*args, **kwargs):
            async with lock:
                return await func(*args, **kwargs)
        return decorator

    handlers: dict[str, AnyMethod] = {}

    def add_handler(event: str, registry: dict[str, AnyMethod] = handlers):
        """Register an event with the handler. Must not pass `registry`."""
        def decorator(func: AnyMethod):
            registry[event] = func
            return func
        return decorator
    
    def check_active(func: AnyMethod):
        """Check whether the current user is active."""
        async def decorator(self: Self, target: User, *args, **kwargs):
            if target.is_player and self.player_active[target.seat]:  # target.seat > -1
                return await func(self, target, *args, **kwargs)
        return decorator

    def check_operator(postive: bool = True):
        """Check whether the current user is the operator (postive) or not (negative)."""
        def decorator_factory(func: AnyMethod):
            async def decorator(self: Self, target: User, *args, **kwargs):
                if (target is self.operator) == postive:
                    return await func(self, target, *args, **kwargs)
            return decorator
        return decorator_factory

    def check_gaming(positive: bool):
        """Check whether the game has started (positive) or not (negative)."""
        def decorator_factory(func: AnyMethod):
            async def decorator(self: Self, *args, **kwargs):
                if self.gaming == positive:
                    return await func(self, *args, **kwargs)
            return decorator
        return decorator_factory

    def make_handler(func: AnyMethod):
        """Make a customized handler a standard one."""
        param_types: dict[str, type] = {}
        for name, param in inspect.signature(func).parameters.items():
            if name == "self" or name == "me":
                continue
            if param.annotation is inspect.Parameter.empty:  # not sure whether to use `==` or `is`
                raise TypeError(f'Argument "{name}" in function "{func.__name__}" has no annotation.')
            param_types[name] = param.annotation
        async def decorator(self: Self, target: User, data: Data):
            params = {}
            for name, annot in param_types.items():
                if name not in data or not isinstance(data[name], annot):
                    return
                params[name] = data[name]
            return await func(self, target, **params)
        return decorator


    # General methods
    def get_seat_status(self, me: User) -> list[Data | None]:
        status_list = []
        for player in self.seats:
            if player is None:
                status_list.append(None)
            else:
                status_list.append({
                    "nickname": player.nickname,
                    "online": player.is_online,
                    "me": player is me,
                    "operator": player is self.operator,
                })
        return status_list

    def get_game_status(self) -> Data:
        return {
            "leader": self.leader.seat,
            "doors": self.doors_opened,
            "scores": self.player_scores,
            "tiles": self.player_tiles,
        }

    async def send_data_to(self, user: User, data_type: DataType) -> None:
        send_data = partial(user.send_data, data_type)
        match data_type:
            case "room.status":
                await send_data({"gaming": self.gaming})
            case "seat.status":
                await send_data({"status": self.get_seat_status(me=user)})
            case "game.settings":
                await send_data(self.settings)
            case "game.status":
                await send_data(self.get_game_status())
            case "tiles.setup":
                await send_data({"active": self.player_active})
            case "dog.place":
                await send_data({"position": self.player_dogs[user.seat] if user.is_player else -1})  # user.seat > -1
            case _:
                raise RuntimeError(f"No such data type: {data_type}")

    async def broadcast_data(self, status_type: DataType) -> None:
        tasks = [
            asyncio.create_task(self.send_data_to(user, status_type))
            for user in self.users.values()
        ]
        await asyncio.gather(*tasks)

    async def register_user(self, ws: WebSocket, user_id: str) -> User:
        if user_id in self.users:
            user = self.users[user_id]
            if ws is user.ws:
                return user
            await user.update_ws(ws)
        else:
            user = User(ws)
            self.users[user_id] = user
        await self.send_data_to(user, "room.status")
        return user

    async def unregister_user(self, user_id: str) -> None:
        if user_id in self.users:
            user = self.users[user_id]
            if user.is_player:
                user.ws = None
                await self.broadcast_data("seat.status")
            else:
                del self.users[user_id]

    async def handle_data(self, target: User, data: Data) -> None:
        data_type = data["type"]
        if data_type in self.handlers:
            await self.handlers[data_type](self, target, data)
        else:
            print(f'Handler {data_type} not found.')

    def set_active(self, *seats: int) -> None:
        for i in range(len(self.seats)):
            self.player_active[i] = False
        for seat in seats:
            self.player_active[seat] = True


    # Handlers for waiting hall (gaming: False)
    @add_handler("hall.init")
    @check_gaming(False)
    @make_handler
    async def hall_init(self, me: User) -> None:
        if me.is_player:
            if self.operator is None:  # `user` offline and then OP leaves, left no one online and no OP
                self.operator = me
            await self.broadcast_data("seat.status")  # in case of the break-in of other users
        else:
            await self.send_data_to(me, "seat.status")

    @with_lock
    @add_handler("user.take_seat")
    @check_gaming(False)
    @make_handler
    async def take_seat(self, me: User, seat: int, nickname: str) -> None:
        if me.seat != seat and self.seats[seat] is None:
            self.seats[seat] = me
            if me.is_player:  # me.seat > -1
                self.seats[me.seat] = None
                me.take_seat(seat, nickname)
            else:
                self.order_issuer += 1
                me.take_seat(seat, nickname, self.order_issuer)
            if self.operator is None:
                self.operator = me
            await self.broadcast_data("seat.status")

    @add_handler("room.remove_seat")
    @check_operator()
    @check_gaming(False)
    @make_handler
    async def remove_seat(self, me: User, seat: int) -> None:
        if self.seats[seat] is None:
            self.seats.pop(seat)
            for i, player in enumerate(self.seats):  # adjust players' seats
                if player is not None:
                    player.seat = i
            await self.broadcast_data("seat.status")

    @add_handler("room.remove_player")
    @check_gaming(False)
    @make_handler
    async def remove_player(self, me: User, seat: int) -> None:
        player_to_remove = self.seats[seat]
        if player_to_remove is not None:
            remove_myself = player_to_remove is me
            im_operator = me is self.operator
            if remove_myself or im_operator:
                player_to_remove.leave_seat()
                self.seats[seat] = None
                if remove_myself and im_operator:  # hand over OP
                    candidate = None  # find an online candidate with min order
                    for player in self.seats:
                        if player is not None and player.is_online:
                            if candidate is None or player.order < candidate.order:
                                candidate = player
                    self.operator = candidate
                await self.broadcast_data("seat.status")

    @add_handler("room.add_seat")
    @check_operator()
    @check_gaming(False)
    @make_handler
    async def add_seat(self, me: User) -> None:
        self.seats.append(None)
        await self.broadcast_data("seat.status")

    @with_lock
    @add_handler("player.take_operator")
    @check_operator(False)
    @check_gaming(False)
    @make_handler
    async def take_operator(self, me: User) -> None:
        if self.operator is None or not self.operator.is_online:
            self.operator = me
            await self.broadcast_data("seat.status")

    @add_handler("game.change_settings")
    @check_operator()
    @check_gaming(False)
    @make_handler
    async def change_settings(self, me: User, quick: bool) -> None:
        settings_changed = False
        if quick != self.settings["quick_game"]:
            self.settings["quick_game"] = quick
            settings_changed = True
        if settings_changed:
            await self.broadcast_data("game.settings")

    @add_handler("game.start")
    @check_operator()
    @check_gaming(False)
    @make_handler
    async def start_game(self, me: User) -> None:
        players: list[User] = []
        for player in self.seats:
            if player is None:
                return
            players.append(player)
        self.gaming = True
        self.leader = players[0]
        self.doors_opened = [False] * 5
        self.player_scores = [[0, 3] for _ in range(len(players))]
        self.player_tiles = [[None] * 5 for _ in range(len(players))]
        self.player_dogs = [-1] * len(players)
        self.last_message = "tiles.setup"
        self.player_active = [True] * len(players)
        await self.broadcast_data("room.status")


    # Handlers for game board (gaming: True)
    @add_handler("board.init")
    @check_gaming(True)
    @make_handler
    async def board_init(self, me: User) -> None:
        await asyncio.gather(
            asyncio.create_task(self.send_data_to(me, "seat.status")),
            asyncio.create_task(self.send_data_to(me, "game.status")),
            asyncio.create_task(self.send_data_to(me, "dog.place")),
            asyncio.create_task(self.send_data_to(me, self.last_message))
        )

    @with_lock
    @add_handler("dog.place")
    @check_active
    @check_gaming(True)
    @make_handler
    async def place_dog(self, me: User, position: int) -> None:
        self.player_dogs[me.seat] = position
        await self.send_data_to(me, "dog.place")
        self.player_tiles[me.seat] = [None] * 5
        self.player_active[me.seat] = False
        if not any(self.player_active):
            self.last_message = "player.act"
            self.set_active(self.leader.seat)
        await asyncio.gather(
            asyncio.create_task(self.broadcast_data("game.status")),
            asyncio.create_task(self.broadcast_data(self.last_message))
        )
