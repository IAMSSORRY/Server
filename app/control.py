"""로봇 미션 원격 제어 — 프론트가 시작·비상정지·해제(이어하기)·멈춤을 보낸다.

실제 제어는 로봇 PC 의 미션 프로그램(Piper `mission.py --serve`, 기본 :8765)이 한다.
이 라우터는 브라우저 요청을 그쪽으로 넘기기만 한다. 로봇 주소와 토큰은 서버에만 있다
(토큰은 /ingest 와 같은 INGEST_TOKEN).

  GET  /control/status            {state, error, index, placed, results}
  POST /control/start  {"apples"} 미션 시작 (숫자 / "all" / 생략)
  POST /control/estop             비상정지
  POST /control/resume            비상정지(또는 오류 정지) 해제 → 멈춘 사과부터 이어서
  POST /control/stop              지금 사과까지만 하고 멈춤

state: idle / running / estopped / error / done. 로봇 프로그램이 안 떠 있으면 503.
"""

import httpx
from fastapi import APIRouter, Body, HTTPException

from app import config

router = APIRouter(prefix="/control", tags=["control"])

# 비상정지는 빨리 가야 하고, 해제는 로봇이 모터를 다시 켜느라 몇 초 걸린다.
_TIMEOUT = {"estop": 3.0, "resume": 15.0}


async def _call(method: str, path: str, body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {config.INGEST_TOKEN}"} if config.INGEST_TOKEN else {}
    timeout = _TIMEOUT.get(path.strip("/"), 5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(method, f"{config.ROBOT_CONTROL_URL}{path}", json=body, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(503, f"로봇 미션 프로그램에 연결할 수 없습니다 ({config.ROBOT_CONTROL_URL}): {e}") from e
    try:
        data = r.json()
    except ValueError:
        data = {"ok": False, "error": r.text[:200]}
    if r.status_code == 401:
        raise HTTPException(502, "로봇 미션 프로그램이 토큰을 거부했습니다 (INGEST_TOKEN 과 로봇 쪽 SSORRY_TOKEN 이 같은지 확인)")
    # 409 = 지금 할 수 없는 명령 (실행 중에 /start 등). 메시지를 그대로 돌려준다
    if r.status_code >= 400 and r.status_code != 409:
        raise HTTPException(502, data.get("error") or f"로봇 쪽 오류 {r.status_code}")
    return data


@router.get("/status")
async def status():
    return await _call("GET", "/status")


@router.post("/start")
async def start(body: dict = Body(default={})):
    apples = body.get("apples")
    return await _call("POST", "/start", {"apples": apples} if apples is not None else {})


@router.post("/estop")
async def estop():
    return await _call("POST", "/estop")


@router.post("/resume")
async def resume():
    return await _call("POST", "/resume")


@router.post("/stop")
async def stop():
    return await _call("POST", "/stop")
