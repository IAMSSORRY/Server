"""외부 프로세스(LeRobot 추론 / 비전 판정 등)가 이벤트와 프레임을 밀어 넣는 입구.

ROS 토픽을 대신한다. 판정과 모션은 HTTP POST 한 번에 이벤트 하나,
카메라 프레임은 웹소켓 하나로 JPEG 바이너리를 계속 보낸다.
INGEST_TOKEN 이 설정돼 있으면 `Authorization: Bearer <token>` 이 필요하다.
"""

import secrets
import time

from fastapi import APIRouter, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect

from app import config
from app.judge import GRADES
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


@router.post("/detections", dependencies=[Depends(_require_token)])
async def ingest_detections(body: dict):
    """실시간 사과 박스 (top 카메라 영상 위에 계속 그린다). DB 에 남기지 않는다.

    {"cam": "top", "ts": <unix 초>, "boxes": [{"bbox": [x, y, w, h], "grade": "상"|"중"|"하"(선택), "score": 0~1(선택)}]}
    bbox 는 그 카메라의 /ws/camera JPEG 원본 픽셀 기준 (판정 bbox 와 같다). boxes 가 빈 배열이면 박스를 지우는 신호.
    """
    cam = str(body.get("cam") or cameras.default)
    hub = cameras.get(cam)
    if hub is None:
        raise HTTPException(404, f"없는 카메라: {cam}")
    try:
        boxes = []
        for b in body.get("boxes") or []:
            bbox = [float(v) for v in b["bbox"]]
            if len(bbox) != 4:
                raise ValueError("bbox 는 [x, y, w, h] 4개")
            box = {"bbox": bbox}
            if b.get("grade") is not None:
                if b["grade"] not in GRADES:
                    raise ValueError(f"알 수 없는 grade: {b['grade']!r}")
                box["grade"] = b["grade"]
            if b.get("score") is not None:
                box["score"] = float(b["score"])
            boxes.append(box)
        message = {"type": "detections", "cam": cam, "ts": float(body.get("ts") or time.time()), "boxes": boxes}
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(422, f"detections 형식 오류: {e}")
    hub.publish_detections(message)
    return {"ok": True, "boxes": len(boxes)}


@router.post("/mission", dependencies=[Depends(_require_token)])
async def ingest_mission(body: dict):
    """미션 진행 이벤트. {"event": "start" | "apple" | "phase" | "pick" | "skip" | "adaptive" | "estop" | "end", ...}"""
    try:
        return judges.add_mission(body)
    except (ValueError, TypeError) as e:
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
