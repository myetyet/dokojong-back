from fastapi.websockets import WebSocket, WebSocketState

from general_types import Data, DataType


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
