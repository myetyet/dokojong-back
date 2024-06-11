from typing import Optional

from fastapi.websockets import WebSocket, WebSocketState

from general_types import Data, DataType


class User:
    def __init__(self, ws: WebSocket) -> None:
        self.ws: WebSocket | None = ws
        self.seat = -1
        self.nickname = ""
        self.order = 0

    @property
    def is_online(self) -> bool:
        return False if self.ws is None else self.ws.client_state == WebSocketState.CONNECTED

    @property
    def is_player(self) -> bool:
        return self.seat > -1
    
    async def update_ws(self, ws: WebSocket) -> None:
        if self.is_online:
            await self.ws.close(reason="close.duplicated_login")
        self.ws = ws
    
    def take_seat(self, seat: int, nickname: str, order: Optional[int] = None) -> None:
        self.seat = seat
        self.nickname = nickname
        if order is not None:
            self.order = order

    def leave_seat(self) -> None:
        self.seat = -1
        self.order = 0

    async def send_data(self, type: DataType, data: Optional[Data] = None) -> None:
        if self.is_online:
            json = {"type": type}
            if data is not None:
                json.update(data)
            await self.ws.send_json(json)
