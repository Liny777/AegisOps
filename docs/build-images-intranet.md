# 内网本地构建镜像手册（前端一体镜像 / sidecar；2026-07-13）

内网改完代码后自主打镜像、就地更换，不再依赖外网侧投递。与 `deploy/build-artifacts.sh`
（Mac 外网侧一键打包）做的是同一件事，本手册把其中镜像部分拆成内网可逐条执行的命令。

| 交付物 | 构建方式 | 改什么代码需要重打 |
|---|---|---|
| openops-frontend 镜像（nginx+dist 一体） | 宿主构建 dist → docker build | `frontend/src/**`（页面/交互） |
| openops-sidecar 镜像（CopilotKit runtime） | docker build（npm ci 在镜像内） | `frontend/server/**`、`frontend/package*.json` |
| 后端 | **无镜像**——代码包 + uvicorn（见 §五） | `backend/**` |

> 铁律：镜像**不要**按测试/生产分别打——后端机地址在启动时经 env 注入（frontend.env 的
> `BACKEND_HOST` / sidecar.env 的 `OPENOPS_BACKEND_URL`），同一镜像两环境共用。

## 一、一次性前置（内网构建机）

构建机 = 任一有 docker 的内网 Linux x64（用前端机本机最省事，省去镜像搬运）。

1. **代码**：内网可达的仓库副本（git 同步或代码包解压均可），下文以仓库根为 cwd。
2. **基础镜像**（仅首次；之后本地缓存长期可用）：
   - 内网有 docker registry 镜像源 → 直接 `docker pull nginx:stable-alpine node:20-alpine`；
   - 没有 → 外网侧一次性带入：
     ```bash
     # 外网机
     docker save nginx:stable-alpine node:20-alpine | gzip > openops-base-images.tar.gz
     # 内网构建机
     docker load -i openops-base-images.tar.gz
     ```
3. **node 20 + npm**（仅前端 dist 构建用；sidecar 的 npm ci 在镜像内跑不需要）：
   ```bash
   npm config set registry <内网 npm 源>
   cd frontend && npm ci        # 首次装 node_modules；package.json 没动过就不用重复
   ```

## 二、前端一体镜像

```bash
cd <仓库根>
VER=$(git rev-parse --short HEAD 2>/dev/null || date +%m%d%H%M)

# 1. 构建 dist —— 必须进程 env 强制 real（.env.local 若有 mock 会被 vite 读到烘焙进包）；
#    文根两 env 与 deploy/frontend/nginx.conf.template 文根段耦合，换文根两处同改
( cd frontend && VITE_OPENOPS_API_MODE=real VITE_OPENOPS_TRANSPORT=agui \
  VITE_OPENOPS_BASE=/openops/ VITE_OPENOPS_API_BASE=/openops/api npm run build )

# 2. 构建产物断言（任一命中即作废重建）
if grep -rq "mock 演示" frontend/dist/assets; then echo "✗ dist 含 mock 指纹"; exit 1; fi
grep -q '/openops/assets' frontend/dist/index.html || { echo "✗ 文根未烘焙进 dist"; exit 1; }
grep -q "Program Files" frontend/dist/index.html && { echo "✗ 文根被 Git Bash 路径翻译污染（见 §六）"; exit 1; }
# API_BASE 落 assets/*.js（client.ts 的常量），index.html 看不见它，故单独断言。
# ⚠必须带引号锚定：CopilotChatPanel 的 runtimeUrl=`${BASE_URL}api/copilotkit` 会被 vite 折叠成
#   "/openops/api/copilotkit"，裸 grep '/openops/api' 在 API_BASE 完全没注入时照样命中（实测）。
grep -rEq "[\"']/openops/api[\"']" frontend/dist/assets \
  || { echo "✗ API_BASE 未烘焙成 /openops/api（回落默认 /api 会全量 404）"; exit 1; }
if grep -rq '/openback' frontend/dist/assets; then echo "✗ dist 仍含已退役的 /openback 文根"; exit 1; fi

# 3. dist staging 进构建上下文 → docker build →打完即清
rm -rf deploy/frontend/dist && cp -R frontend/dist deploy/frontend/dist
docker build -f deploy/frontend/Dockerfile \
  -t "openops-frontend:${VER}" -t openops-frontend:latest deploy/frontend
rm -rf deploy/frontend/dist
```

内网 Linux x64 原生构建**不需要** `--platform linux/amd64`（那是 Mac 交叉构建才要的）。

## 三、sidecar 镜像

```bash
cd <仓库根>
docker build -f deploy/sidecar/Dockerfile \
  --build-arg NPM_REGISTRY=<内网 npm 源> \
  -t "openops-sidecar:${VER}" -t openops-sidecar:latest frontend/
```

`NPM_REGISTRY` 必传（镜像内 `npm ci` 默认打 registry.npmjs.org，内网不通）；
`frontend/package*.json` 没动时该层走缓存、秒级完成。

## 四、沙箱镜像（预装 pip 依赖）

