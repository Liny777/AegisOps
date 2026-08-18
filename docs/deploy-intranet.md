# 内网单主机部署手册（2026-07-17；单文根 xxxx.com/openops 收敛版）

```
用户浏览器 ──> 公司网关（单文根：/openops/* → 本机:80，不 strip）
                └── 单机（docker compose 两容器 + systemd 后端）
                      openops-frontend（nginx+dist 一体镜像，:80）
                        ├── rewrite 剥 /openops 后按 location 分流：
                        ├── /              → 镜像内静态（SPA 回退）
                        ├── /api/copilotkit → compose 网内 sidecar 容器 :4002（CopilotKit 流）
                        └── /api           → 本机 :18082（REST/SSE/agui，关缓冲长超时）
                      openops-sidecar（不发布宿主机端口，仅 compose 网内可达）
                      uvicorn（run.py，:18082，systemd）+（可选）Docker 沙箱
PG 仍在外部（存量库先迁移再跑 core.sql；新库只跑 core.sql）
```

浏览器只有一个 origin（`xxxx.com`），API 与静态同域同文根 ⇒ **无 CORS、无 Cookie 域问题**。
两个镜像**测试/生产共用**——后端地址不烘焙进镜像，启动时经 env 注入 nginx 模板（envsubst）；
环境差异只有三处（见 §四）。本机需要 docker（前端两容器 + 沙箱）与 python3.11+（后端）。
图上旧口径端口 18081 已作废，实况 18082。

> **由双机迁到单机**：浏览器侧本来就同源（`/openops` 与 `/openback` 一直是同一个域上的两个
> 路径前缀，只是网关把它们扇给了两台机器），所以这是纯拓扑变更，前端源码零改动。
> 切换按 §四¾ 的四个阶段走——**不要**把「删网关 /openback」和「上新镜像」放进同一次变更。

## 一、Mac 侧打包（一条命令）

```bash
bash deploy/build-artifacts.sh                  # 全量：四件 + SHA256SUMS
bash deploy/build-artifacts.sh --backend-only   # 仅后端：deploy/artifacts/<BUILD_ID>/ 下完整包 + .sha256
```

| 工件 | 说明 |
|---|---|
| openops-frontend-image.tar.gz | nginx+dist 一体镜像（linux/amd64；构建强制 real，且断言文根烘焙=`/openops/` + API_BASE=`/openops/api`） |
| openops-sidecar-image.tar.gz | sidecar 镜像（linux/amd64）。均 gzip：内网单文件上传限 500MB，`docker load` 原生认 .tar.gz |
| openops-backend-src-`<BUILD_ID>`.tar.gz | 两种模式均生成版本化完整包（不含 venv/真实 env；包内有 `BUILD_INFO`） |
| openops-deploy-conf.tar.gz | compose/env 模板/systemd |

单机拓扑下四件工件都投到同一台机器。

## 二、后端（Linux x64，python3.11+，版本目录原子发布）

