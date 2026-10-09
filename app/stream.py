"""카메라 프레임을 웹소켓 클라이언트들에게 넘긴다.

프레임은 카메라 소스(PIPER 스트림, /ingest/camera, MOCK)에서 이벤트 루프 안으로 들어온다.
클라이언트마다 큐를 두지 않고 "가장 최신 프레임" 하나만 들고 있어서,
느린 클라이언트는 중간 프레임을 건너뛰고 항상 최신 화면을 받는다.
"""

import asyncio
import time

# 이 시간 안에 프레임이 왔으면 살아 있는 카메라로 본다.
LIVE_WINDOW_S = 3.0


class FrameHub:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._frame: bytes | None = None
        self._seq = 0
        self._last_at: float | None = None
        # 소스가 알려준 마지막 실패 이유 (예: "PIPER 에서 연결 안 됨"). 프레임이 오면 지운다.
        self._error: str | None = None

    def publish(self, frame: bytes) -> None:
        self._frame = frame
        self._seq += 1
        self._last_at = time.monotonic()
        self._error = None
        event, self._event = self._event, asyncio.Event()
        event.set()

    def set_error(self, reason: str) -> None:
        """소스가 카메라를 못 받아올 때 이유를 남긴다. 클라이언트의 다운 메시지에 실린다."""
        self._error = reason

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def last_frame_age(self) -> float | None:
        """마지막 프레임 이후 지난 초. 한 번도 안 왔으면 None."""
        return None if self._last_at is None else time.monotonic() - self._last_at

    @property
    def live(self) -> bool:
        age = self.last_frame_age
        return age is not None and age < LIVE_WINDOW_S

    async def next_frame(self, last_seq: int) -> tuple[int, bytes]:
        """last_seq 이후의 새 프레임이 들어올 때까지 기다린다."""
        while self._seq == last_seq or self._frame is None:
            await self._event.wait()
        return self._seq, self._frame


class CameraHubs:
    """카메라 이름(예: top, wrist)마다 FrameHub 하나."""

    def __init__(self, names: list[str]) -> None:
        if not names:
            raise ValueError("카메라가 하나 이상 있어야 합니다")
        self._hubs = {name: FrameHub() for name in names}

    @property
    def names(self) -> list[str]:
        return list(self._hubs)

    @property
    def default(self) -> str:
        return next(iter(self._hubs))

    def get(self, name: str | None) -> FrameHub | None:
        return self._hubs.get(name or self.default)