skill 脚本在沙箱容器里跑，容器运行态只读 rootfs 装不了包，故一组常用 Python 库列在
`deploy/sandbox/requirements.txt`、构建期烘焙进 `openops-sandbox` 镜像。内网无公网直连、
`pip` 默认打 pypi.org 不通，须走公司源（与上面 `NPM_REGISTRY` 同理，用 pip 的 build-arg）。

```bash
cd <仓库根>
export PIP_INDEX_URL=https://mirrors.tools.huawei.com/pypi/simple
export PIP_TRUSTED_HOST=mirrors.tools.huawei.com
bash deploy/sandbox/build-sandbox-image.sh v1
# → deploy/artifacts/openops-sandbox-image.tar.gz（可 docker load 的离线工件）
```

传了 `PIP_INDEX_URL` 的构建，还会把源**持久化进镜像**（`/etc/pip.conf` + `UV_INDEX_URL`
环境变量），所以 skill 运行期在容器里自己 `pip install` / `uv pip install` 额外包时**也自动
走公司源、无需带 `-i`**。运行期装包受沙箱约束：只读 rootfs 只能 `pip install --target
<可写目录>`（`/openops/workspace` 或 `/tmp`）、经 HITL 审批、且需 `container_network_mode=bridge`
能出网——能预装的还是优先进 `requirements.txt` 重建镜像。

改 `requirements.txt` 包清单或 `Dockerfile` 后重跑即可。后端机侧 `docker load` + 管理台把
`container_image` 指向 `openops-sandbox:<版本>` 才吃到预装依赖（默认镜像 `python:3.11-slim`
无这些库），详见 `docs/sandbox-docker-runbook.md`。内网 Linux x64 原生构建可去掉
build 脚本里的 `--platform linux/amd64`（那是 Mac 交叉构建才要）。

## 五、更换镜像（前端机）

```bash
# 构建机 = 前端机：latest 标签已就地翻新，直接
cd /opt/openops/frontend && docker compose up -d     # 只重建镜像变化的容器

# 构建机 ≠ 前端机：save/load 搬运后同上
docker save openops-frontend:latest | gzip > f.tar.gz    # （单文件 >500MB 才需 split）
# 前端机：docker load -i f.tar.gz && cd /opt/openops/frontend && docker compose up -d

# 验证（/ 是 302 → /openops/，页面入口看 /openops/）
curl -sI http://localhost/openops/ | head -1 && curl -s http://localhost/api/health
```

**回滚**：每次构建都带 `${VER}` 版本标签，`docker tag openops-frontend:<旧VER>
openops-frontend:latest && docker compose up -d` 即回。可 `docker images openops-frontend`
查在库版本。

## 六、后端更新（无镜像，勿容器化——拓扑定稿是代码包 + uvicorn）

```bash
# 构建机生成版本化完整包（不含真实 env/venv），目标机按 docs/deploy-intranet.md：
bash deploy/build-artifacts.sh --backend-only
# 目标机：校验 sha256 → 解压到 releases/<BUILD_ID> → 门禁/迁移 → 原子切换 current → 重启
# 禁止把若干 src 文件逐个覆盖进正在运行的目录。
```

DDL 有变时**先库后码**：重跑 `sql/openops_v1_core.sql`（幂等）或该次变更的独立迁移脚本。

## 七、常见坑

| 症状 | 原因 |
|---|---|
| dist 断言命中「mock 指纹」 | 构建 shell 没带 `VITE_OPENOPS_API_MODE=real`（.env.local 的 mock 污染） |
| 沙箱镜像构建卡在 pip | 没 `export PIP_INDEX_URL`/`PIP_TRUSTED_HOST`，在打外网源 pypi.org（内网不通） |
| 资源 URL 混入 `/Program Files/Git/`、页面白屏 | **Windows Git Bash 的 MSYS 路径翻译**把 `/openops/` 等路径型 env 改写成了 Windows 路径。修法任选：①（推荐，任何 shell 免疫）两个路径 env 改写进 `frontend/.env.production.local`（`VITE_OPENOPS_BASE=/openops/` 与 `VITE_OPENOPS_API_BASE=/openops/api`），命令行只留 real/agui；② 构建前 `export MSYS_NO_PATHCONV=1`（MSYS2 用 `MSYS2_ENV_CONV_EXCL`）。重建后跑上方 §二.2 断言再打镜像 |
| sidecar 构建卡在 npm ci | 没传 `--build-arg NPM_REGISTRY=`，在打外网源 |
| docker build 报找不到基础镜像 | 首次未做 §一.2 的基础镜像带入 |
| up -d 后页面没变 | 浏览器缓存——强刷；或 build 用了旧 dist（确认 §二第 3 步 staging 是刚构建的） |
| 换镜像后 /api/copilotkit 502 | sidecar 容器还在启动中，等 healthcheck 过；持续 502 看 `docker logs openops-sidecar` |

关联：docs/deploy-intranet.md（整机部署）、deploy/build-artifacts.sh（外网侧一键打包，
与本手册命令同源）、docs/release-checklist.md（上线前检查）。