```bash
# 以下整段须在 bash 中执行；任一校验失败立即停止，禁止继续解压或切换。
set -euo pipefail

# 显式指定本次包；不得用“最新文件”通配选择，保证校验对象就是部署对象。
PKG=/opt/openops/incoming/openops-backend-src-REPLACE_WITH_BUILD_ID.tar.gz
SUM="${PKG}.sha256"
test -f "$PKG" -a -f "$SUM"
EXPECTED_NAME=$(awk 'NR==1 {sub(/^\*/, "", $2); print $2} END {if (NR != 1) exit 1}' "$SUM")
test "$EXPECTED_NAME" = "$(basename "$PKG")"
(cd "$(dirname "$PKG")" && sha256sum -c "$(basename "$SUM")")

# 文件名、校验文件、包内 BUILD_ID 三者一致；BUILD_ID 必须是安全的直接子目录名。
BUILD_INFO=$(tar -xOf "$PKG" ./BUILD_INFO)
test "$(printf '%s\n' "$BUILD_INFO" | grep -c '^BUILD_ID=')" -eq 1
BUILD_ID=$(printf '%s\n' "$BUILD_INFO" | sed -n 's/^BUILD_ID=//p')
[[ "$BUILD_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{5,94}[A-Za-z0-9]$ ]]
test "$(basename "$PKG")" = "openops-backend-src-${BUILD_ID}.tar.gz"
RELEASES=$(realpath -m /opt/openops/releases)
RELEASE=$(realpath -m "$RELEASES/$BUILD_ID")
test "$(dirname "$RELEASE")" = "$RELEASES"

# 目录权限：releases 父目录归 root，避免运行用户删除/替换历史发布；安装阶段临时把
# 新 release 交给 openops 解包和创建独立 venv。共享真实 env 仅 openops 可读写。
sudo install -d -o root -g openops -m 0750 "$RELEASES"
sudo install -d -o openops -g openops -m 0700 /opt/openops/shared/config
# 不使用 install -d/mkdir -p：root 权限下的排他 mkdir 是最终防线；同名目录或悬空链接
# 已存在时立即失败，绝不重新授权、overlay 或部分改写旧 release。
sudo mkdir -m 0750 "$RELEASE"
sudo chown openops:openops "$RELEASE"
sudo -u openops tar --no-same-owner -xzf "$PKG" -C "$RELEASE"
sudo -u openops python3.11 -m venv "$RELEASE/.venv"
sudo -u openops "$RELEASE/.venv/bin/pip" install "$RELEASE/backend[agentscope]"

# 首次从模板创建共享配置；后续版本不覆盖真实 env，权限固定为 0600。
if ! sudo test -f /opt/openops/shared/config/openops.prod.env; then
  sudo install -o openops -g openops -m 0600 \
    "$RELEASE/backend/config/openops.prod.env.example" \
    /opt/openops/shared/config/openops.prod.env
fi
sudo -u openops vi /opt/openops/shared/config/openops.prod.env
# 必须：IAM=true；oModel 固定 HTTPS 域；两个 tenant 一致；OMODEL/CONSOLE_COOKIE 空；HTTP_DEBUG=0
sudo -u openops bash "$RELEASE/scripts/release_check.sh" \
  --production-env /opt/openops/shared/config/openops.prod.env

# 数据库变更必须在切换 current / 重启后端前完成；按实际状态填写 existing 或 new。
DATABASE_MODE=REPLACE_WITH_existing_OR_new
case "$DATABASE_MODE" in
  existing)
    # 存量库：先无损重命名旧对象，再补多 Agent 派发批次列与 Agent Studio span 表，最后重放 core.sql；
    # 全部成功后才能发布新后端。
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/migrate-2026-07-14-ddl-object-names.sql"
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/migrate-2026-07-14-subagent-activity.sql"
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/migrate-2026-07-30-alert-entry-source.sql"
    # ↑ 内网库同事已有 entry_source 列：该文件的 entry_source 段幂等跳过、task_origin 段生效
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/migrate-2026-08-10-rename-run-source.sql"
    # ↑ 仅我方旧库真执行（run_source→entry_source rename）；内网库/新库自动空转
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/migrate-2026-08-09-alert-category-motype.sql"
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/migrate-2026-08-15-model-asset-extra-headers.sql"
    # ↑ 平台模型资产补 extra_params_json（自定义出站 Header）；用户自带模型侧同名列早已存在
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/migrate-2026-08-17-model-asset-secret.sql"
    # ↑ 平台模型 API Key 密文列。本脚本只加列不搬数据（psql 读不到后端进程的环境变量）——
    #   存量 Key 由后端启动时的一次性 backfill 导入，见下方「平台模型 Key 迁移」小节，顺序不可颠倒。
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/slices/alerts.sql"
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/slices/studio_span.sql"
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/openops_v1_core.sql"
    ;;
  new)
    # 全新库：core.sql + 各垂直切片自带 DDL（sql/slices/*.sql）；成功后再发布新后端。
    # ⚠ 切片这条**不能漏**：漏了服务照常启动、Agent Studio 的 drain 落库异常被吞成 debug 日志、
    #   管理台 Studio 页全空，排查成本极高。
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/openops_v1_core.sql"
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/slices/studio_span.sql"
    ;;
  *)
    echo "DATABASE_MODE 必须显式设置为 existing 或 new" >&2
    exit 1
    ;;
esac

# 安装完成后冻结发布源码/元数据；运行用户只保留 release 内 venv 的写权限。
sudo chown -R root:openops \
  "$RELEASE/backend" "$RELEASE/scripts" "$RELEASE/docs" "$RELEASE/deploy" "$RELEASE/BUILD_INFO"
sudo chmod -R a-w \
  "$RELEASE/backend" "$RELEASE/scripts" "$RELEASE/docs" "$RELEASE/deploy" "$RELEASE/BUILD_INFO"
sudo chown root:openops "$RELEASE"
sudo chmod 0550 "$RELEASE"

# 同一文件系统内原子切换 current；失败时旧 release 保持完整，可立即回滚
sudo rm -f /opt/openops/current.next
sudo ln -s "$RELEASE" /opt/openops/current.next
sudo mv -Tf /opt/openops/current.next /opt/openops/current
```

