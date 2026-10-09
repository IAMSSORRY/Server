# Server — SSORRY 영상 · 판정 · 제어 서버

로봇팔이 사과를 판정하는 과정을 웹에서 실시간으로 보여주는 FastAPI 서버입니다.
카메라 영상을 중계하고, 로봇이 보낸 판정 · 모션 기록을 SQLite 에 쌓고, 대시보드의 제어 명령을 로봇으로 넘깁니다.

---

## 구성

```
[Ubuntu 로봇 PC]
  PIPER Studio ── 카메라 MJPEG 스트림 (top · wrist) ─────┐
  Piper mission.py ── POST /ingest/judge · motion · ... ──┤
                    ◀── /control/* 프록시 (:8765) ────────┤
                                                          ▼
                                              [Server :8000, Docker]
                                                SQLite · AI 조언
                                                          │ WebSocket · REST
                                                          ▼
                                              [Web 대시보드]
```

| 하는 일 | 방법 |
|---|---|
| 영상 중계 | PIPER Studio 카메라 스트림을 받아 카메라별 최신 프레임만 보관하고 `/ws/camera` 로 전송 |
| 판정 수집 | 로봇이 `/ingest/*` 로 보낸 판정 · 모션 · 미션 이벤트를 SQLite 에 기록하고 `/ws/judge` 로 실시간 전송 |
| 원격 제어 | `/control/*` 를 로봇 미션 프로그램(`mission.py --serve`)으로 프록시 |
| 장애 복구 | 카메라 USB 가 끊기면 PIPER 스캔 → 재연결을 자동으로 수행 |
| AI 조언 | 현재 통계 · 최근 판정 · 미션 상태를 Claude 에 보내 운영자용 조언 생성 |

## 실행

```bash
git clone https://github.com/IAMSSORRY/Server.git ssorry && cd ssorry
cp .env.example .env
sed -i "s/^INGEST_TOKEN=.*/INGEST_TOKEN=$(openssl rand -hex 16)/" .env
docker compose up -d --build
docker compose logs -f api
```

같은 네트워크에서 `http://<로봇 PC IP>:8000/` 을 열면 프론트(빌드가 있으면) 또는 내장 카메라 뷰어가 뜹니다.

### 개발 (더미 데이터)

```bash
UVICORN_RELOAD=1 MOCK=1 docker compose up -d
```

`MOCK=1` 이면 카메라 프레임(10fps), 4초마다 판정, 판정 1.5초 뒤 모션 이벤트를 흉내 냅니다.

### 카메라 준비

PIPER Studio 카메라 페이지에서 두 카메라에 라벨 `top`, `wrist` 를 붙입니다. 같은 모델 두 대는 이름이 같아서 라벨로 구분합니다.

```bash
curl -s http://localhost/api/cameras/current | python3 -m json.tool
```

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `SSORRY_PORT` | `8000` | 호스트에 여는 포트 |
| `CAMERAS` | `top,wrist` | 카메라 이름. 첫 번째가 기본 카메라 |
| `CAMERA_SOURCE` | `ingest` | `piper`: PIPER 스트림 수신 / `ingest`: `WS /ingest/camera/{cam}` 으로 수신 |
| `PIPER_URL` | `http://host.docker.internal` | PIPER Studio 주소 |
| `PIPER_CAMERAS` | | `이름=PIPER라벨` 쉼표 목록 |
| `PIPER_AUTO_CONNECT` | `1` | 끊긴 카메라 자동 재연결 |
| `ROBOT_CONTROL_URL` | `http://host.docker.internal:8765` | 로봇 미션 프로그램 원격 제어 주소 |
| `INGEST_TOKEN` | | `/ingest/*` · 로봇 제어 토큰 |
| `ANTHROPIC_API_KEY` | | AI 조언 키. 비우면 `/advice` 는 503 |
| `ADVICE_MODEL` | `claude-haiku-4-5` | AI 조언 모델 |
| `ADVICE_MIN_INTERVAL_S` | `10` | 질문 없는 조언 요청을 이 간격 안에서는 캐시로 응답 |
| `MISSION_STALE_S` | `60` | 로봇 이벤트가 이만큼 없으면 `stalled` |
| `DB_PATH` | `/data/ssorry.db` | SQLite 파일 |
| `CSV_UTC_OFFSET_HOURS` | `9` | CSV 시간대 (한국 시간) |
| `CORS_ORIGINS` | | 허용 출처 (쉼표 구분) |
| `MOCK` | `0` | `1` 이면 더미 데이터 |

