# 前端一体镜像：nginx + dist（26/27 号拓扑前端机主容器）。
# 测试/生产共用同一镜像——环境差异（后端机地址）经 env 注入 nginx 模板（官方镜像
# entrypoint 对 /etc/nginx/templates/*.template 自动 envsubst，只替换已定义的 env，
# $host/$http_cookie 等 nginx 运行时变量不受影响）。
# 构建（Mac，由 build-artifacts.sh 先 staging dist 到本目录）：
#   docker build --platform linux/amd64 -f deploy/frontend/Dockerfile \
#     -t openops-frontend:<版本> deploy/frontend
FROM nginx:stable-alpine
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY dist/ /usr/share/nginx/html/
# BACKEND_HOST 必填（后端机地址，缺了 nginx 启动即报错 fail-fast）；
# SIDECAR_HOST 默认 compose 服务名，单容器手跑时可覆盖
ENV SIDECAR_HOST=sidecar
EXPOSE 80
