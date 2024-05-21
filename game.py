import asyncio
import json
from typing import Any, Literal, Optional

from fastapi.websockets import WebSocket, WebSocketState


class User:
    def __init__(self, websocket: WebSocket, nickname: str) -> None:
        self.ws: WebSocket | None = websocket
        self.nickname = nickname
        self.seat = 0

    @property
    def online(self):
        return False if self.ws is None else self.ws.client_state == WebSocketState.CONNECTED


class Room:
    StatusType = Literal["player", "game"]

    def __init__(self, id: str, seat_number: int = 5) -> None:
        self.id = id
        self.users: dict[str, User] = {}
        self.seat_number = seat_number
        self.seats: dict[int, User] = {}
        self.operator: User | None = None
        self.game_start = False

    def broadcast(self, data: dict, exceptions: Optional[dict[User, dict]] = None) -> None:
        if exceptions is None:
            exceptions = {}
        coros1 = [user.ws.send_json(data) for user in self.users.values() if user not in exceptions and user.ws.client_state == WebSocketState.CONNECTED]
        coros2 = [user.ws.send_json(data) for user, data in exceptions.items()]
        asyncio.gather(*coros1, *coros2)

    def add_user(self, user_id: str, user: User) -> None:
        self.users[user_id] = user
    
    def set_operator(self, user: User, broadcast: bool = True) -> None:
        self.operator = user
        if broadcast:
            self.broadcast_status("player")

    def take_seat(self, to_seat: int, user: User) -> None:
        from_seat = user.seat
        if from_seat != to_seat and to_seat not in self.seats:
            if from_seat > 0:
                self.seats.pop(from_seat)
            self.seats[to_seat] = user
            user.seat = to_seat
            if self.operator is None:
                self.set_operator(user, broadcast=False)
            self.broadcast_status("player")

    def get_player_status(self, me: User) -> list[dict[str, Any] | None]:
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

    def get_game_status(self) -> dict[str, Any]:
        return {
            "start": self.game_start,
        }

    async def send_status(self, user: User, *status_types: StatusType) -> None:
        websocket = user.ws
        for status_type in status_types:
            match status_type:
                case "player":
                    await websocket.send_json({"type": "player.status", "status": self.get_player_status(me=user)})
                case "game":
                    await websocket.send_json({"type": "game.status", "status": self.get_game_status()})

    def broadcast_status(self, *status_type: StatusType) -> None:
        for user in self.users.values():
            print(user, str(user.ws.state))
        asyncio.gather(*[self.send_status(user, *status_type) for user in self.users.values() if user.online])


class WebSocketManager:
    CloseReason = Literal["duplicated_login", "disconnection"]

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()

    async def disconnect(self, websocket: WebSocket, reason: CloseReason) -> None:
        await websocket.close(reason=f"close.{reason}")

    def check_data(self, data: dict) -> bool:
        if data["type"] == "user.register":
            return isinstance(data.get("nickname"), str)
        if data["type"] == "user.take_seat":
            return isinstance(data.get("seat"), int)

    async def receive(self, websocket: WebSocket) -> dict[str, Any] | None:
        try:
            data = await websocket.receive_json()
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict) and self.check_data(data):
            return data
        return None

    async def register(self, websocket: WebSocket, room_id: str, user_id: str, data: dict) -> tuple[Room, User]:
        if room_id in self.rooms:
            room = self.rooms[room_id]
        else:
            room = Room(room_id)
            self.rooms[room_id] = room
        if user_id in room.users:
            user = room.users[user_id]
            await self.disconnect(user.ws, "duplicated_login")
            user.ws = websocket
        else:
            user = User(websocket, data["nickname"])
            room.add_user(user_id, user)
        await room.send_status(user, "player", "game")
        return room, user

    async def handle(self, room: Room, user: User, data: dict) -> None:
        if data["type"] == "user.take_seat":
            room.take_seat(data["seat"], user)
