// 空会话预设问题（点击写入 composer，不自动发送）。
//
// 前三条对应平台内置的 Agent 工具：query_alarm_detail(alarm_code) /
// query_alarm_list(project_id) / 本地 list_scope_apps。第四条「对象和关系」平台不内置
// 对应工具——需用户环境注册拓扑/CMDB 类 MCP server（经 oModel 动态发现）才有真答案，
// 否则模型据实说明「暂无该工具」。这里只管文案与占位符定位，写入由 composerBridge 负责。

/** 问题 1 里待用户替换的占位符；填入后被选中，下一次击键即覆盖。 */
export const PRESET_VARIABLE = "告警编码";

export type PresetQuestion = { id: string; text: string };

export const PRESET_QUESTIONS: readonly PresetQuestion[] = [
  { id: "diagnose-alarm", text: `帮我诊断 ${PRESET_VARIABLE} 告警` },
  { id: "my-alarms", text: "我有哪些告警？" },
  { id: "scope-apps", text: "我看护的范围有哪些应用？" },
  { id: "object-relations", text: "我应用下有哪些对象和关系？" },
];

/**
 * 命中占位符则选中它，否则光标落末尾（空选区）。
 *
 * 用 indexOf 现算而不是在 PRESET_QUESTIONS 里存 [start, end]：手数的偏移量在改文案时
 * 会无声腐烂。全部字符在 BMP 内，indexOf 与 setSelectionRange 同按 UTF-16 计，对得上。
 */
export function presetSelection(text: string, variable = PRESET_VARIABLE): [number, number] {
  const start = text.indexOf(variable);
  return start < 0 ? [text.length, text.length] : [start, start + variable.length];
}
