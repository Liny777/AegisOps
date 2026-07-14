# 内网双主机部署手册（2026-07-13；对齐 26/27 号拓扑）

```
用户浏览器 ──> 前端机（docker compose 两容器）
                └── openops-frontend（nginx+dist 一体镜像，:80）
                      ├── /              → 镜像内静态（SPA 回退）
                      ├── /api/copilotkit → compose 网内 sidecar 容器 :4002（CopilotKit 流）
                      └── /api           → 后端机 :18082（REST/SSE/agui，关缓冲长超时）
                    openops-sidecar（不发布宿主机端口，仅 compose 网内可达）
后端机：uvicorn（run.py，:18082）+（可选）Docker 沙箱 ── PG（存量库先迁移再跑 core.sql；新库只跑 core.sql）
```

前端所有请求都是相对路径（`/api`、`/api/copilotkit`），**同两个镜像测试/生产共用**——
后端机地址不烘焙进镜像，启动时经 env 注入 nginx 模板（envsubst）；环境差异只有三处（见 §四）。
前端机无需安装 nginx/node，只要 docker。图上旧口径端口 18081 已作废，实况 18082。

## 一、Mac 侧打包（一条命令）

```bash
bash deploy/build-artifacts.sh                  # 全量：四件 + SHA256SUMS
bash deploy/build-artifacts.sh --backend-only   # 仅后端：deploy/artifacts/<BUILD_ID>/ 下完整包 + .sha256
```

| 工件 | 投递到 | 说明 |
|---|---|---|
| openops-frontend-image.tar.gz | 前端机 | nginx+dist 一体镜像（linux/amd64；构建时强制 real，脚本内有防 mock 烘焙断言） |
| openops-sidecar-image.tar.gz | 前端机 | sidecar 镜像（linux/amd64）。均 gzip：内网单文件上传限 500MB，`docker load` 原生认 .tar.gz |
| openops-backend-src-`<BUILD_ID>`.tar.gz | 后端机 | 两种模式均生成版本化完整包（不含 venv/真实 env；包内有 `BUILD_INFO`） |
| openops-deploy-conf.tar.gz | 两机 | compose/env 模板/systemd |

## 二、后端机（Linux x64，python3.11+，版本目录原子发布）

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
    # 存量库：先无损重命名旧对象，再补多 Agent 派发批次列，最后重放 core.sql；全部成功后才能发布新后端。
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/migrate-2026-07-14-ddl-object-names.sql"
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/migrate-2026-07-14-subagent-activity.sql"
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/openops_v1_core.sql"
    ;;
  new)
    # 全新库：只执行 core.sql；成功后再发布新后端。
    psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f "$RELEASE/backend/sql/openops_v1_core.sql"
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

## 三、前端机（Linux x64，仅需 docker）

```bash
# 0. 若此前用过宿主机 nginx 方案：先 systemctl stop nginx（80 端口让给容器）

# 1. 两个镜像
docker load -i openops-frontend-image.tar.gz
docker load -i openops-sidecar-image.tar.gz

# 2. compose + 两个 env（唯一必改项都是后端机地址）
mkdir -p /opt/openops/frontend && cd /opt/openops/frontend
tar xzf /path/to/openops-deploy-conf.tar.gz      # → deploy/（compose 与 env 模板在里面）
cp deploy/frontend/docker-compose.yml .
cp deploy/frontend/frontend.test.env.example frontend.env && vi frontend.env   # BACKEND_HOST
cp deploy/sidecar/sidecar.test.env.example sidecar.env && vi sidecar.env       # OPENOPS_BACKEND_URL
docker compose up -d

# 3. 验证链（依次；sidecar 不发布宿主机端口，探活走 docker exec）
docker exec openops-sidecar wget -qO- http://127.0.0.1:4002/healthz   # {"ok":true,...}
curl -s http://localhost/api/health            # frontend 容器→后端机 → {"status":"ok"}
curl -sI http://localhost/ | grep -iE "^HTTP|^location"   # 302 + Location: /openops/（文根跳转）
curl -sI http://localhost/openops/ | head -1  # 静态 200（真正的页面入口）
curl -s http://localhost/openback/health      # {"status":"ok"}（IP 直访兜底：剥前缀转后端）
# 浏览器全链：建 Agent → CopilotChat 流式回包 → 子 Agent 两轮派发/审批/汇报。
# 活动栏需同时验 AG-UI CUSTOM + 备用 SSE 去重、业务/技术切换、刷新恢复、显示更早与窄屏覆盖层；
# DevTools/DOM 抽查不得出现完整参数/响应、stdout/stderr、Authorization/Cookie/token。
```

