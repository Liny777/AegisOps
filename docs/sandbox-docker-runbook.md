# 沙箱 docker 档 Runbook（一个用户一个容器 · Linux）

面向内网 Linux 部署与运维。fake 档（默认）无 OS 隔离、仅联调冒烟用；生产必须 docker 档。

## 1. 架构一页

- **一个用户一个容器**，会话期常驻：run 开启时 `ensure_user_container` 建/复用，末个活跃 run 关闭后置 idle，按 idle TTL 回收（懒回收 + 后台定时器双保险）。运行态是进程内注册表 + docker 为真相源，不落 PG——**部署要求会话粘滞（单副本或 sticky 路由）**。
- **安全基线**（每个容器，硬施加）：非 root（`User=1000:1000`）、只读 rootfs、`CapDrop=ALL`、`no-new-privileges`、`PidsLimit`（防 fork 炸弹）、`Memory`/`NanoCpus` 限额、仅 `workspace`/`tmp` 可写 tmpfs、`Env=[]` 不注入平台上下文。
- **容器内 Bash 四层裁决**：① 平台 deny 前缀（`bash_deny_prefixes`，最高优先，即便用户批准也不放行）② agentscope 内置安全分析 ③ 只读放行/非只读走 HITL 审批 ④ 容器隔离兜底。deny 层是纵深防御——只收提权/文件系统/电源类；`nc`/`curl`/`ssh` 等排查有正当用途的留给审批层。
- **底座**：自建 `SandboxExecutor`（`backend/src/sandbox/executor.py`）+ `DockerContainerBackend`（`backends.py`，aiodocker），底层 exec 复用 agentscope `DockerBackend` 原语。**非** agentscope DockerWorkspace（其 HostConfig 硬编码 root、无资源/网络/安全参数，不满足基线）。

## 2. 前置（Linux 机）

1. `.venv` 装 sandbox extra：`pip install -e ".[sandbox]"`（aiodocker）。
2. docker.sock 权限：systemd 取消注释 `SupplementaryGroups=docker`（`deploy/systemd/openops-backend.service`）+ `systemctl daemon-reload`；或 `usermod -aG docker openops` 后重登。宿主须已有 docker 组。
3. 离线镜像：Mac 侧 `bash deploy/sandbox/build-sandbox-image.sh [ver]` → 传 `deploy/artifacts/openops-sandbox-image.tar.gz` → 后端机 `docker load -i openops-sandbox-image.tar.gz`。镜像自带一组常用 pip 依赖（`requests`/`httpx`/`pyyaml`/`python-dateutil`/`tabulate`/`rich`/`uv`，构建期烘焙，运行态只读 rootfs 装不了包）；**内网构建须先 `export PIP_INDEX_URL=https://mirrors.tools.huawei.com/pypi/simple PIP_TRUSTED_HOST=mirrors.tools.huawei.com` 走公司源**（详见 `docs/build-images-intranet.md` §四）。传源构建还会把源持久化进镜像（`/etc/pip.conf` + `UV_INDEX_URL`），skill 运行期在容器内 `pip`/`uv` 自取额外包也自动走公司源（受只读 rootfs `--target` / HITL / `bridge` 网络约束）。**内网 CA**：把公司根 CA 的 `.crt` 放进 `deploy/sandbox/ca/` 再构建，镜像会同时打通三套互不相通的 TLS 信任源——系统 `/etc/ssl/certs`（apt/curl/Python `ssl`）、certifi（pip/requests/httpx）、uv 的 webpki（`UV_SYSTEM_CERTS=1`）。不放 CA 则 skill 用 requests/httpx 打内网 https API 会 `SSLCertVerificationError`，详见 `deploy/sandbox/ca/README.md`。⚠ 这些依赖只在自建镜像里，默认 `container_image=python:3.11-slim` 没有——须在 §3 把 `container_image` 指向 `openops-sandbox:<版本>` 才吃得到。

## 3. 配置面速查

**runtime_config（sandbox 域，管理台「沙箱与容量」改，改值必填原因走审计；新建容器生效）**

