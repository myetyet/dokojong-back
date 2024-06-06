from json import JSONDecodeError

from fastapi.websockets import WebSocket

from game import Room
from general_types import Data


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

    async def receive(self, websocket: WebSocket) -> Data | None:
        try:
            data = await websocket.receive_json()
        except JSONDecodeError:
            return None
        if isinstance(data, dict) and isinstance(data.get("type"), str):
            return data
        return None
