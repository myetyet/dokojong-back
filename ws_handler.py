import asyncio
import json
from typing import Any, Literal

from fastapi.websockets import WebSocket, WebSocketDisconnect


class User:
    def __init__(self, websocket: WebSocket, nickname: str) -> None:
        self.ws = websocket
        self.nickname = nickname
        self.is_player = False

    async def update_websocket(self, websocket: WebSocket) -> None:
        await WebSocketManager.disconnect(self.ws, "duplicated_login")
        self.ws = websocket


class Game:
    def __init__(self, player_number: int) -> None:
        self.player_number = player_number
        self.seats: list[User | None] = [None] * player_number

    def get_players(self) -> list[str]:
        return ["" if user is None else user.nickname for user in self.seats]


class Room:
    def __init__(self, id: str) -> None:
        self.id = id
        self.users: dict[str, User] = {}
        self.game = Game()

    def broadcast(self, data: dict) -> None:
        asyncio.gather(*[user.ws.send_json(data) for user in self.users.values()])

    def add_user(self, user_id: str, user: User) -> None:
        self.users[user_id] = user

    async def take_seat(self, seat: int, user: User) -> None:
        if seat not in self.seat_map:
            self.seat_map[seat] = user
            self.broadcast({"type": "user.take_seat", "nickname": user.nickname, "seat": seat})

    async def send_status(self, user: User) -> None:
        room_status = {
            "type": "room.status",
            "player_info": self.game.get_players(),
        }
        user.ws.send_json()


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
        room.send_status(user)
        return room, user

    async def handle(self, room: Room, user: User, data: dict) -> None:
        if data["type"] == "user.take_seat":
            await room.take_seat(data["seat"], user)