常驻（systemd）：

```bash
set -euo pipefail                    # 与上一代码块在同一 bash 会话执行
sudo cp /opt/openops/current/deploy/systemd/openops-backend.service /etc/systemd/system/
# 测试机把 EnvironmentFile 改为 shared/config/openops.test.env；User 按实际
sudo systemctl daemon-reload
sudo systemctl enable openops-backend
sudo systemctl restart openops-backend

# 三点必须是同一 BUILD_ID：包内元数据、进程 cwd、启动横幅
cat /opt/openops/current/BUILD_INFO
test "$(readlink -f /opt/openops/current)" = "$RELEASE"
PID=$(systemctl show -p MainPID --value openops-backend)
test "$(readlink -f "/proc/$PID/cwd")" = "$RELEASE/backend"
journalctl -u openops-backend -n 50 | grep "build=$BUILD_ID"
curl -s http://127.0.0.1:18082/health    # → {"status":"ok"}
```

### 平台模型 Key 迁移（2026-08-17，仅升级到该版本时做一次）

平台模型的 API Key 从「后端进程环境变量」改为「PG 密文列」（Fernet，与用户自带模型同一条加密链）。
运行时不再读环境变量，管理员可在管理台「模型资产」弹窗里自助改 Key，不必登服务器重启。

**三步顺序不可颠倒**，颠倒的后果是所有平台模型取不到 Key、全部回退 stub：

1. 跑 `migrate-2026-08-17-model-asset-secret.sql`（只加列，不搬数据——psql 读不到后端进程的环境变量）。
2. **保留** `EnvironmentFile` 里的 `OPENOPS_PLATFORM_*_API_KEY` 重启后端。启动时的一次性 backfill 会把
   这些环境变量加密写进密文列，每导入一把打一行日志：
   `[OpenOps][seed] 平台模型 glm-5.1 的 Key 已从环境变量 OPENOPS_PLATFORM_GLM_API_KEY 导入密文列（fp_…）`
   （只补空的、不覆盖管理台已录入的；幂等，重启多次无副作用）。
3. 验证后才删环境变量：

```bash
cd /opt/openops/current/backend && python check-db.py
# 看两行：「已配 Key 的平台模型 = N 个」+「迁移未完成清单」。
# 清单为空才可以删 export；清单里点名的模型 = 那把 Key 的环境变量当时没注入。
# 删除 EnvironmentFile 里的 OPENOPS_PLATFORM_*_API_KEY 并重启，
# 再起一个真实任务确认走真实模型而非 stub（journalctl 里 [model] building … key=db:fp_…）
```

**多把 Key 的部署**（如 `OPENOPS_PLATFORM_GLM_API_KEY` 之外还有 `..._KEY_2`）：backfill **不认变量名**，
它遍历每个资产、读那一行 `secret_env_var` 列里登记的名字，所以几把都一样自动导入，无需额外配置。
但两个前提要先确认：

