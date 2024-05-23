import asyncio
import json
import random
from typing import Any, Callable, Literal

from fastapi.websockets import WebSocket, WebSocketState


class User:
    def __init__(self, ws: WebSocket, nickname: str) -> None:
        self.ws: WebSocket | None = ws
        self.nickname = nickname
        self.seat = 0

    @property
    def online(self) -> bool:
        return False if self.ws is None else self.ws.client_state == WebSocketState.CONNECTED


def with_lock(func: Callable[..., Any]):
    lock = asyncio.Lock()

    async def wrapper(*args, **kwargs):
        async with lock:
            return await func(*args, **kwargs)

    return wrapper


Data = dict[str, Any]
StatusType = Literal["players", "game"]

DefaultNicknames = ("😀", "😄", "😁", "😆")


class Room:
    def __init__(self, id: str, seat_number: int = 5) -> None:
        self.id = id
        self.users: dict[str, User] = {}
        self.seat_number = seat_number
        self.seats: dict[int, User] = {}
        self.operator: User | None = None
        self.game_start = False
        self.locks: dict[str, asyncio.Lock] = {}

    def get_player_status(self, me: User) -> list[Data | None]:
        info_list = []
        for i in range(1, self.seat_number + 1):
            if i in self.seats:
                user = self.seats[i]
                info_list.append({
                    "nickname": user.nickname,
                    "online": user.online,
                    "me": user is me,
                    "operator": user is self.operator,
                })
            else:
                info_list.append(None)
        return info_list

    def get_game_status(self) -> Data:
        return {
            "start": self.game_start,
        }

    async def send_status(self, user: User, *status_types: StatusType) -> None:
        if not user.online:
            return
        websocket = user.ws
        for status_type in status_types:
            match status_type:
                case "players":
                    await websocket.send_json({"type": "player.status", "status": self.get_player_status(me=user)})
                case "game":
                    await websocket.send_json({"type": "game.status", "status": self.get_game_status()})

    async def broadcast_status(self, *status_type: StatusType) -> None:
        tasks = [asyncio.create_task(self.send_status(user, *status_type)) for user in self.users.values()]
        await asyncio.wait(tasks)

    async def register_user(self, ws: WebSocket, user_id: str, data: Data) -> User:
        if user_id in self.users:
            user = self.users[user_id]
            if user.online:  # close the older ws when duplicated login
                await user.ws.close(reason="close.duplicated_login")
            user.ws = ws
        else:
            nickname = data["nickname"]
            if len(nickname) == 0:
                nickname = random.choice(DefaultNicknames)
            user = User(ws, nickname)
            self.users[user_id] = user
        await ws.send_json({"type": "user.init", "nickname": user.nickname})
        await self.send_status(user, "players", "game")  # send all status to this user
        return user
    
    async def unregister_user(self, user_id: str) -> None:
        if user_id in self.users:
            user = self.users[user_id]
            if user.seat > 0:  # user is a player
                user.ws = None
                await self.broadcast_status("players")
            else:
                del self.users[user_id]

    @with_lock
    async def set_operator(self, user: User, broadcast: bool = True) -> None:
        if self.operator is None or not self.operator.online:
            self.operator = user
            if broadcast:
                await self.broadcast_status("players")

    @with_lock
    async def take_seat(self, to_seat: int, user: User) -> None:
        from_seat = user.seat
        if from_seat != to_seat and to_seat not in self.seats:
            if from_seat > 0:  # this user has taken a seat
                self.seats.pop(from_seat)
            self.seats[to_seat] = user
            user.seat = to_seat
            if self.operator is None:
                await self.set_operator(user, broadcast=False)
            await self.broadcast_status("players")
    
    async def handle_data(self, user: User, data: Data) -> None:
        match data["type"]:
            case "user.take_seat":
                if "nickname" in data:
                    user.nickname = data["nickname"]
                await self.take_seat(data["seat"], user)


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

    async def receive(self, websocket: WebSocket) -> Data | None:
        try:
            data = await websocket.receive_json()
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict) and self.check_data(data):
            return data
        return None
