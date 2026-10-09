"""외부 프로세스(LeRobot 추론 / 비전 판정 등)가 이벤트와 프레임을 밀어 넣는 입구.

ROS 토픽을 대신한다. 판정과 모션은 HTTP POST 한 번에 이벤트 하나,
카메라 프레임은 웹소켓 하나로 JPEG 바이너리를 계속 보낸다.
INGEST_TOKEN 이 설정돼 있으면 `Authorization: Bearer <token>` 이 필요하다.
"""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect

from app import config
from app.state import cameras, judges


def _check_token(authorization: str | None) -> bool:
    if not config.INGEST_TOKEN:
        return True
    return secrets.compare_digest(authorization or "", f"Bearer {config.INGEST_TOKEN}")


def _require_token(authorization: str = Header(default="")) -> None:
    if not _check_token(authorization):
        raise HTTPException(401, "잘못된 ingest 토큰")


router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/judge", dependencies=[Depends(_require_token)])
async def ingest_judge(body: dict):
    try:
        return judges.add_judge(body)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/motion", dependencies=[Depends(_require_token)])
async def ingest_motion(body: dict):
    try:
        return judges.add_motion(body)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.websocket("/camera/{cam}")
async def ingest_camera(ws: WebSocket, cam: str):
    """바이너리 메시지 하나 = JPEG 한 장."""
    await ws.accept()
    if not _check_token(ws.headers.get("authorization")):
        await ws.close(code=4401, reason="bad ingest token")
        return
    hub = cameras.get(cam)
    if hub is None:
        await ws.close(code=4404, reason=f"unknown camera: {cam} (CAMERAS={','.join(cameras.names)})")
        return
    try:
        while True:
            hub.publish(await ws.receive_bytes())
    except WebSocketDisconnect:
        hub.set_error("카메라를 보내던 로봇 쪽 연결이 끊겼다")
