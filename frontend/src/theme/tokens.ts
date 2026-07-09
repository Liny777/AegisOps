/**
 * OpenOps 设计 token —— 从「SRE Agent 工作台.dc.html」抽取，对齐 30.1 前端设计系统。
 * 设计稿以内联样式为主，故此处以 TS 常量导出，供 TSX inline style 直接引用；
 * 全局 reset / 滚动条 / keyframes / 字体见 global.css。
 */

export const color = {
  // 底色与墨色
  pageBg: "#f7f8fa",
  sidebarBg: "#f1f3f6",
  surface: "#ffffff",
  surfaceAlt: "#fbfcfd",
  surfaceTint: "#fbfcfe",
  ink: "#151923",

  // 品牌
  brand: "#1683ff",
  brandStrong: "#155eef",
  brandGrad: "linear-gradient(150deg, #4f9dff, #1683ff)",
  brandTintBg: "#eef4ff", // 浅蓝 chip / 图标底
  brandTintBg2: "#e8f3ff",
  brandTintBorder: "#cfe0fc",

  // 边框（由深到浅）
  border: "#e6e8ec", // 主边框
  borderInput: "#e2e5ea", // 输入/卡片
  borderInner: "#eef0f3", // 卡内分隔
  borderFaint: "#f2f3f6",

  // 文本层级
  textStrong: "#2a2f38",
  textBody: "#3a404b",
  textNav: "#4a5160",
  textMuted: "#788192",
  textSubtle: "#9aa0ab",
  textLabel: "#aab0bb",
  textFaint: "#b3b8c2",
  textFainter: "#c8ccd4",

  // data-tone 语义色
  good: "#1fa463",
  goodText: "#127848",
  goodBg: "#f4faf6",
  goodBorder: "#e2efe8",
  warning: "#c18a20",
  warningText: "#8b641c",
  warningBg: "#fdf9ef",
  warningBorder: "#f0e6cf",
  warningChipBg: "#fbf1e0",
  danger: "#d94841",
  dangerText: "#ad302b",
  neutralBg: "#f2f4f7",

  // 状态点/滚动条
  scrollThumb: "#d7dae0",
  dotIdle: "#c8ccd4",
} as const;

export const radius = {
  sm: "6px",
  md: "8px",
  lg: "10px",
  xl: "12px",
  xxl: "14px",
  modal: "16px",
  pill: "999px",
  full: "50%",
} as const;

export const shadow = {
  card: "0 1px 3px rgba(20,24,31,.05)",
  cardHover: "0 4px 14px rgba(22,131,255,.10)",
  popover: "0 12px 30px rgba(20,24,31,.14)",
  menu: "0 14px 34px rgba(20,24,31,.16)",
  slideIn: "-12px 0 40px rgba(20,24,31,.18)",
  modal: "0 24px 60px rgba(20,24,31,.28)",
  brand: "0 3px 8px rgba(22,131,255,.28)",
} as const;

export const font = {
  sans:
    "'Inter Variable', 'Inter', system-ui, -apple-system, 'PingFang SC', 'Microsoft YaHei', 'Segoe UI', sans-serif",
  mono: "ui-monospace, 'SF Mono', Menlo, monospace",
} as const;

/** data-tone 四档取色（neutral / good / warning / danger） */
export type Tone = "neutral" | "good" | "warning" | "danger";
export const toneColor: Record<Tone, { dot: string; text: string; bg: string; border: string }> = {
  neutral: { dot: color.textSubtle, text: color.textNav, bg: color.neutralBg, border: color.border },
  good: { dot: color.good, text: color.goodText, bg: color.goodBg, border: color.goodBorder },
  warning: { dot: color.warning, text: color.warningText, bg: color.warningBg, border: color.warningBorder },
  danger: { dot: color.danger, text: color.dangerText, bg: "#fdf2f1", border: "#f3d9d7" },
};
