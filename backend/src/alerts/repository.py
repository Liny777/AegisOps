"""sre_alert_* 六表仓储（切片自有；2026-08-15 订阅表下线后为六表）。

铁律：本模块只写 sre_alert_* 六表；core 表（sre_agent_run / sre_task_state）的写操作
一律走 infra.repositories.runs / task_states 的公开函数，切片对 core 表保持只读。
GaussDB 兼容注意：部分唯一索引（WHERE deleted_at IS NULL）不能做 ON CONFLICT 目标，
upsert 一律「update → insert…where not exists → 再 update」三步（同 infra/idempotency 的坑位结论）。
"""
from __future__ import annotations

import uuid
from typing import Any

from infra.db import exec1, jsonb, q_all, q_one

# ============================ rules ============================


async def create_rule(*, instance_id: str, owner: str, name: str, description: str,
                      source: str, match: dict[str, Any], prompt: str, enabled: bool,
                      by: str) -> str:
    rid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_alert_rule
          (alert_rule_id, agent_team_instance_id, owner_user_id, rule_name, description,
           rule_source, match_json, prompt, enabled, created_by, last_updated_by)
        values (%(id)s, %(i)s, %(o)s, %(n)s, %(d)s, %(s)s, %(m)s, %(p)s, %(e)s, %(by)s, %(by)s)
        """,
        {"id": rid, "i": instance_id, "o": owner, "n": name, "d": description,
         "s": source, "m": jsonb(match), "p": prompt, "e": enabled, "by": by},
    )
    return rid


async def list_rules(instance_id: str) -> list[dict[str, Any]]:
    return await q_all(
        "select * from sre_alert_rule where agent_team_instance_id=%(i)s and deleted_at is null "
        "order by creation_date",
        {"i": instance_id},
    )


async def get_rule(rule_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_alert_rule where alert_rule_id=%(r)s and deleted_at is null",
        {"r": rule_id},
    )


async def rule_name_exists(instance_id: str, name: str, exclude_rule_id: str | None = None) -> bool:
    row = await q_one(
        """
        select 1 as x from sre_alert_rule
        where agent_team_instance_id=%(i)s and rule_name=%(n)s and deleted_at is null
          and (%(x)s::uuid is null or alert_rule_id <> %(x)s::uuid)
        limit 1
        """,
        {"i": instance_id, "n": name, "x": exclude_rule_id},
    )
    return row is not None


async def update_rule(rule_id: str, *, name: str, description: str, match: dict[str, Any],
                      prompt: str, enabled: bool, by: str) -> int:
    return await exec1(
        """
        update sre_alert_rule
        set rule_name=%(n)s, description=%(d)s, match_json=%(m)s, prompt=%(p)s, enabled=%(e)s,
            last_update_date=now(), last_updated_by=%(by)s
        where alert_rule_id=%(r)s and deleted_at is null
        """,
        {"r": rule_id, "n": name, "d": description, "m": jsonb(match), "p": prompt,
         "e": enabled, "by": by},
    )


async def list_rules_by_ids(rule_ids: list[str]) -> list[dict[str, Any]]:
    return await q_all(
        "select * from sre_alert_rule where alert_rule_id = any(%(ids)s::uuid[]) "
        "and deleted_at is null",
        {"ids": rule_ids},
    )


async def any_rule_live(rule_ids: list[str]) -> bool:
    """这批规则里还有「未删除且启用」的吗——派发前的规则闸与 :retry 守卫共用。

    不复用 list_rules_by_ids（它只滤 deleted_at 不滤 enabled，且拉全行）；limit 1 走
    主键索引，放在派发热路径上成本可忽略。
    """
    row = await q_one(
        "select 1 as x from sre_alert_rule "
        "where alert_rule_id = any(%(ids)s::uuid[]) and deleted_at is null and enabled limit 1",
        {"ids": rule_ids},
    )
    return row is not None


async def set_rules_enabled(rule_ids: list[str], enabled: bool, by: str) -> int:
    return await exec1(
        "update sre_alert_rule set enabled=%(e)s, last_update_date=now(), last_updated_by=%(by)s "
        "where alert_rule_id = any(%(ids)s::uuid[]) and deleted_at is null",
        {"ids": rule_ids, "e": enabled, "by": by},
    )


async def soft_delete_rules(rule_ids: list[str], by: str) -> int:
    return await exec1(
        "update sre_alert_rule set deleted_at=now(), last_update_date=now(), last_updated_by=%(by)s "
        "where alert_rule_id = any(%(ids)s::uuid[]) and deleted_at is null",
        {"ids": rule_ids, "by": by},
    )


async def soft_delete_rule(rule_id: str, by: str) -> int:
    return await exec1(
        "update sre_alert_rule set deleted_at=now(), last_update_date=now(), last_updated_by=%(by)s "
        "where alert_rule_id=%(r)s and deleted_at is null",
        {"r": rule_id, "by": by},
    )


async def list_enabled_rules() -> list[dict[str, Any]]:
    """matcher 用：全部「白名单内 + 规则开」的规则（内存匹配的加载面）。

    inner join 白名单 = 名单外用户的存量规则**不参与匹配**（算力保护 fail-closed：
    被移出名单即刻断流，不必去关他的规则）。2026-08-15 拍板去掉订阅维度：实例总
    开关下线（三级开关变两级：全局热停 ＞ 规则），批量启停规则即整体暂停。

    ORDER BY 是承重的（2026-08-19）：owner 归属与每个 prompt 组的 alert_rule_id 都取
    组内第 0 条——无序 SQL 会让「哪条规则生效」随行迁移漂移。最老规则优先，对齐
    配置页 list_rules 口径，creation_date 可并列故加主键 tiebreak。
    """
    return await q_all(
        """
        select r.* from sre_alert_rule r
        join sre_alert_user_grant g
          on g.user_id = r.owner_user_id and g.deleted_at is null
        where r.enabled and r.deleted_at is null
        order by r.creation_date, r.alert_rule_id
        """,
    )


# ============================ 用户白名单（算力保护） ============================


async def is_user_granted(user_id: str) -> bool:
    row = await q_one(
        "select 1 as x from sre_alert_user_grant where user_id=%(u)s and deleted_at is null",
        {"u": user_id})
    return row is not None


async def list_user_grants() -> list[dict[str, Any]]:
    return await q_all(
        "select user_id, granted_by, creation_date, last_update_date "
        "from sre_alert_user_grant where deleted_at is null order by creation_date desc")


async def set_user_grant(user_id: str, granted: bool, by: str) -> None:
    """开通=插入或复活软删行；关闭=软删（保留 granted_by 审计链）。幂等。"""
    if granted:
        await exec1(
            """
            insert into sre_alert_user_grant (user_id, granted_by, created_by, last_updated_by)
            values (%(u)s, %(by)s, %(by)s, %(by)s)
            on conflict (user_id) do update
              set deleted_at = null, granted_by = %(by)s,
                  last_update_date = now(), last_updated_by = %(by)s
            """,
            {"u": user_id, "by": by})
    else:
        await exec1(
            "update sre_alert_user_grant set deleted_at = now(), "
            "last_update_date = now(), last_updated_by = %(by)s "
            "where user_id = %(u)s and deleted_at is null",
            {"u": user_id, "by": by})


# ============================ events ============================


async def get_event_by_fingerprint(source_key: str, fingerprint: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_alert_event where source_key=%(s)s and fingerprint=%(f)s",
        {"s": source_key, "f": fingerprint},
    )


async def insert_event(*, source_key: str, external_alert_id: str, fingerprint: str,
                       alert_status: str, severity: str, category: str, alert_name: str,
                       alert_object: str, strategy_name: str,
                       appid: str | None, labels: dict[str, Any], annotations: dict[str, Any],
                       starts_at: str | None, payload: dict[str, Any] | None,
                       retention_days: int = 30) -> str:
    eid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_alert_event
          (alert_event_id, source_key, external_alert_id, fingerprint, alert_status, severity,
           category, alert_name, alert_object, strategy_name, appid, labels_json,
           annotations_json, starts_at, payload_json, expire_at)
        values (%(id)s, %(s)s, %(x)s, %(f)s, %(st)s, %(sev)s,
                %(c)s, %(n)s, %(ob)s, %(sn)s, %(a)s, %(l)s,
                %(an)s, %(sa)s, %(p)s, now() + make_interval(days => %(rd)s))
        """,
        {"id": eid, "s": source_key, "x": external_alert_id, "f": fingerprint, "st": alert_status,
         "sev": severity, "c": category, "n": alert_name, "ob": alert_object, "sn": strategy_name,
         "a": appid, "l": jsonb(labels), "an": jsonb(annotations), "sa": starts_at,
         "p": jsonb(payload or {}), "rd": retention_days},
    )
    return eid


