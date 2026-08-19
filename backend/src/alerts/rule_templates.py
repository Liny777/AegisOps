"""快速配置的内置监控策略模板 + 默认诊断提示词（单一事实源：前端 GET /alerts/rule-templates 也吃这份）。

v2 需求口径：策略类型本期仅 MySQL / PostgreSQL / Docker 三类（各 3 条，与原型逐字对齐）；
告警级别 UI 仅 致命/严重/普通 三档（info 保留在 schema 不出 UI）。
模板 name 即告警平台的「监控策略名」（契约 strategy_name 字段的取值域），
配置弹窗按所选类型列出供勾选（默认全选），落库进 rule.match_json.strategies。
"""
from __future__ import annotations

from typing import Any

RULE_TEMPLATES: list[dict[str, Any]] = [
    # MySQL
    {"category": "MySQL", "name": "MySQL 资源饱和标准监控", "description": "监控CPU/内存/连接数",
     "keywords": ["CPU", "内存", "连接"]},
    {"category": "MySQL", "name": "MySQL 主从延迟监控", "description": "检测主从同步延迟",
     "keywords": ["主从", "延迟"]},
    {"category": "MySQL", "name": "MySQL 慢查询监控", "description": "检测慢查询数量",
     "keywords": ["慢查询"]},
    # PostgreSQL
    {"category": "PostgreSQL", "name": "PostgreSQL 事务锁等待监控", "description": "检测锁等待事件",
     "keywords": ["锁等待", "lock"]},
    {"category": "PostgreSQL", "name": "PostgreSQL 连接池耗尽监控", "description": "检测连接池使用率",
     "keywords": ["连接池"]},
    {"category": "PostgreSQL", "name": "PostgreSQL 死锁检测", "description": "检测死锁事件",
     "keywords": ["死锁", "deadlock"]},
    # Docker
    {"category": "Docker", "name": "Docker 容器重启监控", "description": "检测容器异常重启",
     "keywords": ["重启", "restart"]},
    {"category": "Docker", "name": "Docker 资源限制监控", "description": "监控CPU/内存限制",
     "keywords": ["资源", "limit"]},
    {"category": "Docker", "name": "Docker 健康检查监控", "description": "检测健康检查失败",
     "keywords": ["健康检查", "health"]},
]

# 配置弹窗「默认提示词」（用户可改，存 rule.prompt；空=派发时用这份）。
# 「关键结论写入对话」条款同时服务共享沙箱方案的文件不连续缓解（设计 §八 难点⑤）。
DEFAULT_RULE_PROMPT = """请按五步法完成本次告警诊断：
1. 告警确认：调取告警详情与关联指标，确认告警真实性与当前状态；
2. 影响面分析：定位受影响的应用（APPID）、实例与业务范围；
3. 根因定界：结合指标/日志/链路/变更记录，给出根因假设并逐一验证；
4. 恢复建议：给出可执行的恢复方案（需要执行恢复动作时走审批流程）；
5. 复盘要点：沉淀本次告警的规律与预防建议。
要求：以只读诊断为主；过程结论同步到诊断面板；诊断收尾（update_diagnosis_board 的 step=5）时随 conclusion 一并提交 verdict——故障已恢复/已自愈填 recovered，需人工介入处理填 escalated，无法判定才留空（该值显示为告警清单的「接管结果」）；关键结论与证据写入对话正文（不要只落沙箱文件）；信息不足以定界时，明确说明缺失的数据与建议的人工检查项。"""