```bash
# ① 每把 Key 都得有资产行登记着它的变量名——没登记的变量 backfill 根本不会去读，
#    删掉 export 就丢了（这类 Key 只能事后在管理台手工补填）
psql "$OPENOPS_DATABASE_URL" -c "select model_id, display_name, secret_env_var from sre_model_asset \
  where deleted_at is null and secret_env_var is not null order by creation_date;"
# ② 重启那一刻**所有**变量都在 env 里。漏注入某一把 → 那个资产会留在 check-db 的
#    「迁移未完成清单」里；补上变量再重启一次即可（幂等，已导入的不受影响）。
```

⚠ `OPENOPS_ENCRYPTION_KEY` 自此成为**平台模型的可用性依赖**（原先只影响用户自带模型）：
key 丢失 = 全部密钥不可解，只能在管理台逐个重新录入。备份它。

## 三、前端两容器（同一台机器，仅需 docker）

```bash
# 0. 端口预检——80 被占是本步最常见的失败：容器起不来，docker compose 直接报 port is already allocated
ss -lntp | grep ':80 ' && echo "先停掉占用 80 的服务（如宿主机残留 nginx）"

# 1. 两个镜像
docker load -i openops-frontend-image.tar.gz
docker load -i openops-sidecar-image.tar.gz

# 2. compose + 两个 env（唯一必改项都是后端地址 = 本机内网 LAN IP）
mkdir -p /opt/openops/frontend && cd /opt/openops/frontend
tar xzf /path/to/openops-deploy-conf.tar.gz      # → deploy/（compose 与 env 模板在里面）
cp deploy/frontend/docker-compose.yml .
cp deploy/frontend/frontend.test.env.example frontend.env && vi frontend.env   # BACKEND_HOST
cp deploy/sidecar/sidecar.test.env.example sidecar.env && vi sidecar.env       # OPENOPS_BACKEND_URL
docker compose up -d
```

**⚠ 同机拓扑最大的坑：两个 env 里都必须填「本机内网 LAN IP」，不能填 `127.0.0.1`。**
compose 是 bridge 网络，容器里的 `127.0.0.1` 指向容器自己。两处都**不会 fail-fast**：
- `frontend.env` 填 `127.0.0.1` → `nginx -t` 过、容器起得来、healthcheck（探的是本机静态页）
  **报 healthy**，但每个 `/api/*` 静默 502。镜像的 fail-fast 只覆盖「没设」，不覆盖「填错」。
- `sidecar.env` 缺失或留空 → `copilot-runtime.ts` 的代码默认值就是 `http://127.0.0.1:18082`
  ⇒ **静默只坏 Copilot 对话**，其余功能全正常。

填 IP 字面量而非主机名：`${BACKEND_HOST}` 是字面量插进 `proxy_pass`，nginx 启动期就要解析、
解析不到直接退出；IP 免解析 ⇒ 后端没起时 nginx 照常起（请求期 502，后端起来即自愈），
宿主机重启无 docker/backend 启动顺序依赖。

```bash
# 3. 验证链（依次；sidecar 不发布宿主机端口，探活走 docker exec）
docker exec openops-sidecar wget -qO- http://127.0.0.1:4002/healthz   # {"ok":true,...}
curl -s http://localhost/api/health            # frontend 容器→本机后端 → {"status":"ok"}
                                               # ⇐ 这条过了才说明 BACKEND_HOST 填对（别只看 healthcheck 颜色）
curl -sI http://localhost/ | grep -iE "^HTTP|^location"   # 302 + Location: /openops/（文根跳转）
curl -sI http://localhost/openops/ | head -1  # 静态 200（真正的页面入口）
curl -s http://localhost/openops/api/health   # {"status":"ok"}（★新链路：rewrite 剥 /openops → location /api）
curl -s http://localhost/openback/health      # {"status":"ok"}（灰度期回滚路径 / IP 直访兜底）
# 浏览器全链：建 Agent → CopilotChat 流式回包 → 子 Agent 两轮派发/审批/汇报。
# 活动栏需同时验 AG-UI CUSTOM + 备用 SSE 去重、业务/技术切换、刷新恢复、显示更早与窄屏覆盖层；
# DevTools/DOM 抽查不得出现完整参数/响应、stdout/stderr、Authorization/Cookie/token。
```