async def touch_event(event_id: str, *, alert_status: str, severity: str,
                      bump_seen: bool) -> int:
    """同指纹再现：刷新最近时间/状态/严重度；去重窗口内 bump_seen=True 只加计数。"""
    return await exec1(
        """
        update sre_alert_event
        set last_seen_at=now(), alert_status=%(st)s, severity=%(sev)s,
            seen_count = seen_count + (case when %(b)s then 1 else 0 end),
            last_update_date=now()
        where alert_event_id=%(e)s
        """,
        {"e": event_id, "st": alert_status, "sev": severity, "b": bump_seen},
    )


async def purge_expired_events() -> int:
    return await exec1("delete from sre_alert_event where expire_at < now()")


async def expire_stale_queued(max_age_s: int) -> int:
    """排队超时批量翻失败（2026-08-14 拍板：极端积压下排队超过 alert_queue_max_age_s
    的单不再等派发，直接「接管失败」留痕告知用户，可手动 :retry 重入队）。

    判据必须用 queued_at：requeue（:retry）会重置 queued_at=now()，手动重试单重新
    计时——用 last_alert_at 会把刚重试的陈旧单立刻再翻掉。agent_result 一并置
    'failed'，清单「结果」列直接显红（与诊断失败同投影）。
    """
    return await exec1(
        """
        update sre_alert_incident
        set incident_state='failed', state_reason='queue_expired', agent_result='failed',
            ended_at=now(), last_update_date=now(), last_updated_by='system'
        where incident_state='queued' and deleted_at is null
          and queued_at < now() - make_interval(secs => %(age)s)
        """,
        {"age": int(max_age_s)},
    )


