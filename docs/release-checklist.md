# 发布检查清单（B9，2026-07-13）

自动项跑 `bash scripts/release_check.sh`；下表为完整口径（含手动项）。

## 1. 数据库

- [ ] 目标库执行/重放 `backend/sql/openops_v1_core.sql`（幂等；26 表；增量列在尾部 ALTER 段）
- [ ] GaussDB 环境确认：无 `role` 裸列名（已改 `user_role`）、无 ON CONFLICT 偏索引 target
- [ ] 首次建库：启动一次后端触发 seed（模板/标注/沙箱配置/模型资产）；已播种库 seed 自动跳过

## 2. 敏感信息与安全

- [ ] `scripts/release_check.sh` ② 敏感扫描零命中（Key/Cookie/内网主机名不入库不入码）
- [ ] `OPENOPS_ENCRYPTION_KEY` 已配且已备份（Fernet，丢失=全部 Secret 报废）
- [ ] 平台模型 Key 只进 env（SEC-001）；审计事件抽查无 token/cookie 明文
- [ ] SSRF/egress：公网部署置 `OPENOPS_LLM_EGRESS_BLOCK_PRIVATE=1`（内网默认放行，见 S3）

## 3. IAM（OPENOPS_IAM_ENABLED=true 时）

- [ ] `OPENOPS_IAM_ACCESS_TOKEN_URL` / `OPENOPS_IAM_USERINFO_URL` 已配并连通（curl 带真 cookie 验 code=201）
- [ ] `OPENOPS_IAM_LOGIN_KEY_FIELD` / `OPENOPS_IAM_DISPLAY_NAME_FIELD` 与 IAM userinfo 真实字段对齐（支持点分嵌套）
- [ ] 可选：`OPENOPS_IAM_LOGIN_URL`（401 引导跳转）/ `OPENOPS_IAM_SIGNOUT_URL`（登出）
- [ ] 白名单：管理员账号先入库（`sre_user_whitelist`），再由管理台开通其他用户
- [ ] mock 头在 IAM 开启后不再生效（`X-OpenOps-Mock-*` 只在 disabled 分支读取）

## 4. 禁用入口核对（V1 口径）

- [ ] 侧栏 知识/自动化/本体 置灰；RCA「写入知识库」禁用；OModel 设置占位禁用
- [ ] stdio/command MCP 无入口（仅 HTTP）；对象图 @提及无入口

## 5. 测试

- [ ] `python3 -m pytest tests/ -q`（mock 面）与 `.venv/bin/python -m pytest tests/ -q`（agentscope 面）全绿
- [ ] `npx tsc --noEmit && npm run build` 过；`npm run e2e`（Playwright smoke 6 例，mock 模式免后端）
- [ ] 内网真链验收：真 GLM 派 recover 子 Agent → 审批卡（任务号带 `.recover`）→ 批准 → teal「已汇报」徽标

## 6. 审计串联

- [ ] 管理台 → 审计回放：任一事件点 trace 徽标能串出全链；白名单增删有 `whitelist.granted/revoked`
- [ ] 任一 run 的 `GET /audit/runs/{id}` 事件含 task.started→scope.resolved→…→task.completed 完整链

## 7. 部署形态（上线前置，34 号 §三·④）

- [ ] nginx 对 `/agui`/SSE 路径关代理缓冲 + 长连接超时调大
- [ ] 审计 30 天保留清理任务（DBA 排期）
- [ ] 沙箱镜像离线预构建 + `docker load`（Linux 阶段）
