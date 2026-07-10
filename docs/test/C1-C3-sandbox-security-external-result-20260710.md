---
title: C1–C3 测试报告（Skill 接入 agent 循环+真 ZIP / Secret Fernet+LLM egress SSRF+用户 LLM 回退 / 外部接真开关）
date: 2026-07-10
tester: Claude (Opus 4.8)
branch: feat/workbench-frontend
commit: a8bf7be
target_commit: abd310f(C1) + 9c8d2e6(C2) + a8bf7be(C3)
---

# C1–C3 测试报告

## 结论

C1/C2/C3 主体验证通过，**后端 102 passed / 1 skipped（docker 回归护栏，带开关跑绿）**。C2 的两项硬安全承诺**真兑现并经实证**：Secret 用 **Fernet 真加密**（AES128-CBC+HMAC，落库为 `gAAAAA…` 密文、无明文、篡改抛错、支持 key 轮换），用户 LLM **egress SSRF 防护**扛住了全部经典绕过（十进制/十六进制/IPv6/IPv4-mapped-IPv6/metadata），并在**创建 + 每次调用边界双卡**。C1 把 `run_skill`/容器 Bash 接进 agent 对话循环（双 runtime，未装配 Skill fail-closed）。C3 为三个外部依赖提供 real 变体 + env 开关、未配端点 fail-loud、mock 默认保测试绿。

本轮**无 P0/P1**。记录 **1 个 P2**（C1 真 Skill Hub 的 `X-Checksum-SHA256` 校验算法与 doc 29.3 契约不符 → 联真后每次下载 fail-closed，已实证）与 3 个 P3 / 观察项（SSRF 对 RFC1918 默认放行的设计残留、check 与连接间的 TOCTOU、用户 LLM resolve 边界的残留回退）。

顺带复核：上一轮 B8 的 **B8-SBX-001（P1 真 Docker 写盘）与 B8-SEC-001（P2 deny 串联绕过）已修复并验证**——`test_docker_real_run_skill_write_exec_isolation`（带 `OPENOPS_SANDBOX_DOCKER_TEST=1`）实机跑绿、`test_bash_006_deny_not_bypassed_by_chaining` 通过。

## 测试对象与环境

| 项目 | 结果 |
|---|---|
| 当前 HEAD | `a8bf7be C3 外部接真`（含 C1 `abd310f` / C2 `9c8d2e6` / C3 `a8bf7be`） |
| 后端 Python / AgentScope | 3.11.7 / 2.0.3 |
| Docker / aiodocker | 25.0.3 / 0.27.0（用于 B8-SBX-001 回归护栏实机复核） |
| PostgreSQL | 本机 `openops-v1-pg`，healthy |
| cryptography（Fernet） | venv 内可用（C2 依赖） |
| 运行态 | `OPENOPS_SANDBOX=fake`（默认）+ `docker`（回归护栏）；`OPENOPS_*=mock`（默认）+ `real`（接真开关探针） |
| 前端 | C1–C3 **纯后端**（三提交 0 前端文件），本轮不涉前端构建 |

实证探针向宿主注入 `sk-host-secret-…` 并验证不落容器/不落库/不进事件。

## 基础回归

