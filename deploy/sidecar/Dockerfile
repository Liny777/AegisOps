# CopilotKit sidecar（frontend/server/copilot-runtime.ts）
# 构建（Mac，仓库根）：docker buildx build --platform linux/amd64 \
#   -f deploy/sidecar/Dockerfile -t openops-sidecar:<版本> --load frontend/
# 无编译产物：node --import tsx 直跑 .ts（tsx 已在 dependencies）。
FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json ./
# 内网零外呼：镜像在外网侧构建完成后 docker save/load 投递
RUN npm ci --omit=dev --no-audit --no-fund
COPY server/ server/
COPY tsconfig.json ./
ENV COPILOTKIT_RUNTIME_HOST=0.0.0.0 \
    COPILOTKIT_RUNTIME_PORT=4002 \
    COPILOTKIT_TELEMETRY_DISABLED=true
EXPOSE 4002
CMD ["node", "--import", "tsx", "server/copilot-runtime.ts"]
