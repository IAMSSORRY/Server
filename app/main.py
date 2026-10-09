import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.ros_node import CAMERA_TOPIC, RosBridge
from app.sessions import COOKIE_NAME, SessionStore
from app.stream import FrameHub

STATIC_DIR = Path(__file__).parent / "static"

bridge = RosBridge()
frames = FrameHub()
sessions = SessionStore()


@asynccontextmanager
async def lifespan(_: FastAPI):
    frames.bind(asyncio.get_running_loop())
    bridge.start(on_frame=frames.push)
    yield
    bridge.stop()


app = FastAPI(title="Ssorry ROS2 Web API", lifespan=lifespan)


@app.middleware("http")
async def ensure_session(request: Request, call_next):
    """쿠키에 유효한 세션이 없으면 새로 발급한다. 웹소켓은 이 쿠키로 세션을 찾는다."""
    session = sessions.get(request.cookies.get(COOKIE_NAME))
    created = session is None
    if created:
        session = sessions.create()
    request.state.session = session

    response = await call_next(request)
    if created:
        response.set_cookie(COOKIE_NAME, session.id, httponly=True, samesite="lax")
    return response


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/session")
async def session_info(request: Request):
    s = request.state.session
    return {"session": s.id, "connections": s.connections}


@app.websocket("/ws/camera")
async def camera_ws(ws: WebSocket):
    session = sessions.get(ws.cookies.get(COOKIE_NAME))
    if session is None:
        # 세션 쿠키는 HTTP 응답으로만 줄 수 있으므로, 페이지를 먼저 열어야 한다.
        # accept 전에 닫으면 브라우저에는 1006 만 보이므로 accept 후 4401 로 닫는다.
        await ws.accept()
        await ws.close(code=4401, reason="no session")
        return

    await ws.accept()
    session.connections += 1
    await ws.send_json({"type": "hello", "session": session.id, "topic": CAMERA_TOPIC})

    # 클라이언트가 연결을 끊어도 next_frame 대기 중에는 알 수 없으므로 수신 쪽을 따로 본다.
    async def watch_disconnect():
        while True:
            await ws.receive_text()

    watcher = asyncio.create_task(watch_disconnect())
    try:
        seq = 0
        while not watcher.done():
            frame_task = asyncio.create_task(frames.next_frame(seq))
            done, _ = await asyncio.wait({frame_task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if frame_task not in done:
                frame_task.cancel()
                break
            seq, frame = frame_task.result()
            await ws.send_bytes(frame)
    except WebSocketDisconnect:
        pass
    finally:
        watcher.cancel()
        session.connections -= 1


class PublishRequest(BaseModel):
    text: str


@app.get("/health")
async def health():
    node = bridge.node
    return {
        "status": "ok",
        "ros_node": node.get_name() if node else None,
    }


@app.post("/publish")
async def publish(req: PublishRequest):
    if bridge.node is None:
        raise HTTPException(status_code=503, detail="ROS 노드가 아직 준비되지 않았습니다")
    bridge.node.publish(req.text)
    return {"published": req.text}


@app.get("/last")
async def last():
    if bridge.node is None:
        raise HTTPException(status_code=503, detail="ROS 노드가 아직 준비되지 않았습니다")
    return {"last_message": bridge.node.last_message}


@app.get("/topics")
async def topics():
    if bridge.node is None:
        raise HTTPException(status_code=503, detail="ROS 노드가 아직 준비되지 않았습니다")
    return {
        name: types
        for name, types in bridge.node.get_topic_names_and_types()
    }
