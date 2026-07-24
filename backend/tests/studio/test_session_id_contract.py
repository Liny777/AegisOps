"""子 Agent session_id 的跨模块契约测试（core ↔ studio 之间唯一的非类型化协议）。

**为什么这条测试比切片的包结构更值钱**：
子 Agent 的 `AgentState.session_id` 由 core（`runtime.subagent_dispatch.sub_session_id`）
生产，经 agentscope 当作 `gen_ai.conversation_id` 发进 OTel span，落到
`sre_agent_studio_span.session_id`；Studio 靠**反解它**把 span 归组到派发账本行
（主↔子交接内容）。

这个链路断掉时**没有任何信号**：studio 侧解析不出就返回 None，表现是
「子 Agent 卡的交接内容空着，其余一切正常」——无异常、无日志、无告警，
而 core 侧的测试全绿（core 根本不知道有人在消费这个字符串）。

本文件用**生产方的真实产出**喂**消费方的真实解析**，让 core 改格式时这里必红。
看到它红 = studio 侧需要同步改，不要只改 core 那一侧。
"""
from __future__ import annotations

import uuid

from runtime.subagent_dispatch import parse_sub_session_id, sub_session_id
from studio.service import _match_handover


def test_producer_output_parses_back():
    """生产 → 消费 往返一致：这是契约的核心断言。"""
    fsid = "agentscope-session-ab12cd34"
    did = str(uuid.uuid4())
    sid = sub_session_id(fsid, "inspect", did)

    parsed = parse_sub_session_id(sid)
    assert parsed is not None, f"消费方无法反解生产方产出：{sid}"
    agent_key, did8 = parsed
    assert agent_key == "inspect"
    assert did8 == did[:8]


def test_end_to_end_handover_match():
    """端到端：生产方的 session_id 能在 studio 的 _match_handover 里命中账本行。

    这一条覆盖的是真实调用形态——`_group_spans` 就是这样把子卡片接上交接内容的。
    """
    fsid = "agentscope-session-ab12cd34"
    did = str(uuid.uuid4())
    sid = sub_session_id(fsid, "diagnose", did)
    handovers = [
        {"agent_key": "inspect", "delegation_id": str(uuid.uuid4()), "task_text": "别的角色"},
        {"agent_key": "diagnose", "delegation_id": did, "task_text": "结合变更单分析"},
    ]

    hit = _match_handover(sid, handovers)
    assert hit is not None, "子 session_id 未能匹配到派发账本行——契约已断"
    assert hit["task_text"] == "结合变更单分析"


def test_main_session_id_is_not_a_sub_session():
    """主 Agent 的 session_id 就是裸 framework_session_id，反解必须返回 None（属正常路径）。

    注意 framework_session_id 形如 `agentscope-session-<hex8>`，**自身含 `-`**——
    这正是「用 `:` 分隔、再对后缀 rsplit('-')」的原因。若哪天改成用 `-` 分隔，
    主 session 会被误判成子 session，本条会红。
    """
    assert parse_sub_session_id("agentscope-session-ab12cd34") is None
    assert _match_handover("agentscope-session-ab12cd34", [{"agent_key": "x", "delegation_id": "y"}]) is None


def test_agent_key_containing_hyphen_round_trips():
    """agent_key 里带 `-`（如 log-agent）也必须往返正确。

    反解用的是 `rsplit("-", 1)`（从**右**切一刀），所以 agent_key 内的 `-` 安全；
    若有人改成 `split("-", 1)` 就会把 log-agent 截成 log，本条会红。
    """
    did = str(uuid.uuid4())
    sid = sub_session_id("agentscope-session-ff00ff00", "log-agent", did)
    parsed = parse_sub_session_id(sid)
    assert parsed == ("log-agent", did[:8])


def test_sub_task_id_and_session_id_share_the_same_suffix():
    """子 task_id 与子 session_id 共用 `{agent_key}-{did8}` 后缀——两者必须同步演进。

    子 task_id 用于 registry 路由/审批，子 session_id 用于 span 归组；
    它们同源同格式，改一个忘了另一个会造成「审批能路由但 Studio 归不上组」这类割裂。
    """
    from runtime.subagent_dispatch import _sub_task_id

    did = str(uuid.uuid4())
    task_id = _sub_task_id("tsk_abc123", "recover", did)
    session_id = sub_session_id("agentscope-session-ab12cd34", "recover", did)

    assert task_id.endswith(f"recover-{did[:8]}")
    assert session_id.endswith(f"recover-{did[:8]}")