async def bump_open_incident_counts(event_id: str) -> int:
    """去重窗口内同指纹再现：其所在未完结 incident 的计数与最近告警时间同步前进。"""
    return await exec1(
        """
        update sre_alert_incident
        set alert_count = alert_count + 1, last_alert_at = now(), last_update_date = now()
        where incident_state in ('queued','diagnosing') and deleted_at is null
          and alert_incident_id in (
            select alert_incident_id from sre_alert_incident_event
            where alert_event_id=%(e)s and deleted_at is null
          )
        """,
        {"e": event_id},
    )


# ============================ incidents（队列实体） ============================


async def create_incident(*, instance_id: str, owner: str, rule_id: str | None,
                          matched_rules: list[dict[str, Any]], group_key: str,
                          prompt_group: str | None, title: str,
                          severity: str, category: str, state: str, state_reason: str | None,
                          first_alert_at: str | None) -> str:
    iid = str(uuid.uuid4())
    await exec1(
        """
        insert into sre_alert_incident
          (alert_incident_id, agent_team_instance_id, owner_user_id, alert_rule_id,
           matched_rules_json, group_key, prompt_group, title, severity, category,
           incident_state, state_reason, first_alert_at, last_alert_at)
        values (%(id)s, %(i)s, %(o)s, %(r)s,
                %(mr)s, %(g)s, %(pg)s, %(t)s, %(sev)s, %(c)s, %(st)s,
                %(sr)s, coalesce(%(fa)s::timestamptz, now()), now())
        """,
        {"id": iid, "i": instance_id, "o": owner, "r": rule_id, "mr": jsonb(matched_rules),
         "g": group_key, "pg": prompt_group, "t": title, "sev": severity, "c": category,
         "st": state, "sr": state_reason, "fa": first_alert_at},
    )
    return iid


async def get_incident(incident_id: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_alert_incident where alert_incident_id=%(i)s and deleted_at is null",
        {"i": incident_id},
    )


async def find_open_incident_by_group(group_key: str, prompt_group: str) -> dict[str, Any] | None:
    """附着查找：同 (group_key, prompt_group) 未完结（queued/diagnosing）的最新 incident。

    prompt_group IS NULL 通配（2026-08-19 分单上线的过渡兼容）：存量旧单无组标，
    对所有组可见——命中旧单时调用方按旧「一组一单」语义整体附着，防止部署切换
    瞬间在途风暴双倍建单。精确组优先于 NULL 兜底。
    """
    return await q_one(
        """
        select * from sre_alert_incident
        where group_key=%(g)s and (prompt_group=%(pg)s or prompt_group is null)
          and incident_state in ('queued','diagnosing') and deleted_at is null
        order by (prompt_group is not null) desc, creation_date desc limit 1
        """,
        {"g": group_key, "pg": prompt_group},
    )


async def latest_ended_at_by_group(group_key: str, prompt_group: str) -> dict[str, Any] | None:
    """组冷却判定：同 (group_key, prompt_group) 最近一个 completed/failed 的结束时间。

    prompt_group IS NULL 通配：部署前刚完结的旧单对所有新组继续冷却（防上线瞬间
    重诊风暴）；新单不再写 NULL，旧值随时间自然淡出冷却窗。
    """
    return await q_one(
        """
        select ended_at from sre_alert_incident
        where group_key=%(g)s and (prompt_group=%(pg)s or prompt_group is null)
          and incident_state in ('completed','failed')
          and ended_at is not null and deleted_at is null
        order by ended_at desc limit 1
        """,
        {"g": group_key, "pg": prompt_group},
    )


