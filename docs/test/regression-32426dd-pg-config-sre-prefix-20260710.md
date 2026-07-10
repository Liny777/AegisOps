---
title: 回归测试 — PG 配置拆分 OPENOPS_PG_* + 全表 sre_ 前缀
date: 2026-07-10
tester: Claude (Opus 4.8)
target_commit: 32426dd（feat(db): PG 配置拆分 OPENOPS_PG_* + 全表 sre_ 前缀）
tree_head: 2c772dd（32426dd 的后继；含 role→user_role 等收尾，32426dd 为其祖先）
---

# 回归结论：通过，无 P0/P1

`32426dd` 是一次广泛机械改动（配置拆分 + 22 张表全部加 `sre_` 前缀，扫过 DDL + 全部 10 个 repository 裸 SQL + seed + conftest + 2 个测试文件），回归干净。其后继（`57041b1`/`7d70199`/`14ee820`/`2c772dd`）为同线收尾，本轮护栏在当前 HEAD 上复跑仍绿。

## 被测范围

- **Part A 配置**：`infra/db.py` 新增 `_conninfo()`——`OPENOPS_PG_HOST` 有值 → `make_conninfo` keyword 串（host/port/db/user/password + `OPENOPS_PG_SCHEMA`→`options=-c search_path` + 可选 `OPENOPS_PG_SSLMODE`）；否则回退 `OPENOPS_DATABASE_URL`。
- **Part B 改名**：22 张表 `sre_` 前缀（词边界替换，事件类型串 / `xxx_id` 列 / `ix_/ux_` 索引名有意不改）。
- **Part C**：`.env.example`、`run-backend.ps1.example`、`docs/local-dev-windows.md` 配置文档。

## 验证结果

| 区块 | 结果 | 证据 |
|---|---:|---|
| 全量 pytest（32426dd 当时） | ✅ **118 passed / 1 skipped** | 与提交自述一致；DDL↔repo↔conftest 全锁步（漏前缀即当场炸） |
| 全量 pytest（当前 HEAD + 本轮护栏） | ✅ **120 passed / 2 skipped** | 118 + 新增 2 条逻辑护栏；skipped = docker E2E + schema E2E（均门控，与本改动无关） |
| 静态改名护栏 | ✅ | 恰 22 个 `sre_` CREATE TABLE、0 个非 sre_；无裸表名残留；事件类型串完好；无 `sre_*_id` 列误改 |
| `_conninfo()` 单元（默认 CI 未覆盖） | ✅ | 特殊字符密码 `p@ss w#rd:1'2\x` 经 `make_conninfo` 精确回环；`OPENOPS_PG_SCHEMA→options search_path`；`sslmode` 透传；无 `PG_HOST` → 回退 URL |
| PG_* 路径 E2E | ✅ | keyword conninfo → pool 连上并查 `sre_openops_user` |
| schema search_path 隔离 | ✅ | app 连接 `current_schema()=sre_regtest`、`search_path` 落到该 schema，未加限定的 `sre_` 表解析进 schema（非 public）——共享库隔离成立 |
| App 主链路 | ✅（套件覆盖） | 全部用例经 TestClient 打 `sre_` 表（instance→run→task→state→audit） |
| 前端 | N/A | 本提交 0 前端文件，DB-only |

## 观察项（非阻断）

1. **就地升级残留旧表**：DDL 为 `CREATE IF NOT EXISTS sre_*`——新库/新 schema 干净，但**已有旧无前缀数据的库就地升级需要改名迁移脚本**（`ALTER TABLE x RENAME TO sre_x`），否则旧数据留在孤立旧表。本机 public 已见 22 旧表 + 22 sre_ 表共存。建议部署手册补迁移步骤。
2. **PG_* / schema 路径无自动化护栏** → **本轮已补**：新增 `backend/tests/test_db_config.py`（2 条 `_conninfo()` 逻辑护栏恒跑 + 1 条 `OPENOPS_PG_SCHEMA_TEST=1` 门控的 schema 隔离 E2E）。
3. **DDL 前缀硬编码**：`sre_` 写死在 DDL/repo、无运行时开关；从 19 号重生成 DDL 需再套前缀（DDL 头注已提示）。

## 复现命令

```bash
cd backend
.venv/bin/python -m pytest -q                                  # 120 passed / 2 skipped
OPENOPS_PG_SCHEMA_TEST=1 .venv/bin/python -m pytest tests/test_db_config.py -v   # 含 schema 隔离 E2E
```
