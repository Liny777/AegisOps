# OpenOps 沙箱基础镜像：python:3.11-slim + 一组克制的 SRE 排查工具 + 一组常用 pip 依赖。
#
# 运行时由平台强制施加安全基线（User=1000:1000 / 只读 rootfs / CapDrop=ALL /
# no-new-privileges / PidsLimit / 网络档位），本镜像不设 USER，保持瘦。
# 工具面刻意收在「连通性探测 + JSON 处理 + 进程观察 + 内容搜索」一级——不装 vim/net-tools/ssh 客户端。
# ripgrep 供 agentscope 官方 Grep 工具（绑容器后端）在容器内搜文件内容，见 runtime/sandbox_tool_backend.py。
# pip 依赖预装 requirements.txt 中一组常用库（运行态只读 rootfs 装不了包，故构建期烘焙）；
# 内网源同时持久化进镜像（/etc/pip.conf + UV_INDEX_URL），运行期 skill 自取额外包也自动走公司源。
# 内网 CA：把 .crt 放进 deploy/sandbox/ca/ 即打通三套互不相通的 TLS 信任源（系统 / certifi / uv），
# 否则 skill 用 requests/httpx 打内网 https API 会 SSLCertVerificationError。见 ca/README.md。
#
# 构建/预置见 deploy/sandbox/build-sandbox-image.sh；管理台「沙箱与容量」把
# container_image 指向本镜像 tag（如 openops-sandbox:v1），默认仍是 python:3.11-slim。
FROM python:3.11-slim

