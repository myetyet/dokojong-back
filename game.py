import asyncio
import itertools
import json
from typing import Any, Callable, Generator, Literal

from fastapi.websockets import WebSocket, WebSocketState


Data = dict[str, Any]
DataType = Literal[
    "user.init",
    "seat.status",
    "game.settings", "game.status",
]


class User:
    def __init__(self, ws: WebSocket) -> None:
        self.ws: WebSocket | None = ws
        self.seat = 0
        self.order = 0
        self.nickname = ""

    @property
    def is_online(self) -> bool:
        return False if self.ws is None else self.ws.client_state == WebSocketState.CONNECTED

    @property
    def is_player(self) -> bool:
        return self.seat > 0
    
    async def update_websocket(self, ws: WebSocket) -> None:
        if self.is_online:
            await self.ws.close(reason="close.duplicated_login")
        self.ws = ws
    
    def take_seat(self, seat: int, order: int, nickname: str) -> None:
        self.seat = seat
        self.order = order
        self.nickname = nickname

    def leave_seat(self) -> None:
        self.seat = 0
        self.order = 0

    async def send_data(self, type: DataType, data: Data) -> None:
        if self.is_online:
            await self.ws.send_json({"type": type, **data})


def with_lock(func: Callable[..., Any]):
    lock = asyncio.Lock()

    async def wrapper(*args, **kwargs):
        async with lock:
            return await func(*args, **kwargs)

    return wrapper


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

    def seats_iter(self) -> Generator[User | None, None, None]:
        for i in range(1, len(self.seats)):
            yield self.seats[i]

    def get_seat_status(self, me: User) -> list[Data | None]:
        status_list = []
        for user in self.seats_iter():
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
            "quick_game": self.quick_game,
        }

    async def send_data_to(self, user: User, data_type: DataType) -> None:
        match data_type:
            case "seat.status":
                await user.send_data(data_type, {"status": self.get_seat_status(me=user)})
            case "game.settings":
                await user.send_data(data_type, self.get_game_settings())
            case "game.status":
                await user.send_data(data_type, {"start": self.game_start})
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
            if ws is not user.ws:
                await user.update_websocket(ws)
        else:
            user = User(ws)
            self.users[user_id] = user
        match data["stage"]:
            case "hall":
                if user.is_player:
                    if self.operator is None:  # `user` offline and then OP offline, left no one online and no OP
                        self.operator = user
                    await self.broadcast_data("seat.status")  # in case of the break-in of other users
                else:
                    await self.send_data_to(user, "seat.status")
            case "board":
                await user.ws.send_json({"type": "game.setup", "welcome": "GAME START"})
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
    async def take_seat(self, me: User, to_seat: int, nickname: str) -> None:
        from_seat = me.seat
        if from_seat != to_seat and self.seats[to_seat] is None:
            if from_seat > 0:  # `me` have taken a seat
                self.seats[from_seat] = None
            self.seats[to_seat] = me
            me.take_seat(to_seat, next(self.order_issuer), nickname)
            if self.operator is None:
                self.operator = me
            await self.broadcast_data("seat.status")

    async def remove_seat(self, me: User, seat: int) -> None:
        if me is self.operator and self.seats[seat] is None:
            self.seats.pop(seat)
            for i, player in enumerate(self.seats_iter(), start=1):  # adjust players' seats
                if player is not None:
                    player.seat = i
            await self.broadcast_data("seat.status")

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
                    for user in self.seats_iter():
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

    async def change_settings(self, me: User, quick: bool) -> None:
        if me is self.operator:
            settings_changed = False
            if quick != self.quick_game:
                self.quick_game = quick
                settings_changed = True
            if settings_changed:
                await self.broadcast_data("game.settings")

    async def start_game(self, me: User) -> None:
        if me is self.operator and not self.game_start:
            self.game_start = True
            await self.broadcast_data("game.status")

    async def handle_data(self, me: User, data: Data) -> None:
        match data["type"]:
            case "user.take_seat":
                await self.take_seat(me, data["seat"], data["nickname"])
            case "room.remove_seat":
                await self.remove_seat(me, data["seat"])
            case "room.remove_player":
                await self.remove_player(me, data["seat"])
            case "room.add_seat":
                await self.add_seat(me)
            case "player.take_operator":
                await self.take_operator(me)
            case "game.change_settings":
                await self.change_settings(me, data["quick"])
            case "game.start":
                await self.start_game(me)


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
                return isinstance(data.get("stage"), str)
            case "user.take_seat":
                return isinstance(data.get("seat"), int) \
                    and isinstance(data.get("nickname"), str)
            case "room.remove_seat" | "room.remove_player":
                return isinstance(data.get("seat"), int)
            case "room.add_seat" | "player.take_operator" | "game.start":
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
