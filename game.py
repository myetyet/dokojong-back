import asyncio
import itertools
import json
import random
from typing import Any, Callable, Generator, Literal

from fastapi.websockets import WebSocket, WebSocketState


Data = dict[str, Any]
DataType = Literal[
    "user.init",
    "seat.status",
    "game.settings", "game.status",
]


class User:
    def __init__(self, ws: WebSocket, nickname: str, order: int) -> None:
        self.ws: WebSocket | None = ws
        self.nickname = nickname
        self.seat = 0
        self.order = order

    @property
    def is_online(self) -> bool:
        return False if self.ws is None else self.ws.client_state == WebSocketState.CONNECTED

    @property
    def is_player(self) -> bool:
        return self.seat > 0

    async def send_data(self, type: DataType, data: Data) -> None:
        if self.is_online:
            await self.ws.send_json({"type": type, **data})


def with_lock(func: Callable[..., Any]):
    lock = asyncio.Lock()

    async def wrapper(*args, **kwargs):
        async with lock:
            return await func(*args, **kwargs)

    return wrapper


MIN_SEAT_NUMBER = 2
MAX_SEAT_NUMBER = 5
DEFAULT_NICKNAMES = ("😀", "😄", "😁", "😆")


class Room:
    def __init__(self, id: str, seat_number: int = 4) -> None:
        self.id = id
        self.order_issuer = itertools.count(1)
        self.users: dict[str, User] = {}
        self.seats: list[User | None] = [None for _ in range(seat_number + 1)]
        self.operator: User | None = None
        self.game_start = False
        self.quick_game = True

    def seats_iter(self) -> Generator[tuple[int, User | None], None, None]:
        for i in range(1, len(self.seats)):
            yield i, self.seats[i]

    def get_seat_status(self, me: User) -> list[Data | None]:
        status_list = []
        for _, user in self.seats_iter():
            if user is None:
                status_list.append(None)
            else:
                status_list.append({
                    "nickname": user.nickname,
                    "online": user.is_online,
                    "me": user is me,
                    "operator": user is self.operator,
                })
        return status_list
    
    def get_game_settings(self) -> Data:
        return {
            "min_seats": MIN_SEAT_NUMBER,
            "max_seats": MAX_SEAT_NUMBER,
            "quick_game": self.quick_game,
        }

    def get_game_status(self) -> Data:
        return {
            "start": self.game_start,
        }

    async def send_data_to(self, user: User, data_type: DataType) -> None:
        match data_type:
            case "user.init":
                await user.send_data(data_type, {"nickname": user.nickname})
            case "seat.status":
                await user.send_data(data_type, {"status": self.get_seat_status(me=user)})
            case "game.settings":
                await user.send_data(data_type, self.get_game_settings())
            case "game.status":
                await user.send_data(data_type, self.get_game_status())
            case _:
                raise RuntimeError(f"No such data type: {data_type}")

    async def broadcast_data(self, status_type: DataType) -> None:
        tasks = [
            asyncio.create_task(self.send_data_to(user, status_type))
            for user in self.users.values()
        ]
        await asyncio.gather(*tasks)

    async def register_user(self, ws: WebSocket, user_id: str, data: Data) -> User:
        if user_id in self.users:
            user = self.users[user_id]
            if user.is_online:  # close the older `ws` when duplicated login
                await user.ws.close(reason="close.duplicated_login")
            user.ws = ws
        else:
            nickname = data["nickname"]
            if len(nickname) == 0:
                nickname = random.choice(DEFAULT_NICKNAMES)
            user = User(ws, nickname, next(self.order_issuer))
            self.users[user_id] = user
        await asyncio.gather(
            asyncio.create_task(self.send_data_to(user, "user.init")),
            asyncio.create_task(self.send_data_to(user, "game.status")),
            asyncio.create_task(self.send_data_to(user, "game.settings")),
        )
        if user.is_player:
            if self.operator is None:
                self.operator = user
            await self.broadcast_data("seat.status")
        else:
            await self.send_data_to(user, "seat.status")
        return user

    async def unregister_user(self, user_id: str) -> None:
        if user_id in self.users:
            user = self.users[user_id]
            if user.is_player:
                user.ws = None
                await self.broadcast_data("seat.status")
            else:
                del self.users[user_id]

    @with_lock
    async def take_seat(self, to_seat: int, me: User) -> None:
        from_seat = me.seat
        if from_seat != to_seat and self.seats[to_seat] is None:
            if from_seat > 0:  # `me` have taken a seat
                self.seats[from_seat] = None
            self.seats[to_seat] = me
            me.seat = to_seat
            if self.operator is None:
                self.operator = me
            await self.broadcast_data("seat.status")

    async def remove_seat(self, seat: int, me: User) -> None:
        if me is self.operator and self.seats[seat] is None:
            self.seats.pop(seat)
            for i, player in self.seats_iter():  # adjust players' seats
                if player is not None:
                    player.seat = i
            await self.broadcast_data("seat.status")

    async def remove_player(self, seat: int, me: User) -> None:
        player_to_remove = self.seats[seat]
        if player_to_remove is not None:
            remove_myself = player_to_remove is me
            im_operator = me is self.operator
            if remove_myself or im_operator:
                player_to_remove.seat = 0
                self.seats[seat] = None
                if remove_myself and im_operator:  # hand over OP
                    candidate = None  # find an online candidate with min order
                    for _, user in self.seats_iter():
                        if user is not None and user.is_online:
                            if candidate is None or user.order < candidate.order:
                                candidate = user
                    self.operator = candidate
                await self.broadcast_data("seat.status")

    async def add_seat(self, me: User) -> None:
        if me is self.operator:
            self.seats.append(None)
            await self.broadcast_data("seat.status")

    @with_lock
    async def take_operator(self, me: User) -> None:
        if me is not self.operator:
            if self.operator is None or not self.operator.is_online:
                self.operator = me
                await self.broadcast_data("seat.status")

    async def change_settings(self, quick: bool, me: User) -> None:
        if me is self.operator:
            settings_changed = False
            if quick != self.quick_game:
                self.quick_game = quick
                settings_changed = True
            if settings_changed:
                await self.broadcast_data("game.settings")

    async def handle_data(self, me: User, data: Data) -> None:
        match data["type"]:
            case "user.take_seat":
                if "nickname" in data:
                    me.nickname = str(data["nickname"])
                await self.take_seat(data["seat"], me)
            case "room.remove_seat":
                await self.remove_seat(data["seat"], me)
            case "room.remove_player":
                await self.remove_player(data["seat"], me)
            case "room.add_seat":
                await self.add_seat(me)
            case "player.take_operator":
                await self.take_operator(me)
            case "game.change_settings":
                await self.change_settings(data["quick"], me)


class WebSocketManager:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()

    def get_room(self, room_id: str) -> Room:
        if room_id in self.rooms:
            room = self.rooms[room_id]
        else:
            room = Room(room_id)
            self.rooms[room_id] = room
        return room

    def check_data(self, data: Data) -> bool:
        match data["type"]:
            case "user.register":
                return isinstance(data.get("nickname"), str)
            case "user.take_seat":
                return isinstance(data.get("seat"), int) \
                    and isinstance(data.get("nickname", ""), str)
            case "room.remove_seat" | "room.remove_player":
                return isinstance(data.get("seat"), int)
            case "room.add_seat" | "player.take_operator":
                return True
            case "game.change_settings":
                return isinstance(data.get("quick"), bool)
        return False

    async def receive(self, websocket: WebSocket) -> Data | None:
        try:
            data = await websocket.receive_json()
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict) and self.check_data(data):
            return data
        return None
