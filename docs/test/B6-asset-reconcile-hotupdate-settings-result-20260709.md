---
title: B6 资产对账 + 配置热更新 + 设置页写闭环测试报告
date: 2026-07-09
tester: Claude (Opus 4.8)
branch: feat/workbench-frontend
commit: deca093
target_commit: c7f4118
---

# B6 资产对账 + 配置热更新 + 设置页写闭环测试报告

## 结论

B6 主体验证通过。`feat/workbench-frontend@deca093` 中的 B6 提交 `c7f4118 feat(assets): B6 资产对账 + 配置热更新 + 设置页写闭环` 已具备：Skill Hub / MCP Registry 对账（只拉 `source=openops`、checksum 变化补版本、幂等）、MCP `tools/list` → `schema_hash` 变化后标注不继承并运行时 fail-closed、配置版本不可变链派生（save/bind/unbind 结转绑定、历史绑定行不原地改写）、运行中工具边界标注热更新（`openops.runtime_plan.updated`）、被引用资产删除 `ASSET_IN_USE`、用户 MCP endpoint 展示脱敏、对账失败收口不停服，以及前端设置页三 tab 写闭环。

本轮**没有发现阻断 B6 演示的 P0/P1 问题**。核心安全不变量（未标注 / blocked 平台工具不被执行）在 **mock 与 agentscope 两条 runtime 下均成立**。记录 1 个 **P2**（agentscope runtime 下工具 fail-closed 为「软处理」，引发行为分叉、审计可观测性弱、审批后被 block 的结论误导，且两个 B6 热更新用例是 mock 专属）与 6 个 P3 / 观察项。

## 测试对象与环境

| 项目 | 结果 |
|---|---|
| 测试位置 | 原工程目录（未另开 worktree），分支 `feat/workbench-frontend` |
| 当前 HEAD | `deca093 fix: B5 冒烟反馈两项`（含 B6 `c7f4118` + B5 修复；B5 两项非本轮对象） |
| 被测提交 | `c7f4118`（B6 资产对账 + 配置热更新 + 设置页写闭环） |
| 后端 Python | 3.11.7（`backend/.venv`） |
| AgentScope | 2.0.3（venv 内可用，用于 runtime 分叉验证） |
| PostgreSQL | 复用本机 `openops-v1-pg`，`localhost:5432`，healthy |
| 后端执行方式 | pytest + FastAPI `TestClient`（进程内，走完整 lifespan / PG） |
| 运行态 | `OPENOPS_RUNTIME=mock`（pytest 默认）为主；`agentscope` 用于分叉探针 |
| oModel | `OPENOPS_OMODEL=mock` |

未写入、未打印、未保存任何真实 API Key、Authorization、Bearer token、Cookie、完整 MCP endpoint 或 prompt/messages。

## 基础回归

| 检查项 | 结果 | 备注 |
|---|---:|---|
| 后端单测 | 通过 | `49 passed, 1 warning in 7.89s`（较 B5 的 43 增 6，即 `test_assets.py`） |
| `backend/tests/test_assets.py`（mock） | 通过 | 6/6：绑定结转+历史行 SQL 断言、save 保绑定、unbind 派生+ASSET_IN_USE、reconcile 幂等只拉 openops、schema 变化 TOOL_NOT_ANNOTATED、mid-run 热更新 |
| 前端 `npx tsc -b` | 通过 | exit 0 |
| 前端 `npm run build` | 通过 | 主 chunk `index-MwHRpq2Z.js 537.47 kB`（>500 KB 警告，见 B6-FE-001） |
| `npm ci` 依赖漏洞 | 观察 | 2 vulnerabilities（1 moderate、1 high），见 B6-DEP-001 |
| DDL 静态检查 | 通过 | `sql/openops_v1_core.sql` 无 `FOREIGN KEY`/`REFERENCES`/`CREATE TRIGGER`/`CREATE FUNCTION` |
| 分层静态检查 | 通过 | routers 无 `from infra`；runtime 无 `from app`；routers 无 `service._` 私有访问 |
| 禁用项静态检查 | 通过 | 无 `riskLevel`、`/agui/run`、`自动审批低风险` 入口 |

## B6 验收标准逐项

