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
docker-compose.yml   포트 8000, app/ 마운트, 설정은 .env
.env.example         Ubuntu 배포용 설정 예시
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

## 배포 (Ubuntu 로봇 PC)

PIPER Studio 와 같은 PC 에 띄운다. 프론트는 시연 노트북에서 로컬로 띄우고 이 서버 IP 로 붙는다.

1. **PIPER Studio 에서 카메라 준비**: 카메라 페이지에서 두 카메라를 등록 / 연결하고 라벨을 `top`, `wrist` 로 붙인다.
   같은 모델 두 대는 이름이 같아서 라벨이 없으면 구분할 수 없다. 확인:

   ```bash
   curl -s http://localhost/api/cameras/current | python3 -m json.tool   # label, id, connected 확인
   ```

2. **설정과 실행**

   ```bash
   git clone https://github.com/IAMSSORRY/Server.git ssorry && cd ssorry
   cp .env.example .env
   sed -i "s/^INGEST_TOKEN=.*/INGEST_TOKEN=$(openssl rand -hex 16)/" .env   # 나머지 값도 확인
   docker compose up -d --build
   docker compose logs -f api      # "PIPER 카메라 연결: top ← ..." 가 보이면 정상
   sudo ufw allow 8000/tcp         # ufw 를 쓰는 경우
   ```

3. **확인**: 같은 네트워크의 다른 기기에서 `http://<Ubuntu IP>:8000/` 을 열면 내장 카메라 뷰어가 뜬다.
   `http://<Ubuntu IP>:8000/cameras` 에서 두 카메라가 `"live": true` 인지 본다.

4. **업데이트**: CI/CD 러너를 설치했으면 `main` 에 push 하면 자동으로 배포된다(아래). 수동으로는 `git pull && docker compose up -d --build`.
   컨테이너는 `restart: unless-stopped` 라 재부팅 후에도 다시 뜬다.

- 서버는 uvicorn 워커 1개로 돈다. 세션 / 통계 / 이벤트가 프로세스 메모리에 있으므로 워커를 늘리지 않는다.
- PIPER 카메라는 라벨로 찾고, 재연결할 때마다 `/api/cameras/current` 에서 현재 id 를 다시 찾는다
  (`/dev/videoN` 은 재부팅이나 USB 재연결로 바뀔 수 있다).
  PIPER 에서 `connected: false` 인 카메라에는 붙지 않고 로그에 "연결돼 있지 않다" 를 남기며 다시 확인한다.
  라벨은 실제로 쓸 카메라에 붙였는지 확인한다(다른 USB 카메라에 붙어 있으면 그 화면이 나간다).
- LeRobot 쪽 `SsorryClient` 에는 `.env` 의 `INGEST_TOKEN` 과 같은 값을 준다. 같은 PC 면 주소는 `http://localhost:8000`.

### CI/CD (자동 배포)

