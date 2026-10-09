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
# MOCK 미션을 중간에 멈춰서(이벤트를 끊어서) 멈춘 미션 처리를 재현한다
MOCK_STALL = os.environ.get("MOCK_STALL") == "1"

# 진행 중 미션에서 로봇 이벤트가 이만큼 없거나 로봇팔이 이만큼 끊겨 있으면 stalled 로 본다.
# 사과 하나(집기→검사→놓기→복귀)가 실제 장비에서 20~25초라 60초면 여유가 있다.
MISSION_STALE_S = float(os.environ.get("MISSION_STALE_S", "60"))

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
# PIPER 의 로봇팔 상태를 읽어 GET /arm 으로 알릴지. 기본은 카메라를 PIPER 에서 받을 때 켠다. 읽기만 한다.
ARM_MONITOR = (os.environ.get("ARM_MONITOR") or ("1" if CAMERA_SOURCE == "piper" else "0")) == "1"
# PIPER 에서 끊긴(꽂혀 있는) 카메라를 자동으로 다시 연결할지
PIPER_AUTO_CONNECT = os.environ.get("PIPER_AUTO_CONNECT", "1") == "1"

# 판정 / 모션 기록 SQLite 파일. docker-compose 에서 ./data 를 /data 로 마운트해 컨테이너를 다시 만들어도 남긴다.
DB_PATH = Path(os.environ.get("DB_PATH", "/data/ssorry.db"))

# CSV 의 time 열 시간대 (UTC 기준 시). 컨테이너는 UTC 라서 한국 시간으로 맞춘다.
CSV_UTC_OFFSET_HOURS = float(os.environ.get("CSV_UTC_OFFSET_HOURS", "9"))

# 로봇 미션 프로그램(Piper `mission.py --serve`)의 원격 제어 주소. /control/* 가 여기로 넘긴다.
ROBOT_CONTROL_URL = os.environ.get("ROBOT_CONTROL_URL", "http://host.docker.internal:8765").rstrip("/")

# AI 조언 (POST /advice). 키는 서버 .env 에만 둔다.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ADVICE_MODEL = os.environ.get("ADVICE_MODEL", "claude-haiku-4-5")
# 질문 없는 조언 요청이 이 간격 안에 또 오면 직전 조언을 돌려준다 (공개 주소라 비용 보호)
ADVICE_MIN_INTERVAL_S = float(os.environ.get("ADVICE_MIN_INTERVAL_S", "10"))

# /ingest/* 를 보호하는 토큰. 비어 있으면 검사하지 않는다(LAN 신뢰).
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")

# 쉼표로 구분한 허용 출처. 예: http://192.168.0.5:5173,http://localhost:5173
CORS_ORIGINS = _list("CORS_ORIGINS")

# 빌드한 프론트(index.html 이 있는 폴더). 있으면 / 에서 서빙하고, 없으면 내장 카메라 뷰어를 띄운다.
# 프론트를 API 와 같은 출처에서 열어야 세션 쿠키가 웹소켓에 실린다.
FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIR", "/workspace/frontend"))
STATIC_DIR = Path(__file__).parent / "static"
