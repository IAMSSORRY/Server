"""PIPER Studio 게이트웨이의 카메라 MJPEG 스트림을 받아 FrameHub 에 넣는다.

PIPER Studio 는 카메라를 camerad 데몬이 독점하고, 게이트웨이가
`GET /api/cameras/{cam_id}/stream` 으로 `multipart/x-mixed-replace` 스트림을 낸다.
카메라마다 연결 하나를 열어 두고, 끊기면 다시 붙는다.

PIPER 의 일반 USB 카메라 id 는 `/dev/videoN` 이라 재부팅이나 USB 재연결로 번호가 바뀐다.
그래서 설정에는 PIPER 화면에서 붙인 **라벨**을 적고, 연결할 때마다
`GET /api/cameras/current` 에서 그 라벨의 현재 id 를 찾는다.

PIPER 는 USB 가 빠졌다 다시 꽂힌 카메라를 스스로 다시 열지 않는다(`present` 이지만 `connected: false`).
그래서 등록돼 있고 꽂혀 있는데 연결이 끊긴 카메라는 `POST /api/cameras/connect` 로 다시 연다.
PIPER 화면의 [연결] 버튼과 같은 호출이다. PIPER_AUTO_CONNECT=0 이면 하지 않는다.
"""

import asyncio
import logging
import time
from urllib.parse import quote

import httpx

from app.stream import FrameHub

log = logging.getLogger(__name__)

# 카메라가 복구되면 이 시간 안에 다시 붙는다
RECONNECT_MAX_S = 3.0
# 스트림이 열려 있어도 이 시간 동안 데이터가 없으면 끊고 카메라 상태를 다시 본다.
# PIPER 는 한 번 프레임을 보낸 스트림을 카메라가 빠져도 닫지 않는다.
STALL_TIMEOUT_S = 5.0
# 자동 연결 시도 간격. 장치를 여는 일이라 매 재시도마다 하지 않는다.
AUTO_CONNECT_INTERVAL_S = 10.0


async def _read_parts(response: httpx.Response):
    """multipart 응답에서 JPEG 바이트를 하나씩 꺼낸다.

    PIPER 는 파트마다 Content-Length 를 넣어 주므로 그 길이만큼 읽는다.
    """
    buf = b""
    async for chunk in response.aiter_bytes():
        buf += chunk
        while True:
            header_end = buf.find(b"\r\n\r\n")
            if header_end < 0:
                break
            headers = buf[:header_end].decode("latin-1").lower()
            length = None
            for line in headers.split("\r\n"):
                if line.startswith("content-length:"):
                    length = int(line.split(":", 1)[1])
            if length is None:
                # 헤더가 아닌 쓰레기(경계 앞의 빈 줄 등)를 건너뛴다
                buf = buf[header_end + 4:]
                continue
            body_start = header_end + 4
            if len(buf) < body_start + length:
                break
            yield buf[body_start:body_start + length]
            buf = buf[body_start + length:]


def _describe(e: Exception) -> str:
    """화면에 보여줄 실패 이유."""
    if isinstance(e, httpx.ConnectError):
        return "PIPER Studio 에 연결할 수 없다 (PIPER 가 꺼져 있거나 주소가 틀렸다)"
    if isinstance(e, httpx.ReadTimeout):
        return f"카메라에서 {STALL_TIMEOUT_S:.0f}초 넘게 프레임이 오지 않는다 (PIPER 스트림 멈춤)"
    if isinstance(e, httpx.TimeoutException):
        return "PIPER Studio 가 응답하지 않는다"
    if isinstance(e, httpx.HTTPStatusError):
        return f"PIPER Studio 가 {e.response.status_code} 를 돌려줬다 ({e.request.url.path})"
    return str(e) or type(e).__name__


async def _auto_connect(client: httpx.AsyncClient, base_url: str, ref: str, cam: dict) -> None:
    """끊긴 카메라를 PIPER 에서 다시 연다. 실패하면 이유를 담아 RuntimeError."""
    log.info("PIPER 카메라 %r(%s) 가 끊겨 있어 다시 연결을 요청한다", ref, cam["id"])
    response = await client.post(f"{base_url}/api/cameras/connect", json={"id": cam["id"]})
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise RuntimeError(f"PIPER 에서 {ref!r}({cam['id']}) 다시 연결 실패: {detail}")
    log.info("PIPER 카메라 %r(%s) 다시 연결됨", ref, cam["id"])


