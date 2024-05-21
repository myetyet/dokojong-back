import os
import random
import uuid
from typing import Annotated, Optional

from fastapi import FastAPI
from fastapi.param_functions import Cookie, Path
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocket, WebSocketDisconnect

from game import WebSocketManager


root_dir = os.path.realpath(os.path.join(__file__, "..", "..", "dokojong-front", "dist"))
app = FastAPI()
app.mount("/assets", StaticFiles(directory=os.path.join(root_dir, "assets")), name="assets")
ws_manager = WebSocketManager()


@app.get("/vite.svg")
async def favicon():
    return FileResponse(os.path.join(root_dir, "vite.svg"))


def check_user_id(user_id: str) -> bool:
    try:
        uuid.UUID(user_id, version=4)
        return True
    except ValueError:
        return False


@app.get("/")
@app.get("/{room_id}")
async def index(room_id: Optional[str] = None, user_id: Annotated[Optional[str], Cookie()] = None):
    if room_id is None or len(room_id) == 4 and str.isdigit(room_id):
        with open(os.path.join(root_dir, "index.html"), "rb") as fp:
            index_html = fp.read()
        html_rsp = HTMLResponse(index_html)
        if user_id is None or not check_user_id(user_id):
            user_id = str(uuid.uuid4())
        html_rsp.set_cookie("user_id", user_id, 3 * 24 * 60 * 60, httponly=True)
        return html_rsp
    if room_id == "xxxx":
        return RedirectResponse("/" + "".join(chr(random.randint(48, 57)) for _ in range(4)))
    return RedirectResponse("/")


@app.websocket("/{room_id}")
async def ws_handler(websocket: WebSocket, room_id: Annotated[str, Path()], user_id: Annotated[str, Cookie()]):
    await ws_manager.connect(websocket)
    room = None
    user = None
    try:
        while True:
            data = await ws_manager.receive(websocket)
            if data is None:
                continue
            if data["type"] == "user.register":
                room, user = await ws_manager.register(websocket, room_id, user_id, data)
            else:
                await ws_manager.handle(room, user, data)
    except WebSocketDisconnect:
        print(room_id, user_id, user.nickname)
        print(room.users)


@app.get("/{_:path}")
async def other_url(_: str):
    return RedirectResponse("/")


if __name__ == "__main__":
    from uvicorn import Config, Server
    Server(Config(app, "0.0.0.0", 8765, reload=True)).run()
