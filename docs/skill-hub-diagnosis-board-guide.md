# 定界 SKILL.md 进度上报指引(交 Skill Hub 维护方)

> 配套 PR:[#26 定界五步法实时进度 + 定界卡片](https://github.com/Liny777/AegisOps/pull/26)。
> 本文是**给外部 Skill Hub 里「诊断定界」SKILL.md 的更新文案**:平台新增了展示工具
> `update_diagnosis_board`,模型按五步法执行时逐步调用,前端右侧「定界」面板据此实时
> 显示当前步骤,并在定界完成后定格为定界卡片。SKILL.md 正文写明调用指引后,模型漏调
> 概率最低(平台侧另有四层运行时注入兜底,见文末)。

---

## 一、即贴文案

### 1. 五步法总述处,新增一段:

> **进度上报(必须)**:执行本手册五步法时,每进入一个新步骤,立即调用平台工具
> `update_diagnosis_board`(step=步骤号),并把该步已取得的事实(facts)/假设
> (hypotheses)/证据源(sources)等一并增量提交;定界完成时以 `step=5、
> step_completed=true` 提交 `conclusion`(定界结论:影响边界、最可能根因方向、
> 建议下一步)。内容必须来自本轮真实取得的数据,不得虚构。

### 2. 每步小节末尾,各加一行:

| 步骤 | 小节末尾追加行 |
|---|---|
| 1 范围 | → 调用 `update_diagnosis_board(step=1, title=…, tiles=…, current_question=…)` 上报事件标题、症状/时间窗/影响面概览与当前关键问题 |
| 2 证据 | → 调用 `update_diagnosis_board(step=2, facts=…, sources=…)` 上报已确认事实与各证据源采集状态 |
| 3 假设 | → 调用 `update_diagnosis_board(step=3, hypotheses=…, unknowns=…)` 上报假设排行(含置信度)与未知待验证项 |
| 4 验证 | → 调用 `update_diagnosis_board(step=4, hypotheses=…, facts=…)` 上报验证后更新的置信度与新增事实 |
| 5 结论 | → 调用 `update_diagnosis_board(step=5, step_completed=true, conclusion=…, actions=…)` 收尾:定界结论 + 建议动作 |

---

## 二、工具参数速查(写手册示例时对照)

五步定义固定:**1=范围 2=证据 3=假设 4=验证 5=结论**。除 `step` 外全部可选、增量提交。

| 参数 | 类型/上限 | 说明 |
|---|---|---|
| `step` | int,1..5 | 当前所处步骤号(必填) |
| `step_completed` | bool | 当前步骤是否完成;`step=5` 且 `true` = 整个定界结束 |
| `title` | ≤80 字符 | 事件短标题(如「支付下单 P99 突增」),首次调用必填 |
| `tiles` | ≤6 项 `{label≤40, value≤120}` | 概览信息块(症状/时间窗/影响面等) |
| `current_question` | ≤200 | 当前正在回答的关键问题(一句话) |
| `why` | ≤300 | 为什么这个问题是当前关键(一句话) |
| `facts` | ≤20 项 `{text≤300}` | 已确认事实 |
| `unknowns` | ≤20 项 `{text≤300}` | 未知待验证项 |
| `sources` | ≤10 项 `{name≤60, status≤40, tone}` | 证据源状态;`tone` ∈ `good/warning/danger/neutral` |
| `hypotheses` | ≤8 项 `{text≤200, tag≤40, tagTone, conf}` | 假设排行;`conf` 为 0..1 置信度;`tagTone` 枚举同 tone |
| `actions` | ≤8 项 `{tier≤20, text≤200, confirm:bool, impact≤120, status≤40, statusTone}` | 建议动作;`confirm:true` 表示需人工批准 |
| `conclusion` | ≤1200 | 定界结论;`step=5` 收尾时**必填**(缺失会被拒收并要求补交) |

### 调用示例

```json
// 第 1 步 · 范围
{"step": 1, "title": "支付下单 P99 突增",
 "tiles": [{"label": "症状", "value": "P99 180ms→1.4s"},
           {"label": "时间窗", "value": "10:02 起 · 持续 9min"}],
 "current_question": "延迟突增的影响边界是哪些服务?"}

// 第 3 步 · 假设(增量:只提交本步新增内容,之前的 title/tiles 自动保留)
{"step": 3,
 "hypotheses": [{"text": "H1 Redis 连接泄漏", "tag": "支持", "tagTone": "good", "conf": 0.72},
                {"text": "H2 下游慢查询占用连接", "tag": "部分支持", "tagTone": "warning", "conf": 0.41}],
 "unknowns": [{"text": "连接是泄漏还是被慢查询长期占用"}]}

// 第 5 步 · 收尾
{"step": 5, "step_completed": true,
 "conclusion": "根因 H1(连接泄漏)已确认:重启 svc-a 后连接回落、P99 恢复。建议跟进连接回收配置。",
 "actions": [{"tier": "短期", "text": "对 Redis 连接加 max-idle 与超时回收",
              "impact": "配置", "status": "建议", "statusTone": "neutral"}]}
```

---

## 三、行为约定(手册里可摘要给模型)

- **增量合并**:未传的字段保留上次的值,传了的字段整体替换;每次只需提交新增或变化的内容。
- **步骤只进不退**:回退提交不报错,服务端钳制在已到步骤并在返回文本中注明;允许跳步(证据充分可快进)。
- **revision/状态由服务端管**:`revision`、`steps` 状态、`phaseLabel`、`status` 均由平台派生,模型提交会被拒收——手册不要指示模型传这些字段。
- **参数校验 deny-by-default**:未知键、HTML 尖括号/控制字符、超长/超量、`tone` 枚举外、`conf` 越界、`step` 越界都会被拒收,拒收信息为中文纠正文本,模型据此自纠重交即可。
- **漏调的后果**:界面定界面板保持空态(平台不伪造进度),定界过程对用户不可见——这是「必须上报」的原因。

---

## 四、平台侧现状(供 Hub 维护方了解,非 SKILL.md 内容)

- 平台已做四层运行时注入兜底:工具 docstring、平台规则(主/子 Agent 共享)、手册型 skill 返回文本追加指引、diagnose 角色 prompt。SKILL.md 正文写明是第五层,也是最贴近执行上下文、效果最好的一层。
- 工具免审批、只读、不产生对话噪音(不发 tool.call.* 事件),每步一次调用、全程 ≤6 次,对迭代预算影响极小。
- 存量环境注意:diagnose 子 Agent 角色 prompt 的指引仅对新库 seed 生效,存量环境需管理员在模板编辑器同步一次(平台规则层已运行时保底生效)。
