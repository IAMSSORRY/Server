# Ssorry — 판정 결과 / 카메라 실시간 웹 서버

PIPER 로봇팔로 사과를 집어 품질(상/중/하)을 판정하는 과정을 웹에서 실시간으로 보여주는 FastAPI 서버.
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
app/db.py            판정 / 모션 기록 SQLite (단일 DB 스레드)
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

- 서버는 uvicorn 워커 1개로 돈다. 세션 / 현재 회차 통계 / 이벤트 구독이 프로세스 메모리에 있으므로 워커를 늘리지 않는다.
- 판정 기록은 `./data/ssorry.db` 에 남는다(아래 [데이터 보관](#데이터-보관)). CI/CD 배포의 `git reset --hard` 도 `data/` 는 건드리지 않는다.
- PIPER 카메라는 라벨로 찾고, 재연결할 때마다 `/api/cameras/current` 에서 현재 id 를 다시 찾는다
  (`/dev/videoN` 은 재부팅이나 USB 재연결로 바뀔 수 있다).
  **USB 를 뽑았다 다시 꽂아도 자동으로 돌아온다.** PIPER 는 스스로 복구하지 않으므로 사람이 화면에서 하던 일을 서버가 한다
  (끄려면 `PIPER_AUTO_CONNECT=0`):
  1. 카메라가 끊긴 게 보이면 PIPER [스캔](`GET /api/cameras/scan`)을 돌린다 — PIPER 는 스캔해야 다시 꽂힌 걸 안다 (5초에 한 번까지)
  2. 같은 장치 번호로 돌아왔으면 [연결](`POST /api/cameras/connect`) (10초에 한 번까지)
  3. 번호가 바뀌어 돌아왔으면(`/dev/video4` → `/dev/video6`) 같은 USB 포트의 새 장치로 라벨을 옮긴다
     (옛 등록 해제 → 새 장치를 같은 라벨로 등록). 다른 포트에 꽂았으면 같은 이름의 카메라가 하나뿐일 때만 옮긴다.
  - 스트림이 5초 넘게 조용하면 끊고 카메라 상태부터 다시 본다.
  - 카메라가 뽑혀 있는 동안은 5초마다 스캔한다. PIPER 에서 녹화 / 추론이 카메라를 쥐고 있으면 스캔은 캐시만 돌려준다.
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
| `PIPER_AUTO_CONNECT` | `1` | PIPER 에서 끊긴(꽂혀 있는) 카메라를 자동으로 다시 연결 |
| `PIPER_CAMERAS` | (비어 있음) | `이름=PIPER카메라라벨` 쉼표 목록 (id 도 가능). 빠진 카메라는 ingest 로 받을 수 있다 |
| `INGEST_TOKEN` | (비어 있음) | 설정하면 `/ingest/*` 에 `Authorization: Bearer <token>` 이 필요하다 |
| `DB_PATH` | `/data/ssorry.db` | 판정 기록 SQLite 파일 (compose 는 `./data` 를 `/data` 에 마운트) |
| `CSV_UTC_OFFSET_HOURS` | `9` | `/export.csv` 의 time 열 시간대 (한국 시간) |
| `ARM_MONITOR` | `CAMERA_SOURCE=piper` 면 `1` | PIPER 로봇팔 상태 감시(`GET /arm`). 읽기만 한다 |
| `MISSION_STALE_S` | `60` | 진행 중 미션에서 로봇 이벤트가 이만큼 없거나 로봇팔이 이만큼 끊기면 `stalled` |
| `MOCK_STALL` | `0` | `1` 이면 MOCK 미션을 중간에 멈춰 `stalled` 를 재현 |
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
| `GET /history?run=<id>` | 판정 이력. `run` 을 생략하면 현재 회차 |
| `POST /stats/reset` | 현재 회차를 닫고 새 회차 시작 (통계 0 부터). 이전 기록은 DB 에 남는다 |
| `GET /runs` | 회차 목록과 회차별 통계 (최신순) |
| `GET /export.csv?run=<id>` | 회차 이력 CSV (UTF-8 BOM). `run` 을 생략하면 전체 회차 |
| `GET /mission?events=<n>` | 로봇 미션 진행 상태. `events` 를 주면 현재 회차의 최근 미션 이벤트 n 개도 |
| `POST /ingest/mission` | (로봇 쪽) 미션 진행 이벤트 하나 |
| `GET /arm` | 로봇팔 상태 (PIPER 에서 3초마다 읽기만 한다). `ok` 가 false 면 `message` 에 이유 |
| `GET /control/status` | 로봇 미션 프로그램 상태 `{state, error, index, placed, results}` |
| `POST /control/start` | 미션 시작 `{"apples": 5}` (`"all"` = 트레이가 빌 때까지, 생략 = 로봇 설정) |
| `POST /control/estop` | 비상정지: **즉시 그 자리 정지 → 모터 정지** (~0.2초). 팔을 낮추지 않는다. 다른 명령 처리 중에도 바로 나간다 |
| `POST /control/park` | 정리 후 정지: 그 자리 정지 → 쥔 사과를 집은 자리에 되돌림 → 팔을 낮게 → 모터 정지 (수 초, 타임아웃 20초) |
| `POST /control/resume` | 비상정지·오류 정지 해제 → 멈춘 사과부터 이어서 |
| `POST /control/stop` | 지금 사과까지만 하고 멈춤 |
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

### 미션 진행 (로봇 쪽에서 보내는 실시간 값)

로봇 쪽([IAMSSORRY/Piper](https://github.com/IAMSSORRY/Piper) `mission.py`)이 판정 / 모션 말고도
진행 상황을 `POST /ingest/mission` 으로 보낸다. 서버는 상태를 갱신해 `/ws/judge` 로
`{"type": "mission", "event": {...}, "state": {...}}` 를 보내고, 이벤트를 DB `events` 테이블에 남긴다.

| event | 필드 | 뜻 |
|---|---|---|
| `start` | `apple_count`, `sim` | 미션 시작 (상태 초기화) |
| `apple` | `index`, `total` | n 번째 사과 시작 (1부터) |
| `phase` | `phase` | 지금 동작: `pick` / `inspect` / `place` / `home` / `nudge`(벽에 붙은 사과 굴리기) / `estop_return`(비상정지 때 쥔 사과 되돌리기) / `estop_rest`(비상정지 때 팔 내리기) |
| `pick` | `ok`, `attempt`, `width_mm` | 파지 결과 (`attempt` 0 = 첫 시도) |
| `skip` | `index`, `reason` | 그 사과 건너뜀 (파지 실패, 도달 불가 등) |
| `adaptive` | `scale`, `release_h`, `frozen`, `down_streak` | 굴림 적응 조정 상태. `frozen` 이면 자동 조정 중단 |
| `estop` | `reason` | 비상정지 / 로봇 고장 |
| `end` | `duration_s`, `results` | 미션 끝 |
| `stalled` | `idle_s`, `reason` | **서버가 만든다.** 진행 중인데 로봇 소식이 끊김 (아래) |

알 수 없는 event 도 받아서 저장 / 전달한다(상태는 안 바뀐다). `event` 가 없으면 422.
로봇이 상태 표에 없는 이벤트도 보낸다 (저장 / 전달만):

| event | 필드 | 뜻 |
|---|---|---|
| `box` | `info`, `상`, `중`, `하` | 미션 시작 때 사진으로 찾은 상자 칸 위치 [x, y] m 와 기준 대비 이동·회전 |
| `drop` | `where`, `width_mm` | 운반 중 사과를 놓침 (경고로 보일 것) |
| `drop_measure` | `index`, `drop_mm`, `dropped` | 놓을 때 낙하 거리. `dropped` 가 true 면 경고 |
| `control` | `state`, `error` | 원격 제어 상태 변화 (`/control/status` 의 state) |
| `resume` | `index` | 비상정지 해제 후 이어서 시작. 바로 뒤에 `apple` 이 와서 상태가 진행 중으로 돌아간다 |

어떤 이유로 멈췄든(원격 비상정지, 힘 이상 자동 정지, 오류, PIPER Studio 비상정지) 로봇은 `estop` 을 보낸다.

```json
// /ws/judge 연결 직후 snapshot 다음에 한 번 (event 는 null), 그 뒤로 이벤트마다
{"type": "mission", "event": {"event": "pick", "ts": 1791543850.1, "ok": false, "attempt": 0, "width_mm": 0.0},
 "state": {"status": "running", "sim": false, "apple_count": 10, "apple_index": 3, "phase": "pick",
           "picks_ok": 2, "picks_failed": 1, "skipped": 0,
           "adaptive": {"scale": 0.8, "release_h": 0.04, "frozen": false, "down_streak": 1},
           "estop_reason": null, "started_at": 1791543842.8, "ended_at": null, "duration_s": null,
           "updated_at": 1791543850.1}}
```

`POST /stats/reset`(새 회차) 때 회차별 집계(`picks_ok`, `picks_failed`, `skipped`)는 0 이 된다. 미션이 진행 중(`running`)이면
진행 상태(`apple_count`, `apple_index`, `phase`, `adaptive` 등)는 그대로 두고, 아니면 state 전체가 idle 초기값이 된다.
reset 직후 모든 `/ws/judge` 에 연결 직후와 같은 순서로 snapshot → mission(event: null) 을 보낸다.

`status`: `idle`(서버 시작 후 아직 없음) / `running` / `stalled`(멈춤) / `finished` / `estop`.

**멈춘 미션 (`stalled`)**: `running` 인데 마지막 로봇 이벤트 이후 `MISSION_STALE_S`(기본 60초) 동안 이벤트가 없거나,
로봇팔이 끊긴 상태(`GET /arm` 의 `ok: false`)가 그만큼 이어지면 서버가 스스로 `stalled` 로 바꾼다
(1초마다 검사, 클라이언트가 없어도 바뀐다. 사과 하나에 실제 장비로 20~25초 걸린다).
- 이벤트 `{"event": "stalled", "ts": ..., "idle_s": 61.2, "reason": "로봇에서 60초 동안 이벤트가 없습니다"}` 를 기록하고
  mission 메시지로 보낸다. 로봇팔 때문이면 reason 은 "로봇팔 연결이 끊겨 미션이 멈췄습니다".
- `apple_index`, `phase`, `picks_ok` 등은 그대로 둔다(어디서 멈췄는지 보이게). 정상 종료가 아니므로 `ended_at` 은 비워 둔다.
- 그 뒤 로봇 이벤트가 다시 오면 `running` 으로 돌아온다. `start` 가 오면 새 미션, `end` / `estop` 은 그대로 반영.
- 멈춤 판단은 서버가 이벤트를 **받은** 시각으로 한다(로봇 PC 시계와 무관).
- 재현: `MOCK=1 MOCK_STALL=1` — 첫 미션의 사과 2 를 집은 뒤 `MISSION_STALE_S + 10` 초 동안 이벤트를 끊었다가 이어 간다. 미션 상태는 메모리에만 있어 서버를 재시작하면
`idle` 로 돌아온다(이벤트 기록은 DB 에 남는다).

**판정 추가 근거 `extra`**: 등급은 빨강 비율(`v_value`)과 흠·멍·상처를 함께 본다. 로봇이 `extra` 객체를 보내면
판정 메시지와 DB 에 그대로 실린다(보내지 않으면 판정 메시지에 `extra` 필드가 없다 — 기존과 같다).
서버는 내용을 해석하지 않고 전달만 한다. 로봇 쪽(Piper `vision.defect_extra`)이 보내는 키:

| 키 | 뜻 |
|---|---|
| `dark_ratio`, `bruise_ratio`, `wound_ratio` | 흠(아주 어두운 점) / 멍 / 상처(드러난 과육) 비율, 사과 안쪽 원 기준 |
| `dark_max`, `bruise_max`, `wound_max` | 상 기준 (이하여야 상) |
| `dark_low`, `bruise_low`, `wound_low`, `red_low` | 하 기준 (결함은 초과면 하, `red_low` 는 빨강 비율 미만이면 하) |
| `reasons` | 등급 이유 문자열 목록. 예: `["멍 0.050 > 0.03"]`. 상이면 빈 목록 |

```json
{"type": "judge", "id": 6, "grade": "중", "confidence": 0.71, "v_value": 0.62, "threshold": 0.5,
 "bbox": [412, 188, 96, 96], "cam": "top", "ts": 1791527986.84,
 "extra": {"dark_ratio": 0.14, "dark_max": 0.1, "bruise_ratio": 0.0, "wound_ratio": 0.0,
           "dark_low": 0.25, "bruise_max": 0.03, "bruise_low": 0.12, "wound_max": 0.005, "wound_low": 0.02,
           "red_low": 0.25, "reasons": ["흠 0.140 > 0.1"]}}
```

### 로봇 원격 제어 (`/control/*`)

프론트의 시작·비상정지·해제 버튼이 부른다. 서버는 로봇 PC 의 미션 프로그램
(Piper `mission.py --serve`, 기본 `:8765`)으로 그대로 넘긴다. 로봇 주소는 `ROBOT_CONTROL_URL`
(기본 `http://host.docker.internal:8765`), 토큰은 `/ingest` 와 같은 `INGEST_TOKEN` 이라 브라우저에는 나가지 않는다.

- `state`: `idle` 대기 / `running` 실행 중 / `stopping` `/park` 정리 중 / `estopped` 비상정지 / `error` 오류로 그 자리 정지 / `done` 완료
- `/control/estop` 은 즉시 정지라 위급할 때 쓰고, 위급하지 않으면 `/control/park` 로 팔을 낮춘 뒤 멈춘다(높은 곳에서 모터가 멈추면 처질 수 있다).
  `/park` 정리 중에 `/control/estop` 을 보내면 정리를 버리고 바로 멈춘다(요청마다 새 연결이라 앞 요청을 기다리지 않는다).
- 타임아웃: `estop` 3초, `park` 20초, `resume` 15초, 나머지 5초.
- 지금 할 수 없는 명령(실행 중에 `start`, 이미 비상정지인데 `park` 등)은 로봇 응답 그대로 `ok: false` 와 `error`(= `message`) 를 준다 (웹 `api.ts` 는 `error` 를 읽는다).
- 로봇 미션 프로그램이 안 떠 있으면 `503`, 토큰이 안 맞으면 `502`.
- ⚠ 비상정지 해제 순간 모터 전원이 잠깐 빠져 팔이 처질 수 있다 — 해제 버튼에는 확인창을 띄운다.
- 상태가 바뀔 때마다 로봇이 `POST /ingest/mission` 으로 `{"event": "control", "state", "error"}` 도 보낸다.

### 로봇팔 상태

`GET /arm` — PIPER 의 `/api/robots/current` 를 3초마다 읽어 알린다. **팔은 건드리지 않는다.**
PIPER 에서 팔을 다시 연결하면 슬레이브 설정과 토크 OFF 가 함께 일어나므로, 사람이 없는 사이 자동으로 하지 않는다.

```json
{"source": "piper", "ok": false,
 "message": "로봇팔 can_arm1 가 연결돼 있지 않다 — PIPER 로봇 페이지에서 다시 연결하세요",
 "arms": [{"iface": "can_arm1", "role": "follower", "connected": false, "responding": null,
           "state": "UP", "ready": true, "transport": "can"}],
 "checked_at": 1791542773.31}
```

- `ok`: 등록된 팔이 모두 연결 / 응답 / CAN UP 이면 true. 등록된 팔이 없거나 PIPER 를 못 읽으면 false.
  감시하지 않으면(`source: "none"`) null. MOCK 에서는 항상 true.
- **팔이 끊겼을 때 (사람이 PIPER 화면에서)**: USB-CAN 어댑터와 팔 전원을 확인 → 로봇 페이지에서 CAN 포트 스캔
  → 인터페이스 이름이 `can0` 처럼 바뀌었으면 원래 이름(`can_arm1`)으로 변경 → UP → [연결].
  연결하면 토크가 꺼지므로 팔을 받친 상태에서 한다. 복구되면 `/arm` 이 3초 안에 `ok: true` 가 된다.

### 판정 / 모션 이벤트

로봇 쪽 프로세스가 `POST /ingest/judge`, `POST /ingest/motion` 으로 보낸다.
판정 이벤트는 하나도 빠지면 안 되므로 웹소켓 클라이언트마다 큐를 둔다
(카메라는 최신 프레임만 보낸다). 기록은 SQLite 에 남아 재시작해도 이어진다([데이터 보관](#데이터-보관)).

**로봇 쪽에서 보내는 형식** (`tools/ssorry_client.py` 가 이 형식으로 보낸다)

```json
// POST /ingest/judge — id 는 서버가 붙인다. ts 를 빼면 수신 시각, cycle_time 을 빼면 직전 판정과의 간격을 쓴다.
// cam 은 bbox 기준 카메라. 빼면 기본 카메라. 형식이 틀리면 422.
{"grade": "상", "confidence": 0.87, "v_value": 0.62, "threshold": 0.5, "bbox": [412, 188, 96, 96], "cam": "top", "ts": 1791527966.54}

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
// 연결 직후 한 번, 그리고 POST /stats/reset 직후 모든 클라이언트에 한 번 (둘 다 바로 뒤에 mission 메시지가 온다)
{"type": "snapshot",
 "stats": {"상": 3, "중": 2, "하": 0, "total": 5},
 "cycle_time": 4.2,
 "recent": [{"id": 5, "grade": "중", "confidence": 0.81, "v_value": 0.41, "threshold": 0.5,
             "ts": 1791527982.74, "roll_detected": null}]}

// 판정마다. bbox 는 cam 카메라의 /ws/camera JPEG 기준 픽셀 [x, y, w, h]
{"type": "judge", "id": 6, "grade": "상", "confidence": 0.87, "v_value": 0.62, "threshold": 0.5,
 "bbox": [412, 188, 96, 96], "cam": "top", "ts": 1791527986.84}

// 판정 직후 갱신된 누적 통계
{"type": "stats", "stats": {"상": 4, "중": 2, "하": 0, "total": 6}, "cycle_time": 4.1}

// 모션 이벤트
{"type": "motion", "approach_speed": 0.8, "place_height": 0.12, "roll_detected": false, "ts": 1791527988.34}
```

**REST 응답**

```json
// GET /stats?limit=50
{"stats": {"상": 4, "중": 2, "하": 0, "total": 6}, "cycle_time": 4.1,
 "recent": [{"id": 6, "grade": "상", "confidence": 0.87, "v_value": 0.62, "threshold": 0.5,
             "ts": 1791527986.84, "roll_detected": false}]}

// GET /history
[{"id": 1, "grade": "상", "confidence": 0.93, "v_value": 0.88, "threshold": 0.5,
  "ts": 1791527966.54, "roll_detected": true}]

// POST /stats/reset
{"stats": {"상": 0, "중": 0, "하": 0, "total": 0}}

// GET /runs  (현재 회차는 ended_at 이 null)
[{"id": 2, "started_at": 1791540643.52, "ended_at": null, "stats": {"상": 6, "중": 2, "하": 0, "total": 8}},
 {"id": 1, "started_at": 1791540620.35, "ended_at": 1791540643.52, "stats": {"상": 2, "중": 3, "하": 0, "total": 5}}]

// GET /export.csv?run=1  (첫 줄 앞에 UTF-8 BOM)
run_id,id,time,grade,confidence,v_value,threshold,roll_detected,cam
1,1,2026-10-09T19:10:24.408+09:00,상,0.88,0.77,0.5,true,top

// GET /cameras
// live: 최근 3초 안에 프레임이 왔는가. last_frame_age: 마지막 프레임 이후 초 (한 번도 안 왔으면 null)
// error: live 가 false 일 때 소스가 알려준 원인 (없으면 null)
{"default": "top", "cameras": [{"name": "top", "live": true, "last_frame_age": 0.04},
                               {"name": "wrist", "live": false, "last_frame_age": null}]}
```

**등급**: `grade` 는 `"상"`, `"중"`, `"하"` 중 하나다. 그 밖의 값은 `/ingest/judge` 가 422 로 거부한다.
통계(`stats`)는 항상 세 등급 키와 `total` 을 가진다(아직 없는 등급도 0). 로봇 쪽(Piper)은 지금 상 / 중 두 등급만 낸다.
MOCK 은 빨강 비율 0.3 미만을 하로 낸다(화면 개발용 기준).

**값의 범위** (실제 장비 = IAMSSORRY/Piper 기준, MOCK 도 같다)
- `v_value`: 빨강 비율 0~1 (소수 셋째 자리), `threshold`: 그 임계값 (`red_ratio_min`, 현재 0.5)
- `confidence`: 임계값에서 떨어진 정도 0.5~1.0
- 로봇이 카메라 판정 없이 등급만 낸 경우(수동 입력 모드 등) `v_value`, `threshold` 는 null, `confidence` 는 0.0, `bbox` 는 `[0,0,0,0]` 이다.

`roll_detected` 는 그 판정에 대한 모션 이벤트가 오기 전까지 `null` 이다.
`cycle_time` 은 판정이 두 번 이상 들어오기 전까지 `null` 이다.

## 데이터 보관

판정 / 모션 기록은 SQLite(`DB_PATH`, 기본 `/data/ssorry.db`)에 남는다. 파이썬 기본 `sqlite3` 만 쓴다.
compose 가 `./data` 를 마운트하므로 컨테이너를 다시 만들어도 파일이 남는다. `data/` 는 git 에 올라가지 않는다.

- **회차(run)**: 기록은 지우지 않는다. `POST /stats/reset` 은 현재 회차에 `ended_at` 을 채우고 새 회차를 연다.
  대시보드가 보는 통계 / recent / `GET /stats` / `GET /history` 는 현재 회차 기준이다.
- **재시작**: 서버가 뜰 때 끝나지 않은 가장 최근 회차를 이어 쓰고, 그 회차의 판정으로 통계, recent, 다음 id 를 복원한다.
  `cycle_time` 은 재시작 뒤 다음 판정부터 다시 잰다(그 전까지 null).
- **id / ts**: 판정 id 는 회차 안에서 1부터 증가하고, id 와 ts 는 판정 하나에 고정이다(복원해도 같다).
  프론트의 IndexedDB 백업 키 `${round(ts*1000)}-${id}` 가 이 값에 기대고 있다. 회차가 바뀌면 id 는 1부터 다시 시작한다.
- **모션**: 들어오면 `motions` 에 쌓고, 대상 판정의 `roll_detected` 를 DB 에도 갱신한다.
- **쓰기 방식**: DB 작업은 전용 스레드 하나에서 순서대로 돈다. 쓰기는 기다리지 않아 asyncio 루프를 막지 않고,
  실패하면 로그(`DB 쓰기 실패`)만 남기고 판정 브로드캐스트는 계속된다. `PRAGMA journal_mode=WAL, synchronous=NORMAL`.
- **테이블**

  ```sql
  runs(id INTEGER PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL)
  judges(run_id, id, grade, confidence, v_value, threshold, bbox /* JSON */, cam, ts, roll_detected, PRIMARY KEY(run_id, id))
  motions(run_id, judge_id, approach_speed, place_height, roll_detected, ts)
  ```

- **백업**: 아래 "Ubuntu 로봇 PC 에서" 참고.

### Ubuntu 로봇 PC 에서

**설치할 것은 없다.** SQLite 는 컨테이너 안의 파이썬 기본 모듈이 쓰고, `~/ssorry/data/` 는 배포(`docker compose up`) 때
자동으로 생긴다. 아래는 확인과 운영용이다 (명령은 `~/ssorry` 에서).

```bash
# 1. 배포 후 확인
ls -la data/                                   # ssorry.db, ssorry.db-wal, ssorry.db-shm
docker logs ssorry-api 2>&1 | grep 회차         # "새 회차 1 시작" 또는 "회차 N 이어서 사용: 판정 M건 복원"
curl -s localhost:8000/runs                     # 회차 목록

# 2. 직접 들여다보기 (선택, sqlite3 CLI 설치)
sudo apt install -y sqlite3
sqlite3 -readonly -header -column data/ssorry.db \
  "SELECT run_id, id, grade, v_value, roll_detected, datetime(ts, 'unixepoch', '+9 hours') AS time
   FROM judges ORDER BY run_id DESC, id DESC LIMIT 20"

# 3. 백업 — 서버가 도는 중에도 안전하다. data/ 는 root 소유라 백업은 내 홈 아래에 둔다
mkdir -p ~/ssorry-backup
sqlite3 data/ssorry.db ".backup $HOME/ssorry-backup/ssorry-$(date +%Y%m%d-%H%M).db"
curl -s -o ~/ssorry-backup/ssorry-all.csv localhost:8000/export.csv   # 또는 CSV 로 (엑셀용)
```

- `data/` 는 컨테이너(root)가 만들어서 소유자가 root 다. 읽기와 `.backup`(다른 곳으로)은 그대로 되고,
  `data/` 안에 파일을 만들거나 지울 때만 `sudo` 가 필요하다.
- ⚠ 위의 "도는 중에도 안전"은 **리눅스 호스트**(Ubuntu 배포) 이야기다. macOS 의 Docker Desktop 은 마운트한 폴더에서
  SQLite 공유 메모리(WAL)를 컨테이너와 나누지 못해, 컨테이너가 쓰는 중에 맥에서 `sqlite3` 로 열면 DB 가 깨질 수 있다
  (실제로 겪었다). 맥에서는 `curl .../export.csv` 를 쓰거나 컨테이너 안에서 연다.
- `cp` 로 복사하려면 서버를 멈추거나(`docker compose stop api`), `ssorry.db-wal`, `ssorry.db-shm` 까지 같이 복사한다.
  `.backup` 은 그럴 필요가 없다.
- 대회 중에는 회차를 넘길 때(`POST /stats/reset` 전후) 한 번씩 `.backup` 해 두면 안전하다. 자동으로 하려면:

  ```bash
  # 30분마다 ~/ssorry-backup 에 백업, 최근 48개만 남김
  (crontab -l 2>/dev/null; echo '*/30 * * * * mkdir -p $HOME/ssorry-backup && sqlite3 $HOME/ssorry/data/ssorry.db ".backup $HOME/ssorry-backup/ssorry-$(date +\%Y\%m\%d-\%H\%M).db" && ls -1t $HOME/ssorry-backup/ssorry-*.db | tail -n +49 | xargs -r rm -f') | crontab -
  crontab -l          # 등록 확인
  ```

- **모든 기록을 지우고 처음부터** (되돌릴 수 없다. 먼저 백업):

  ```bash
  docker compose stop api && sudo rm -f data/ssorry.db* && docker compose start api
  ```

  평소 데모를 다시 시작할 때는 이게 아니라 `POST /stats/reset`(새 회차)을 쓴다.
- 크기: 판정 한 건이 수백 바이트라 하루 수천 건이어도 몇 MB 다.

## 개발 환경

로컬 `.venv` (Python 3.10) 에 `requirements.txt` 를 설치하면 PyCharm 에서 바로 실행 / 디버깅할 수 있다.
ROS 를 걷어냈으므로 컨테이너가 필수는 아니다.

```bash
.venv/bin/pip install -r requirements.txt
MOCK=1 FRONTEND_DIR=./frontend-dist DB_PATH=./data/dev.db .venv/bin/uvicorn app.main:app --reload
```