async def attach_alert(incident_id: str, *, severity_rank_sql_hint: str = "") -> None:
    """附着：计数++、last_alert_at 前进（severity 升级由 service 判定后单独 set）。"""
    _ = severity_rank_sql_hint
    await exec1(
        "update sre_alert_incident set alert_count = alert_count + 1, last_alert_at=now(), "
        "last_update_date=now() where alert_incident_id=%(i)s",
        {"i": incident_id},
    )


async def escalate_severity(incident_id: str, severity: str) -> None:
    await exec1(
        "update sre_alert_incident set severity=%(s)s, last_update_date=now() "
        "where alert_incident_id=%(i)s",
        {"i": incident_id, "s": severity},
    )


async def count_open_by_instance(instance_id: str) -> int:
    row = await q_one(
        "select count(*) as n from sre_alert_incident "
        "where agent_team_instance_id=%(i)s and incident_state in ('queued','diagnosing') "
        "and deleted_at is null",
        {"i": instance_id},
    )
    return int(row["n"]) if row else 0


async def count_queued_global() -> int:
    row = await q_one(
        "select count(*) as n from sre_alert_incident "
        "where incident_state='queued' and deleted_at is null",
    )
    return int(row["n"]) if row else 0


def _rank_expr(severity_order: list[str]) -> str:
    return ("case severity " + " ".join(f"when '{s}' then {i}" for i, s in enumerate(severity_order))
            + " else 99 end")


def _eff_priority_expr(severity_order: list[str]) -> str:
    """有效优先级（§5.3 三级队列、老化与插队，2026-08-15 落地）：

        manual_priority ? -1 : greatest(0, severity_rank − floor(等待分钟 / aging))

    置顶恒 -1 高于致命；老化每满 alert_aging_minutes 升一档、封顶致命档——普通告警
    不会被持续流入的高级别永远压住（防饿死）。取队/踢除/位次三处必须同用本表达式。
    需要绑定参数 %(aging)s（分钟，≥1）。
    """
    return (f"case when manual_priority then -1 else greatest(0, {_rank_expr(severity_order)}"
            f" - floor(extract(epoch from (now() - queued_at)) / 60 / greatest(1, %(aging)s))::int) end")


async def lowest_queued(severity_order: list[str], aging_minutes: int) -> dict[str, Any] | None:
    """队列满时的被踢候选：有效优先级最低、最晚入队的 queued 单（置顶单天然不被踢）。"""
    eff = _eff_priority_expr(severity_order)
    return await q_one(
        f"select * from sre_alert_incident where incident_state='queued' and deleted_at is null "
        f"order by {eff} desc, queued_at desc limit 1",
        {"aging": int(aging_minutes)},
    )


async def pick_next_queued(severity_order: list[str], aging_minutes: int) -> dict[str, Any] | None:
    """dispatcher 取队首：有效优先级 ASC（置顶 -1 最先），同级 FIFO。"""
    eff = _eff_priority_expr(severity_order)
    return await q_one(
        f"select * from sre_alert_incident where incident_state='queued' and deleted_at is null "
        f"order by {eff} asc, queued_at asc limit 1",
        {"aging": int(aging_minutes)},
    )


async def set_manual_priority(incident_id: str, on: bool, by: str) -> int:
    """置顶/取消（仅 queued；rowcount=0 = 非排队态，调用方转 409）。"""
    return await exec1(
        "update sre_alert_incident set manual_priority=%(p)s, last_update_date=now(), "
        "last_updated_by=%(by)s where alert_incident_id=%(i)s and incident_state='queued' "
        "and deleted_at is null",
        {"i": incident_id, "p": on, "by": by},
    )


async def transition(incident_id: str, *, from_states: list[str], to_state: str,
                     state_reason: str | None = None, by: str = "system",
                     set_started: bool = False, set_ended: bool = False) -> int:
    """条件 UPDATE 状态迁移（乐观抢占：rowcount=0 即别处已迁走）。"""
    return await exec1(
        f"""
        update sre_alert_incident
        set incident_state=%(to)s, state_reason=%(sr)s,
            {"started_at=now()," if set_started else ""}
            {"ended_at=now()," if set_ended else ""}
            last_update_date=now(), last_updated_by=%(by)s
        where alert_incident_id=%(i)s and incident_state = any(%(from)s) and deleted_at is null
        """,
        {"i": incident_id, "to": to_state, "sr": state_reason, "by": by, "from": from_states},
    )


async def bind_run(incident_id: str, run_id: str, task_id: str | None) -> None:
    await exec1(
        "update sre_alert_incident set agent_run_id=%(r)s::uuid, diagnosis_task_id=%(t)s, "
        "last_update_date=now() where alert_incident_id=%(i)s",
        {"i": incident_id, "r": run_id, "t": task_id},
    )