| 验收（doc 33 / [[28.7]]） | 结果 | 证据 |
|---|---:|---|
| 历史 binding 不原地改写（不可变链） | 通过 | `test_asset_bind_carries_forward_and_history_immutable` / `test_asset_save_config_keeps_bindings` / `test_asset_unbind_derives_new_version`；补充冒烟 D1–D3：bind×2 + main append + unbind 后 = 4 个不可变配置版本、历史绑定行 `deleted_at is null`（0 原地软删） |
| 对账只拉 source=openops + 幂等 + checksum→版本 | 通过 | `test_asset_reconcile_source_openops_and_versions`；冒烟 A1/A2：#1 `skill_versions_added=1 & tools_unchanged=2`，#2 `skill_versions_added=0` |
| schema_hash 变化 → 标注不继承 → fail-closed | 通过 | `test_asset_schema_change_annotation_not_inherited`（mock → `TOOL_NOT_ANNOTATED`）；agentscope 下工具不执行（见 B6-RT-001） |
| 运行中 RuntimePlan 热更新（边界重取标注） | 通过 | `test_asset_hot_update_mid_run`（mock：`runtime_plan.updated` + `TOOL_BLOCKED`，未真调 MCP）；agentscope 探针同样发 `runtime_plan.updated` + `tool.blocked` 且未执行 |
| 资产被 active 配置引用删除 → `ASSET_IN_USE` | 通过 | `test_asset_unbind_derives_new_version`（409 `ASSET_IN_USE` → 解绑后放行删除） |
| 对账失败 → `asset.reconcile_failed`，不整体停服 | 通过（补测） | 冒烟 B1–B3：注入 Skill Hub 异常 → 返回 `{failed:True}`、审计 `asset.reconcile_failed(RECONCILE_FAILED)`、后续 API 仍 200 |
| MCP endpoint 展示脱敏（30.5） | 通过（补测） | 冒烟 C1/C2：`GET /assets/mcps` 只返回 `endpoint_config_redacted`（`https://inte…` 截断），无 `endpoint_config_json`，完整 endpoint 与 token 未出现在响应 |
| 用户资产 disabled/deleted 运行时 fail-closed | 部分 | 平台 MCP tool 标注路径已验证；**用户 Skill 运行时执行属 B8 沙箱，未接入** |

补充冒烟（`b6_smoke.py`，mock runtime）合计 **11/11 通过**，并对全部 `audit_event.payload_redacted_json` 做敏感串扫描：`Authorization`/`Bearer `/`Cookie`/注入的 endpoint 与 token 均未命中（E1 通过）。

## Runtime 分叉验证（mock vs agentscope）

B6 的两个热更新用例默认在 mock runtime 下运行并全绿。为验证生产/演示所用的 **agentscope 2.0.3** runtime，逐场景探针（`OPENOPS_RUNTIME=agentscope OPENOPS_OMODEL=mock`）：

| 场景 | recover 是否执行 | 阻断审计事件 | task 终态（agentscope / mock） |
|---|---:|---|---|
| 审批通过后被 block（mid-run 热更新） | 否 ✓ | `runtime_plan.updated` + `tool.blocked/TOOL_BLOCKED` ✓ | `completed` / `failed` |
| 未标注（schema_hash 变化） | 否 ✓ | **无**（工具在 toolkit 构建期被裁剪，未进 Gateway） | `completed` / `failed` |

**安全不变量在两条 runtime 均成立**：被 block / 未标注的 `recover_execute` 都未被执行（无 `external_request_id`）。差异来自 agentscope 工具封装对 `ToolBlocked` 的**软处理**（`agentscope_runtime.py:166-167` 捕获后作为 `ToolResponse` 回给模型），而 mock 编排器让其硬失败。由此引出 B6-RT-001。

## B1–B5 兼容结果

| 兼容项 | 结果 |
|---|---:|
| B1 AgentScope runtime | 通过（agentscope 探针主链路正常推进） |
| B2 Model Gateway 事件 | 通过（`model.call.*` 正常） |
| B3 Scope Service | 通过（`scope.resolved` 正常） |
| B4 Tool Gateway | 通过；B6 在其上叠加「每边界取最新标注」热读，未回退既有 allowed/blocked/ASK 判定 |
| B5 AG-UI / SSE 主链路 | 未在本轮重复验证（B6 未触及 AG-UI/SSE 事件契约） |
| 全量后端单测 | 49 passed，无回归 |

## 发现的问题与建议

### B6-RT-001 P2：agentscope runtime 下工具 fail-closed 为「软处理」，引发行为分叉 / 结论误导 / 审计缺口

现象（agentscope 2.0.3 + stub model）：

1. **两 B6 热更新用例是 mock 专属**：`test_asset_hot_update_mid_run`、`test_asset_schema_change_annotation_not_inherited` 断言 `active_task.status == "failed"`。在 agentscope 下这两用例**失败**，因为 task 终态为 `completed`（工具被拦截后模型被告知并继续收口）。即生产/演示所用 runtime 的 fail-closed 路径**未被自动化断言覆盖**。
2. **审批后被 block 的结论误导**：mid-run 将 `recover_execute` 拉黑并批准后，`recover_execute` 确实未执行（✓），但 `task.completed` 仍携带 stub 结论：

   ```
   已确认根因 H1（Redis 连接泄漏）：重启 svc-a 后连接回落、P99 恢复 210ms，事件闭环。
   ```

   与「恢复动作未执行」事实矛盾。根因：`recovery_denied` 仅在 ASK 被拒绝/超时/取消时置位；审批**通过后**才发生的工具 block 不置位，runtime 遂采纳模型最终文本作为结论（`agentscope_runtime.py:302-315`）。
3. **未标注工具无 tool.blocked 审计**：schema 变化后 `recover_execute` 未标注，agentscope 在 `_build_toolkit` 期按 `status==allowed` 裁剪该工具（`agentscope_runtime.py:174-178`），`tool_gateway.invoke` 从不触达 → 不产生 `tool.blocked` / `TOOL_NOT_ANNOTATED` 审计事件（mock 会产生）。审计缺少「为何未恢复」的显式标记。