## 엔드포인트

### 대시보드

| 엔드포인트 | 설명 |
|---|---|
| `GET /` | 프론트 빌드 또는 내장 카메라 뷰어 (세션 쿠키 발급) |
| `WS /ws/camera?cam=<이름>` | 카메라 JPEG 프레임 |
| `WS /ws/judge` | 연결 직후 `snapshot`, 이후 `judge` · `stats` · `motion` · `mission` 이벤트 |
| `GET /stats` | 누적 통계 · 사이클 타임 · 최근 판정 |
| `GET /history?run=<id>` | 판정 이력 |
| `GET /runs` | 회차 목록과 회차별 통계 |
| `POST /stats/reset` | 현재 회차를 닫고 새 회차 시작. 이전 기록은 남습니다 |
| `GET /export.csv?run=<id>` | 회차 CSV (`run` 생략 시 전체) |
| `GET /cameras` | 카메라 목록과 수신 여부 |
| `GET /mission` | 미션 진행 상태 |
| `GET /arm` | 로봇팔 상태 |
| `POST /advice` | AI 조언 `{"question": "..."}` |
| `GET /health` | 서버 상태 |

### 로봇 제어 (`mission.py --serve` 로 프록시)

| 엔드포인트 | 설명 |
|---|---|
| `GET /control/status` | 미션 프로그램 상태 |
| `POST /control/start` | 미션 시작 `{"apples": 5}` |
| `POST /control/estop` | 즉시 그 자리 정지 → 모터 정지 |
| `POST /control/park` | 사과를 되돌리고 팔을 낮춘 뒤 정지 |
| `POST /control/resume` | 비상정지 해제 → 멈춘 사과부터 이어서 |
| `POST /control/stop` | 지금 사과까지만 하고 멈춤 |

### 로봇 → 서버 (`Authorization: Bearer <INGEST_TOKEN>`)

| 엔드포인트 | 설명 |
|---|---|
| `POST /ingest/judge` | 판정 하나 (등급 · 빨강 비율 · 임계값 · 신뢰도 · bbox) |
| `POST /ingest/motion` | 모션 결과 (하강 속도 · 놓는 높이 · 굴림) |
| `POST /ingest/detections` | 위 카메라 실시간 사과 박스 |
| `POST /ingest/mission` | 미션 진행 이벤트 |
| `WS /ingest/camera/{cam}` | JPEG 프레임 (`CAMERA_SOURCE=ingest`) |

전체 스키마는 `GET /docs` (Swagger UI) 에서 볼 수 있습니다.

## 데이터 보관

- 판정 · 모션 기록은 `./data/ssorry.db` 에 남고 지우지 않습니다. 통계 초기화는 회차를 새로 여는 것입니다
- CSV 는 UTF-8 BOM 으로 내보내 엑셀에서 바로 열립니다
- 세션 · 현재 회차 통계는 프로세스 메모리에 있으므로 uvicorn 워커는 1개로 유지합니다

## CI / CD

`.github/workflows/ci-cd.yml`

| 단계 | 실행 위치 | 내용 |
|---|---|---|
| 테스트 | GitHub (모든 push · PR) | 문법 검사, MOCK 스모크 테스트, Docker 이미지 빌드 |
| 배포 | 로봇 PC 자체 호스팅 러너 (`main` push) | 커밋으로 맞춘 뒤 `docker compose up -d --build`, `/health` 확인 |

로봇 PC 는 사설 IP 라 GitHub 이 직접 들어올 수 없어서 PC 안의 러너가 작업을 받아 갑니다.

```bash
gh api -X POST repos/IAMSSORRY/Server/actions/runners/registration-token --jq .token
./deploy/setup-runner.sh <토큰>
```

## 프론트 연결

세션 쿠키가 `SameSite=Lax` 라서 프론트를 API 와 **같은 출처**에서 열어야 웹소켓에 쿠키가 실립니다.

- 로컬 시연: 프론트 빌드 결과를 `frontend-dist/` 에 두면 서버가 `/` 에서 서빙합니다
- 개발: Vite 개발 서버 프록시로 이 서버에 붙습니다 ([Web](https://github.com/IAMSSORRY/Web) 참고)
