/**
 * 产品介绍页文案数据（纯数据，面向使用者口径）。
 * 命名口径：主体叫「感知快恢 Agent」，OpenOps 仅页脚署名（26 号文档命名约定）。
 */

/* ------------------------- 三大核心能力 ------------------------- */
export interface Capability {
  icon: string;
  title: string;
  tag: string;
  desc: string;
}

export const capabilities: Capability[] = [
  {
    icon: "heart-rate-monitor",
    title: "巡检",
    tag: "感知",
    desc: "基于你的看护范围主动查看应用健康状态、异常信号和潜在风险，输出结构化健康报告——问题还没变成故障之前就被看见。",
  },
  {
    icon: "zoom-scan",
    title: "定界",
    tag: "诊断",
    desc: "围绕告警、指标、日志、链路、变更和资源拓扑交叉排查，判断问题边界与爆炸半径，给出带证据的根因假设——每一步推理都摊开给你看。",
  },
  {
    icon: "rocket",
    title: "恢复",
    tag: "快恢",
    desc: "基于受控的恢复手段（扩缩容、实例重启、限流等）给出恢复建议。所有恢复动作都需要你确认后才执行，绝不擅自碰生产。",
  },
];

/* ------------------------- 定界五步法 ------------------------- */
export interface Step {
  n: number;
  title: string;
  desc: string;
}

export const fiveSteps: Step[] = [
  { n: 1, title: "范围", desc: "锁定症状、时间窗与影响面" },
  { n: 2, title: "证据", desc: "并行收集指标、日志、告警、变更" },
  { n: 3, title: "假设", desc: "列出候选根因并给出置信度" },
  { n: 4, title: "验证", desc: "逐一验证假设，排除或加强" },
  { n: 5, title: "结论", desc: "给出根因与下一步行动建议" },
];

/* ------------------------- 子 Agent 能力矩阵 ------------------------- */
export interface AgentCard {
  icon: string;
  name: string;
  role: string;
  desc: string;
  ask: string; // 典型问法
  main?: boolean;
}

export const agentCards: AgentCard[] = [
  {
    icon: "topology-star-3",
    name: "主 Agent",
    role: "调度与结论",
    desc: "理解你的问题、拆解任务、指挥专业子 Agent 并行调查并汇总结论。自身掌握拓扑查询、爆炸半径分析、故障诊断五步法与应用健康巡检。",
    ask: "这个告警的影响范围有多大？",
    main: true,
  },
  {
    icon: "git-commit",
    name: "变更 Agent",
    role: "变更排查",
    desc: "查询关联时间窗内的变更记录，作为定界的参考线索——变更信息只作参考，不会武断地把变更当成根因。",
    ask: "支付应用最近两小时有没有变更？",
  },
  {
    icon: "file-text",
    name: "日志 Agent",
    role: "日志检索",
    desc: "检索与聚合应用日志：日志量趋势、异常日志分布、错误明细样本，一次查清后完整汇报。",
    ask: "查一下这个应用最近一小时的错误日志",
  },
  {
    icon: "chart-line",
    name: "指标 Agent",
    role: "指标取数",
    desc: "精准查询任务指定的监控指标（P99 时延、QPS、错误率等），只查你要的，不夹带无关数据。",
    ask: "看下网关 P99 时延这半小时的趋势",
  },
  {
    icon: "bell",
    name: "告警 Agent",
    role: "告警查询",
    desc: "查询当前与历史告警列表、告警详情，为定界提供第一手异常信号。",
    ask: "我名下现在有哪些未恢复的告警？",
  },
  {
    icon: "route",
    name: "APM Agent",
    role: "微服务排查",
    desc: "从微服务资源维度排查瓶颈，定位是哪个服务的资源出了问题。",
    ask: "支付服务的资源瓶颈在哪里？",
  },
  {
    icon: "database",
    name: "数据库 Agent",
    role: "数据库排查",
    desc: "排查数据库资源问题：慢查询、连接数、容量与负载异常。",
    ask: "数据库连接数为什么突然飙升？",
  },
  {
    icon: "bolt",
    name: "Redis Agent",
    role: "缓存排查",
    desc: "排查 Redis 资源问题：连接池、内存水位、命中率与热点异常。",
    ask: "Redis 命中率怎么突然掉了？",
  },
  {
    icon: "stack-2",
    name: "MQS Agent",
    role: "消息队列排查",
    desc: "排查消息队列问题：消息堆积、消费延迟、吞吐异常。",
    ask: "MQ 现在有没有消息堆积？",
  },
  {
    icon: "layout-dashboard",
    name: "Grafana 看板 Agent",
    role: "看板搭建",
    desc: "按你的需求一次成型地创建或更新 Grafana 看板，完成后交付可直接访问的看板链接；只增改、绝不删除已有看板。",
    ask: "帮我建一个支付应用的健康看板",
  },
];