async def finish(incident_id: str, *, to_state: str, state_reason: str | None,
                 result_summary: str | None, agent_result: str | None) -> int:
    return await exec1(
        """
        update sre_alert_incident
        set incident_state=%(st)s, state_reason=%(sr)s, result_summary=%(rs)s,
            agent_result=%(ar)s, ended_at=now(), last_update_date=now()
        where alert_incident_id=%(i)s and incident_state='diagnosing' and deleted_at is null
        """,
        {"i": incident_id, "st": to_state, "sr": state_reason, "rs": result_summary, "ar": agent_result},
    )


async def requeue(incident_id: str, *, bump_retry: bool, by: str = "system") -> int:
    return await exec1(
        f"""
        update sre_alert_incident
        set incident_state='queued', state_reason=null, queued_at=now(),
            started_at=null, ended_at=null,
            {"retry_count = retry_count + 1," if bump_retry else "retry_count = 0,"}
            last_update_date=now(), last_updated_by=%(by)s
        where alert_incident_id=%(i)s and deleted_at is null
        """,
        {"i": incident_id, "by": by},
    )


async def set_feedback(incident_id: str, *, feedback: str, note: str, by: str) -> int:
    return await exec1(
        "update sre_alert_incident set user_feedback=%(f)s, feedback_note=%(n)s, "
        "last_update_date=now(), last_updated_by=%(by)s "
        "where alert_incident_id=%(i)s and deleted_at is null",
        {"i": incident_id, "f": feedback, "n": note, "by": by},
    )


async def list_diagnosing() -> list[dict[str, Any]]:
    return await q_all(
        "select * from sre_alert_incident where incident_state='diagnosing' and deleted_at is null",
    )


async def global_overview_counts() -> dict[str, Any]:
    """admin overview：全局各态计数 + 24h 丢弃 + 有未完结单的 owner 数（容器占位参考）。"""
    row = await q_one(
        """
        select
          count(*) filter (where incident_state='queued') as queued,
          count(*) filter (where incident_state='diagnosing') as diagnosing,
          count(*) filter (where incident_state='completed') as completed,
          count(*) filter (where incident_state='failed') as failed,
          count(*) filter (where incident_state='skipped'
                           and ended_at > now() - interval '24 hours') as skipped_24h,
          count(*) filter (where incident_state='ignored') as ignored,
          count(distinct owner_user_id) filter (
            where incident_state in ('queued','diagnosing')) as owners_with_open
        from sre_alert_incident where deleted_at is null
        """,
    )
    return {k: int(v or 0) for k, v in (row or {}).items()}


