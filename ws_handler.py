import json
from typing import Any, Literal

from fastapi.websockets import WebSocket, WebSocketDisconnect


class Room:
    def __init__(self, id: str) -> None:
        self.id = id
        self.users: dict[str, User] = {}
        self.occupied_seats: set[int] = set()

    def add_user(self, user_id: str, user: "User") -> None:
        self.users[user_id] = user

    async def handle(self, data: dict[str, Any]) -> None:
        pass

    # def broadcast(self, message: ws.Data) -> None:
    #     ws.broadcast(self.users.values(), message)


class User:
    def __init__(self, websocket: WebSocket, nickname: str) -> None:
        self.ws = websocket
        self.nickname = nickname
        self.is_player = False

    async def update_websocket(self, websocket: WebSocket) -> None:
        await WebSocketManager.disconnect(self.ws, "duplicated_login")
        self.ws = websocket


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
        return room, user
