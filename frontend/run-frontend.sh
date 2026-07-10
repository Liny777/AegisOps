#!/usr/bin/env bash
# OpenOps 前端启动脚本（Git Bash / WSL / macOS / Linux）。
# 访问 http://127.0.0.1:5175 ，/api 自动代理到后端 http://127.0.0.1:18082。
# 无密钥，可直接跑。首次先 npm install（内网无外网走内部 npm 镜像）。
#   用法： bash run-frontend.sh   或   ./run-frontend.sh
set -euo pipefail
cd "$(dirname "$0")"          # 切到 frontend/
npm run dev
