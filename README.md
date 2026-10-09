# Ssorry — 판정 결과 / 카메라 실시간 웹 서버

SO-101 로봇팔로 물체를 집어 상/중으로 판정하는 과정을 웹에서 실시간으로 보여주는 FastAPI 서버.
카메라 영상, 판정 결과, 누적 통계를 웹소켓과 REST 로 내보낸다.

## 전체 구성

```
[Ubuntu 로봇 PC]
  PIPER Studio ── 카메라(camerad) 소유, MJPEG 스트림 ──┐  (CAMERA_SOURCE=piper)
  LeRobot 프로세스 ── SO-101 제어 / 추론 / 상중 판정 ──┤  POST /ingest/judge, /ingest/motion
                     (카메라를 직접 쥐면 프레임도) ────┤  WS   /ingest/camera/{cam}  (CAMERA_SOURCE=ingest)
                                                      ▼
                                          [Ssorry 서버 :8000]
                                                      │  WS /ws/camera, /ws/judge, REST
                                                      ▼
                                          [브라우저 — 이 서버가 서빙한 프론트]
```

- **장비**: SO-101 팔 2대(리더 / 팔로워, 그리퍼가 다르다), 카메라 2대(손목 1, 고정 1).
- **PIPER Studio** 는 Ubuntu PC 에만 설치돼 있다. 카메라 장치를 camerad 가 쥐고
  `GET /api/cameras/{id}/stream` (MJPEG) 으로 내보내므로, 이 서버는 그 스트림을 받아 쓴다.
  PIPER Studio 에서 SO-101 은 리더로만 지원되므로 팔 제어와 추론은 LeRobot 이 직접 한다.
- **LeRobot 쪽 프로세스**는 [`tools/ssorry_client.py`](tools/ssorry_client.py) 로 판정 / 모션
  이벤트를 이 서버에 보낸다. 카메라를 LeRobot 이 직접 쥐는 경우에는 프레임도 이걸로 밀어 넣는다
  (한 카메라를 두 프로세스가 동시에 열 수 없다).
- ROS2 는 쓰지 않는다.

## 구조

```
Dockerfile           python:3.10-slim + fastapi/uvicorn
docker-compose.yml   포트 8000, app/ 를 마운트해 --reload
requirements.txt     fastapi, uvicorn, pydantic, httpx(PIPER 스트림), pillow(MOCK 프레임)
app/main.py          FastAPI 앱, 세션 미들웨어, 웹소켓 / REST, 프론트 서빙
app/config.py        환경변수
app/state.py         카메라 허브, 판정 허브, 세션 저장소 (프로세스당 하나)
app/stream.py        카메라별 최신 프레임 허브
app/judge.py         판정 / 모션 이벤트, 누적 통계, 이력, 구독자별 큐
app/sessions.py      로그인 없는 쿠키 세션
app/ingest.py        로봇 쪽 프로세스가 이벤트와 프레임을 밀어 넣는 입구
app/sources/piper.py PIPER Studio 카메라 MJPEG 스트림 수신
app/sources/mock.py  MOCK=1 일 때 카메라 / 판정 / 모션을 흉내 내는 태스크
app/static/          내장 카메라 뷰어 (프론트 빌드가 없을 때)
tools/ssorry_client.py  LeRobot 쪽에서 쓰는 클라이언트 (표준 라이브러리 + websockets)
```

## 실행

```bash
docker compose up -d
docker compose logs -f api
```

로봇과 카메라 없이 프론트를 개발할 때는 `MOCK=1` 로 띄운다.
카메라마다 프레임(10fps, 640x480), 4초마다 판정, 판정 1.5초 뒤 모션 이벤트가 나온다.

```bash
MOCK=1 docker compose up -d
```

PIPER Studio 의 카메라를 쓸 때 (같은 Ubuntu PC 에서 띄우는 경우):

```bash
CAMERA_SOURCE=piper \
PIPER_URL=http://host.docker.internal \
PIPER_CAMERAS="top=<PIPER 카메라 id>,wrist=<PIPER 카메라 id>" \
docker compose up -d
```