| 检查项 | 结果 | 备注 |
|---|---:|---|
| 后端单测 | 通过 | `102 passed, 1 skipped`（B8 为 87；C1–C3 +15）；1 skipped = `test_docker_real_run_skill_write_exec_isolation`（`OPENOPS_SANDBOX_DOCKER_TEST=1` 时实机跑绿） |
| `tests/test_security.py` | 通过 | SEC-008 Fernet 真加密+篡改抛错 / SEC-009 egress 5 例矩阵 / SEC-010 无 secret+SSRF 拒绝 / SEC-011 用户 LLM 不静默回退 / EXT-007 real 开关 fail-loud / SEC-001·002 脱敏 |
| `tests/test_sandbox.py`（C1 增量） | 通过 | SKILL-009 agent 循环驱动绑定 Skill `[mock]+[agentscope]` / SKILL-010 未绑定 fail-closed / BASH-007 agent 循环驱动容器命令 `[mock]+[agentscope]` |
| B8-SBX-001 回归护栏（实机 docker） | 通过 | `OPENOPS_SANDBOX_DOCKER_TEST=1`：真 Docker run_skill 写盘+执行+output.json+跨用户隔离全过 |
| 分层静态检查 | 通过 | runtime（`sandbox_skill`/`sandbox_bash`）无 `from app`；新增 `infra/egress`·`infra/crypto`·外部 client 属 infra 层 |
| Secret 落库脱敏 | 通过（实证） | `user_secret.ciphertext` = Fernet token（`gAAAAAB…`）、无明文、`key_version` 记录 |

## C2 安全硬缺口验证（本轮重点）

### Secret Fernet 真加密（13/SEC-001）

| 项 | 结果 | 证据 |
|---|---:|---|
| 真 Fernet（非 XOR 混淆） | 通过 | 密文 `gAAAAA` 前缀、`plain not in cipher`；`crypto.encrypt/decrypt` 往返一致 |
| 篡改检测（HMAC，非静默产垃圾） | 通过 | 改末 4 字节 → `decrypt` 抛 `ValueError`（`InvalidToken`） |
| key 轮换（MultiFernet） | 通过 | 新 primary + 旧 key(OLD) 可解旧密文；**移除旧 key 后旧密文解不出**（证明真依赖 key，非派生常量） |
| 落库为密文、明文不在库 | 通过（实证） | 经 API 建 Secret 后查 PG：`ciphertext` 为 Fernet token、无明文 |
| dev 回退仍真 Fernet | 通过 | 未配 `OPENOPS_ENCRYPTION_KEY` 时从 `OPENOPS_SECRET_KEY` 派生确定性 Fernet key（`key_version=dev`，warn 提示生产须显式配置） |

### LLM egress SSRF（13/28.4）—— 绕过向量矩阵（实证）

| base_url | 判定 | 说明 |
|---|---:|---|
| `http://127.0.0.1` / `[::1]` | block | 环回 |
| `http://169.254.169.254` | block | 云 metadata（链路本地） |
| `http://2130706433` / `http://0x7f000001` | block | 十进制/十六进制编码的 127.0.0.1（getaddrinfo 解析后逐 IP 比对拦下） |
| `http://[::ffff:127.0.0.1]` / `[::ffff:169.254.169.254]` | block | IPv4-mapped IPv6（经 `is_reserved` 拦下） |
| `http://172.17.0.1` | block | Docker bridge（默认 deny 网段） |
| `http://localhost` | block | deny host 名单 |
| `file:///etc/passwd` | block | 非 http/https |
| `https://api.openai.com` / `https://1.1.1.1` | ALLOW | 公网正常放行 |
| `http://10.0.0.5` / `http://192.168.1.1` | ALLOW | RFC1918 默认放行（见 C2-OBS-001） |

校验点：**创建 LLM 配置**（`secret_model_gateway`）与**每次调用边界**（`model_gateway.resolve`）双卡；解析所有 IP 逐一比对（防 rebinding-at-rest）。

### 用户 LLM 无静默回退（28.4）

| 项 | 结果 | 证据 |
|---|---:|---|
| 选中不存在/非本人/未激活 user LLM → fail-closed | 通过 | `select_model` 校验归属+active → `SECRET_REQUIRED`（`test_sec_011`） |
| 创建 LLM 无 secret → `SECRET_REQUIRED` | 通过 | 修 `user_llm_config.secret_ref_id NOT NULL` 不一致（`test_sec_010`） |
| user LLM Secret 仅构建边界瞬时解密 | 通过（代码核对） | `resolve` 只带 `user_secret_ref_id` 不解密；runtime `_build_model` 边界 decrypt（SEC-001） |