TLS：内网证书就绪后挂载证书目录 + 以自有 conf 覆盖模板（compose 给 frontend 加
`volumes: [./openops-ssl.conf:/etc/nginx/templates/default.conf.template, ./certs:/etc/nginx/certs]`
并加 443 端口映射），或前置负载均衡终结 TLS。

## 四、测试/生产双环境差异（仅三处，其余工件共用）

| 位置 | 测试 | 生产 |
|---|---|---|
| 后端机 `backend/config/openops.<env>.env` | openops.test.env（测试库/测试端点/测试 cookie） | openops.prod.env（生产库/生产凭证；上线核对清单在模板尾部） |
| 前端机 `sidecar.env` 的 `OPENOPS_BACKEND_URL` | 测试后端机 IP | 生产后端机 IP |
| 前端机 `frontend.env` 的 `BACKEND_HOST`（注入 nginx 模板） | 测试后端机 IP | 生产后端机 IP |

规则：`run-backend.sh test|prod` 选文件；systemd 用 `EnvironmentFile` 指同一份；实值 env 文件
永不入 git（.gitignore 已拦）。frontend 与 sidecar 镜像**不要**按环境重打——地址全在 env 注入层。

## 四½、域名与文根（xxxx.com/openops · xxxx.com/openback）

镜像 dist 已烘焙前端文根 `base=/openops/`、后端 API 前缀 `/openback/api`（build-artifacts.sh
注入；换文根须同改 nginx.conf.template 文根段并重打前端镜像）。IP 直访与域名访问同时可用
（`http://前端机IP/` 会 302 到 `/openops/`）。

**给运维的公司网关规则（两条，注意不对称）**：

```nginx
# ① 前端：不 strip（前端机 nginx 自己剥前缀）
location /openops {
    proxy_pass http://<前端机IP>:80;
    proxy_set_header Host $host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
# ② 后端：剥不剥前缀均可（后端 RootPathShim 按 OPENOPS_ROOT_PATH 自剥）——
#    域名系统只支持「ip+端口」直指后端机:18082 也能工作
location /openback/ {
    proxy_pass http://<后端机IP>:18082;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;               # ⚠SSE/agui 流经此路：关缓冲 + 长超时缺一不可
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

**后端 env**：`OPENOPS_ROOT_PATH=/openback`（env 模板已带）。后端应用层 RootPathShim 按它
自剥前缀（网关剥不剥都兼容），并回写 root_path 使重定向/docs URL 带前缀；sidecar 直连
IP:18082 裸路径不受任何影响。⚠勿用 uvicorn --root-path——新版会把前缀拼回请求路径，
遇不剥前缀的网关变双前缀 404（内网实测）。
**IAM 回跳**：仅浏览器导航用的 `OPENOPS_IAM_LOGIN_URL` / `OPENOPS_IAM_SIGNOUT_URL` 支持
`{host}` 占位符。携带 Cookie/token 的 access-token 与 userinfo URL 必须是固定 HTTPS 地址，禁止
请求域名替换。回跳示例：
`OPENOPS_IAM_LOGIN_URL=https://{host}/epstenant/#/login?redirect=https%3A%2F%2F{host}%2Fopenops%2F%3F`。

**客户端 IP 透传**（IAM 会话绑 IP）：`/openback` 全链（公司网关→前端机→后端）每跳都要
`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`——后端取 XFF 首跳作为用户真实
浏览器 IP 发给 IAM。缺则后端拿到网关 IP、IAM 绑 IP 校验失败 → /me 持续 401 登录横跳。

**域名侧验证**：`https://xxxx.com/openback/health` → `{"status":"ok"}`；
`https://xxxx.com/openops/` → 页面；对话流式不断流（验证网关 buffering 关闭生效）。

## 五、故障排查

| 症状 | 查什么 |
|---|---|
| frontend 容器起不来/秒退 | `docker logs openops-frontend`——`frontend.env` 缺 `BACKEND_HOST` 时 nginx 启动即报错（fail-fast，模板变量未注入） |
| 对话发送后 HTTP 500 | `docker exec openops-sidecar wget -qO- http://127.0.0.1:4002/healthz`；sidecar 日志 `docker logs openops-sidecar`；`sidecar.env` 的 `OPENOPS_BACKEND_URL` 是否可达（容器内 `wget -qO- <url>/health`） |
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
