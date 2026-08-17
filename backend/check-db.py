"""独立 DB 连通性自检——把 PoolTimeout 吞掉的真实原因暴露出来。

后端启动报 `psycopg_pool.PoolTimeout: pool initialization incomplete after 30.0 sec` 时，
真正的根因（认证失败 / 主机不可达 / 连接数超限 / SSL 不匹配 / schema 错）被连接池的重试
掩盖了。本脚本用**与后端完全相同**的 OPENOPS_PG_* 逻辑（infra.db.DATABASE_URL）直连一次，
connect_timeout=10 快速失败，直接打印真实异常。

用法（复用 run-backend.sh 里已 export 的 PG 凭证，无需重输密码）：
  把 run-backend.sh 最后一行  `exec "$PY" run.py`  临时改成  `exec "$PY" check-db.py`，
  跑 `bash run-backend.sh`，看完 [check-db] 输出后把那行改回。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from infra.db import DATABASE_URL  # noqa: E402  —— 同后端的 _conninfo() 结果


def _redact(s: str) -> str:
    pw = os.environ.get("OPENOPS_PG_PASSWORD")
    return s.replace(pw, "***") if pw else s


def main() -> None:
    import psycopg

    print("[check-db] conninfo:", _redact(DATABASE_URL))  # 核对 host/port/dbname/user/search_path 是否如预期
    print("[check-db] connecting (connect_timeout=10s)...", flush=True)
    try:
        conn = psycopg.connect(DATABASE_URL, connect_timeout=10)
    except Exception as e:  # noqa: BLE001 —— 就是要把真实异常原样显出来
        print(f"[check-db] ❌ 连接失败: {type(e).__name__}: {e}")
        print("[check-db] 常见对照：authentication failed=密码/用户错；could not translate host=主机名错；"
              "timeout expired/Connection refused=网络不通或库没起；"
              "too many connections/remaining connection slots=连接数超限（多次重启泄漏，等几分钟或找 DBA 清理）")
        raise SystemExit(1)
    print("[check-db] ✅ 连接建立成功")
    try:
        one = conn.execute("select 1").fetchone()[0]
        print(f"[check-db]   select 1 = {one}")
        try:
            n = conn.execute("select count(*) from pg_stat_activity where usename = current_user").fetchone()[0]
            print(f"[check-db]   当前用户已占用连接数 = {n}（接近库上限就会 PoolTimeout）")
        except Exception as e:  # noqa: BLE001 —— GaussDB 目录差异不影响主判定
            print(f"[check-db]   （连接数查询跳过：{type(e).__name__}: {e}）")
        try:
            # 增量迁移哨兵：缺列=旧库没跑迁移（会话名会一直显示「新对话」，改名接口报错）
            has_title = conn.execute(
                "select 1 from information_schema.columns "
                "where table_name='sre_agent_run' and column_name='run_title'"
            ).fetchone()
            if has_title:
                print("[check-db]   ✅ sre_agent_run.run_title 列存在（会话名迁移已跑）")
            else:
                print("[check-db]   ❌ sre_agent_run.run_title 列缺失——请执行 sql/migrate-2026-07-12-run-title.sql"
                      "（或重跑 core.sql，尾部增量段幂等补列），否则会话自动起名/重命名不生效")
        except Exception as e:  # noqa: BLE001
            print(f"[check-db]   （run_title 列检测跳过：{type(e).__name__}: {e}）")
        try:
            # 增量迁移哨兵：缺列=旧库没跑 08-17 迁移，平台模型取不到 Key 会全部跌 stub
            has_secret_col = conn.execute(
                "select 1 from information_schema.columns "
                "where table_name='sre_model_asset' and column_name='secret_ciphertext'"
            ).fetchone()
            if has_secret_col:
                n_keyed = conn.execute(
                    "select count(*) from sre_model_asset "
                    "where deleted_at is null and secret_ciphertext is not null"
                ).fetchone()[0]
                print(f"[check-db]   ✅ sre_model_asset.secret_ciphertext 列存在（Key 入库迁移已跑）"
                      f"；已配 Key 的平台模型 = {n_keyed} 个"
                      + ("" if n_keyed else "——0 个意味着所有平台模型都会跌 stub，"
                                            "请确认 backfill 的环境变量已注入，或在管理台补填 API Key"))
            else:
                print("[check-db]   ❌ sre_model_asset.secret_ciphertext 列缺失——请执行 "
                      "sql/migrate-2026-08-17-model-asset-secret.sql（或重跑 core.sql，尾部增量段幂等补列），"
                      "否则平台模型取不到 API Key，全部回退 stub")
        except Exception as e:  # noqa: BLE001
            print(f"[check-db]   （secret_ciphertext 列检测跳过：{type(e).__name__}: {e}）")
        try:
            sp = conn.execute("show search_path").fetchone()[0]
            print(f"[check-db]   search_path = {sp}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        conn.close()


if __name__ == "__main__":
    main()