影响与定级：核心安全性（不执行未授权工具）成立，故非 P0/P1；但（a）测试不覆盖生产 runtime、（b）SRE 场景下「恢复未执行却宣称已闭环」的运营结论误导、（c）审计可观测性弱，合并记为 P2。缓解：结论文本部分是 stub 产物，真实 GLM 可能据 `ToolResponse` 的拦截提示自我纠偏——但 runtime 未**强制**该不变量。

建议：
- 让 agentscope 工具封装在 `ToolBlocked` 时也置 `recovery_denied`（或据「本 task 出现过 tool.blocked」抑制成功结论），避免审批后被 block 仍宣称恢复成功。
- 为未标注/裁剪工具补一条 `tool.blocked`（或 `tool.filtered`）审计，使两 runtime 审计对齐。
- B6 用例改为断言 runtime 无关的安全不变量（未执行 + 阻断事件），或对 agentscope 增加对应期望，覆盖生产 runtime。

### B6-TEST-001 P3：对账失败与 endpoint 脱敏无自动化覆盖

`asset.reconcile_failed` 收口与 `GET /assets/mcps` 的 endpoint 脱敏均**功能正确**（本轮补充冒烟 B1–B3 / C1–C2 通过），但 `test_assets.py` 未覆盖。建议补两条用例固化回归护栏。

### B6-DOC-001 P3：`tool_gateway` 模块 docstring 与 B6 行为矛盾

`runtime/tool_gateway.py` 顶部 docstring 仍写「runtime 只消费不回读 DB（22 号分层：runtime 不 import app）」，而 B6 已引入 `get_runtime_annotation` 在**每次工具边界回读 DB**（28.7 热更新）。分层铁律（runtime 不 import app）仍成立，但「不回读 DB」一句已过时，建议更新为「每边界读最新标注、读失败回退启动快照」。

### B6-SCOPE-001 P3：28.7「使用时派生」（模板升级自动派生）未接线

`derive_config_version` 仅由用户动作（`save_config` / `bind` / `unbind`）调用；`start_task` 及工具/模型边界**无** `derive_if_template_changed` / `refresh_if_needed`，代码库无 `config.version.derived` / `config.changed_notice` 事件。B6 doc-33 验收（标注热读 + `runtime_plan.updated`）**已达成**，但 28.7 描述的「平台模板升级 → 已有实例在边界自动派生新配置版本」尚未实现。判断属后续块，建议在 ROADMAP 明确其归属，避免与 B6「配置热更新」表述混淆。

### B6-FE-001 P3：前端主 chunk 超过 500 KB

`npm run build` 通过，主 chunk `index-MwHRpq2Z.js 537.47 kB`（承 B5-FE-001，B6 设置页代码后略增）。上线前建议按管理台/工作台/AG-UI 依赖做路由级拆包或 `manualChunks`。

### B6-DEP-001 P3：前端依赖 2 个 npm audit 漏洞

`npm ci` 显示 1 moderate、1 high（与 B5-DEP-001 一致，未见新增）。上线前建议补一轮依赖审计。

### B6-PERF-001 观察：每工具边界一次 DB 读

`tool_gateway._effective_annotation` 在每次工具调用边界 `get_runtime_annotation`（一次 PG 查询）。V1 单机可接受，且读取失败已回退启动快照（ASSET-006 缓存兜底）；高频工具循环或多实例下建议评估短 TTL 内存缓存，减少边界 DB 压力。

## 未覆盖 / 未执行

- **浏览器 E2E 未执行**：与 B5 同口径，本轮前端以 `npm run build`、`tsc`、`SettingsPage.tsx` 代码路径核对与 API facade（real/mock 双实现）为验证依据；版本链不可变性已在 API 层独立验证（冒烟 D1–D3）。设置页三 tab（Skill·MCP 库 / 角色提示词 / 模型配置）、上传 Skill / 注册 HTTP MCP 弹窗、库表绑定+删除、已绑表解绑、刷新对账、main append 保存的调用链均已在源码核对到位。
- **用户 Skill 运行时 disabled/deleted fail-closed**：依赖 B8 沙箱执行面，未接入，不在本轮范围。

## 总体建议

B6 可作为「资产对账 + 配置热更新 + 设置页写闭环」的 smoke 通过版本合入主线；核心安全不变量（未授权工具不执行）在 mock 与 agentscope 均成立。推进 B7 前建议优先处理 **B6-RT-001**：

1. agentscope 工具封装在 `ToolBlocked` / 工具裁剪时补置 `recovery_denied` 与阻断审计，消除「恢复未执行却宣称闭环」的结论误导并对齐两 runtime 审计。
2. 将 B6 两个热更新用例改为断言 runtime 无关的安全不变量，覆盖生产 runtime（agentscope）。
3. 顺带补 B6-TEST-001（对账失败 / endpoint 脱敏用例）与 B6-DOC-001（docstring）两项低成本改善。
