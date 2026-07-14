# OpenOps 沙箱基础镜像：python:3.11-slim + 一组克制的 SRE 排查工具。
#
# 运行时由平台强制施加安全基线（User=1000:1000 / 只读 rootfs / CapDrop=ALL /
# no-new-privileges / PidsLimit / 网络档位），本镜像不设 USER、不预装 pip 包，保持瘦。
# 工具面刻意收在「连通性探测 + JSON 处理 + 进程观察」一级——不装 vim/net-tools/ssh 客户端。
#
# 构建/预置见 deploy/sandbox/build-sandbox-image.sh；管理台「沙箱与容量」把
# container_image 指向本镜像 tag（如 openops-sandbox:v1），默认仍是 python:3.11-slim。
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      jq \
      procps \
      iputils-ping \
      dnsutils \
      ca-certificates \
      less \
    && rm -rf /var/lib/apt/lists/*