| 键 | 默认 | 说明 |
|---|---|---|
| `max_user_containers_per_host` | 26 | 单机最大用户容器数（容量准入，满则先回收到期 idle，仍满 `SANDBOX_CAPACITY_FULL`） |
| `per_user_running_task_limit` | 2 | 每用户最多 running task |
| `user_container_idle_ttl_minutes` | 15 | idle 容器保留时间 |
| `capacity_full_policy` | strict_ttl | 容量满策略 |
| `container_cpu_limit` | 0.5 | 新建容器 CPU 限额（NanoCpus） |
| `container_memory_limit_mib` | 2048 | 新建容器内存限额 |
| `container_image` | python:3.11-slim | 沙箱镜像（**须已 docker load**）；切自建镜像改这里 |
| `container_network_mode` | bridge | `bridge`（可出网）/ `none`（断网）；非法值写时被拒、读时回退 bridge |
| `container_pids_limit` | 256 | 容器内进程数上限（防 fork 炸弹） |
| `bash_deny_prefixes` | docker,sudo,su,mount,umount,mkfs,shutdown,reboot,halt,poweroff | 逗号分隔；层 1 平台 deny 前缀（token 词边界匹配防串联绕过） |

**env 开关**

| env | 默认 | 说明 |
|---|---|---|
| `OPENOPS_SANDBOX` | fake | 生产须 `docker` |
| `OPENOPS_SANDBOX_SWEEP_INTERVAL_S` | 60 | idle 后台回收间隔（秒）；0=关 |
| `OPENOPS_SANDBOX_LABEL_SCOPE` | default | 容器标签 scope，隔离同宿主多环境孤儿清理 |

## 4. 既有环境升级 / 回滚

**升级顺序**：更新代码包 → `.venv` 补 `pip install -e ".[sandbox]"` → `docker load` 沙箱镜像 → systemd 取消注释 `SupplementaryGroups=docker` + `daemon-reload` → env 翻 `OPENOPS_SANDBOX=docker` → 重启（启动时 `ensure_sandbox_defaults` **自动补新键**、docker 档自动清上次进程遗留的孤儿容器）→ 管理台核对新键出现、按需把 `container_image` 切自建镜像 → 冒烟。

> 老库升级无 DDL、无迁移脚本：`ensure_sandbox_defaults()` 在 seed 早退守卫**之前**跑，只补缺失键、不覆盖管理员改过的值。

**回滚**：env 翻回 `OPENOPS_SANDBOX=fake` + 重启即止血（纯后端）。残留容器下次 docker 档启动自动清；要立即清：`docker rm -f $(docker ps -aq -f label=com.openops.sandbox=1)`。

## 5. 运维操作

- **改配置**：管理台「沙箱与容量」，改值填原因（写 `runtime_config.updated` 审计）。
- **看/销毁容器**：管理台容器页（读进程内注册表，非 `docker ps`）；`POST /admin/sandbox/containers/{user}:destroy`。
- **宿主侧对账**：`docker ps -a -f label=com.openops.sandbox=1 --format '{{.Names}}\t{{.Label "com.openops.sandbox.user"}}\t{{.Status}}'`；容器名形如 `openops-sbx-<safe>-<hash>`（每用户确定性一名）。
- **孤儿清理**：进程重启时自动按 label + 本 scope 清（注册表此刻必空）；手工同上 `docker rm -f`。

## 6. 故障速查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `SANDBOX_CONTAINER_FAILED`（用户容器创建失败） | 镜像未 load / docker 守护未起 / 运行用户无 docker.sock 权限 | `docker images \| grep openops-sandbox`；`systemctl status docker`；确认 `SupplementaryGroups=docker` 已生效 |
| `SANDBOX_CAPACITY_FULL`（429） | 容器数达 `max_user_containers_per_host` 且无到期 idle | 调大上限或降 idle TTL；低峰重试 |
| 启动日志无「清理孤儿沙箱容器」 | 非 docker 档 / 无遗留 / 守护进程未就绪 | 确认 `OPENOPS_SANDBOX=docker`；孤儿清理异常只 warning 不阻断启动 |
| 409 同名冲突 | 崩溃残留同名容器 | `start()` 已自愈（强删旧再建一次）；反复失败查 docker 守护 |
| 网络 `none` 下 skill 外呼全失败 | 管理员显式切了断网档 | 预期行为；需外呼把 `container_network_mode` 改回 `bridge` |
| 容器内命令 `sudo/mount/...` 被拒（层 1） | 命中 `bash_deny_prefixes` | 预期纵深防御；确需放开由管理员改前缀列表 |

## 7. release-checklist 对照

见 docs/release-checklist.md 沙箱三勾选项（镜像已 load / docker.sock 权限 / `container_image` 已指向）。
