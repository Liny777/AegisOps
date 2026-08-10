# 发布检查清单（B9，2026-07-14）

静态自动项跑 `bash scripts/release_check.sh`；上线门禁必须再跑
`bash scripts/release_check.sh --production-env backend/config/openops.prod.env`。脚本只显示设置状态，
不输出变量值。下表为完整口径（含手动项）。

## 1. 数据库

- [ ] 存量库严格按顺序执行 `backend/sql/migrate-2026-07-14-ddl-object-names.sql` → `backend/sql/migrate-2026-07-14-subagent-activity.sql` → `backend/sql/migrate-2026-07-30-alert-entry-source.sql` → `backend/sql/migrate-2026-08-10-rename-run-source.sql` → `backend/sql/migrate-2026-08-09-alert-category-motype.sql` → `backend/sql/slices/studio_span.sql` → `backend/sql/slices/alerts.sql` → `backend/sql/openops_v1_core.sql` → 发布/重启新后端；迁移均可幂等重跑，任一步失败均停止发布
  - 三种库形态：全新库=只跑 core.sql + slices/*；我方旧库=全序列；内网库（同事已有 entry_source）=07-30 的 entry_source 段自动跳过、task_origin 段生效，08-10 rename 自动空转
- [ ] 全新库执行 `backend/sql/openops_v1_core.sql` **+ `backend/sql/slices/*.sql`**（幂等；合计 27 表）——切片 DDL 不在 core.sql 里，漏跑会让 Agent Studio 静默失效
- [ ] Agent Studio（管理员回溯）：`sre_agent_studio_span` 存 LLM/工具**原文**（仅 /admin/studio/* 可读，30 天硬删）；不需要此能力的环境设 `OPENOPS_AGENT_STUDIO_ENABLED=false`
- [ ] GaussDB 环境确认：无 `role` 裸列名（已改 `user_role`）、无 ON CONFLICT 偏索引 target
- [ ] 首次建库：启动一次后端触发 seed（模板/标注/沙箱配置/模型资产）；已播种库 seed 自动跳过

## 2. 敏感信息与安全

- [ ] `scripts/release_check.sh` ② 敏感扫描零命中（Key/Cookie/内网主机名不入库不入码）
- [ ] `OPENOPS_ENCRYPTION_KEY` 已配且已备份（Fernet，丢失=全部 Secret 报废）
- [ ] 平台模型 Key 只进 env（SEC-001）；审计事件抽查无 token/cookie 明文
- [ ] 抽查 `/state`、`/events`、AG-UI CUSTOM 与页面 DOM：无完整参数/响应、stdout/stderr、
  `Authorization`/Basic/Bearer/Cookie/password/token；仅出现后端白名单摘要
- [ ] SSRF/egress：公网部署置 `OPENOPS_LLM_EGRESS_BLOCK_PRIVATE=1`（内网默认放行，见 S3）

## 3. IAM（OPENOPS_IAM_ENABLED=true 时）

- [ ] `OPENOPS_IAM_ACCESS_TOKEN_URL` / `OPENOPS_IAM_USERINFO_URL` 是固定 HTTPS 地址，不含 userinfo/query/fragment、`{host}` 或其他模板，并已连通
- [ ] **承载 `/api` 的每一跳**透传 `X-Forwarded-For`，且必须用 `$proxy_add_x_forwarded_for`（不能用 `$remote_addr`，会丢首跳）。单文根拓扑该链为「公司网关 → 本机 nginx → 本机后端」两跳。后端取首跳作用户真实 IP 供 IAM 绑 IP 校验；缺则 /me 持续 401 登录横跳
- [ ] 上条须**实测**而非目视：`curl -s -H "X-Forwarded-For: 203.0.113.7" https://xxxx.com/openops/api/openops/v1/me`，确认后端解析出的是 203.0.113.7 而不是网关 IP（按路径打勾容易空转——`/openback` 收敛后已不承载鉴权流量）
- [ ] `OPENOPS_IAM_LOGIN_KEY_FIELD` / `OPENOPS_IAM_DISPLAY_NAME_FIELD` 与 IAM userinfo 真实字段对齐（支持点分嵌套）
- [ ] 可选：`OPENOPS_IAM_LOGIN_URL`（401 引导跳转）/ `OPENOPS_IAM_SIGNOUT_URL`（登出）
- [ ] 白名单：管理员账号先入库（`sre_user_whitelist`），再由管理台开通其他用户
- [ ] mock 头在 IAM 开启后不再生效（`X-OpenOps-Mock-*` 只在 disabled 分支读取）
- [ ] `OPENOPS_OMODEL_TENANT_ID` 与 `OPENOPS_APPTREE_ENTERPRISE_ID` 显式配置为当前企业且一致
- [ ] 所有静态 `OPENOPS_*_COOKIE` 未设置；oModel/console 系使用当前 IAM 用户 Cookie 透传
- [ ] `OPENOPS_OMODEL_BASE_URL` 为无 userinfo/query/fragment 的固定 HTTPS 域名且不含 `{host}`
- [ ] `OPENOPS_HTTP_DEBUG=0`、`OPENOPS_TLS_INSECURE=0`、`OPENOPS_HTTP_TRUST_ENV=0`
- [ ] `OPENOPS_APPTREE_USER_ID` / `OPENOPS_SCOPE_OVERRIDE_APPIDS` 等联调覆盖项未设置
- [ ] 初始化页实建 workspace：POST 成功、owner 正确、workspace tenant 为当前企业、跨租 scopes 保留各自 tenant

## 4. 禁用入口核对（V1 口径）

- [ ] 侧栏 知识/自动化/本体 置灰；RCA「写入知识库」禁用；OModel 设置占位禁用
- [ ] stdio/command MCP 无入口（仅 HTTP）；对象图 @提及无入口

## 5. 测试

- [ ] `python3 -m pytest tests/ -q`（mock 面）与 `.venv/bin/python -m pytest tests/ -q`（agentscope 面）全绿
- [ ] 前端目录执行 `npm run test:activity`（统一 reducer 11 例）与 `npm run build`；再执行
  `npm run e2e`（Playwright smoke 16 例，mock 模式免后端）
- [ ] 内网真链验收：真 GLM 派 recover 子 Agent → 审批卡（任务号带 `.recover-<delegation>`）→
  批准 →「已汇报」；同时检查 AG-UI CUSTOM + 备用 SSE 不重复、两轮/同角色独立轨迹、业务/技术视图、
  刷新恢复、显示更早、窄屏覆盖层和安全 DOM

## 6. 审计串联

- [ ] 管理台 → 审计回放：任一事件点 trace 徽标能串出全链；白名单增删有 `whitelist.granted/revoked`
- [ ] 任一 run 的 `GET /audit/runs/{id}` 事件含 task.started→scope.resolved→…→task.completed 完整链

## 7. 部署形态（上线前置，34 号 §三·④）

- [ ] `bash deploy/build-artifacts.sh --backend-only` 生成版本化后端包和 `.sha256`；包内 `BUILD_INFO` 与文件名一致
- [ ] 后端解压到 `/opt/openops/releases/<BUILD_ID>`，原子切换 `/opt/openops/current`；禁止逐文件覆盖旧目录
- [ ] `/opt/openops/releases` 与发布根目录归 `root:openops` 且运行用户不可写；仅 release 内 `.venv` 保留必要写权限
- [ ] 重启后进程 cwd 位于同一 release，启动日志 `build=<BUILD_ID>` 与 `BUILD_INFO` 一致
- [ ] 反代调优已烘焙进 openops-frontend 镜像（/api 与 /api/copilotkit 关缓冲+3600s 超时）——升级镜像后 `docker exec openops-frontend nginx -T` 抽查生效即可
- [ ] 审计 30 天保留清理任务（DBA 排期）
- [ ] 沙箱 docker 档就绪（Linux 阶段，详见 docs/sandbox-docker-runbook.md）：
  - [ ] 沙箱镜像离线预构建 + `docker load`（`bash deploy/sandbox/build-sandbox-image.sh` → 传 `openops-sandbox-image.tar.gz` → 后端机 `docker load`）
  - [ ] 运行用户可读写 `/var/run/docker.sock`（systemd 取消注释 `SupplementaryGroups=docker` 或 `usermod -aG docker openops`）
  - [ ] `OPENOPS_SANDBOX=docker` 且管理台 `container_image` 已指向 `openops-sandbox:<ver>`（改值填原因走审计）