PIPER 카메라 id 는 PIPER Studio 의 카메라 페이지(또는 `GET <PIPER>/api/cameras/current`)에서 확인한다.
다른 PC 에서 띄우면 `PIPER_URL` 을 `http://<Ubuntu IP>` 로 준다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `MOCK` | `0` | `1` 이면 카메라 / 판정 / 모션을 흉내 낸다. 다른 소스 설정은 무시된다 |
| `CAMERAS` | `top,wrist` | 카메라 이름. 첫 번째가 기본 카메라 |
| `CAMERA_SOURCE` | `ingest` | `piper`: PIPER 스트림을 받아온다 / `ingest`: `WS /ingest/camera/{cam}` 으로 받는다 |
| `PIPER_URL` | `http://host.docker.internal` | PIPER Studio 웹 주소 (nginx, 기본 포트 80) |
| `PIPER_CAMERAS` | (비어 있음) | `이름=PIPER카메라id` 쉼표 목록. 빠진 카메라는 ingest 로 받을 수 있다 |
| `INGEST_TOKEN` | (비어 있음) | 설정하면 `/ingest/*` 에 `Authorization: Bearer <token>` 이 필요하다 |
| `CORS_ORIGINS` | (비어 있음) | 쉼표로 구분한 허용 출처. 비어 있으면 CORS 를 걸지 않는다 |
| `FRONTEND_DIST` | `./frontend-dist` | (compose) 프론트 빌드 결과 폴더. 컨테이너의 `/workspace/frontend` 에 마운트된다 |

## 엔드포인트

| 엔드포인트 | 설명 |
|---|---|
| `GET /` | 프론트 빌드 결과, 없으면 내장 카메라 뷰어 (세션 쿠키 발급) |
| `GET /session` | 현재 세션 ID, 열린 웹소켓 수 |
| `GET /cameras` | 카메라 목록, 기본 카메라, 프레임이 들어오고 있는지 |
| `WS /ws/camera?cam=<이름>` | 카메라 JPEG 프레임을 바이너리로 계속 전송. `cam` 이 없으면 기본 카메라 |
| `WS /ws/judge` | 연결 직후 `snapshot`, 이후 `judge` / `stats` / `motion` 이벤트를 JSON 텍스트로 전송 |
| `GET /stats?limit=50` | 누적 통계, 사이클 타임, 최근 판정 `limit` 개 |
| `GET /history` | 판정 이력 전체 |
| `POST /stats/reset` | 누적 통계와 이력 초기화 (데모 재시작용) |
| `GET /health` | 서버 상태, 카메라 소스 |
| `POST /ingest/judge` | (로봇 쪽) 판정 하나 |
| `POST /ingest/motion` | (로봇 쪽) 모션 결과 하나 |
| `WS /ingest/camera/{cam}` | (로봇 쪽) 바이너리 메시지 하나 = JPEG 한 장 |
| `GET /docs` | Swagger UI |

### 프론트 서빙

세션은 `SameSite=Lax` 쿠키라서 프론트를 API 와 **같은 출처**에서 열어야 웹소켓에 쿠키가 실린다.
다른 기기에서 열 때도 프론트를 따로 띄우지 말고 이 서버가 서빙한 `http://<서버 IP>:8000/` 로 연다.

- 프론트 빌드 결과(`index.html` 이 있는 폴더)를 `frontend-dist/` 에 두거나 `FRONTEND_DIST` 로 경로를 준다.
- `index.html` 이 있으면 `/` 에서 서빙하고, API 에 없는 경로는 `index.html` 로 돌린다(SPA 라우팅).
- 폴더가 비어 있으면 내장 카메라 뷰어(`app/static/index.html`)가 뜬다.
- 폴더를 처음 채웠거나 비웠을 때는 서버를 재시작해야 반영된다(`docker compose restart api`). 이미 서빙 중일 때 파일만 바꾸는 건 바로 반영된다.
- 개발 중에 Vite 같은 dev 서버를 쓸 때는 dev 서버의 프록시로 `/ws`, `/stats`, `/history`, `/session`, `/cameras` 를 `localhost:8000` 에 넘기면 같은 출처가 된다.

### 카메라 스트리밍

- 웹소켓에 처음 붙으면 `{"type":"hello","session":...,"cam":"top","cams":["top","wrist"]}` 텍스트가 오고, 그 뒤로는 프레임마다 JPEG 바이너리가 온다.
- 클라이언트마다 최신 프레임만 보내므로 느린 클라이언트는 중간 프레임을 건너뛴다.
- 세션은 `ssorry_sid` 쿠키로 구분하며 메모리에만 있다. 쿠키 없이 웹소켓에 붙으면 4401, 없는 카메라는 4404 로 닫힌다.
  연결 없이 1시간 지난 세션은 정리된다.
- 카메라 해상도는 소스가 정한다. PIPER Studio 를 쓰면 그쪽 카메라 설정의 **출력 해상도**가 그대로 나온다.
  ELP 글로벌 셔터(AR0234) 카메라는 저해상도로 열면 센서 가운데만 잘라 화각이 좁아지므로,
  PIPER 에서 캡처 해상도를 넓게(예: 1280x960) 잡고 출력 해상도로 줄이는 것이 좋다.

### 판정 / 모션 이벤트