[`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml)

- **테스트** (GitHub 서버, 모든 push / PR): 문법 검사, MOCK 서버 스모크 테스트(`deploy/smoke_test.py`), Docker 이미지 빌드.
- **배포** (Ubuntu 로봇 PC 의 자체 호스팅 러너, `main` push 와 수동 실행만): 테스트가 통과하면
  `~/ssorry` 를 그 커밋으로 맞추고 `docker compose up -d --build` 후 `/health` 를 확인한다.
  로봇 PC 는 사설 IP(172.30.1.21)라 GitHub 서버가 직접 들어올 수 없어서, PC 안의 러너가 일을 받아 간다.
- 공개 저장소이므로 배포 잡은 PR 에서 돌지 않는다(포크의 코드가 로봇 PC 에서 실행되지 않게).

**러너 설치 (Ubuntu 에서 한 번)**

```bash
# 1. 등록 토큰 받기 (저장소 관리자, 1시간 유효)
gh api -X POST repos/IAMSSORRY/Server/actions/runners/registration-token --jq .token
# 2. Ubuntu 에서
cd ~/ssorry && ./deploy/setup-runner.sh <토큰>
```

- 서버 클론 위치가 `~/ssorry` 가 아니면 저장소 Settings → Variables → Actions 에 `DEPLOY_DIR` 을 만든다.
- 배포는 `git reset --hard` 로 클론을 커밋에 맞춘다. `.env`, `frontend-dist/` 같은 git 밖 파일은 그대로지만,
  로봇 PC 클론에서 직접 고친 추적 파일은 지워진다. 고칠 것은 커밋해서 올린다.
- 수동 배포: GitHub → Actions → CI/CD → Run workflow.

### 시연 노트북에서 프론트 붙이기

세션 쿠키가 `SameSite=Lax` 라서 브라우저가 `localhost:5173` 에서 `http://<Ubuntu IP>:8000` 을 직접 부르면
쿠키가 실리지 않아 웹소켓이 4401 로 끊긴다. Vite dev 서버의 프록시로 넘기면 같은 출처가 된다.

```js
// vite.config.js
const API = "http://<Ubuntu IP>:8000";
export default {
  server: {
    proxy: {
      "/ws": { target: API, ws: true },
      "/stats": API, "/history": API, "/session": API, "/cameras": API,
    },
  },
};
```

## 개발

```bash
UVICORN_RELOAD=1 MOCK=1 docker compose up -d   # 소스 변경 시 자동 재시작 + 더미 데이터
docker compose logs -f api
```

`MOCK=1` 이면 카메라마다 프레임(10fps, 640x480), 4초마다 판정, 판정 1.5초 뒤 모션 이벤트가 나온다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `SSORRY_PORT` | `8000` | 호스트에 여는 포트 |
| `UVICORN_RELOAD` | (비어 있음) | `1` 이면 소스 변경 시 자동 재시작 (개발용) |
| `MOCK` | `0` | `1` 이면 카메라 / 판정 / 모션을 흉내 낸다. 다른 소스 설정은 무시된다 |
| `CAMERAS` | `top,wrist` | 카메라 이름. 첫 번째가 기본 카메라 |
| `CAMERA_SOURCE` | `ingest` | `piper`: PIPER 스트림을 받아온다 / `ingest`: `WS /ingest/camera/{cam}` 으로 받는다 |
| `PIPER_URL` | `http://host.docker.internal` | PIPER Studio 웹 주소 (nginx, 기본 포트 80) |
| `PIPER_CAMERAS` | (비어 있음) | `이름=PIPER카메라라벨` 쉼표 목록 (id 도 가능). 빠진 카메라는 ingest 로 받을 수 있다 |
| `INGEST_TOKEN` | (비어 있음) | 설정하면 `/ingest/*` 에 `Authorization: Bearer <token>` 이 필요하다 |
| `CORS_ORIGINS` | (비어 있음) | 쉼표로 구분한 허용 출처. 비어 있으면 CORS 를 걸지 않는다 |
| `FRONTEND_DIST` | `./frontend-dist` | (compose) 프론트 빌드 결과 폴더. 컨테이너의 `/workspace/frontend` 에 마운트된다 |

## 엔드포인트

| 엔드포인트 | 설명 |
|---|---|
| `GET /` | 프론트 빌드 결과, 없으면 내장 카메라 뷰어 (세션 쿠키 발급) |
| `GET /session` | 현재 세션 ID, 열린 웹소켓 수 |
| `GET /cameras` | 카메라 목록, 기본 카메라, 최근 3초 안에 프레임이 왔는지(`live`) |
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
- **카메라 다운 / 복구**: 3초 넘게 프레임이 안 오면 연결을 끊지 않고 상태 메시지를 보낸다. 다시 프레임이 오면
  `live: true` 를 보낸 뒤 같은 연결로 실시간 프레임을 이어서 보낸다. 다운 상태에서 새로 붙으면 마지막(옛날) 프레임은
  보내지 않고 다운 메시지부터 보낸다. PIPER 소스는 끊기면 최대 3초 간격으로 다시 붙는다.

  ```json
  {"type": "camera_status", "cam": "top", "live": false, "message": "카메라를 불러오지 못했습니다",
   "reason": "PIPER 에서 'wrist'(/dev/video4) 가 연결돼 있지 않다 — 카메라 페이지에서 연결하세요", "last_frame_age": 5.1}
  {"type": "camera_status", "cam": "top", "live": true}
  ```

  `reason` 은 원인을 아는 경우 그 이유, 모르면 "3초 넘게 프레임이 들어오지 않았다". `last_frame_age` 는 한 번도 안 왔으면 null.
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
// live: 최근 3초 안에 프레임이 왔는가. last_frame_age: 마지막 프레임 이후 초 (한 번도 안 왔으면 null)
// error: live 가 false 일 때 소스가 알려준 원인 (없으면 null)
{"default": "top", "cameras": [{"name": "top", "live": true, "last_frame_age": 0.04},
                               {"name": "wrist", "live": false, "last_frame_age": null}]}
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
