# CopilotKit sidecar（frontend/server/copilot-runtime.ts）
# 构建（Mac，仓库根）：docker buildx build --platform linux/amd64 \
#   -f deploy/sidecar/Dockerfile -t openops-sidecar:<版本> --load frontend/
# 无编译产物：node --import tsx 直跑 .ts（tsx 已在 dependencies）。
FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json ./
# 外网侧构建：不传 NPM_REGISTRY，走默认源；
# 内网侧构建：--build-arg NPM_REGISTRY=<内网 npm 源>（docs/build-images-intranet.md）
ARG NPM_REGISTRY=
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi \
 && npm ci --omit=dev --no-audit --no-fund
COPY server/ server/
COPY tsconfig.json ./
ENV COPILOTKIT_RUNTIME_HOST=0.0.0.0 \
    COPILOTKIT_RUNTIME_PORT=4002 \
    COPILOTKIT_TELEMETRY_DISABLED=true
EXPOSE 4002
CMD ["node", "--import", "tsx", "server/copilot-runtime.ts"]
