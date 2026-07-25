/** Agent Studio 切片的 mock 数据（原在 lib/api/mockData.ts，随切片外移）。
 *  围绕「支付域延迟突增」这条演示故事，与 core mockData 的会话/活动数据同源同调。 */
import type { StudioMessage, StudioRunDetail, StudioRunsPage } from "./types";

export const mockStudioRuns = (userId: string): StudioRunsPage => ({
  items: [
    {
      agent_run_id: "run_mock_pay_latency",
      run_title: "支付域延迟突增排查",
      run_status: "active",
      started_at: "2026-07-23T10:02:11+08:00",
      ended_at: null,
      deleted: false,
      framework_session_id: "agentscope-session-mock1",
      stats: { agents: 3, llm_calls: 7, tool_calls: 5, total_tokens: 48213, latency_ms: 93210 },
    },
    {
      agent_run_id: "run_mock_disk_alarm",
      run_title: "磁盘告警误报确认",
      run_status: "closed",
      started_at: "2026-07-22T15:40:03+08:00",
      ended_at: "2026-07-22T15:52:47+08:00",
      deleted: false,
      framework_session_id: "agentscope-session-mock2",
      stats: { agents: 1, llm_calls: 3, tool_calls: 2, total_tokens: 9800, latency_ms: 21050 },
    },
    {
      agent_run_id: "run_mock_deleted",
      run_title: `历史会话（${userId} 已删除）`,
      run_status: "closed",
      started_at: "2026-07-20T09:10:00+08:00",
      ended_at: "2026-07-20T09:31:00+08:00",
      deleted: true,
      framework_session_id: "agentscope-session-mock3",
      stats: { agents: 2, llm_calls: 4, tool_calls: 3, total_tokens: 15600, latency_ms: 40400 },
    },
  ],
  total: 3,
  page: 1,
  page_size: 20,
});

