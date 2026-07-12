# 内网双主机部署手册（2026-07-13；对齐 26/27 号拓扑）

```
用户浏览器 ──> 前端机（宿主机 nginx :80）
                ├── /              → /opt/openops/frontend/dist（静态，SPA 回退）
                ├── /api/copilotkit → 本机 sidecar 容器 127.0.0.1:4002（CopilotKit 流）
                └── /api           → 后端机 :18082（REST/SSE/agui，关缓冲长超时）
后端机：uvicorn（run.py，:18082）+（可选）Docker 沙箱 ── PG（内网库，先跑 core.sql）
```

前端所有请求都是相对路径（`/api`、`/api/copilotkit`），**同一份 dist 与同一个 sidecar 镜像
测试/生产共用**；环境差异只有三处（见 §四）。图上旧口径端口 18081 已作废，实况 18082。

## 一、Mac 侧打包（一条命令）

```bash
bash deploy/build-artifacts.sh          # 产出 deploy/artifacts/ 四件 + SHA256SUMS
```

| 工件 | 投递到 | 说明 |
|---|---|---|
| openops-frontend-dist.tar.gz | 前端机 | 静态包（构建时强制 real，脚本内有防 mock 烘焙断言） |
| openops-sidecar-image.tar | 前端机 | linux/amd64 镜像，`docker load` |
| openops-backend-src.tar.gz | 后端机 | 代码包（不含 venv） |
| openops-deploy-conf.tar.gz | 两机 | nginx/systemd/compose/env 模板 |

## 二、后端机（Linux x64，python3.11+，可选 docker）

```bash
sudo mkdir -p /opt/openops && cd /opt/openops
tar xzf openops-backend-src.tar.gz && mv backend ./backend && cd backend

python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[agentscope]"          # 内网 pip 源；沙箱切 docker 再补 ".[sandbox]"

# 双环境配置（含凭证，已 gitignore；模板内有全量分组注释）
cp config/openops.test.env.example config/openops.test.env   # 测试机
# cp config/openops.prod.env.example config/openops.prod.env # 生产机
vi config/openops.test.env             # PG 六件/GLM Key/ENCRYPTION_KEY/console 系/…

# 建表（幂等，26 表；GaussDB 保留字已规避）——每个库/schema 一次
psql "host=<PG> dbname=<db> user=<u>" -v ON_ERROR_STOP=1 -f sql/openops_v1_core.sql

./run-backend.sh test                  # 前台验证
curl -s http://127.0.0.1:18082/health  # → {"status":"ok"}
```

常驻（systemd）：

```bash
sudo cp deploy/systemd/openops-backend.service /etc/systemd/system/
# 编辑：EnvironmentFile 指向本机的 openops.test.env 或 openops.prod.env；User 按实际
sudo systemctl daemon-reload && sudo systemctl enable --now openops-backend
journalctl -u openops-backend -f       # 看启动横幅（runtime/omodel/mcp 开关一目了然）
```

## 三、前端机（Linux x64，宿主机 nginx + docker）

```bash
# 1. 静态包
sudo mkdir -p /opt/openops/frontend && cd /opt/openops/frontend
tar xzf openops-frontend-dist.tar.gz          # → dist/

# 2. nginx（宿主机）
sed "s/<BACKEND_HOST>/10.x.x.x/" deploy/nginx/openops.conf | sudo tee /etc/nginx/conf.d/openops.conf
sudo nginx -t && sudo nginx -s reload

# 3. sidecar 容器
docker load -i openops-sidecar-image.tar
mkdir -p /opt/openops/sidecar && cd /opt/openops/sidecar
cp deploy/sidecar/docker-compose.yml .
cp deploy/sidecar/sidecar.test.env.example sidecar.env && vi sidecar.env   # 后端机地址
docker compose up -d

# 4. 验证链（依次）
curl -s http://127.0.0.1:4002/healthz          # sidecar → {"ok":true,"backend":...}
curl -s http://localhost/api/health            # nginx→后端机 → {"status":"ok"}
curl -sI http://localhost/ | head -1           # 静态 200
# 浏览器全链：建 Agent → 对话（CopilotChat 流式回包）→ 活动栏 SSE 持续推送不断流
```

## 四、测试/生产双环境差异（仅三处，其余工件共用）

| 位置 | 测试 | 生产 |
|---|---|---|
| 后端机 `backend/config/openops.<env>.env` | openops.test.env（测试库/测试端点/测试 cookie） | openops.prod.env（生产库/生产凭证；上线核对清单在模板尾部） |
| 前端机 `sidecar.env` 的 `OPENOPS_BACKEND_URL` | 测试后端机 IP | 生产后端机 IP |
| 前端机 nginx `<BACKEND_HOST>` | 测试后端机 IP | 生产后端机 IP |

规则：`run-backend.sh test|prod` 选文件；systemd 用 `EnvironmentFile` 指同一份；实值 env 文件
永不入 git（.gitignore 已拦）。dist 与 sidecar 镜像**不要**按环境重打——地址全在反代层。

## 五、故障排查

| 症状 | 查什么 |
|---|---|
| 对话发送后 HTTP 500 | `curl 127.0.0.1:4002/healthz`；sidecar 日志 `docker logs openops-sidecar`；`sidecar.env` 的 `OPENOPS_BACKEND_URL` 是否可达（容器内 `wget -qO- <url>/health`） |
| 活动栏/流式卡住、几十秒断流 | nginx `/api` 段的 `proxy_buffering off`+`proxy_read_timeout` 是否生效（`nginx -T | grep -A6 'location /api'`） |
| 401/身份错乱 | nginx 是否透传 `Cookie` 与 `X-Forwarded-For`（IAM Client-Ip 依赖 XFF 首跳）；后端 `OPENOPS_IAM_ENABLED` 与 mock 头口径 |
| scope/告警工具失败 | 后端机跑 `python -m` 诊断工具箱 check-db / check-net（35 号 §五）；console cookie 过期 401 即换 |
| 刷新子路由 404 | nginx `try_files $uri /index.html` 是否在（SPA 回退） |
| 建表报错 | GaussDB 保留字（已规避 role→user_role）；旧库列缺失=重跑 core.sql（增量段幂等） |

## 六、升级

- 前端：解压新 dist tar 覆盖 → 完成（浏览器强刷）；
- sidecar：`docker load` 新镜像 → `docker compose up -d`；
- 后端：解压新代码包覆盖（config/ 与 .venv 不动）→ `pip install -e ".[agentscope]"`（依赖有变时）
  → `systemctl restart openops-backend`；DDL 有新表时先重跑 core.sql（幂等）。
- 上线前过一遍 `bash scripts/release_check.sh` + docs/release-checklist.md。
