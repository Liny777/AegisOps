# 内网双主机部署手册（2026-07-13；对齐 26/27 号拓扑）

```
用户浏览器 ──> 前端机（docker compose 两容器）
                └── openops-frontend（nginx+dist 一体镜像，:80）
                      ├── /              → 镜像内静态（SPA 回退）
                      ├── /api/copilotkit → compose 网内 sidecar 容器 :4002（CopilotKit 流）
                      └── /api           → 后端机 :18082（REST/SSE/agui，关缓冲长超时）
                    openops-sidecar（不发布宿主机端口，仅 compose 网内可达）
后端机：uvicorn（run.py，:18082）+（可选）Docker 沙箱 ── PG（内网库，先跑 core.sql）
```

前端所有请求都是相对路径（`/api`、`/api/copilotkit`），**同两个镜像测试/生产共用**——
后端机地址不烘焙进镜像，启动时经 env 注入 nginx 模板（envsubst）；环境差异只有三处（见 §四）。
前端机无需安装 nginx/node，只要 docker。图上旧口径端口 18081 已作废，实况 18082。

## 一、Mac 侧打包（一条命令）

```bash
bash deploy/build-artifacts.sh          # 产出 deploy/artifacts/ 四件 + SHA256SUMS
```

| 工件 | 投递到 | 说明 |
|---|---|---|
| openops-frontend-image.tar.gz | 前端机 | nginx+dist 一体镜像（linux/amd64；构建时强制 real，脚本内有防 mock 烘焙断言） |
| openops-sidecar-image.tar.gz | 前端机 | sidecar 镜像（linux/amd64）。均 gzip：内网单文件上传限 500MB，`docker load` 原生认 .tar.gz |
| openops-backend-src.tar.gz | 后端机 | 代码包（不含 venv） |
| openops-deploy-conf.tar.gz | 两机 | compose/env 模板/systemd |

## 二、后端机（Linux x64，python3.11+，可选 docker）

```bash
sudo mkdir -p /opt/openops && cd /opt/openops     # 两个工件（backend-src / deploy-conf）先放这里
tar xzf openops-backend-src.tar.gz               # → backend/ scripts/ docs/
tar xzf openops-deploy-conf.tar.gz               # → deploy/（systemd unit 在里面）
cd backend

python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[agentscope]"          # 内网 pip 源；沙箱切 docker 再补 ".[sandbox]"

# 双环境配置（含凭证，已 gitignore；模板内有全量分组注释）
cp config/openops.test.env.example config/openops.test.env   # 测试机
# cp config/openops.prod.env.example config/openops.prod.env # 生产机
vi config/openops.test.env             # PG 六件/GLM Key/ENCRYPTION_KEY/console 系/…

# 建表（幂等，26 表；GaussDB 保留字已规避）——每个库/schema 一次
# 已建过表的旧库升级也重跑本文件：尾部「增量迁移」段幂等补齐（07-13 缺陷批新增
# sre_idempotency_key.request_hash 列 + result_json 去 NOT NULL/DEFAULT，必须跑）
psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f sql/openops_v1_core.sql

./run-backend.sh test                  # 前台验证
curl -s http://127.0.0.1:18082/health  # → {"status":"ok"}
```

常驻（systemd）：

```bash
sudo cp /opt/openops/deploy/systemd/openops-backend.service /etc/systemd/system/
# 编辑：EnvironmentFile 指向本机的 openops.test.env 或 openops.prod.env；User 按实际
sudo systemctl daemon-reload && sudo systemctl enable --now openops-backend
journalctl -u openops-backend -f       # 看启动横幅（runtime/omodel/mcp 开关一目了然）
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
curl -sI http://localhost/ | grep -iE "^HTTP|^location"   # 302 + Location: /aegisops/（文根跳转）
curl -sI http://localhost/aegisops/ | head -1  # 静态 200（真正的页面入口）
curl -s http://localhost/aegisback/health      # {"status":"ok"}（IP 直访兜底：剥前缀转后端）
# 浏览器全链：建 Agent → 对话（CopilotChat 流式回包）→ 活动栏 SSE 持续推送不断流
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

## 四½、域名与文根（xxxx.com/aegisops · xxxx.com/aegisback）

镜像 dist 已烘焙前端文根 `base=/aegisops/`、后端 API 前缀 `/aegisback/api`（build-artifacts.sh
注入；换文根须同改 nginx.conf.template 文根段并重打前端镜像）。IP 直访与域名访问同时可用
（`http://前端机IP/` 会 302 到 `/aegisops/`）。

**给运维的公司网关规则（两条，注意不对称）**：

```nginx
# ① 前端：不 strip（前端机 nginx 自己剥前缀）
location /aegisops {
    proxy_pass http://<前端机IP>:80;
    proxy_set_header Host $host;
    proxy_set_header Cookie $http_cookie;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
# ② 后端：剥不剥前缀均可（后端 RootPathShim 按 OPENOPS_ROOT_PATH 自剥）——
#    域名系统只支持「ip+端口」直指后端机:18082 也能工作
location /aegisback/ {
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

**后端 env**：`OPENOPS_ROOT_PATH=/aegisback`（env 模板已带）。后端应用层 RootPathShim 按它
自剥前缀（网关剥不剥都兼容），并回写 root_path 使重定向/docs URL 带前缀；sidecar 直连
IP:18082 裸路径不受任何影响。⚠勿用 uvicorn --root-path——新版会把前缀拼回请求路径，
遇不剥前缀的网关变双前缀 404（内网实测）。
**IAM 回跳**：`OPENOPS_IAM_LOGIN_URL` / `OPENOPS_IAM_SIGNOUT_URL` 及 token/userinfo URL 均支持
`{host}` 占位符（运行时替换为请求域名，测试/生产双域名共用一份配置），例：
`OPENOPS_IAM_LOGIN_URL=https://{host}/epstenant/#/login?redirect=https%3A%2F%2F{host}%2Faegisops%2F%3F`。

**域名侧验证**：`https://xxxx.com/aegisback/health` → `{"status":"ok"}`；
`https://xxxx.com/aegisops/` → 页面；对话流式不断流（验证网关 buffering 关闭生效）。

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
- 后端：解压新代码包覆盖（config/ 与 .venv 不动）→ `pip install -e ".[agentscope]"`（依赖有变时）
  → `systemctl restart openops-backend`；DDL 有新表时先重跑 core.sql（幂等）。
- 上线前过一遍 `bash scripts/release_check.sh` + docs/release-checklist.md。
