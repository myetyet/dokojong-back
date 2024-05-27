import asyncio
import itertools
import json
import random
from typing import Any, Callable, Generator, Literal

from fastapi.websockets import WebSocket, WebSocketState


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


def with_lock(func: Callable[..., Any]):
    lock = asyncio.Lock()

    async def wrapper(*args, **kwargs):
        async with lock:
            return await func(*args, **kwargs)

    return wrapper


Data = dict[str, Any]
StatusType = Literal["players", "game"]

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

    def get_player_status(self, me: User) -> list[Data | None]:
        info_list = []
        for _, user in self.seats_iter():
            if user is None:
                info_list.append(None)
            else:
                info_list.append({
                    "nickname": user.nickname,
                    "online": user.is_online,
                    "me": user is me,
                    "operator": user is self.operator,
                })
        return info_list

    def get_game_status(self) -> Data:
        return {
            "start": self.game_start,
            "min_seats": MIN_SEAT_NUMBER,
            "max_seats": MAX_SEAT_NUMBER,
            "quick_game": self.quick_game,
        }

    async def send_status(self, user: User, status_type: StatusType) -> None:
        if not user.is_online:
            return
        match status_type:
            case "players":
                await user.ws.send_json({
                    "type": "player.status",
                    "status": self.get_player_status(me=user)
                })
            case "game":
                await user.ws.send_json({
                    "type": "game.status",
                    "status": self.get_game_status()
                })

    async def broadcast_status(self, status_type: StatusType) -> None:
        tasks = [
            asyncio.create_task(self.send_status(user, status_type))
            for user in self.users.values()
        ]
        await asyncio.wait(tasks)

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
        await ws.send_json({"type": "user.init", "nickname": user.nickname})
        await self.send_status(user, "game")
        if user.is_player:
            if self.operator is None:
                self.operator = user
            await self.broadcast_status("players")
        else:
            await self.send_status(user, "players")
        return user

    async def unregister_user(self, user_id: str) -> None:
        if user_id in self.users:
            user = self.users[user_id]
            if user.is_player:
                user.ws = None
                await self.broadcast_status("players")
            else:
                del self.users[user_id]

    @with_lock
    async def take_seat(self, to_seat: int, me: User) -> None:
        from_seat = me.seat
        if from_seat != to_seat and self.seats[to_seat] is None:
            if from_seat > 0:  # `user` has taken a seat
                self.seats[from_seat] = None
            self.seats[to_seat] = me
            me.seat = to_seat
            if self.operator is None:
                self.operator = me
            await self.broadcast_status("players")

    async def remove_seat(self, seat: int, me: User) -> None:
        if me is self.operator and self.seats[seat] is None:
            self.seats.pop(seat)
            for i, user in self.seats_iter():  # adjust users' seats
                if user is not None:
                    user.seat = i
            await self.broadcast_status("players")

    async def remove_player(self, seat: int, me: User) -> None:
        remove_myself = self.seats[seat] is me
        im_operator = me is self.operator
        if remove_myself or im_operator:
            self.seats[seat].seat = 0
            self.seats[seat] = None
            if remove_myself and im_operator:  # hand over OP
                candidate = None  # find an online candidate with min order
                for _, user in self.seats_iter():
                    if user is not None and user.is_online:
                        if candidate is None or user.order < candidate.order:
                            candidate = user
                self.operator = candidate
            await self.broadcast_status("players")

    async def add_seat(self, me: User) -> None:
        if me is self.operator:
            self.seats.append(None)
            await self.broadcast_status("players")

    @with_lock
    async def take_operator(self, me: User) -> None:
        if me is not self.operator:
            if self.operator is None or not self.operator.is_online:
                self.operator = me
                await self.broadcast_status("players")

    async def set_quick_game(self, quick: bool, me: User) -> None:
        if me is self.operator:
            self.quick_game = quick
            await self.broadcast_status("game")

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
            case "game.set_quick":
                await self.set_quick_game(data["quick"], me)


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
            case "game.set_quick":
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
