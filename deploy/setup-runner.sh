#!/usr/bin/env bash
# Ubuntu 로봇 PC 에 GitHub Actions 자체 호스팅 러너를 설치한다 (CI/CD 의 deploy 잡이 여기서 돈다).
#
#   ./deploy/setup-runner.sh <등록 토큰>
#
# 등록 토큰(1시간 유효)은 저장소 관리자가 받는다:
#   gh api -X POST repos/IAMSSORRY/Server/actions/runners/registration-token --jq .token
#   또는 GitHub 저장소 → Settings → Actions → Runners → New self-hosted runner 화면의 --token 값
#
# 러너는 이 스크립트를 실행한 사용자로 systemd 서비스가 되어 재부팅 후에도 돈다.
# 그 사용자가 sudo 없이 docker 를 쓸 수 있어야 한다 (docker 그룹).
set -euo pipefail

TOKEN="${1:?사용법: $0 <등록 토큰>}"
REPO_URL="https://github.com/IAMSSORRY/Server"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
LABELS="ssorry"

if ! docker info > /dev/null 2>&1; then
  echo "현재 사용자가 docker 를 쓸 수 없다. 'sudo usermod -aG docker $USER' 후 다시 로그인하세요" >&2
  exit 1
fi

case "$(uname -m)" in
  x86_64) ARCH=x64 ;;
  aarch64) ARCH=arm64 ;;
  *) echo "지원하지 않는 아키텍처: $(uname -m)" >&2; exit 1 ;;
esac

VERSION=$(curl -fsSL https://api.github.com/repos/actions/runner/releases/latest | grep -oP '"tag_name": "v\K[^"]+')
mkdir -p "$RUNNER_DIR" && cd "$RUNNER_DIR"
if [ ! -f ./config.sh ]; then
  curl -fsSL -o runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${VERSION}/actions-runner-linux-${ARCH}-${VERSION}.tar.gz"
  tar xzf runner.tar.gz && rm runner.tar.gz
fi

./config.sh --unattended --replace \
  --url "$REPO_URL" --token "$TOKEN" \
  --name "ssorry-$(hostname)" --labels "$LABELS"

sudo ./svc.sh install "$USER"
sudo ./svc.sh start
echo "러너 설치 완료. GitHub 저장소 → Settings → Actions → Runners 에서 Idle 로 보이면 된다."
