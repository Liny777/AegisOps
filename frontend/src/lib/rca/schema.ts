import { z } from "zod";
import type { RcaCardData } from "../api/types";

/**
 * openops.rca.updated payload 的 zod 镜像（仿 rich-ui/schema.ts 的「渲染前校验」纪律）。
 *
 * 与图表契约不同，这里刻意**宽容**：定界面板是增量合并出来的快照，早期步骤大量字段
 * 缺失（demo/旧后端也可能整块缺字段），除 steps 外多数字段 optional 给默认值；
 * 未知键静默剥离（emit 注入的 agent_key 等不进渲染模型）。校验失败静默丢弃该
 * revision（防后端版本漂移崩 UI），不弹错——下一条合法事件自然覆盖。
 */

const toneSchema = z.enum(["neutral", "good", "warning", "danger"]).default("neutral");

const stepSchema = z.object({
  num: z.number().int().min(1),
  label: z.string().min(1),
  state: z.enum(["done", "active", "waiting"]),
});

const factSchema = z.object({ text: z.string() });

export const rcaCardSchema = z.object({
  title: z.string().default(""),
  phaseLabel: z.string().default(""),
  time: z.string().optional(),
  /** task 内单调自增（服务端权威）；旧 payload 可缺失 → guard 走 last-write-wins。 */
  revision: z.number().int().nonnegative().optional(),
  /** 完成态判定的唯一权威信号；缺失视为进行中（兼容旧后端）。 */
  status: z.enum(["in_progress", "concluded"]).optional(),
  tiles: z.array(z.object({ label: z.string(), value: z.string() })).default([]),
  /** 五步法骨架是卡片的心脏：连 steps 都没有的 payload 没有渲染意义，直接拒收。 */
  steps: z.array(stepSchema).min(1),
  currentQ: z.string().default(""),
  why: z.string().default(""),
  facts: z.array(factSchema).default([]),
  unknowns: z.array(factSchema).default([]),
  sources: z.array(z.object({
    name: z.string(),
    status: z.string().default(""),
    tone: toneSchema,
  })).default([]),
  hypotheses: z.array(z.object({
    text: z.string(),
    tag: z.string().default(""),
    tagTone: toneSchema,
    conf: z.number().min(0).max(1).default(0),
  })).default([]),
  actions: z.array(z.object({
    tier: z.string().default(""),
    text: z.string(),
    confirm: z.boolean().optional(),
    impact: z.string().default(""),
    status: z.string().default(""),
    statusTone: toneSchema,
  })).default([]),
  conclusion: z.string().default(""),
});

/** payload → RcaCardData；不合法返回 undefined（调用方静默丢弃该 revision）。 */
export function parseRcaCard(payload: unknown): RcaCardData | undefined {
  const result = rcaCardSchema.safeParse(payload);
  return result.success ? (result.data as RcaCardData) : undefined;
}
