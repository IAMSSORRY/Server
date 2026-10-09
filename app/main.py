import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Coroutine, TypeVar

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel

from app.judge import HISTORY_MAX, JudgeHub, SubscriberOverflow
from app.ros_node import CAMERA_TOPIC, BridgeCallbacks, RosBridge
from app.sessions import COOKIE_NAME, Session, SessionStore
from app.stream import FrameHub

STATIC_DIR = Path(__file__).parent / "static"
# 빌드한 프론트(index.html 이 있는 폴더). 있으면 / 에서 서빙하고, 없으면 내장 카메라 뷰어를 띄운다.
# 프론트를 API 와 같은 출처에서 열어야 세션 쿠키가 웹소켓에 실린다.
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", "/workspace/frontend"))
MOCK = os.environ.get("MOCK") == "1"
# 쉼표로 구분한 허용 출처. 예: http://192.168.0.5:5173,http://localhost:5173
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]

T = TypeVar("T")

bridge = RosBridge()
frames = FrameHub()
judges = JudgeHub()
sessions = SessionStore()


@asynccontextmanager
async def lifespan(_: FastAPI):
    loop = asyncio.get_running_loop()
    frames.bind(loop)
    judges.bind(loop)

    extra_nodes = []
    if MOCK:
        from app.mock import MockPublisher  # Pillow 는 MOCK 일 때만 필요하다

        extra_nodes.append(MockPublisher)

    bridge.start(
        BridgeCallbacks(on_frame=frames.push, on_judge=judges.push_judge, on_motion=judges.push_motion),
        extra_node_factories=extra_nodes,
    )
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


if CORS_ORIGINS:
    # 마지막에 추가한 미들웨어가 가장 바깥이다. preflight 가 세션 미들웨어보다 먼저 처리된다.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/session")
async def session_info(request: Request):
    s = request.state.session
    return {"session": s.id, "connections": s.connections}


async def _accept_session(ws: WebSocket) -> Session | None:
    """쿠키로 세션을 찾아 accept 한다. 세션이 없으면 4401 로 닫고 None.

    세션 쿠키는 HTTP 응답으로만 줄 수 있으므로, 페이지(또는 아무 HTTP 요청)를 먼저 열어야 한다.
    accept 전에 닫으면 브라우저에는 1006 만 보이므로 accept 후 4401 로 닫는다.
    """
    await ws.accept()
    session = sessions.get(ws.cookies.get(COOKIE_NAME))
    if session is None:
        await ws.close(code=4401, reason="no session")
    return session


async def _pump(ws: WebSocket, next_item: Callable[[], Coroutine[Any, Any, T]], send: Callable[[T], Awaitable[None]]) -> None:
    """next_item 으로 받은 것을 클라이언트가 끊을 때까지 계속 보낸다.

    클라이언트가 끊어도 next_item 대기 중에는 알 수 없으므로 수신 쪽을 따로 본다.
    """

    async def watch_disconnect():
        while True:
            await ws.receive_text()

    watcher = asyncio.create_task(watch_disconnect())
    try:
        while True:
            item_task = asyncio.create_task(next_item())
            done, _ = await asyncio.wait({item_task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if item_task not in done:
                item_task.cancel()
                return
            await send(item_task.result())
    except WebSocketDisconnect:
        pass
    finally:
        watcher.cancel()


async def _send_json(ws: WebSocket, message: dict) -> None:
    # 기본 send_json 은 한글을 \uXXXX 로 내보내므로 그대로 보이게 직접 직렬화한다.
    await ws.send_text(json.dumps(message, ensure_ascii=False))


@app.websocket("/ws/camera")
async def camera_ws(ws: WebSocket):
    session = await _accept_session(ws)
    if session is None:
        return

    session.connections += 1
    await ws.send_json({"type": "hello", "session": session.id, "topic": CAMERA_TOPIC})

    seq = 0

    async def next_frame() -> bytes:
        nonlocal seq
        seq, frame = await frames.next_frame(seq)
        return frame

    try:
        await _pump(ws, next_frame, ws.send_bytes)
    finally:
        session.connections -= 1


@app.websocket("/ws/judge")
async def judge_ws(ws: WebSocket):
    session = await _accept_session(ws)
    if session is None:
        return

    session.connections += 1
    # subscribe 와 snapshot 은 await 없이 한 번에 만들어지므로, 그 사이 이벤트가 빠지지 않는다.
    sub, snapshot = judges.subscribe()
    try:
        await _send_json(ws, snapshot)
        await _pump(ws, sub.get, lambda message: _send_json(ws, message))
    except SubscriberOverflow:
        # 너무 밀린 클라이언트. 재연결하면 snapshot 부터 다시 받는다.
        await ws.close(code=4408, reason="event queue overflow")
    finally:
        judges.unsubscribe(sub)
        session.connections -= 1


@app.get("/stats")
async def get_stats(limit: int = Query(50, ge=0, le=HISTORY_MAX)):
    return {"stats": judges.stats(), "cycle_time": judges.cycle_time, "recent": judges.history(limit)}


@app.get("/history")
async def get_history():
    return judges.history()


@app.post("/stats/reset")
async def reset_stats():
    judges.reset()
    return {"stats": judges.stats()}


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


class SPAStaticFiles(StaticFiles):
    """없는 경로는 index.html 로 돌려서 프론트 라우터가 처리하게 한다."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404:
                raise
            return await super().get_response("index.html", scope)


# API 라우트를 모두 등록한 뒤에 마운트해야 API 경로가 먼저 매칭된다.
if (FRONTEND_DIR / "index.html").is_file():
    app.mount("/", SPAStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")
