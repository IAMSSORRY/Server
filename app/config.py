"""환경변수 설정. 다른 모듈은 os.environ 을 직접 읽지 않고 여기서 가져간다."""

import os
from pathlib import Path


def _list(name: str, default: str = "") -> list[str]:
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


def _map(name: str) -> dict[str, str]:
    """"top=a,wrist=b" → {"top": "a", "wrist": "b"}"""
    out = {}
    for item in _list(name):
        key, _, value = item.partition("=")
        out[key.strip()] = value.strip() or key.strip()
    return out


# 카메라 이름. 첫 번째가 기본 카메라(/ws/camera 에 cam 을 안 주면 이것).
CAMERAS = _list("CAMERAS", "top,wrist")

MOCK = os.environ.get("MOCK") == "1"

# 카메라 프레임을 어디서 받을지. MOCK=1 이면 무시된다.
#   piper  : PIPER Studio 게이트웨이의 MJPEG 스트림을 받아온다
#   ingest : 외부 프로세스(LeRobot 등)가 WS /ingest/camera/{cam} 으로 밀어 넣는다
CAMERA_SOURCE = os.environ.get("CAMERA_SOURCE", "ingest")

# PIPER Studio 웹 주소 (nginx). 예: http://192.168.0.20
PIPER_URL = os.environ.get("PIPER_URL", "http://localhost").rstrip("/")
# 우리 카메라 이름 → PIPER 카메라 라벨(권장) 또는 id. 예: top=top,wrist=wrist
# /dev/videoN 은 재부팅하면 바뀔 수 있으므로 PIPER 화면에서 라벨을 붙여 라벨로 적는다.
PIPER_CAMERAS = _map("PIPER_CAMERAS")
PIPER_STREAM_FPS = float(os.environ.get("PIPER_STREAM_FPS", "15"))
# PIPER 에서 끊긴(꽂혀 있는) 카메라를 자동으로 다시 연결할지
PIPER_AUTO_CONNECT = os.environ.get("PIPER_AUTO_CONNECT", "1") == "1"

# /ingest/* 를 보호하는 토큰. 비어 있으면 검사하지 않는다(LAN 신뢰).
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")

# 쉼표로 구분한 허용 출처. 예: http://192.168.0.5:5173,http://localhost:5173
CORS_ORIGINS = _list("CORS_ORIGINS")

# 빌드한 프론트(index.html 이 있는 폴더). 있으면 / 에서 서빙하고, 없으면 내장 카메라 뷰어를 띄운다.
# 프론트를 API 와 같은 출처에서 열어야 세션 쿠키가 웹소켓에 실린다.
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", "/workspace/frontend"))
STATIC_DIR = Path(__file__).parent / "static"
