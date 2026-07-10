# OpenOps 前端启动脚本（Vite dev）。
# 访问 http://127.0.0.1:5175 ，/api 自动代理到后端 http://127.0.0.1:18082。
# 无密钥，可直接提交/运行。首次先装依赖：npm install（内网无外网走内部 npm 镜像）。
Set-Location $PSScriptRoot   # 切到 frontend/
npm run dev