TLS：内网证书就绪后挂载证书目录 + 以自有 conf 覆盖模板（compose 给 frontend 加
`volumes: [./openops-ssl.conf:/etc/nginx/templates/default.conf.template, ./certs:/etc/nginx/certs]`
并加 443 端口映射），或前置负载均衡终结 TLS。

## 三½、日志容量（一次性主机配置，装完就做）

两个日志槽**默认都无上限**，不设就是磁盘定时炸弹——本机已经因磁盘满导致 PG 起不来过一次。

**① 后端 → journald。** 单元不设 `StandardOutput=`，全部进 journald；单元里的
`LogRateLimitIntervalSec/Burst` 只管**瞬时速率**，总容量是主机级配置，必须单独设：

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/openops.conf >/dev/null <<'EOF'
[Journal]
SystemMaxUse=2G
SystemMaxFileSize=200M
MaxRetentionSec=14day
EOF
sudo systemctl restart systemd-journald
journalctl --disk-usage          # 确认已收敛到 2G 以内
```

**② 前端/sidecar/PG 容器 → docker json-file。** compose 里已带 `logging:` 上限
（`max-size: 50m` × `max-file: 5` = 单容器封顶 250M），随包发布，无需手工改。
但**上限只对新建容器生效**——升级时 `docker compose up -d` 会重建容器，故自动生效；
若容器未重建，需 `docker compose up -d --force-recreate`。核对：

```bash
docker inspect openops-frontend --format '{{json .HostConfig.LogConfig}}'
# → {"Type":"json-file","Config":{"max-file":"5","max-size":"50m"}}
du -sh /var/lib/docker/containers/*/*-json.log | sort -h | tail -5
```

兜底（推荐，覆盖将来任何新容器）：主机 `/etc/docker/daemon.json` 设同款默认值，
改完 `sudo systemctl restart docker`（会重启所有容器，挑窗口做）：

```json
{ "log-driver": "json-file", "log-opts": { "max-size": "50m", "max-file": "5" } }
```

**③ 应用日志级别。** 五个旋钮**全部有默认值，不配即可**；下面只在需要调整时用：

| 变量 | 默认 | 何时动 |
|---|---|---|
| `OPENOPS_LOG_LEVEL` | INFO | 日志量仍偏大 → `WARNING`（会连 uvicorn access 行一起静掉） |
| `OPENOPS_ACCESS_LOG_DEDUP_S` | 60 | 排查某个具体请求 → `0` 关抑制（健康检查路径恒不记，与本项无关） |
| `OPENOPS_THIRD_PARTY_LOG_LEVEL` | WARNING | 查第三方库自身问题 → `INFO`。⚠ 调高后 httpx 会**按每次出站刷屏且带完整 URL**，查完立刻调回 |
| `OPENOPS_IAM_TOKEN_TTL_S` | 300 | 一般不动。**别设 0**——内网 j2c 无 token 缓存，0 = 每次出站同步打一次 IAM 并阻塞事件循环 |
| `OPENOPS_SCOPE_PEEK_TTL_S` / `_FAIL_TTL_S` | 30 / 10 | 一般不动。oModel 长期不可用又想快速重试时才调小负缓存 |

`OPENOPS_HTTP_DEBUG` 本次**建议先留 1**：403 已被限频压到每 30 秒最多一对，留着才看得到
它有没有好；定位完再按 `docs/release-checklist.md` 改回 0。

## 四、测试/生产双环境差异（仅三处，其余工件共用）

| 位置 | 测试 | 生产 |
|---|---|---|
| `backend/config/openops.<env>.env` | openops.test.env（测试库/测试端点/测试 cookie） | openops.prod.env（生产库/生产凭证；上线核对清单在模板尾部） |
| `sidecar.env` 的 `OPENOPS_BACKEND_URL` | 测试机内网 LAN IP | 生产机内网 LAN IP |
| `frontend.env` 的 `BACKEND_HOST`（注入 nginx 模板） | 测试机内网 LAN IP | 生产机内网 LAN IP |

规则：`run-backend.sh test|prod` 选文件；systemd 用 `EnvironmentFile` 指同一份；实值 env 文件
永不入 git（.gitignore 已拦）。frontend 与 sidecar 镜像**不要**按环境重打——地址全在 env 注入层。

⚠ 测试与生产**不要放同一台机器**：compose 的 `container_name`（`openops-frontend` /
`openops-sidecar`）与宿主端口 80 都是固定值，两套会直接撞名撞端口；沙箱容器还需靠
`OPENOPS_SANDBOX_LABEL_SCOPE` 区分才不会互删孤儿。要共存须先解决这三处。

## 四½、域名与文根（单文根 xxxx.com/openops）

镜像 dist 已烘焙前端文根 `base=/openops/` 与后端 API 前缀 `/openops/api`（build-artifacts.sh
注入并断言；换文根须同改 nginx.conf.template 文根段并重打前端镜像）。IP 直访与域名访问同时
可用（`http://本机IP/` 会 302 到 `/openops/`）。

**给运维的公司网关规则（只剩一条）**：

```nginx
# 前端：不 strip（本机 nginx 自己剥前缀，剥完 API/静态/copilotkit 各归其位）
location /openops {
    proxy_pass http://<本机内网IP>:80;
    proxy_http_version 1.1;              # ⚠ 新增
    proxy_set_header Connection "";      # ⚠ 新增
    proxy_set_header Host $host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;                 # ⚠ 新增——SSE/agui 现在全量走这条规则
    proxy_cache off;                     # ⚠ 新增
    proxy_read_timeout 3600s;            # ⚠ 新增
    proxy_send_timeout 3600s;            # ⚠ 新增
}
```

**⚠ 那五个「新增」是本次收敛的关键路径，不是可选项。** 收敛前 SSE/agui 走的是 `/openback`
规则（它一直带着关缓冲+长超时）；收敛后这条 `/openops` 规则承载 100% 流式流量，而它**原本
一条流式指令都没有**。且失效模式是**静默缓冲而非断流**：后端有 15s 心跳
（`event_stream_service.py`、`agui_service.py`）会不断重置网关默认的 60s 读超时，所以连接不会断，
只会把 token 攒成一坨吐出来——表现像「模型变慢了」，无报错、无日志、`/api/health` 全绿。
`proxy_http_version 1.1` 更是应用侧完全无法补救。**先落地并验证这条规则，再上新镜像**（§四¾）。

**后端 env**：`OPENOPS_ROOT_PATH` 见 §四¾ 分阶段取值。后端应用层 RootPathShim 幂等——带前缀
就剥、不带就原样放行，所以新旧文根可同时服务（这是灰度回滚的支点）；sidecar 直连 IP:18082
裸路径不受任何影响。⚠勿用 uvicorn --root-path——新版会把前缀拼回请求路径，遇不剥前缀的网关
变双前缀 404（内网实测）。

> **订正一处长期口径错误**：曾有注释称「网关对 `/openback` 带尾斜杠 ⇒ 由网关 strip」。实际
> 给运维的规则 `proxy_pass http://<IP>:18082;` **无尾斜杠 ⇒ 不 strip**，真正剥 `/openback` 的
> 一直是后端 RootPathShim。因为 shim 幂等，两种读法都能跑通，所以这个矛盾长期没被发现——
> 但它会诱导出「同机了就把 ROOT_PATH 清空」的操作，而网关只要还挂着 `/openback`，一清空
> 就是**全部 API 404**。`scripts/release_check.sh` 已加门禁锁死这对组合。
**IAM 回跳**：仅浏览器导航用的 `OPENOPS_IAM_LOGIN_URL` / `OPENOPS_IAM_SIGNOUT_URL` 支持
`{host}` 占位符。携带 Cookie/token 的 access-token 与 userinfo URL 必须是固定 HTTPS 地址，禁止
请求域名替换。回跳示例：
`OPENOPS_IAM_LOGIN_URL=https://{host}/epstenant/#/login?redirect=https%3A%2F%2F{host}%2Fopenops%2F%3F`。
**登出链路**：`OPENOPS_IAM_SIGNOUT_URL`（如 `https://{host}/gw/iam/auth/logout`）是对称于 login 的
SSO 登出 API，**只收 POST + CSRF 头**——前端从 `OPENOPS_IAM_CSRF_COOKIE_NAME`（默认 `IAM-Csrf-Token`）
取 token，用 `fetch` **后台 POST**（同源）调它清 SSO cookie，**绝不用浏览器 GET 导航过去**（GET
必 404 Whitelabel 白页）；随后浏览器只跳 `OPENOPS_IAM_LOGOUT_REDIRECT_URL`（设 `/openops/` 而非 `/`）
或 `login_url`。

**客户端 IP 透传**（IAM 会话绑 IP）：**承载 `/api` 的每一跳**都要
`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`（必须是 `$proxy_add_x_forwarded_for`，
不能用 `$remote_addr`——后者会丢掉首跳）。后端取 XFF 首跳作为用户真实浏览器 IP 发给 IAM；
缺则后端拿到网关 IP、IAM 绑 IP 校验失败 → /me 持续 401 登录横跳。
单文根收敛后这条链是「公司网关 → 本机 nginx → 本机后端」两跳，两跳都已带该头
（网关规则见上；nginx 模板 `location /api` 自带）⇒ XFF = `浏览器IP, 网关IP`，后端取首跳。

**域名侧验证**：
```bash
curl -s https://xxxx.com/openops/api/health      # {"status":"ok"}（新链路）
curl -s https://xxxx.com/openops/ -o /dev/null -w '%{http_code}\n'   # 200 页面
# XFF 断言（防门禁空转）：伪造首跳，确认后端解析出的是它而不是网关 IP
curl -s -H "X-Forwarded-For: 203.0.113.7" https://xxxx.com/openops/api/openops/v1/me
```
对话流式**逐字**回包（不是攒一坨才出现）——这是网关 buffering 是否真的关掉的唯一可靠判据。

## 四¾、双机 → 单机的分阶段切换

**不要把「删网关 /openback」和「上新镜像」放进同一次变更**：那样一旦出问题就必须等运维改
规则才能回滚，5 分钟的回滚会变成故障期的跨部门等待。靠 RootPathShim 的幂等把它拆开——
后端可以零成本同时服务新旧两个文根（`backend/tests/test_init.py` 已钉住此行为）。

| 阶段 | 动作 | 回滚方式 |
|---|---|---|
| **1** | 运维给 `/openops` 规则加上那五条流式指令。`/openback` 保留不动。 | 尚未发布任何东西 |
| **2** | 同机化：`BACKEND_HOST` / `OPENOPS_BACKEND_URL` 改为本机 LAN IP；网关 `/openops` 指向本机。旧镜像仍打 `/openback/api`，链路不变。 | 改回 env + 网关一行 |
| **3** | 上新前端镜像（`/openops/api`）。`OPENOPS_ROOT_PATH` **仍为** `/openback`。 | **重 load 旧镜像 tag ⇒ 完事，不需要运维** |
| **4** | 泡稳后：运维删 `/openback`；`OPENOPS_ROOT_PATH` 留空；重启后端。 | 运维重加规则（慢——所以刻意放最后） |

阶段 3 前先在机器上留一份上一版 `openops-frontend-image.tar.gz`：`build-artifacts.sh` 同时打
`:<VER>` 与 `:latest`，而 compose 钉的是 `:latest` ⇒ 回滚 = 重 load 旧 tar 并重打 `latest`
（或把 compose 改成 `:<旧VER>`）。

⚠ **阶段 4 的回退不受版本回滚保护**：`EnvironmentFile` 指向 `/opt/openops/shared/config/`，是
**跨 release 共享**的，而 `/opt/openops/releases/<BUILD_ID>` 不可变 ⇒ 把 `current` 软链指回旧版
**不会**恢复 `OPENOPS_ROOT_PATH`，必须手工改回。

## 五、故障排查

| 症状 | 查什么 |
|---|---|
| frontend 容器起不来/秒退 | `docker logs openops-frontend`——`frontend.env` 缺 `BACKEND_HOST` 时 nginx 启动即报错（fail-fast，模板变量未注入） |
| **容器 healthy 但每个 `/api/*` 都 502** | **`frontend.env` 的 `BACKEND_HOST` 填成了 `127.0.0.1`**（容器里指向 nginx 容器自己）。healthcheck 探的是本机静态页，所以照样绿。改本机内网 LAN IP。`docker exec openops-frontend nginx -T \| grep proxy_pass` 看实效值 |
| **只有 Copilot 对话坏、其余全正常** | `sidecar.env` 缺失/留空 ⇒ 回落代码默认 `http://127.0.0.1:18082`（= sidecar 容器自己）。`docker exec openops-sidecar wget -qO- http://127.0.0.1:4002/healthz` 看返回里的 `backend` 字段是不是本机 LAN IP |
| 对话发送后 HTTP 500 | `docker exec openops-sidecar wget -qO- http://127.0.0.1:4002/healthz`；sidecar 日志 `docker logs openops-sidecar`；`sidecar.env` 的 `OPENOPS_BACKEND_URL` 是否可达（容器内 `wget -qO- <url>/health`） |
| **对话不逐字出、攒一坨才吐（像「模型变慢」）** | 公司网关 `/openops` 规则缺 `proxy_buffering off`（§四½ 的五条）。**不会报错也不会断流**——后端 15s 心跳一直在重置读超时，只是被缓冲住。这是单文根收敛的头号风险 |
| 全部 API 404（页面能开、接口全挂） | `OPENOPS_ROOT_PATH` 与前端烘焙的 API_BASE 不自洽：网关还挂着 `/openback` 却把 ROOT_PATH 清空了（真正剥前缀的是后端 shim，不是网关）。见 §四¾ 阶段表；`bash scripts/release_check.sh --production-env <env>` 会拦 |
| 活动栏/流式卡住、几十秒断流 | 容器内实效配置 `docker exec openops-frontend nginx -T \| grep -A6 'location /api'`（`proxy_buffering off`+长超时应在） |
| 80 端口被占 | 宿主机残留 nginx/其它服务占 80：`ss -lntp \| grep :80`，停掉或改 compose 端口映射 |
| 401/身份错乱 | nginx 是否透传 `Cookie` 与 `X-Forwarded-For`（IAM Client-Ip 依赖 XFF 首跳）；后端 `OPENOPS_IAM_ENABLED` 与 mock 头口径 |
| scope/告警工具失败 | 后端机跑 `python -m` 诊断工具箱 check-db / check-net（35 号 §五）；console cookie 过期 401 即换 |
| 刷新子路由 404 | nginx `try_files $uri /index.html` 是否在（SPA 回退） |
| 建表报错 | GaussDB 保留字（已规避 role→user_role）；旧库列缺失=重跑 core.sql（增量段幂等） |

## 六、升级

- 前端/sidecar：`docker load` 新镜像 → `docker compose up -d`（镜像 tag latest 就地翻新；浏览器强刷）；
- 后端：新包解压到新的 `/opt/openops/releases/<BUILD_ID>`，生产 env 门禁和迁移通过后再原子切换
  `/opt/openops/current`，随后 `systemctl restart openops-backend`；禁止逐文件覆盖。回滚只需把 `current`
  原子指回上一个完整 release 并重启。
- 上线前过一遍 `bash scripts/release_check.sh --production-env <真实 env>` + docs/release-checklist.md。
