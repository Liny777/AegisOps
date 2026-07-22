import type { CSSProperties, ReactNode } from "react";
import { color, radius, shadow, font } from "../../theme/tokens";
import { Icon, Interactive } from "../../ui";
import { styles, SectionHeading, Screenshot, Reveal } from "./ui";
import { OrchestrationDiagram, FiveStepFlow } from "./illustrations";
import { shots } from "./shots";
import {
  capabilities,
  fiveSteps,
  agentCards,
  agentDiscipline,
  onboardingSteps,
  askWays,
  safetyPoints,
} from "./content";

/* ------------------------- 局部小件 ------------------------- */

/** 营销尺度的大按钮（应用内 Button 是 13px 工具尺度，hero 需要更大的点击目标） */
function BigButton({
  children,
  icon,
  variant = "primary",
  onClick,
}: {
  children: ReactNode;
  icon?: string;
  variant?: "primary" | "secondary";
  onClick?: () => void;
}) {
  const primary = variant === "primary";
  return (
    <Interactive
      as="button"
      onClick={onClick}
      baseStyle={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        padding: "13px 26px",
        fontSize: 15,
        fontWeight: 700,
        fontFamily: font.sans,
        borderRadius: radius.xl,
        cursor: "pointer",
        border: primary ? "none" : `1px solid ${color.borderInput}`,
        background: primary ? color.brandGrad : "#fff",
        color: primary ? "#fff" : color.textStrong,
        boxShadow: primary ? shadow.brand : shadow.card,
        transition: "transform .15s ease, box-shadow .15s ease",
      }}
      hoverStyle={{
        transform: "translateY(-1px)",
        boxShadow: primary ? "0 6px 16px rgba(22,131,255,.36)" : shadow.cardHover,
      }}
    >
      {icon ? <Icon name={icon} size={17} /> : null}
      {children}
    </Interactive>
  );
}

/** 截图未就绪时的占位画布 */
function ShotPlaceholder({ label, ratio = "16 / 9", maxWidth = 960 }: { label: string; ratio?: string; maxWidth?: number }) {
  return (
    <div
      style={{
        maxWidth,
        width: "100%",
        margin: "0 auto",
        aspectRatio: ratio,
        borderRadius: radius.modal,
        border: `1.5px dashed ${color.brandTintBorder}`,
        background: color.brandTintBg,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 10,
        color: color.brand,
      }}
    >
      <Icon name="photo" size={28} />
      <span style={{ fontSize: 13, fontWeight: 600 }}>{label}</span>
    </div>
  );
}

function Shot({
  src,
  alt,
  caption,
  placeholder,
  maxWidth,
  frame,
  ratio,
}: {
  src: string | null;
  alt: string;
  caption?: string;
  placeholder: string;
  maxWidth?: number;
  frame?: boolean;
  ratio?: string;
}) {
  return src ? (
    <Screenshot src={src} alt={alt} caption={caption} maxWidth={maxWidth} frame={frame} ratio={ratio} />
  ) : (
    <ShotPlaceholder label={placeholder} maxWidth={maxWidth} ratio={ratio} />
  );
}

const card: CSSProperties = {
  background: "#fff",
  border: `1px solid ${color.border}`,
  borderRadius: radius.xxl,
  padding: "26px 24px",
  boxShadow: shadow.card,
};

