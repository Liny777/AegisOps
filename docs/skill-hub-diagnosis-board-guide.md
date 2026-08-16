# 诊断 SKILL.md 进度上报指引(交 Skill Hub 维护方)

> 配套 PR:[#26 定界五步法实时进度 + 定界卡片](https://github.com/Liny777/AegisOps/pull/26)(本轮迭代已把用户可见口径统一为「诊断」,并新增 `step_summary` 每步小结)。
> 本文是**给外部 Skill Hub 里「诊断」SKILL.md 的更新文案**:平台新增了展示工具
> `update_diagnosis_board`,模型按五步法执行时逐步调用,前端右侧「诊断」面板据此实时
> 显示当前步骤,并在诊断完成后定格为诊断卡片。SKILL.md 正文写明调用指引后,模型漏调
> 概率最低(平台侧另有四层运行时注入兜底,见文末)。

---

## 一、即贴文案

### 1. 五步法总述处,新增一段:

> **进度上报(必须)**:执行本手册五步法时,每进入一个新步骤,立即调用平台工具
> `update_diagnosis_board`(step=步骤号),并把该步已取得的事实(facts)/假设
> (hypotheses)/证据源(sources)等一并增量提交;每次调用都附带 `step_summary`
> (≤120 字)对当前步进展做一句话小结,界面在步骤收起时以该小结展示;诊断完成时以
> `step=5、step_completed=true` 提交 `conclusion`(诊断结论:影响边界、最可能根因
> 方向、建议下一步)。内容必须来自本轮真实取得的数据,不得虚构。

### 2. 每步小节末尾,各加一行:

| 步骤 | 小节末尾追加行 |
|---|---|
| 1 范围 | → 调用 `update_diagnosis_board(step=1, title=…, tiles=…, current_question=…, step_summary=…)` 上报事件标题、症状/时间窗/影响面概览与当前关键问题,附带 step_summary 一句话小结 |
| 2 证据 | → 调用 `update_diagnosis_board(step=2, facts=…, sources=…, step_summary=…)` 上报已确认事实与各证据源采集状态,附带 step_summary 一句话小结 |
| 3 假设 | → 调用 `update_diagnosis_board(step=3, hypotheses=…, unknowns=…, step_summary=…)` 上报假设排行(含置信度)与未知待验证项,附带 step_summary 一句话小结 |
| 4 验证 | → 调用 `update_diagnosis_board(step=4, hypotheses=…, facts=…, step_summary=…)` 上报验证后更新的置信度与新增事实,附带 step_summary 一句话小结 |
| 5 结论 | → 调用 `update_diagnosis_board(step=5, step_completed=true, conclusion=…, actions=…, step_summary=…)` 收尾:诊断结论 + 建议动作,附带 step_summary 一句话小结 |

---

## 二、工具参数速查(写手册示例时对照)

五步定义固定:**1=范围 2=证据 3=假设 4=验证 5=结论**。除 `step` 外全部可选、增量提交。

| 参数 | 类型/上限 | 说明 |
|---|---|---|
| `step` | int,1..5 | 当前所处步骤号(必填) |
| `step_completed` | bool | 当前步骤是否完成;`step=5` 且 `true` = 整个诊断结束 |
| `step_summary` | ≤120 字符 | 当前步骤的一句话小结;每次推进或完成一步时提交,界面在步骤收起时展示 |
| `title` | ≤80 字符 | 事件短标题(如「支付下单 P99 突增」),首次调用必填 |
| `tiles` | ≤6 项 `{label≤40, value≤120}` | 概览信息块(症状/时间窗/影响面等) |
| `current_question` | ≤200 | 当前正在回答的关键问题(一句话) |
| `why` | ≤300 | 为什么这个问题是当前关键(一句话) |
| `facts` | ≤20 项 `{text≤300}` | 已确认事实 |
| `unknowns` | ≤20 项 `{text≤300}` | 未知待验证项 |
| `sources` | ≤10 项 `{name≤60, status≤40, tone}` | 证据源状态;`tone` ∈ `good/warning/danger/neutral` |
| `hypotheses` | ≤8 项 `{text≤200, tag≤40, tagTone, conf}` | 假设排行;`conf` 为 0..1 置信度;`tagTone` 枚举同 tone |
| `actions` | ≤8 项 `{tier≤20, text≤200, confirm:bool, impact≤120, status≤40, statusTone}` | 建议动作;`confirm:true` 表示需人工批准 |
| `conclusion` | ≤1200 | 诊断结论;`step=5` 收尾时**必填**(缺失会被拒收并要求补交) |

### 调用示例

```json
// 第 1 步 · 范围
{"step": 1, "title": "支付下单 P99 突增",
 "tiles": [{"label": "症状", "value": "P99 180ms→1.4s"},
           {"label": "时间窗", "value": "10:02 起 · 持续 9min"}],
 "current_question": "延迟突增的影响边界是哪些服务?",
 "step_summary": "范围锁定:支付下单链路,10:02 起 P99 180ms→1.4s"}

// 第 3 步 · 假设(增量:只提交本步新增内容,之前的 title/tiles 自动保留)
{"step": 3,
 "hypotheses": [{"text": "H1 Redis 连接泄漏", "tag": "支持", "tagTone": "good", "conf": 0.72},
                {"text": "H2 下游慢查询占用连接", "tag": "部分支持", "tagTone": "warning", "conf": 0.41}],
 "unknowns": [{"text": "连接是泄漏还是被慢查询长期占用"}],
 "step_summary": "假设排行:H1 连接泄漏领先(conf 0.72)"}

// 第 5 步 · 收尾
{"step": 5, "step_completed": true,
 "conclusion": "根因 H1(连接泄漏)已确认:重启 svc-a 后连接回落、P99 恢复。建议跟进连接回收配置。",
 "actions": [{"tier": "短期", "text": "对 Redis 连接加 max-idle 与超时回收",
              "impact": "配置", "status": "建议", "statusTone": "neutral"}],
 "step_summary": "已确认 H1 连接泄漏,恢复动作执行完毕、事件闭环"}
```

---

## 三、行为约定(手册里可摘要给模型)

- **增量合并**:未传的字段保留上次的值,传了的字段整体替换;每次只需提交新增或变化的内容。
- **step_summary 按步累积**:每步的小结按提交的 `step` 归档到对应步骤并一直保留;重复提交同一步会覆盖该步旧小结。
- **步骤只进不退,且禁止跳步**:回退提交不报错,服务端钳制在已到步骤并在返回文本中注明;服务端**不阻止**跳步,但跳步违反手册——必须 1→2→3→4→5 每步各上报一次(详见第五节)。
- **子 Agent 只能提内容**:被派发的 Sub-Agent 提交的 `step` 推进、`step_completed`、`conclusion`、`step_summary` 会被服务端忽略(返回文本注明),只有 `facts`/`sources`/`hypotheses` 等内容照常合并;步骤推进与诊断收尾只认主任务(详见第五节)。
- **revision/状态由服务端管**:`revision`、`steps` 状态、`phaseLabel`、`status` 均由平台派生,模型提交会被拒收——手册不要指示模型传这些字段。
- **参数校验 deny-by-default**:未知键、HTML 尖括号/控制字符、超长/超量、`tone` 枚举外、`conf` 越界、`step` 越界都会被拒收,拒收信息为中文纠正文本,模型据此自纠重交即可。
- **漏调的后果**:界面诊断面板保持空态(平台不伪造进度),诊断过程对用户不可见——这是「必须上报」的原因。

---

## 四、平台侧现状(供 Hub 维护方了解,非 SKILL.md 内容)

- 平台已做四层运行时注入兜底:工具 docstring、平台规则(主/子 Agent **各一版**)、手册型 skill 返回文本追加指引、diagnose 角色 prompt。SKILL.md 正文写明是第五层,也是最贴近执行上下文、效果最好的一层。
- 工具免审批、只读、不产生对话噪音(不发 tool.call.* 事件),每步一次调用、全程 ≤6 次,对迭代预算影响极小。
- 存量环境注意:diagnose 子 Agent 角色 prompt 的指引仅对新库 seed 生效,存量环境需管理员在模板编辑器同步一次(平台规则层已运行时保底生效)。

---

## 五、本轮修订(交同事同步到 Skill Hub 的 SKILL.md)

> **背景**:内网反馈「调查时间线经常走到第2步(证据)就直接生成完整卡片、跳到根因报告」。定位到两条成因:
> ① 并行派发的 Sub-Agent 也能调 `update_diagnosis_board`,它一报 `step=5, step_completed=true` 就把
> run 级单例面板一步打成「诊断完成」(旧文案还明确教它这么做);② 跳步在各层文案里被显式允许。
> 平台侧已修:服务端硬收窄子任务权限(只并内容)、主/子平台规则拆两版、工具 docstring 与手册指引补「不得跳步」。
> **SKILL.md 侧需同步下列 6 处**,这是最贴近执行上下文的一层,不改则主 Agent 仍会「合规地」跳步。

以下 `原文` / `改为` 均为**未转义的正常 Markdown**,按小节定位即可。

### 5.1 「关键原则」列表末尾 —— 新增第 9 条

在现有第 8 条(**进度上报(必须)**)之后新增一条,原第 8 条不动:

```markdown
9. **逐步推进,禁止跳步(强制)**:`update_diagnosis_board` 必须按 1→2→3→4→5 逐步上报,**每步各调用一次、step 相对上次只 +1**。禁止把假设(3)、验证(4)的产出并进一次 `step=5` 提交收尾;也禁止在证据(2)阶段直接跳到结论。用户界面的调查时间线就是靠这条推进链呈现推理过程——跳步等于把假设与验证过程对用户隐藏,即使证据已高度指向某个原因也不例外。
```

### 5.2 「强制约束 → 进度上报约束」 —— 改 1 条 + 增 1 条

原文:

```markdown
  - **步骤只进不退**:回退提交不报错,服务端钳制在已到步骤;允许跳步(证据充分可快进)
```

改为(**这句是当前跳步行为的直接授权来源,必须改**):

```markdown
  - **步骤只进不退,且禁止跳步**:回退提交不报错,服务端钳制在已到步骤;服务端**不阻止**跳步,但跳步违反本手册(见关键原则 9)——必须 1→2→3→4→5 每步各上报一次,不得把中间步骤的产出并进收尾提交
  - **子 Agent 不得推进步骤或收尾**:被派发的 Sub-Agent 只提交本轮取得的 `facts`/`sources`/`hypotheses`,其提交的 `step` 推进、`step_completed`、`conclusion` 会被平台忽略;步骤推进与诊断收尾一律由主Agent提交
```

### 5.3 「Sub-Agent 能力域」表下方说明块 —— 新增一段

在 `> **变更Agent说明**:...` 那段**之前**插入(同为引用块 `>` 层级):

```markdown
> **进度上报归属**:`update_diagnosis_board` 的**步骤推进与收尾只由主Agent执行**。Sub-Agent 若调用该工具,只能提交本轮取得的 `facts`/`sources`/`hypotheses`;它提交的 `step` 推进、`step_completed`、`conclusion` 会被平台忽略(否则任一并行子Agent「我这块查完了」就会把整个诊断面板一步打成完成态)。
```

### 5.4 Step 3(假设)的「步骤完成检查清单」 —— 拆 1 条为 2 条

原文最后一条:

```markdown
- [ ] `update_diagnosis_board` 已调用
```

改为:

```markdown
- [ ] 上一步(证据)的 `update_diagnosis_board`(step=2) 已调用过——未调用说明发生了跳步,先补交
- [ ] `update_diagnosis_board`(step=3) 已调用(本步单独一次,不与其他步合并提交)
```

### 5.5 Step 4(验证)的「步骤完成检查清单」 —— 同上

原文最后一条:

```markdown
- [ ] `update_diagnosis_board` 已调用
```

改为:

```markdown
- [ ] 上一步(假设)的 `update_diagnosis_board`(step=3) 已调用过——未调用说明发生了跳步,先补交
- [ ] `update_diagnosis_board`(step=4) 已调用(本步单独一次;结论的 `step=5` 必须是**另一次**调用)
```

> Step 1 / Step 2 / Step 5 的检查清单不动。

### 5.6 Step 5(结论)小节开头 —— 新增「前置条件(强制)」

插在 `**输入**:scope.json + evidence.json + hypotheses.json + test_results.json` 之后、
`**执行逻辑**:` 之前:

```markdown
**前置条件(强制)**:进入本步之前,必须同时满足以下三条,否则**不得**输出RCA报告,必须先回到对应步骤补做:
- [ ] 假设(Step 3)已完成,并已单独调用过 `update_diagnosis_board`(step=3)
- [ ] 验证(Step 4)已完成,并已单独调用过 `update_diagnosis_board`(step=4)
- [ ] 至少一个候选假设已产出 CONFIRMED / REFUTED / INCONCLUSIVE 判定(`test_results.json` 非空)

> 常见反模式:证据(Step 2)收集完毕后,主Agent自认为根因已明显,直接跳到 `step=5、step_completed=true` 收尾。这会让用户界面的调查时间线从第2步瞬间跳到根因报告,假设与验证过程完全不可见——**即使根因判断正确,这也是违规**。
```

### 5.7 Step 3(假设)小节 —— 新增「用户介入处理」段(假设 checkpoint 特性)

> **背景**:平台新增「假设 checkpoint」——主Agent 提交 `update_diagnosis_board(step=3)` 后,
> 平台自动向用户弹卡(添加假设 / 继续排查 / N 秒后自动继续),**工具调用会阻塞到用户决策或超时**,
> 决策结果拼在工具返回值里。SKILL.md 必须教模型处理返回值,否则用户补充的假设会被忽略。

在 Step 3 的进度上报说明之后追加:

```markdown
**用户介入处理(强制)**:本步调用 `update_diagnosis_board`(step=3) 后,平台会向用户弹出「假设确认卡」(添加假设 / 继续排查,超时自动继续),工具**返回值**中会包含用户的决策:
- 返回值含「用户已确认继续排查」或「超时未操作,默认继续排查」→ 直接进入验证(Step 4)
- 返回值含「用户补充了一条候选假设」→ **必须**先把该假设并入候选集合、重新评估各假设置信度与排序,**重新调用** `update_diagnosis_board`(step=3) 提交更新后的假设排行(此次不会再次弹卡),然后再进入验证(Step 4)。不得忽略用户补充的假设,也不得跳过重排直接验证
```

平台行为参考(不必写进 SKILL.md):每个诊断任务只弹一次卡(重交 step=3 不再弹,不会死锁);
子 Agent 提交不触发弹卡;首窗默认 10s(`OPENOPS_DIAG_CHECKPOINT_TIMEOUT_S`),用户点「添加假设」
开始输入后延长至 180s(`OPENOPS_DIAG_CHECKPOINT_HOLD_S`);超时=默认继续排查。

### 同步检查

改完后自查:全文搜索「允许跳步」应为 0 处;搜索 `step_completed=true` 出现的位置都应属于**主Agent**语境
(关键原则 8、Step 5 收尾示例),不应出现在任何 Sub-Agent 相关小节;Step 3 小节应含「用户介入处理」段(5.7)。