로봇 쪽 프로세스가 `POST /ingest/judge`, `POST /ingest/motion` 으로 보낸다.
판정 이벤트는 하나도 빠지면 안 되므로 웹소켓 클라이언트마다 큐를 둔다
(카메라는 최신 프레임만 보낸다). 통계와 이력은 메모리에만 있어서 재시작하면 초기화된다.

**로봇 쪽에서 보내는 형식** (`tools/ssorry_client.py` 가 이 형식으로 보낸다)

```json
// POST /ingest/judge — id 는 서버가 붙인다. ts 를 빼면 수신 시각, cycle_time 을 빼면 직전 판정과의 간격을 쓴다.
// cam 은 bbox 기준 카메라. 빼면 기본 카메라. 형식이 틀리면 422.
{"grade": "상", "confidence": 0.87, "v_value": 182, "threshold": 160, "bbox": [412, 188, 96, 96], "cam": "top", "ts": 1791527966.54}

// POST /ingest/motion — id 를 넣으면 그 판정에, 빼면 모션이 아직 없는 가장 최근 판정에 roll_detected 가 기록된다.
{"approach_speed": 0.8, "place_height": 0.12, "roll_detected": false, "ts": 1791527968.04}
```

```python
from ssorry_client import SsorryClient

client = SsorryClient("http://<서버IP>:8000", token=None)
client.send_judge(grade="상", confidence=0.87, v_value=182, threshold=160, bbox=[412, 188, 96, 96], cam="top")
client.send_motion(approach_speed=0.8, place_height=0.12, roll_detected=False)

# 카메라를 LeRobot 이 쥐고 있을 때 (CAMERA_SOURCE=ingest)
pusher = client.camera_pusher("wrist")
pusher.push(cv2.imencode(".jpg", frame)[1].tobytes())   # 막히지 않고, 밀리면 최신 프레임만 보낸다
```

**`WS /ws/judge` 로 나가는 메시지**

세션 처리는 `/ws/camera` 와 같다(쿠키가 없으면 4401). 큐가 1000개 넘게 밀린 클라이언트는
이벤트를 버리는 대신 4408 로 끊는다. 재연결하면 `snapshot` 부터 다시 받는다.

```json
// 연결 직후 한 번, 그리고 POST /stats/reset 직후 모든 클라이언트에 한 번
{"type": "snapshot",
 "stats": {"상": 3, "중": 2, "total": 5},
 "cycle_time": 4.2,
 "recent": [{"id": 5, "grade": "중", "confidence": 0.81, "v_value": 140, "threshold": 160,
             "ts": 1791527982.74, "roll_detected": null}]}

// 판정마다. bbox 는 cam 카메라의 /ws/camera JPEG 기준 픽셀 [x, y, w, h]
{"type": "judge", "id": 6, "grade": "상", "confidence": 0.87, "v_value": 182, "threshold": 160,
 "bbox": [412, 188, 96, 96], "cam": "top", "ts": 1791527986.84}

// 판정 직후 갱신된 누적 통계
{"type": "stats", "stats": {"상": 4, "중": 2, "total": 6}, "cycle_time": 4.1}

// 모션 이벤트
{"type": "motion", "approach_speed": 0.8, "place_height": 0.12, "roll_detected": false, "ts": 1791527988.34}
```

**REST 응답**

```json
// GET /stats?limit=50
{"stats": {"상": 4, "중": 2, "total": 6}, "cycle_time": 4.1,
 "recent": [{"id": 6, "grade": "상", "confidence": 0.87, "v_value": 182, "threshold": 160,
             "ts": 1791527986.84, "roll_detected": false}]}

// GET /history
[{"id": 1, "grade": "상", "confidence": 0.93, "v_value": 229, "threshold": 160,
  "ts": 1791527966.54, "roll_detected": true}]

// POST /stats/reset
{"stats": {"상": 0, "중": 0, "total": 0}}

// GET /cameras
{"default": "top", "cameras": [{"name": "top", "live": true}, {"name": "wrist", "live": false}]}
```

`roll_detected` 는 그 판정에 대한 모션 이벤트가 오기 전까지 `null` 이다.
`cycle_time` 은 판정이 두 번 이상 들어오기 전까지 `null` 이다.

## 개발 환경

로컬 `.venv` (Python 3.10) 에 `requirements.txt` 를 설치하면 PyCharm 에서 바로 실행 / 디버깅할 수 있다.
ROS 를 걷어냈으므로 컨테이너가 필수는 아니다.

```bash
.venv/bin/pip install -r requirements.txt
MOCK=1 FRONTEND_DIR=./frontend-dist .venv/bin/uvicorn app.main:app --reload
```