function IconTile({ name, size = 40 }: { name: string; size?: number }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: radius.lg,
        background: color.brandTintBg,
        border: `1px solid ${color.brandTintBorder}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flex: `0 0 ${size}px`,
      }}
    >
      <Icon name={name} size={size * 0.52} color={color.brand} />
    </div>
  );
}

/* ========================= 1. Hero ========================= */

export function Hero({ onStart, onTutorial }: { onStart: () => void; onTutorial: () => void }) {
  return (
    <div id="intro-hero" style={{ position: "relative" }}>
      {/* 标题区提亮：3D 城市在下层固定背景里，这层白色径向光保住文字可读性 */}
      <div
        aria-hidden
        style={{
          position: "absolute",
          inset: 0,
          background: "radial-gradient(640px 320px at 50% 30%, rgba(255,255,255,.6), transparent 68%)",
          pointerEvents: "none",
        }}
      />
      <section style={{ ...styles.section, position: "relative", paddingBottom: "clamp(40px, 5vw, 72px)", textAlign: "center" }}>
        <div style={{ animation: "omFade .5s ease" }}>
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "7px 16px",
              borderRadius: radius.pill,
              background: "#fff",
              border: `1px solid ${color.brandTintBorder}`,
              boxShadow: shadow.card,
              fontSize: 13,
              fontWeight: 600,
              color: color.brandStrong,
              marginBottom: 26,
            }}
          >
            <Icon name="sparkles" size={15} color={color.brand} />
            智能故障 感知 · 定界 · 快恢
          </div>
          <h1 style={{ ...styles.h1, maxWidth: 860, margin: "0 auto" }}>
            <span style={styles.brandText}>感知快恢 Agent</span>
            <br />
            会自己查故障的智能助理
          </h1>
          <p style={{ ...styles.lede, maxWidth: 680, margin: "22px auto 0" }}>
            像和同事聊天一样描述问题，它自动跨告警、指标、日志、链路、变更并行调查，
            边查边把推理过程摊开给你看——高危动作永远等你点头才执行。
          </p>
          <div style={{ display: "flex", gap: 14, justifyContent: "center", flexWrap: "wrap", marginTop: 34 }}>
            <BigButton icon="player-play" onClick={onStart}>
              开始使用
            </BigButton>
            <BigButton icon="book-2" variant="secondary" onClick={onTutorial}>
              查看使用教程
            </BigButton>
          </div>
        </div>
        <Reveal delay={120} style={{ marginTop: "clamp(40px, 5vw, 64px)" }}>
          <Shot
            src={shots.workbench}
            alt="感知快恢 Agent 对话工作台：左侧对话，右侧调查时间线与定界卡片"
            placeholder="对话工作台截图"
            maxWidth={1000}
            ratio="8 / 5"
          />
        </Reveal>
      </section>
    </div>
  );
}

/* ========================= 2. 三大核心能力 ========================= */

export function Capabilities() {
  return (
    <section id="intro-caps" style={styles.section}>
      <Reveal>
        <SectionHeading
          center
          kicker="核心能力"
          title="一个闭环：感知 → 定界 → 恢复"
          lede="从「发现不对劲」到「恢复正常」，全程由同一个 Agent 接力完成，不用你在十几个系统之间来回切换。"
        />
      </Reveal>
      <div style={{ ...styles.cardGrid, marginTop: 44 }}>
        {capabilities.map((c, i) => (
          <Reveal key={c.title} delay={i * 90}>
            <div style={{ ...card, height: "100%", display: "flex", flexDirection: "column", gap: 14 }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <IconTile name={c.icon} size={44} />
                <span
                  style={{
                    fontSize: 11.5,
                    fontWeight: 700,
                    letterSpacing: "0.08em",
                    color: color.brand,
                    background: color.brandTintBg,
                    padding: "4px 10px",
                    borderRadius: radius.pill,
                  }}
                >
                  {c.tag}
                </span>
              </div>
              <div style={{ fontSize: 18.5, fontWeight: 800, color: color.textStrong }}>{c.title}</div>
              <p style={{ ...styles.body, color: color.textMuted }}>{c.desc}</p>
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

/* ========================= 3. 工作方式 ========================= */

export function HowItWorks() {
  return (
    <div id="intro-how" style={{ background: "rgba(255,255,255,.9)", borderTop: `1px solid ${color.border}`, borderBottom: `1px solid ${color.border}`, scrollMarginTop: 72 }}>
      <section style={styles.section}>
        {/* 行 1：编排图文 */}
        <div style={styles.zigzag}>
          <div style={styles.zigzagCol}>
            <Reveal>
              <SectionHeading
                kicker="工作方式"
                title="一个主 Agent，指挥一支专家团队"
                lede="你只需要面对一个 Agent。它理解你的问题后，把调查拆给告警、日志、指标、变更等专业子 Agent 并行执行，再把各路发现汇总成一份结论。多路调查同时进行，定位速度不再受限于人肉翻系统的速度。"
              />
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 24 }}>
                {agentDiscipline.map((d) => (
                  <div key={d.text} style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
                    <Icon name={d.icon} size={16} color={color.good} style={{ marginTop: 3 }} />
                    <span style={{ fontSize: 13.5, lineHeight: 1.7, color: color.textBody }}>{d.text}</span>
                  </div>
                ))}
              </div>
            </Reveal>
          </div>
          <div style={styles.zigzagCol}>
            <Reveal delay={100}>
              <div style={{ background: color.surfaceAlt, border: `1px solid ${color.borderInner}`, borderRadius: radius.modal, padding: "clamp(14px, 2vw, 26px)" }}>
                <OrchestrationDiagram />
              </div>
            </Reveal>
          </div>
        </div>

        {/* 行 2：五步法 */}
        <div style={{ marginTop: "clamp(64px, 8vw, 104px)" }}>
          <Reveal>
            <SectionHeading
              center
              kicker="定界五步法"
              title="推理过程，一步一步摊开给你看"
              lede="每次故障定界都严格走五步：范围 → 证据 → 假设 → 验证 → 结论。进展实时更新在定界卡片上，你随时知道它查到了哪一步、下一步要干什么。"
            />
          </Reveal>
          <Reveal delay={80} style={{ maxWidth: 860, margin: "40px auto 0" }}>
            <FiveStepFlow />
          </Reveal>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              gap: 12,
              maxWidth: 980,
              margin: "26px auto 0",
            }}
          >
            {fiveSteps.map((s, i) => (
              <Reveal key={s.n} delay={i * 70}>
                <div style={{ padding: "14px 16px", borderRadius: radius.xl, background: color.surfaceAlt, border: `1px solid ${color.borderInner}` }}>
                  <div style={{ fontSize: 12, fontWeight: 800, color: color.brand, marginBottom: 6 }}>
                    STEP {s.n} · {s.title}
                  </div>
                  <div style={{ fontSize: 13, lineHeight: 1.65, color: color.textMuted }}>{s.desc}</div>
                </div>
              </Reveal>
            ))}
          </div>
          <Reveal delay={120} style={{ marginTop: 48 }}>
            <Shot
              src={shots.rca}
              alt="定界卡片：五步法进度、证据源状态、假设置信度与最终结论"
              caption="定界卡片：假设、证据与结论在一张卡片上原地更新，而不是刷屏的日志。"
              placeholder="定界卡片截图"
              maxWidth={560}
              frame={false}
              ratio="1018 / 2144"
            />
          </Reveal>
        </div>
      </section>
    </div>
  );
}

/* ========================= 4. 子 Agent 能力矩阵 ========================= */

export function SubAgentMatrix() {
  const main = agentCards.find((a) => a.main)!;
  const rest = agentCards.filter((a) => !a.main);
  return (
    <section id="intro-agents" style={{ ...styles.section, scrollMarginTop: 72 }}>
      <Reveal>
        <SectionHeading
          center
          kicker="能力矩阵"
          title="每个运维对象，都有一位专业子 Agent"
          lede="告警、日志、指标、变更、微服务、数据库、缓存、消息队列、看板——每一路都由专职子 Agent 负责，查得又快又专。"
        />
      </Reveal>

      {/* 主 Agent 横幅卡 */}
      <Reveal delay={60} style={{ marginTop: 44 }}>
        <div
          style={{
            ...card,
            border: `1px solid ${color.brandTintBorder}`,
            background: `linear-gradient(135deg, ${color.brandTintBg} 0%, #ffffff 46%)`,
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: 20,
          }}
        >
          <div
            style={{
              width: 54,
              height: 54,
              borderRadius: radius.xl,
              background: color.brandGrad,
              boxShadow: shadow.brand,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flex: "0 0 54px",
            }}
          >
            <Icon name={main.icon} size={28} color="#fff" />
          </div>
          <div style={{ flex: "1 1 380px", minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 17, fontWeight: 800, color: color.textStrong }}>{main.name}</span>
              <RolePill>{main.role}</RolePill>
            </div>
            <p style={{ ...styles.body, color: color.textMuted, marginTop: 8 }}>{main.desc}</p>
          </div>
          <AskChip ask={main.ask} />
        </div>
      </Reveal>

      <div style={{ ...styles.cardGrid, marginTop: 16 }}>
        {rest.map((a, i) => (
          <Reveal key={a.name} delay={(i % 3) * 80}>
            <div style={{ ...card, height: "100%", display: "flex", flexDirection: "column", gap: 12 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <IconTile name={a.icon} />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 15.5, fontWeight: 800, color: color.textStrong }}>{a.name}</div>
                  <div style={{ fontSize: 12, color: color.textSubtle, marginTop: 2 }}>{a.role}</div>
                </div>
              </div>
              <p style={{ ...styles.body, fontSize: 13.5, color: color.textMuted, flex: 1 }}>{a.desc}</p>
              <AskChip ask={a.ask} block />
            </div>
          </Reveal>
        ))}
      </div>
    </section>
  );
}