async def resolve_camera_id(
    client: httpx.AsyncClient, base_url: str, ref: str, connect=None,
) -> str:
    """라벨(또는 id, profile_key, 표시명) → PIPER 의 현재 카메라 id.

    connect 가 주어지면 꽂혀 있는데 끊긴 카메라를 만났을 때 connect(cam) 을 await 한다.
    목록에서 못 찾으면 ref 를 id 로 보고 그대로 쓴다.
    """
    response = await client.get(f"{base_url}/api/cameras/current")
    response.raise_for_status()
    cams = response.json().get("cameras", [])
    for key in ("label", "id", "profile_key", "display_name"):
        matches = [c for c in cams if c.get(key) == ref]
        if len(matches) == 1:
            cam = matches[0]
            if cam.get("present") is False:
                raise RuntimeError(f"{ref!r}({cam['id']}) 카메라가 뽑혀 있다 — USB 를 확인하세요")
            if cam.get("connected") is False:
                # 연결 안 된 카메라의 스트림은 멈춘 프레임만 준다. 그대로 붙지 않는다.
                if connect is None:
                    raise RuntimeError(
                        f"PIPER 에서 {ref!r}({cam['id']}) 가 연결돼 있지 않다 — 카메라 페이지에서 연결하세요")
                await connect(cam)
            return cam["id"]
        if len(matches) > 1:
            raise RuntimeError(f"PIPER 카메라 {key}={ref!r} 가 {len(matches)}개라 고를 수 없다")
    known = ", ".join(f"{c.get('label') or '-'}({c.get('id')})" for c in cams) or "없음"
    log.warning("PIPER 카메라 목록에 %r 가 없어 id 로 보고 그대로 쓴다. 등록된 카메라: %s", ref, known)
    return ref


async def pull_camera(
    name: str, ref: str, hub: FrameHub, base_url: str, fps: float, auto_connect: bool = True,
) -> None:
    """ref 는 PIPER 카메라 라벨(권장) 또는 id."""
    backoff = 1.0
    last_connect_try = -AUTO_CONNECT_INTERVAL_S
    # read 는 스트림 파트 사이의 간격이다 — 이만큼 프레임이 없으면 멈춘 스트림으로 보고 다시 붙는다.
    timeout = httpx.Timeout(10.0, read=STALL_TIMEOUT_S)

    async def connect(cam: dict) -> None:
        nonlocal last_connect_try
        wait = AUTO_CONNECT_INTERVAL_S - (time.monotonic() - last_connect_try)
        if wait > 0:
            raise RuntimeError(f"PIPER 에서 {ref!r}({cam['id']}) 가 끊겨 있다 — {wait:.0f}초 뒤 다시 연결을 시도한다")
        last_connect_try = time.monotonic()
        await _auto_connect(client, base_url, ref, cam)

    async with httpx.AsyncClient(timeout=timeout) as client:
        while True:
            try:
                # 재연결할 때마다 다시 찾는다 — 그 사이 장치 번호가 바뀌었을 수 있다
                cam_id = await resolve_camera_id(client, base_url, ref, connect if auto_connect else None)
                url = f"{base_url}/api/cameras/{quote(cam_id, safe='/')}/stream"
                async with client.stream("GET", url, params={"fps": fps}) as response:
                    response.raise_for_status()
                    log.info("PIPER 카메라 연결: %s ← %s", name, url)
                    backoff = 1.0
                    async for jpeg in _read_parts(response):
                        hub.publish(jpeg)
                log.warning("PIPER 카메라 스트림 종료: %s", name)
                hub.set_error("PIPER 카메라 스트림이 끊겼다")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                reason = _describe(e)
                log.warning("PIPER 카메라 %s 연결 실패 (%s), %.0f초 뒤 재시도", name, reason, backoff)
                hub.set_error(reason)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_MAX_S)
