import asyncio
import json
from typing import Any, Literal, Optional

from fastapi.websockets import WebSocket


class User:
    def __init__(self, websocket: WebSocket, nickname: str) -> None:
        self.ws = websocket
        self.nickname = nickname
        self.seat = 0

    async def update_websocket(self, websocket: WebSocket) -> None:
        await WebSocketManager.disconnect(self.ws, "duplicated_login")
        self.ws = websocket


class Room:
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
        coroutines = [user.ws.send_json(data) for user in self.users.values() if user not in exceptions]
        coroutines.extend(user.ws.send_json(data) for user, data in exceptions.items())
        asyncio.gather(*coroutines)

    def add_user(self, user_id: str, user: User) -> None:
        self.users[user_id] = user

    def set_operator(self, seat: int) -> None:
        self.operator_seat = seat
        self.broadcast({"type": "room.set_operator", "seat": seat})

    def take_seat(self, to_seat: int, user: User) -> None:
        from_seat = user.seat
        if from_seat != to_seat and to_seat not in self.seats:
            if from_seat > 0:
                self.seats.pop(from_seat)
            self.seats[to_seat] = user
            user.seat = to_seat
            self.broadcast(
                data={"type": "user.take_seat", "nickname": user.nickname, "from": from_seat, "to": to_seat},
                exceptions={user: {"type": "user.take_seat", "nickname": user.nickname, "from": from_seat, "to": to_seat, "me": True}}
            )
            if self.operator is None or self.operator is user:
                if self.operator is None:
                    self.operator = user
                self.broadcast({"type": "room.set_operator", "seat": to_seat})


    async def send_status(self, user: User) -> None:
        for i in range(1, self.seat_number + 1):
            if i in self.seats:
                print(i, self.seats[i], self.seats[i].nickname)
            else:
                print(i, "empty")
        room_status = {
            "type": "room.status",
            "nicknames": [self.seats[i].nickname if i in self.seats else "" for i in range(1, self.seat_number + 1)],
            "seat": {
                "my": user.seat,
                "operator": 0 if self.operator is None else self.operator.seat,
            },
            "game": {
                "start": self.game_start,
            }
        }
        await user.ws.send_json(room_status)


class WebSocketManager:
    CloseReason = Literal["duplicated_login", "disconnection"]

    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}

    @staticmethod
    async def connect(websocket: WebSocket) -> None:
        await websocket.accept()

    @staticmethod
    async def disconnect(websocket: WebSocket, reason: CloseReason) -> None:
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
        user = User(websocket, data["nickname"])
        room.add_user(user_id, user)
        await room.send_status(user)
        return room, user

    async def handle(self, room: Room, user: User, data: dict) -> None:
        if data["type"] == "user.take_seat":
            room.take_seat(data["seat"], user)
