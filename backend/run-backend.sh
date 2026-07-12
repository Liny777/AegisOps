#!/usr/bin/env bash
# 双环境启动入口：./run-backend.sh test|prod
# 机制：source config/openops.<env>.env（后端不读 .env——变量必须进真进程环境），再 exec run.py。
# systemd 常驻用 deploy/systemd/openops-backend.service（EnvironmentFile 同一份 env 文件）。
set -euo pipefail
cd "$(dirname "$0")"

ENV_NAME="${1:-}"
if [[ "$ENV_NAME" != "test" && "$ENV_NAME" != "prod" ]]; then
  echo "用法: ./run-backend.sh test|prod"; exit 1
fi
ENV_FILE="config/openops.${ENV_NAME}.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "缺 $ENV_FILE —— 从 config/openops.${ENV_NAME}.env.example 复制并填值（该文件含凭证，已 gitignore）"; exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
export OPENOPS_ENV="$ENV_NAME"

if [[ -x ".venv/bin/python" ]]; then PY=".venv/bin/python";
elif [[ -x ".venv/Scripts/python.exe" ]]; then PY=".venv/Scripts/python.exe";
else echo "未找到 venv（python3.11 -m venv .venv && pip install -e '.[agentscope]'）"; exit 1; fi

echo "[openops] env=$ENV_NAME pg=$OPENOPS_PG_HOST/$OPENOPS_PG_DB schema=${OPENOPS_PG_SCHEMA:-public} runtime=${OPENOPS_RUNTIME:-mock}"
exec "$PY" run.py