export const mockStudioRunDetail = (runId: string): StudioRunDetail => ({
  run: {
    agent_run_id: runId,
    user_id: "0026demo01",
    run_title: "支付域延迟突增排查",
    run_status: "closed",
    started_at: "2026-07-23T10:02:11+08:00",
    ended_at: "2026-07-23T10:18:44+08:00",
    deleted: false,
    framework_session_id: "agentscope-session-mock1",
  },
  agents: [
    {
      session_id: "agentscope-session-mock1",
      role: "main",
      agent_name: "sre-rca",
      is_main: true,
      agent_io: {
        input: "支付核心交易 10:00 起 P99 延迟从 180ms 涨到 2.4s，帮我定位原因。",
        output: "结论：支付网关 10:00 发布后连接池配置回退（max=8），下游订单履约超时级联。已建议回滚配置并扩容连接池。",
      },
      handover: null,
      calls: [
        {
          kind: "llm", model: "glm-5.1", provider: "zhipu", input_tokens: 1832, output_tokens: 412,
          cache_input_tokens: 0, latency_ms: 2960, started_at: "2026-07-23T10:02:14+08:00",
          finish_reason: "tool_calls",
          input: '[{"role":"system","content":"你是 SRE 值班专家…"},{"role":"user","content":"支付核心交易 10:00 起 P99 延迟从 180ms 涨到 2.4s…"}]',
          output: '[{"role":"assistant","parts":[{"type":"tool_call","name":"dispatch_subagents","arguments":{"agents":["inspect","diagnose"]}}]}]',
          status: "OK",
        },
        {
          kind: "tool", tool_name: "dispatch_subagents",
          tool_args: '{"dispatches":[{"agent":"inspect","task":"拉取支付核心 09:50-10:10 指标与告警"},{"agent":"diagnose","task":"结合变更单分析 10:00 前后的发布"}]}',
          tool_result: "2 个子 Agent 已完成并汇报（见各自轨迹）",
          latency_ms: 68210, started_at: "2026-07-23T10:02:18+08:00", status: "OK",
        },
        {
          kind: "llm", model: "glm-5.1", provider: "zhipu", input_tokens: 5210, output_tokens: 640,
          cache_input_tokens: 1832, latency_ms: 4120, started_at: "2026-07-23T10:16:40+08:00",
          finish_reason: "stop",
          input: '[…历史消息 + 子 Agent 汇报…]',
          output: '[{"role":"assistant","parts":[{"type":"text","content":"结论：支付网关连接池配置回退…建议回滚"}]}]',
          status: "OK",
        },
      ],
      totals: { llm_calls: 2, tool_calls: 1, input_tokens: 7042, output_tokens: 1052, total_tokens: 8094, latency_ms: 75290 },
    },
    {
      session_id: "agentscope-session-mock1:inspect-a1b2c3d4",
      role: "inspect",
      agent_name: "sre-inspect",
      is_main: false,
      agent_io: {
        input: "拉取支付核心 09:50-10:10 指标与告警",
        output: "P99 从 10:00:30 陡增；网关活跃连接打满 8/8；伴随 502 告警 12 条。",
      },
      handover: {
        delegation_id: "a1b2c3d4-0000-0000-0000-000000000001",
        agent_key: "inspect",
        task_text: "拉取支付核心 09:50-10:10 指标与告警",
        report_text: "P99 从 10:00:30 陡增；网关活跃连接打满 8/8；伴随 502 告警 12 条。",
        delegation_status: "completed",
        dispatch_batch_no: 1,
        had_final_report: true,
        creation_date: "2026-07-23T10:02:18+08:00",
      },
      calls: [
        {
          kind: "llm", model: "qwen-max", provider: "dashscope", input_tokens: 980, output_tokens: 210,
          cache_input_tokens: 0, latency_ms: 1890, started_at: "2026-07-23T10:02:21+08:00",
          finish_reason: "tool_calls",
          input: '[{"role":"user","content":"拉取支付核心 09:50-10:10 指标与告警"}]',
          output: '[{"role":"assistant","parts":[{"type":"tool_call","name":"query_metrics","arguments":{"app":"支付核心交易","range":"09:50-10:10"}}]}]',
          status: "OK",
        },
        {
          kind: "tool", tool_name: "query_metrics",
          tool_args: '{"app":"支付核心交易","range":"09:50-10:10","metrics":["p99","conn_pool"]}',
          tool_result: '{"p99":[["10:00:00",182],["10:00:30",2410]],"conn_pool":"8/8 exhausted"}',
          latency_ms: 3210, started_at: "2026-07-23T10:02:24+08:00", status: "OK",
        },
      ],
      totals: { llm_calls: 1, tool_calls: 1, input_tokens: 980, output_tokens: 210, total_tokens: 1190, latency_ms: 5100 },
    },
    {
      session_id: "agentscope-session-mock1:diagnose-e5f6a7b8",
      role: "diagnose",
      agent_name: "sre-diagnose",
      is_main: false,
      agent_io: null,
      handover: {
        delegation_id: "e5f6a7b8-0000-0000-0000-000000000002",
        agent_key: "diagnose",
        task_text: "结合变更单分析 10:00 前后的发布",
        report_text: "10:00 支付网关 v2026.7.23-1 发布，diff 显示连接池 max 从 64 回退为 8（配置模板覆盖）。",
        delegation_status: "completed",
        dispatch_batch_no: 1,
        had_final_report: true,
        creation_date: "2026-07-23T10:02:18+08:00",
      },
      calls: [
        {
          kind: "tool", tool_name: "mcp.change_orders.search",
          tool_args: '{"app":"支付网关","window":"09:30-10:10"}',
          tool_result: '[{"change_id":"CHG-88121","published_at":"10:00:02","diff_summary":"conn_pool.max 64→8"}]',
          latency_ms: 2870, started_at: "2026-07-23T10:02:30+08:00", status: "OK",
        },
        {
          kind: "llm", model: "glm-5.1", provider: "zhipu", input_tokens: 1460, output_tokens: 268,
          cache_input_tokens: 0, latency_ms: 2230, started_at: "2026-07-23T10:02:35+08:00",
          finish_reason: "stop",
          input: '[{"role":"user","content":"结合变更单分析 10:00 前后的发布"},{"role":"tool","content":"CHG-88121…"}]',
          output: '[{"role":"assistant","parts":[{"type":"text","content":"发布 CHG-88121 将连接池 max 回退为 8，与延迟陡增时间吻合"}]}]',
          status: "OK",
        },
      ],
      totals: { llm_calls: 1, tool_calls: 1, input_tokens: 1460, output_tokens: 268, total_tokens: 1728, latency_ms: 5100 },
    },
  ],
  rollup: {
    agents: 3, llm_calls: 4, tool_calls: 3,
    input_tokens: 9482, output_tokens: 1530, total_tokens: 11012,
    total_latency_ms: 85490, models: ["glm-5.1", "qwen-max"],
  },
  delegations: [
    {
      delegation_id: "a1b2c3d4-0000-0000-0000-000000000001", agent_key: "inspect",
      task_text: "拉取支付核心 09:50-10:10 指标与告警",
      report_text: "P99 从 10:00:30 陡增；网关活跃连接打满 8/8；伴随 502 告警 12 条。",
      delegation_status: "completed", dispatch_batch_no: 1, had_final_report: true,
      creation_date: "2026-07-23T10:02:18+08:00",
    },
    {
      delegation_id: "e5f6a7b8-0000-0000-0000-000000000002", agent_key: "diagnose",
      task_text: "结合变更单分析 10:00 前后的发布",
      report_text: "10:00 支付网关 v2026.7.23-1 发布，diff 显示连接池 max 从 64 回退为 8（配置模板覆盖）。",
      delegation_status: "completed", dispatch_batch_no: 1, had_final_report: true,
      creation_date: "2026-07-23T10:02:18+08:00",
    },
  ],
});

export const mockStudioRunMessages = (runId: string): StudioMessage[] => [
  { id: `msg-${runId}-1`, role: "user", content: "支付核心交易 10:00 起 P99 延迟从 180ms 涨到 2.4s，帮我定位原因。" },
  {
    id: `msg-${runId}-2`, role: "assistant",
    content: "收到。我先拉取 09:50-10:10 的指标与告警，并核对同时段的变更单，已派发巡检与诊断两个子 Agent 并行排查。",
  },
  { id: `msg-${runId}-3`, role: "user", content: "订单履约那边也在报超时，是不是被拖累的？" },
  {
    id: `msg-${runId}-4`, role: "assistant",
    content:
      "根因分析结论：\n\n1. 10:00:02 支付网关发布 CHG-88121，配置模板覆盖导致连接池 max 从 64 回退为 8；\n2. 10:00:30 起活跃连接打满（8/8），P99 从 182ms 陡增至 2.4s，伴随 502 告警 12 条；\n3. 订单履约的超时是下游级联（对支付网关的调用排队），不是独立故障。\n\n建议：回滚 CHG-88121 或将 conn_pool.max 恢复 64 并观察 10 分钟；已在恢复建议中生成对应工单草稿，需要审批后执行。",
  },
];