/** 子 Agent 共同的工作纪律（用户利益视角） */
export const agentDiscipline: { icon: string; text: string }[] = [
  { icon: "eye", text: "查询类子 Agent 全部只读，调查过程不会改动任何线上配置" },
  { icon: "list-check", text: "全部步骤执行完才汇报结论，不会用半截结果打扰你" },
  { icon: "user-check", text: "缺少关键参数时会明确告诉你缺什么，绝不凭空编造" },
];

/* ------------------------- 使用教程 ------------------------- */
export interface TutorialStep {
  n: number;
  icon: string;
  title: string;
  desc: string;
}

export const onboardingSteps: TutorialStep[] = [
  {
    n: 1,
    icon: "wand",
    title: "选择模板",
    desc: "首次进入时选择「感知快恢 Agent」模板，卡片上能看到内置的巡检 / 定界 / 恢复能力。",
  },
  {
    n: 2,
    icon: "checkup-list",
    title: "配置 Agent",
    desc: "给 Agent 起个名字，选择使用的模型，再从应用树勾选你的「系统看护范围」——它只会在这个范围内工作。",
  },
  {
    n: 3,
    icon: "player-play",
    title: "激活进入工作台",
    desc: "等待工作空间就绪后进入对话工作台，像和同事聊天一样把问题交给它。",
  },
];

export interface AskWay {
  icon: string;
  title: string;
  desc: string;
}

export const askWays: AskWay[] = [
  {
    icon: "message-2",
    title: "自然语言直接问",
    desc: "不需要记命令。直接说「查一下支付应用的健康状态」，它自己决定派哪些子 Agent、查哪些数据。",
  },
  {
    icon: "slash",
    title: "「/」唤起技能",
    desc: "在输入框键入 / 可以唤起技能列表，显式指定要执行的技能，适合目标明确的老手。",
  },
  {
    icon: "shield-check",
    title: "该确认的一定会问你",
    desc: "不需要配置任何审批开关——平台按动作风险自动判定：只读查询直接执行，恢复类动作一律弹出审批卡等你确认。",
  },
];

/* ------------------------- 安全与可控 ------------------------- */
export interface SafetyPoint {
  icon: string;
  title: string;
  desc: string;
}

export const safetyPoints: SafetyPoint[] = [
  {
    icon: "lock-check",
    title: "恢复动作强制人工审批",
    desc: "重启、扩缩容、限流等一切会改变生产的动作，都会先弹出审批卡等你确认，超时未确认自动作废。",
  },
  {
    icon: "eye",
    title: "调查过程全程只读",
    desc: "告警、日志、指标、变更、链路等排查全部是只读查询，看得多、动得少，调查本身零风险。",
  },
  {
    icon: "target",
    title: "看护范围硬边界",
    desc: "Agent 只能访问你配置的看护范围内的应用，范围之外的系统它既看不到也碰不着。",
  },
  {
    icon: "list-check",
    title: "每一步可回看",
    desc: "每次调查的工具调用、子 Agent 汇报都记录在右侧时间线里，结论是怎么来的随时可追溯。",
  },
];
