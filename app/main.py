import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Coroutine, TypeVar

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import config, ingest
from app.judge import HISTORY_MAX, SubscriberOverflow
from app.sessions import COOKIE_NAME, Session
from app.state import cameras, judges, sessions

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
log = logging.getLogger(__name__)

T = TypeVar("T")


def _start_sources() -> list[asyncio.Task]:
    """카메라 / 이벤트 소스를 백그라운드 태스크로 띄운다."""
    if config.MOCK:
        from app.sources import mock  # Pillow 는 MOCK 일 때만 필요하다

        log.info("MOCK 모드: 카메라 %s, 판정 / 모션 이벤트를 만든다", cameras.names)
        return [asyncio.create_task(mock.run(cameras, judges))]

    if config.CAMERA_SOURCE == "piper":
        from app.sources import piper

        tasks = []
        for name in cameras.names:
            cam_id = config.PIPER_CAMERAS.get(name)
            if cam_id is None:
                log.warning("PIPER_CAMERAS 에 %s 가 없어 이 카메라는 비워 둔다", name)
                continue
            tasks.append(asyncio.create_task(piper.pull_camera(
                name, cam_id, cameras.get(name), config.PIPER_URL, config.PIPER_STREAM_FPS,
            )))
        return tasks

    log.info("카메라는 WS /ingest/camera/{cam} 으로 들어오기를 기다린다 (CAMERAS=%s)", ",".join(cameras.names))
    return []


@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = _start_sources()
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Ssorry Web API", lifespan=lifespan)
app.include_router(ingest.router)


@app.middleware("http")
async def ensure_session(request: Request, call_next):
    """쿠키에 유효한 세션이 없으면 새로 발급한다. 웹소켓은 이 쿠키로 세션을 찾는다.

    /ingest 는 브라우저가 아니라 로봇 쪽 프로세스가 부르므로 세션을 만들지 않는다.
    """
    if request.url.path.startswith("/ingest"):
        return await call_next(request)
    session = sessions.get(request.cookies.get(COOKIE_NAME))
    created = session is None
    if created:
        session = sessions.create()
    request.state.session = session

    response = await call_next(request)
    if created:
        response.set_cookie(COOKIE_NAME, session.id, httponly=True, samesite="lax")
    return response


if config.CORS_ORIGINS:
    # 마지막에 추가한 미들웨어가 가장 바깥이다. preflight 가 세션 미들웨어보다 먼저 처리된다.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
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
async def camera_ws(ws: WebSocket, cam: str | None = None):
    """?cam=<이름> 으로 카메라를 고른다. 없으면 기본(CAMERAS 의 첫 번째) 카메라."""
    session = await _accept_session(ws)
    if session is None:
        return
    hub = cameras.get(cam)
    if hub is None:
        await ws.close(code=4404, reason=f"unknown camera: {cam}")
        return

    session.connections += 1
    await _send_json(ws, {
        "type": "hello",
        "session": session.id,
        "cam": cam or cameras.default,
        "cams": cameras.names,
    })

    seq = 0

    async def next_frame() -> bytes:
        nonlocal seq
        seq, frame = await hub.next_frame(seq)
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


@app.get("/cameras")
async def list_cameras():
    return {
        "default": cameras.default,
        "cameras": [{"name": n, "live": cameras.get(n).has_frame} for n in cameras.names],
    }


@app.get("/health")
async def health():
    source = "mock" if config.MOCK else config.CAMERA_SOURCE
    return {"status": "ok", "camera_source": source, "cameras": cameras.names}


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
if (config.FRONTEND_DIR / "index.html").is_file():
    app.mount("/", SPAStaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
else:
    @app.get("/")
    async def index():
        return FileResponse(config.STATIC_DIR / "index.html")