## C1 Skill 接入 agent 循环 + 真 ZIP 验证

| 验收 | 结果 | 证据 |
|---|---:|---|
| Agent 调 `run_platform_skill` 在容器内执行（双 runtime） | 通过 | `test_skill_009[mock]+[agentscope]`；`sandbox_skill.run_bound_skill` 发 skill.call.started/succeeded |
| 未装配 Skill fail-closed（不执行、不宣称成功） | 通过 | `test_skill_010`：`available_skills` 外 → skill.call.blocked + `st.tool_blocked`（B6-RT-001 一致） |
| 容器内 Bash 作 agent 工具（双 runtime） | 通过 | `test_bash_007[mock]+[agentscope]` |
| 真 ZIP 投递 + 传输完整性校验 | **mock 通过 / 真 Skill Hub 契约不符（C1-CHK-001）** | mock：合成可执行包 run_skill 端到端；real：见下 P2 |

## C3 外部接真开关验证

| 依赖 | real 变体 | fail-loud（无 BASE_URL） |
|---|---|---:|
| 平台 HTTP MCP（`OPENOPS_MCP`） | `POST {base}/tools/{name}:call`，Tool Gateway header 透传（28.2） | 通过（`test_ext_007`） |
| MCP Registry（`OPENOPS_MCPREGISTRY`） | `POST {base}/mcps/proxy` tools/list，OpenOps 侧自算 schema_hash | 通过 |
| Skill Hub list（`OPENOPS_SKILLHUB`） | `GET {base}/skills?source=openops` | 通过 |
| Skill Hub download（C1 已接） | `GET {base}/skills/{id}/versions/{v}/download` + 校验 | 契约不符（C1-CHK-001） |

mock 默认全绿、real 未配端点 raise（不静默降级）。`backend/docs/EXTERNAL-INTEGRATION.md` 收录开关总表与联调顺序。

## 发现的问题与建议

### C1-CHK-001 P2：Skill 包 `X-Checksum-SHA256` 校验算法与 doc 29.3 契约不符 → 联真后每次下载 fail-closed

**现象（实证）**：`download_skill_package` 的 real 分支取响应头 `X-Checksum-SHA256`，与 `package_checksum(files)` 比对。但两者算法不同：

- **doc 29.3**（`29.3 …SkillHub与MCPRegistry接口.md` 第 363 行）明确定义：`X-Checksum-SHA256` 值为 **「ZIP 文件的 SHA-256」**（`sha256(zip_bytes)`）。
- **C1 实现**：`package_checksum(files) = sha256(按文件名排序的 name\0content 拼接)`（解压后内容摘要），且 `skill_package.py` docstring 自称「与 Skill Hub `X-Checksum-SHA256`（29.3 真包）同一算法」——**该表述与 29.3 矛盾**。

实证（构造 spec-compliant ZIP 响应，头 = `sha256(zip_bytes)`）：

```
29.3 X-Checksum-SHA256 (sha256 ZIP bytes) = 66dd3257…
C1 package_checksum(files)                = 587eebf5…   两者相等? False
>>> RuntimeError：Skill 包传输校验失败：X-Checksum-SHA256 与内容不符
```

即联真 Skill Hub（`OPENOPS_SKILLHUB=real`）后**每个 Skill 下载都会 fail-closed** → run_skill 走 skill.call.failed → Skill 执行面在真环境不可用。mock 之所以绿，是因其自造头值 = `package_checksum(_MOCK_FILES)`（自洽但非 29.3 口径）。

定级：behind `OPENOPS_SKILLHUB=real`（mock 默认、单测绿），无 live 影响，非 P0/P1；但 C3「代码就绪待联调」在此项不成立（首次联调即断），记 **P2**。