async def list_incidents(*, owner: str, instance_id: str | None, states: list[str] | None,
                         severities: list[str] | None, search: str | None,
                         page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
    where = ["owner_user_id=%(o)s", "deleted_at is null"]
    params: dict[str, Any] = {"o": owner}
    if instance_id:
        where.append("agent_team_instance_id=%(i)s")
        params["i"] = instance_id
    if states:
        where.append("incident_state = any(%(st)s)")
        params["st"] = states
    if severities:
        where.append("severity = any(%(sev)s)")
        params["sev"] = severities
    if search:
        where.append("(title ilike %(q)s or category ilike %(q)s or alert_incident_id::text ilike %(q)s)")
        params["q"] = f"%{search}%"
    cond = " and ".join(where)
    total_row = await q_one(f"select count(*) as n from sre_alert_incident where {cond}", params)
    params.update({"lim": page_size, "off": (page - 1) * page_size})
    rows = await q_all(
        f"select * from sre_alert_incident where {cond} "
        f"order by creation_date desc limit %(lim)s offset %(off)s",
        params,
    )
    return rows, int(total_row["n"]) if total_row else 0


async def queue_position(row: dict[str, Any], severity_order: list[str],
                         aging_minutes: int) -> int:
    """queued 单的队列位次（1-based）：与 pick_next_queued 同用有效优先级表达式。

    目标单的有效优先级同样在 SQL 里按绑定值现算（老化随 now() 流动，Python 侧
    预算会与行表达式产生时钟偏差）。
    """
    eff = _eff_priority_expr(severity_order)
    my_rank = severity_order.index(str(row["severity"])) if str(row["severity"]) in severity_order else 99
    my_eff = (f"case when %(mp)s then -1 else greatest(0, %(r)s"
              f" - floor(extract(epoch from (now() - %(q)s::timestamptz)) / 60 / greatest(1, %(aging)s))::int) end")
    ahead = await q_one(
        f"""
        select count(*) as n from sre_alert_incident
        where incident_state='queued' and deleted_at is null
          and ({eff} < ({my_eff}) or ({eff} = ({my_eff}) and queued_at < %(q)s))
        """,
        {"r": my_rank, "q": row["queued_at"], "mp": bool(row.get("manual_priority")),
         "aging": int(aging_minutes)},
    )
    return int(ahead["n"]) + 1 if ahead else 1


async def latest_change_seq(*, owner: str, instance_id: str | None) -> int:
    """owner（可选按实例）名下 incident 的最近变更序号（epoch 毫秒）——前端未读基线用。"""
    where = "owner_user_id=%(o)s and deleted_at is null"
    params: dict[str, Any] = {"o": owner}
    if instance_id:
        where += " and agent_team_instance_id=%(i)s"
        params["i"] = instance_id
    row = await q_one(
        f"select coalesce(floor(extract(epoch from max(last_update_date)) * 1000), 0) as seq "
        f"from sre_alert_incident where {where}",
        params,
    )
    return int(row["seq"]) if row else 0


async def summary_counts(*, owner: str, instance_id: str | None) -> dict[str, int]:
    where = "owner_user_id=%(o)s and deleted_at is null"
    params: dict[str, Any] = {"o": owner}
    if instance_id:
        where += " and agent_team_instance_id=%(i)s"
        params["i"] = instance_id
    row = await q_one(
        f"""
        select
          count(*) filter (where incident_state='queued') as queued,
          count(*) filter (where incident_state='diagnosing') as diagnosing,
          count(*) filter (where incident_state='completed'
                           and ended_at > now() - interval '24 hours') as completed_24h
        from sre_alert_incident where {where}
        """,
        params,
    )
    q, d = int(row["queued"] or 0), int(row["diagnosing"] or 0)
    return {"queued": q, "diagnosing": d,
            "completed_24h": int(row["completed_24h"] or 0), "attention": q + d}


# ---- incident ↔ event 关联 ----


async def linked_pairs(event_id: str) -> set[tuple[str, str | None]]:
    """该事件已链接过单的 (实例, prompt 组) 对集（含已完结单——接过≠再接，重建节奏归组冷却管）。

    去重窗口补路由（2026-08-19）用：滚动窗口下持续重发永不过窗，仅对「命中规则但
    从未建过单」的 (实例, 组) 补路由。组标 None 的存量单视为该实例**全组已接**
    （宁漏勿双建：老 link 行无组标，按组补建会对已接实例重复建全套组单）。
    alert_event_id 有索引。
    """
    rows = await q_all(
        """
        select distinct i.agent_team_instance_id, i.prompt_group
        from sre_alert_incident_event l
        join sre_alert_incident i on i.alert_incident_id = l.alert_incident_id
        where l.alert_event_id=%(e)s and l.deleted_at is null and i.deleted_at is null
        """,
        {"e": event_id},
    )
    return {(str(r["agent_team_instance_id"]),
             str(r["prompt_group"]) if r["prompt_group"] is not None else None)
            for r in rows}


async def link_event(incident_id: str, event_id: str, rule_ids: list[str]) -> None:
    await exec1(
        """
        insert into sre_alert_incident_event
          (link_id, alert_incident_id, alert_event_id, matched_rule_ids_json)
        select %(id)s, %(i)s, %(e)s, %(r)s
        where not exists (
          select 1 from sre_alert_incident_event
          where alert_incident_id=%(i)s and alert_event_id=%(e)s and deleted_at is null
        )
        """,
        {"id": str(uuid.uuid4()), "i": incident_id, "e": event_id, "r": jsonb(rule_ids)},
    )


async def list_incident_events(incident_id: str, limit: int = 50) -> tuple[list[dict[str, Any]], int]:
    total_row = await q_one(
        "select count(*) as n from sre_alert_incident_event "
        "where alert_incident_id=%(i)s and deleted_at is null",
        {"i": incident_id},
    )
    rows = await q_all(
        """
        select e.* from sre_alert_incident_event l
        join sre_alert_event e on e.alert_event_id = l.alert_event_id
        where l.alert_incident_id=%(i)s and l.deleted_at is null
        order by e.last_seen_at desc limit %(lim)s
        """,
        {"i": incident_id, "lim": limit},
    )
    return rows, int(total_row["n"]) if total_row else 0


# ============================ 事件清单（全量告警视角，v2 需求 1.3） ============================

_TAKEOVER_COND = {
    # 接管三值与「分派」状态共用口径：skipped/ignored 视为未接管（留痕单不算接管）
    "done": "i.inc_state in ('completed','failed')",
    "processing": "i.inc_state in ('queued','diagnosing')",
    "none": "(i.inc_state is null or i.inc_state in ('skipped','ignored'))",
}

_LATERAL = """
    left join lateral (
      select x.alert_incident_id as inc_id, x.incident_state as inc_state,
             x.agent_result as inc_agent_result, x.user_feedback as inc_user_feedback,
             x.feedback_note as inc_feedback_note, x.agent_run_id as inc_run_id,
             x.owner_user_id as inc_owner, x.agent_team_instance_id as inc_instance_id,
             x.result_summary as inc_summary, x.manual_priority as inc_manual_priority,
             x.state_reason as inc_state_reason, x.matched_rules_json as inc_matched_rules
      from sre_alert_incident x
      join sre_alert_incident_event l
        on l.alert_incident_id = x.alert_incident_id
       and l.alert_event_id = e.alert_event_id and l.deleted_at is null
      where x.deleted_at is null
        and (%(lat_owner)s::text is null or x.owner_user_id = %(lat_owner)s)
        and (%(lat_inst)s::uuid is null or x.agent_team_instance_id = %(lat_inst)s::uuid)
      order by (x.incident_state in ('queued','diagnosing')) desc,
               x.last_update_date desc, x.alert_incident_id desc limit 1
    ) i on true
"""


def _proj_exists(lateral_owner: str | None, lateral_instance: str | None) -> str:
    """镜像 _LATERAL 过滤条件的存在性判定：该事件在当前视角（owner/inst）下有未删单。
    与「投影非空（i.inc_id is not null）」严格等价——limit 1 只决定投影哪张单，不影响
    是否存在；owner/inst 按参数是否为 None 在 Python 侧拼接（而非 `$1 is null or ...`），
    planner 才能稳定走 incident 的 owner 索引做 semi-join。别名 le/xe 避开同查询的 l2/x2/lo/xo。"""
    conds = ""
    if lateral_owner is not None:
        conds += " and xe.owner_user_id = %(lat_owner)s"
    if lateral_instance is not None:
        conds += " and xe.agent_team_instance_id = %(lat_inst)s::uuid"
    return ("exists (select 1 from sre_alert_incident_event le "
            "join sre_alert_incident xe on xe.alert_incident_id = le.alert_incident_id "
            "where le.alert_event_id = e.alert_event_id and le.deleted_at is null "
            f"and xe.deleted_at is null{conds})")


async def list_events(*, lateral_owner: str | None, lateral_instance: str | None,
                      scope_appids: list[str] | None,
                      alert_status: str | None, severities: list[str] | None,
                      takeover: str | None, category: str | None, search: str | None,
                      since_days: int | None = None, categories: list[str] | None = None,
                      matched_only: bool = False, owner_filter: str | None = None,
                      page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
    """事件 LEFT JOIN LATERAL「该视角下最新聚合单」（活跃单优先——按 prompt 分单后
    同事件可有多张姊妹单，投影先取 queued/diagnosing 再取最近更新，行状态不随创建毫秒序随机）。

    - lateral_owner/lateral_instance：接管投影取谁的单（用户视角=本人[+实例]；admin 按用户查看=目标用户；
      admin 全量=None 取任意 owner 最新单）。
    - owner_filter：行集收窄为「该用户接管过的事件」（admin 按用户查看，2026-08-19 语义修正：
      原纯 LATERAL 投影不收窄行集，管理员输了 user_id 看到的仍是全量行、只有接管列变化——
      与直觉相悖）。与 lateral_owner 配套传同一用户：过滤+投影，接管人列全是他。
    - scope_appids：普通用户可见性——`有本人单` 或 `未接管但 appid ∈ 范围`；None=admin 不加可见性子句。
      注意：他人接管的告警若 appid 在我范围内，会以「未接管」形态出现（他人单不投影，隐私口径）。
    """
    params: dict[str, Any] = {"lat_owner": lateral_owner, "lat_inst": lateral_instance,
                              "scope": scope_appids}
    where = ["1=1"]
    needs_lateral = False  # 引用投影列 i.* 的子句置 True：count 才带 _LATERAL（见下）
    if owner_filter:
        where.append("""exists (select 1 from sre_alert_incident_event lo
            join sre_alert_incident xo on xo.alert_incident_id = lo.alert_incident_id
            where lo.alert_event_id = e.alert_event_id and lo.deleted_at is null
              and xo.deleted_at is null and xo.owner_user_id = %(owner_f)s)""")
        params["owner_f"] = owner_filter
    if matched_only:
        # 管理台清单默认口径（2026-08-15）：只看「命中规则建过单 ∧ 属主仍在白名单」的行。
        # 用与 LATERAL 解耦的 EXISTS——朴素 i.inc_id is not null 会在「按用户查看」时把
        # 他人接管的行一并滤掉（lat_owner 过滤了 LATERAL），杀掉 per-user 投影语义。
        # 内层 grant EXISTS = 白名单活口径：被移出名单用户的旧单行不再展示。
        where.append("""exists (select 1 from sre_alert_incident_event l2
            join sre_alert_incident x2 on x2.alert_incident_id = l2.alert_incident_id
            where l2.alert_event_id = e.alert_event_id
              and l2.deleted_at is null and x2.deleted_at is null
              and exists (select 1 from sre_alert_user_grant g
                          where g.user_id = x2.owner_user_id and g.deleted_at is null))""")
    if scope_appids is not None:
        # 「有本视角单」不用 i.inc_id is not null（会把全表逐行 LATERAL 拖进过滤路径），
        # 改用与 LATERAL 解耦的等价 EXISTS；用户清单主路径恒传 []（只看本人接管过的单），
        # 空数组臂恒假，裁掉 OR 才能让 planner 把 EXISTS 上拉成 semi-join 从 owner 索引反向驱动。
        proj = _proj_exists(lateral_owner, lateral_instance)
        where.append(f"({proj} or e.appid = any(%(scope)s::text[]))" if scope_appids else proj)
    if alert_status == "closed":
        where.append("e.alert_status = 'resolved'")
    elif alert_status == "assigned":
        where.append("e.alert_status <> 'resolved' and i.inc_id is not null "
                     "and i.inc_state not in ('skipped','ignored')")
        needs_lateral = True
    elif alert_status == "unassigned":
        where.append("e.alert_status <> 'resolved' "
                     "and (i.inc_id is null or i.inc_state in ('skipped','ignored'))")
        needs_lateral = True
    if severities:
        where.append("e.severity = any(%(sev)s)")
        params["sev"] = severities
    if takeover in _TAKEOVER_COND:
        # 接管/分派筛选语义绑定「投影单」（top-1，非任意姊妹单），不可 EXISTS 化
        where.append(_TAKEOVER_COND[takeover])
        needs_lateral = True
    if category:
        where.append("e.category = %(cat)s")
        params["cat"] = category
    elif categories:  # 预览降级路径的多类别过滤（历史预览类型多选；单值调用零改动）
        where.append("e.category = any(%(cats)s::text[])")
        params["cats"] = categories
    if search:
        where.append("(e.external_alert_id ilike %(q)s or e.category ilike %(q)s "
                     "or e.alert_object ilike %(q)s)")
        params["q"] = f"%{search}%"
    if since_days:  # 配置弹窗「最近 N 天同类告警预览」窗口
        where.append("e.last_seen_at > now() - make_interval(days => %(sd)s)")
        params["sd"] = int(since_days)
    cond = " and ".join(where)

    # count 只在过滤条件真引用投影列时才带 LATERAL——PG 不会对带 order/limit 的相关
    # 子查询做 join removal，无脑带上等于对全表每行跑一次两表探查（清单打开 1-3s 主因）。
    total_row = await q_one(
        f"select count(*) as n from sre_alert_event e "
        f"{_LATERAL if needs_lateral else ''} where {cond}", params)
    params.update({"lim": page_size, "off": (page - 1) * page_size})
    # 排序键必须稳定（creation_date + id）：last_seen_at 会被 dedup touch 高频前移，
    # 内网流量下翻页窗口持续洗牌，第 2 页看到的还是刚顶上来的同一批行（2026-08-13 反馈）。
    rows = await q_all(
        f"select e.*, i.* from sre_alert_event e {_LATERAL} where {cond} "
        f"order by e.creation_date desc, e.alert_event_id desc limit %(lim)s offset %(off)s",
        params,
    )
    return rows, int(total_row["n"]) if total_row else 0


# ============================ pull_state ============================


async def get_pull_state(source_key: str) -> dict[str, Any] | None:
    return await q_one(
        "select * from sre_alert_pull_state where source_key=%(s)s and deleted_at is null",
        {"s": source_key},
    )


async def save_pull_state(*, source_key: str, cursor: str | None, ok: bool,
                          error: str | None) -> None:
    params = {"s": source_key, "id": str(uuid.uuid4()),
              "c": jsonb({"cursor": cursor}) if cursor is not None else None,
              "st": "ok" if ok else "failed", "err": (error or "")[:500] or None}
    update_sql = """
        update sre_alert_pull_state
        set cursor_json = coalesce(%(c)s::jsonb, cursor_json), last_pull_at=now(),
            last_ok_at = case when %(st)s='ok' then now() else last_ok_at end,
            last_status=%(st)s, last_error=%(err)s, last_update_date=now()
        where source_key=%(s)s and deleted_at is null
        """
    if await exec1(update_sql, params):
        return
    await exec1(
        """
        insert into sre_alert_pull_state (pull_state_id, source_key, cursor_json)
        select %(id)s, %(s)s, '{}'::jsonb
        where not exists (
          select 1 from sre_alert_pull_state where source_key=%(s)s and deleted_at is null
        )
        """,
        params,
    )
    await exec1(update_sql, params)