# 内网同样打不到 deb.debian.org（且公司代理常不可达）：传 APT_MIRROR 换成公司 Debian 源。
# 传的是替换 http://deb.debian.org 的【基址】，如
#   --build-arg APT_MIRROR=http://mirrors.tools.huawei.com
# 其下须有 /debian 与 /debian-security。外网构建不传，走官方源。
# ⚠ 用 http:// 而非 https://——公司镜像站证书由内网 CA 签发，基础镜像只带公共 CA，
#   https 会 certificate verify failed（内网实测）。apt 的完整性由 GPG 签名保证
#   （见 debian.sources 的 Signed-By），与 Debian 官方默认源同为 http，不是降级。
# 基础镜像为 Debian 13(trixie)，apt 源是 deb822 格式的 /etc/apt/sources.list.d/debian.sources
# （无传统 /etc/apt/sources.list），故用 sed 改其 URIs。
ARG APT_MIRROR=
RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
      unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      curl \
      jq \
      procps \
      iputils-ping \
      dnsutils \
      ca-certificates \
      less \
      ripgrep; \
    rm -rf /var/lib/apt/lists/*

# ── 内网 CA（信任源 1/3：系统 /etc/ssl/certs —— 供 apt、curl、Python 标准库 ssl）──────────
# 把 .crt 放进 deploy/sandbox/ca/ 即被烘焙。用【目录 COPY】而非条件 COPY：Dockerfile 无法
# 条件化 COPY，但目录常在——空目录（只有 README）时 update-ca-certificates 自然 no-op，
# 外网构建行为不变。详见 deploy/sandbox/ca/README.md。
COPY ca/ /usr/local/share/ca-certificates/
RUN set -eux; update-ca-certificates

# 内网构建走公司 PyPI 源（无公网直连）：由 build-sandbox-image.sh 透传，或直接
#   --build-arg PIP_INDEX_URL=https://mirrors.tools.huawei.com/pypi/simple \
#   --build-arg PIP_TRUSTED_HOST=mirrors.tools.huawei.com
# 外网构建不传，走默认 PyPI。与 sidecar 的 NPM_REGISTRY 惯例一致（deploy/sidecar/Dockerfile）。
ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=
# 把源【持久化进镜像】，让构建期 + 运行期（skill 在容器内自己 pip/uv install 时）都自动走公司源、
# 无需脚本带 -i：pip 读系统级 /etc/pip.conf；uv 不读 pip.conf，只认 UV_INDEX_URL 环境变量
# （镜像 ENV 在平台 Env:[] 下仍存活，已实测）。空值（外网构建未传源）时不写 pip.conf、
# UV_INDEX_URL 为空串 → pip 与 uv 都回落默认 PyPI（已实测，空串不报错）。
RUN set -eux; \
    if [ -n "$PIP_INDEX_URL" ]; then \
      printf '[global]\nindex-url = %s\n' "$PIP_INDEX_URL" > /etc/pip.conf; \
      if [ -n "$PIP_TRUSTED_HOST" ]; then \
        printf 'trusted-host = %s\n' "$PIP_TRUSTED_HOST" >> /etc/pip.conf; \
      fi; \
    fi
ENV UV_INDEX_URL=${PIP_INDEX_URL}
COPY requirements.txt /tmp/requirements.txt
# Docker 的代理是【逐层注入】的——apt 层里的 unset 不会带到这一层，故此处必须再来一次，
# 否则 pip 会走公司代理（内网实测 proxyuk.huawei.com:8080 不可达，ConnectTimeout 后装包失败）。
# 名单比 apt 层更关键：pip 的 vendored requests 会读 all_proxy/ALL_PROXY，而 apt 不读——
# 早期 apt 层漏掉 all_proxy 却侥幸通过，pip 这里漏了就直接炸。
# ⚠ gate 必须保留：它保护的不是外网构建，而是【在公司网内、走公司代理打公网 PyPI】这第三种
#   模式（既不传 PIP_INDEX_URL 也没内网源）——无条件 unset 会掐掉它唯一的出网通道。
RUN set -eux; \
    if [ -n "$PIP_INDEX_URL" ]; then \
      unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY; \
    fi; \
    pip install --no-cache-dir -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt

# ── 内网 CA（信任源 2/3：certifi bundle —— 供 pip 的 vendored requests、requests、httpx）──
# 【顺序要求】必须排在 pip install 之后：certifi 是随 requests/httpx 才装进来的，早于它就没得改。
# 系统 update-ca-certificates 修不到这一套——certifi 自带独立 bundle，httpx 更是显式加载它。
# 空 ca/ 目录时 ls 无匹配 → 整层 no-op。
RUN set -eux; \
    if ls /usr/local/share/ca-certificates/*.crt >/dev/null 2>&1; then \
      cat /usr/local/share/ca-certificates/*.crt \
        >> "$(python3 -c 'import certifi; print(certifi.where())')"; \
    fi

# ── 内网 CA（信任源 3/3：uv 的 webpki）+ 运行期缓存目录 ────────────────────────────────
# uv 用 rustls + 编进二进制的 webpki-roots，既不读系统 CA 也不读 certifi/pip.conf，
# 须显式让它改用系统信任源（无 CA 时等价于用公共 CA，外网构建无害）。
# ⚠ 用 UV_SYSTEM_CERTS 而非 UV_NATIVE_TLS：后者在 uv 0.11.x 已废弃，实测每次调用都会往 stderr
#   打 deprecation 告警（会污染 skill 输出），且未来版本移除。二选一，别为"兼容老版"同时设。
#   （若某天需要跳过校验而非信任 CA，uv 的对应物是 --allow-insecure-host / UV_INSECURE_HOST，
#    相当于 pip 的 trusted-host；本镜像走装 CA 的正路，不用它。）
# 缓存目录：运行期 uid 1000 + 只读 rootfs，HOME 不可写会让 break-glass 装包失败，指到 tmpfs。
# 注意这些都是【字面常量】——不能写成 ENV X=${ARG} 形式，外网构建会展开成空串。
ENV UV_SYSTEM_CERTS=1 \
    PIP_CACHE_DIR=/tmp/pip-cache \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV_LINK_MODE=copy \
    PIP_DISABLE_PIP_VERSION_CHECK=1
