"""RCA 卡演示内容（B1 脚本化/stub 阶段）：mock orchestrator 与 stub AgentScope runtime 共用，
保证两条后端产出的 `openops.rca.updated` 卡片结构完全一致（前端 RcaCard 直接渲染）。

B2 真 GLM 接入后，真 runtime 改由模型输出构造 RCA payload，本模块只留 mock/demo 用。
"""
from __future__ import annotations

from typing import Any


def rca(revision: int, phase: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "revision": revision,
        "title": "支付延迟突增",
        "phaseLabel": phase,
        # status 与真流程（rca_board 派生）同形：revision 3 = demo 剧本「已闭环」
        "status": "concluded" if revision >= 3 else "in_progress",
        "tiles": [
            {"label": "症状", "value": "下单 P99 180ms→1.4s"},
            {"label": "时间窗", "value": "10:02 起 · 持续 9min"},
            {"label": "影响面", "value": "APP-A 支付下单 · 0.6% 错误"},
            {"label": "当前阶段", "value": phase},
        ],
        "steps": [
            {"num": 1, "label": "范围", "state": "done"},
            {"num": 2, "label": "证据", "state": "done" if revision >= 2 else "active"},
            {"num": 3, "label": "假设", "state": "done" if revision >= 2 else "waiting"},
            {"num": 4, "label": "验证", "state": "done" if revision >= 3 else ("active" if revision >= 2 else "waiting")},
            {"num": 5, "label": "结论", "state": "done" if revision >= 3 else "waiting"},
        ],
        "currentQ": "Redis 连接饱和是慢查询导致，还是连接泄漏导致？",
        "why": "两者恢复动作不同：慢查询→限流/优化，连接泄漏→重启释放。",
        "facts": [
            {"text": "10:02 P99 由 180ms 升至 1.4s，错误率 0.6%"},
            {"text": "svc-payment-api → Redis active 连接打满（1000/1000）"},
        ],
        "unknowns": [{"text": "连接是否泄漏还是被慢查询长期占用"}],
        "sources": [
            {"name": "Prometheus", "status": "done", "tone": "good"},
            {"name": "Loki", "status": "running" if revision < 2 else "done", "tone": "warning" if revision < 2 else "good"},
            {"name": "oModel 拓扑", "status": "done", "tone": "good"},
        ],
        "hypotheses": [
            {"text": "H1 Redis 连接泄漏（svc-a 未释放）", "tag": "支持", "tagTone": "good", "conf": 0.72 if revision < 3 else 0.9},
            {"text": "H2 下游慢查询占用连接", "tag": "部分支持", "tagTone": "warning", "conf": 0.41},
        ],
        "actions": [
            {"tier": "立即", "text": "重启 svc-payment-api 实例 svc-a 释放连接", "confirm": True, "impact": "3 实例",
             "status": "待确认" if revision < 3 else "已执行", "statusTone": "warning" if revision < 3 else "good"},
            {"tier": "短期", "text": "对 Redis 连接加 max-idle 与超时回收", "impact": "配置", "status": "建议", "statusTone": "neutral"},
        ],
        "conclusion": "根因倾向 H1（连接泄漏）：重启 svc-a 后确认连接回落即可定论。"
        if revision < 3 else "已确认 H1：重启 svc-a 后连接数回落、P99 恢复 210ms，事件闭环。",
    }
    base.update(extra or {})
    return base
