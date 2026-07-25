"""Agent Studio 切片测试的直插库 helper。

这三个函数原本定义在 test_admin_api.py 里，被 test_replay_api.py 用
`from test_admin_studio_api import _insert_span, ...`（**跨测试模块 import 下划线私有名**）
反向依赖。两个文件一起搬进本目录时把这个坏味道一并修掉，改放独立模块。

⚠ 为什么不叫 conftest.py：`pythonpath` 含 `tests`，而 pytest 又会把测试文件所在目录
（tests/studio）插到 sys.path 最前——若本文件名为 conftest.py，测试里的
`from conftest import ADMIN_HEADERS`（本意是取 tests/conftest.py 的 core 常量）会被
本目录的同名文件遮蔽，且本文件自身的 `from conftest import` 会变成自引用循环 import。
命名为 _helpers.py 后：core 常量走 `from conftest import ...`（tests/ 在 pythonpath 上），
切片 helper 走 `from _helpers import ...`，两者互不遮蔽。

core 的 tests/conftest.py 仍是本目录的**祖先 conftest**，`client` / `runtime_backend`
等 fixture 与环境 setdefault 照常自动生效，无需 import。
"""
from __future__ import annotations

import os
from typing import Any

import psycopg


def _insert_span(**kw: Any) -> None:
    """测试直插 span 行（TestClient 的池在 app 线程上，测试侧用同步 psycopg，同 reset_database）。"""
    row = {
        "user_id": "", "agent_run_id": "", "task_id": "", "session_id": "", "agent_role": "",
        "agent_name": "", "kind": "", "model": "", "provider": "", "input_tokens": 0,
        "output_tokens": 0, "cache_input_tokens": 0, "latency_ms": 0.0, "started_at": 1.0,
        "ended_at": 2.0, "tool_name": "", "tool_args": "", "tool_result": "",
        "input_messages": "", "output_messages": "", "finish_reason": "", "span_status": "",
        "trace_id": "", "span_id": "", "parent_span_id": "",
    }
    row.update(kw)
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            """
            insert into sre_agent_studio_span
              (user_id, agent_run_id, task_id, session_id, agent_role, agent_name, kind,
               model, provider, input_tokens, output_tokens, cache_input_tokens, latency_ms,
               started_at, ended_at, tool_name, tool_args, tool_result,
               input_messages, output_messages, finish_reason, span_status,
               trace_id, span_id, parent_span_id, expire_at)
            values (%(user_id)s, %(agent_run_id)s, %(task_id)s, %(session_id)s, %(agent_role)s,
                    %(agent_name)s, %(kind)s, %(model)s, %(provider)s, %(input_tokens)s,
                    %(output_tokens)s, %(cache_input_tokens)s, %(latency_ms)s,
                    to_timestamp(%(started_at)s), to_timestamp(%(ended_at)s), %(tool_name)s,
                    %(tool_args)s, %(tool_result)s, %(input_messages)s, %(output_messages)s,
                    %(finish_reason)s, %(span_status)s, %(trace_id)s, %(span_id)s,
                    %(parent_span_id)s, now() + interval '30 days')
            """,
            row,
        )


def _insert_delegation(run_id: str, delegation_id: str, agent_key: str,
                       task_text: str, report_text: str) -> None:
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        conn.execute(
            """
            insert into sre_agent_delegation
              (delegation_id, run_id, leader_task_id, dispatch_batch_no, agent_key, task_text,
               delegation_status, had_final_report, report_text, created_by, last_updated_by)
            values (%(d)s, %(r)s, 'tsk_main', 1, %(k)s, %(t)s,
                    'completed', true, %(rp)s, '0026demo01', '0026demo01')
            """,
            {"d": delegation_id, "r": run_id, "k": agent_key, "t": task_text, "rp": report_text},
        )


def _fsid(client, run_id: str) -> str:
    with psycopg.connect(os.environ["OPENOPS_DATABASE_URL"], autocommit=True) as conn:
        row = conn.execute(
            "select framework_session_id from sre_agent_run where agent_run_id=%s", (run_id,)
        ).fetchone()
    return row[0]