function RolePill({ children }: { children: ReactNode }) {
  return (
    <span
      style={{
        fontSize: 11.5,
        fontWeight: 700,
        color: color.brand,
        background: color.brandTintBg,
        border: `1px solid ${color.brandTintBorder}`,
        padding: "3px 10px",
        borderRadius: radius.pill,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

function AskChip({ ask, block }: { ask: string; block?: boolean }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 8,
        padding: "9px 12px",
        borderRadius: radius.lg,
        background: color.neutralBg,
        border: `1px solid ${color.borderFaint}`,
        flex: block ? undefined : "0 1 260px",
        minWidth: block ? undefined : 220,
      }}
    >
      <Icon name="message-2" size={14} color={color.textSubtle} style={{ marginTop: 2 }} />
      <span style={{ fontSize: 12.5, lineHeight: 1.6, color: color.textNav }}>“{ask}”</span>
    </div>
  );
}

/* ========================= 5. 使用教程 ========================= */

export function Tutorial() {
  return (
    <div id="intro-tutorial" style={{ background: "rgba(255,255,255,.9)", borderTop: `1px solid ${color.border}`, borderBottom: `1px solid ${color.border}`, scrollMarginTop: 72 }}>
      <section style={styles.section}>
        <Reveal>
          <SectionHeading center kicker="使用教程" title="三步上手，五分钟开始第一次排查" />
        </Reveal>

        {/* 三步上手 */}
        <div style={{ ...styles.cardGrid, gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", marginTop: 44 }}>
          {onboardingSteps.map((s, i) => (
            <Reveal key={s.n} delay={i * 90}>
              <div style={{ ...card, height: "100%", position: "relative", overflow: "hidden" }}>
                <div
                  aria-hidden
                  style={{
                    position: "absolute",
                    top: -14,
                    right: 6,
                    fontSize: 84,
                    fontWeight: 800,
                    color: color.brandTintBg,
                    lineHeight: 1,
                    userSelect: "none",
                  }}
                >
                  {s.n}
                </div>
                <div style={{ position: "relative", display: "flex", flexDirection: "column", gap: 12 }}>
                  <IconTile name={s.icon} />
                  <div style={{ fontSize: 16, fontWeight: 800, color: color.textStrong }}>
                    {s.n}. {s.title}
                  </div>
                  <p style={{ ...styles.body, fontSize: 13.5, color: color.textMuted }}>{s.desc}</p>
                </div>
              </div>
            </Reveal>
          ))}
        </div>

        <Reveal delay={100} style={{ marginTop: 40 }}>
          <Shot
            src={shots.initWizard}
            alt="初始化向导：选择模板、配置 Agent、激活"
            caption="初始化向导：选模板 → 配置 → 激活，全程不到五分钟。"
            placeholder="初始化向导截图"
            maxWidth={880}
            ratio="8 / 5"
          />
        </Reveal>

        {/* 怎么提问 */}
        <div style={{ marginTop: "clamp(64px, 8vw, 96px)" }}>
          <Reveal>
            <SectionHeading
              center
              title="怎么提问都可以"
              lede="没有需要背的命令，也没有需要学的查询语法——用你描述故障给同事听的方式提问就行。"
            />
          </Reveal>
          <div style={{ ...styles.cardGrid, marginTop: 40 }}>
            {askWays.map((w, i) => (
              <Reveal key={w.title} delay={i * 90}>
                <div style={{ ...card, height: "100%", display: "flex", flexDirection: "column", gap: 12 }}>
                  <IconTile name={w.icon} />
                  <div style={{ fontSize: 15.5, fontWeight: 800, color: color.textStrong }}>{w.title}</div>
                  <p style={{ ...styles.body, fontSize: 13.5, color: color.textMuted }}>{w.desc}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </div>

      </section>
    </div>
  );
}

/* ========================= 6. 安全与可控 ========================= */

export function Safety() {
  return (
    <section id="intro-safety" style={styles.section}>
      <div style={styles.zigzag}>
        <div style={styles.zigzagCol}>
          <Reveal>
            <SectionHeading
              kicker="安全与可控"
              title="放心交给它，主动权始终在你手里"
            />
            <div style={{ display: "flex", flexDirection: "column", gap: 18, marginTop: 28 }}>
              {safetyPoints.map((s) => (
                <div key={s.title} style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
                  <IconTile name={s.icon} size={38} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 15, fontWeight: 800, color: color.textStrong }}>{s.title}</div>
                    <p style={{ ...styles.body, fontSize: 13.5, color: color.textMuted, marginTop: 5 }}>{s.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
        <div style={styles.zigzagCol}>
          <Reveal delay={100}>
            <Shot
              src={shots.hitl}
              alt="人工审批卡：恢复动作等待用户批准"
              caption="恢复动作审批卡：批准之前，什么都不会发生。"
              placeholder="审批卡截图"
              maxWidth={560}
              frame={false}
              ratio="894 / 622"
            />
          </Reveal>
        </div>
      </div>
    </section>
  );
}

/* ========================= 7. 页脚 CTA ========================= */

export function FooterCta({ onStart }: { onStart: () => void }) {
  return (
    <section id="intro-cta" style={{ ...styles.section, paddingTop: 0 }}>
      <Reveal>
        <div
          style={{
            position: "relative",
            overflow: "hidden",
            borderRadius: 24,
            background: `radial-gradient(640px 300px at 22% -30%, rgba(255,255,255,.24), transparent 60%), ${color.brandGrad}`,
            padding: "clamp(48px, 6vw, 76px) clamp(24px, 5vw, 64px)",
            textAlign: "center",
            boxShadow: "0 18px 44px rgba(22,131,255,.30)",
          }}
        >
          <h2 style={{ ...styles.h2, color: "#fff" }}>下一次故障，让 Agent 先上</h2>
          <p style={{ ...styles.lede, color: "rgba(255,255,255,.88)", maxWidth: 560, margin: "14px auto 0" }}>
            从一句「帮我查一下」开始，把跨系统翻日志、盯指标的时间还给自己。
          </p>
          <div style={{ marginTop: 30 }}>
            <Interactive
              as="button"
              onClick={onStart}
              baseStyle={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                padding: "13px 30px",
                fontSize: 15,
                fontWeight: 800,
                fontFamily: font.sans,
                borderRadius: radius.xl,
                border: "none",
                cursor: "pointer",
                background: "#fff",
                color: color.brandStrong,
                boxShadow: "0 6px 18px rgba(9,54,116,.28)",
                transition: "transform .15s ease",
              }}
              hoverStyle={{ transform: "translateY(-1px)" }}
            >
              <Icon name="player-play" size={16} />
              开始使用
            </Interactive>
          </div>
        </div>
      </Reveal>
    </section>
  );
}