建议（择一，且订正 docstring）：
- 传输完整性按 29.3 校验 **ZIP 字节**：`hashlib.sha256(r.content).hexdigest() == header`（`r.content` 手边即有）；沙箱落盘完整性仍可用 `package_checksum(files)` 作**第二道**校验（两个 checksum 语义分开）。
- 或推动 doc 29.3 + Skill Hub 改用内容摘要口径（注意 ZIP 字节含 mtime/压缩级不可复现，内容摘要其实更稳）——但需两侧一致落地，不能只在客户端单方声称「同一算法」。

### C2-OBS-001 P3：SSRF 对 RFC1918 内网默认放行（设计残留，可配置收紧）

`check_llm_egress` 默认放行 RFC1918（`10/172.16/192.168`，仅 `172.17.0.0/16` docker bridge 在默认 deny）。设计如此（SRE 平台内网 GLM 网关常在私网），但也意味着恶意用户可把 user LLM base_url 指向内网 HTTP 服务（如 `http://10.x:6379`）由平台代发请求，构成受限 SSRF。最高价值目标（云 metadata 169.254）已拦。建议部署置 `OPENOPS_LLM_EGRESS_BLOCK_PRIVATE=1` 或经 `OPENOPS_LLM_EGRESS_DENY` 精确列出内网基础设施网段；文档已提示，建议在部署清单中列为必配项。

### C2-OBS-002 P3：egress check 与实际连接间的 TOCTOU

`check_llm_egress` 用 `socket.getaddrinfo` 解析并逐 IP 比对，但真正发起请求的 HTTP 客户端（构建模型时）会**独立再解析**域名。掌控 DNS 的攻击者可在 check 时返回安全 IP、连接时返回 `169.254.169.254`（rebinding-at-connect）。缓解：调用边界已复校（缩小窗口）、多数 user LLM 指向稳定主机。彻底消除需将校验通过的 IP **pin 给连接**（或用固定解析器）。低频高门槛，记 P3。

### C2-OBS-003 P3：用户 LLM 在 resolve 边界的残留回退

`select_model` 对无效 user LLM fail-closed（`SECRET_REQUIRED`），但 `resolve_runtime_model` 在「已选中的 user LLM 于选后被禁用/删除」时**回退平台默认模型**（非 fail-closed）。非凭证泄漏（平台模型已授权），但与「不再静默回退平台模型」的表述存在边界不一致：用户以为在用自有 LLM，实际可能落到平台模型且无显式提示。建议 resolve 命中「选中值为 UUID 但 user LLM 不可用」时发一条可见提示事件，或与 select 口径统一。

## 未覆盖 / 未执行

- **联真外部依赖**：real 变体（MCP 网关 / MCP Registry / Skill Hub / 真 GLM Key）代码就绪但未接真环境（无端点，本轮以 fail-loud + mock 默认为验证）；C1-CHK-001 即联真才暴露的契约问题。
- **真 GLM live E2E**：无 Key，`_build_model` 回退 stub（B2 runbook 口径）。
- **浏览器 E2E**：C1–C3 纯后端，无前端面。

## 总体建议

C1/C2/C3 可作为「Skill/Bash 接入 agent 循环 + Secret 真加密 + LLM egress SSRF + 外部接真开关」的 smoke 通过版本；C2 两项安全承诺经实证兑现，SSRF 防护对经典绕过稳健。推进后续块前建议：

1. **C1-CHK-001（P2）**：把传输完整性校验改为 doc 29.3 口径的 `sha256(ZIP 字节)`（或两侧统一改内容摘要口径），并订正 `skill_package.py` docstring；否则 Skill Hub 首次联调即每包 fail-closed。补一条「spec-compliant 头值」的 real 分支用例固化。
2. **C2-OBS-001**：部署清单将 `OPENOPS_LLM_EGRESS_BLOCK_PRIVATE=1` / 内网 deny 网段列为必配。
3. C2-OBS-002（TOCTOU pin IP）、C2-OBS-003（resolve 残留回退提示）作为安全硬化跟进。
