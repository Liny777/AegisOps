import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { color, radius, shadow, font } from "../../theme/tokens";

/* ------------------------- 页面级样式常量 ------------------------- */
/** 介绍页营销尺度的排版常量：全部从 tokens 派生，clamp() 承担响应式（内联样式无媒体查询）。 */
export const styles = {
  section: {
    maxWidth: 1120,
    margin: "0 auto",
    padding: "clamp(56px, 8vw, 104px) 24px",
  } as CSSProperties,
  h1: {
    fontSize: "clamp(32px, 5.2vw, 54px)",
    fontWeight: 800,
    lineHeight: 1.16,
    letterSpacing: "-0.025em",
    color: color.textStrong,
    margin: 0,
  } as CSSProperties,
  h2: {
    fontSize: "clamp(24px, 3.2vw, 34px)",
    fontWeight: 800,
    lineHeight: 1.25,
    letterSpacing: "-0.015em",
    color: color.textStrong,
    margin: 0,
  } as CSSProperties,
  lede: {
    fontSize: "clamp(15px, 1.7vw, 17.5px)",
    lineHeight: 1.85,
    color: color.textMuted,
    margin: 0,
  } as CSSProperties,
  body: {
    fontSize: 14.5,
    lineHeight: 1.8,
    color: color.textBody,
    margin: 0,
  } as CSSProperties,
  brandText: {
    background: color.brandGrad,
    WebkitBackgroundClip: "text",
    backgroundClip: "text",
    WebkitTextFillColor: "transparent",
    color: "transparent",
  } as CSSProperties,
  cardGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))",
    gap: 16,
  } as CSSProperties,
  zigzag: {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    gap: "clamp(28px, 4vw, 60px)",
  } as CSSProperties,
  zigzagCol: { flex: "1 1 400px", minWidth: 0 } as CSSProperties,
  mono: { fontFamily: font.mono } as CSSProperties,
} as const;

/* ------------------------- 区块标题 ------------------------- */
export function SectionHeading({
  kicker,
  title,
  lede,
  center,
}: {
  kicker?: string;
  title: ReactNode;
  lede?: ReactNode;
  center?: boolean;
}) {
  return (
    <div style={{ maxWidth: 720, margin: center ? "0 auto" : undefined, textAlign: center ? "center" : "left" }}>
      {kicker ? (
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontSize: 12.5,
            fontWeight: 700,
            letterSpacing: "0.12em",
            color: color.brand,
            marginBottom: 14,
          }}
        >
          {kicker}
        </div>
      ) : null}
      <h2 style={styles.h2}>{title}</h2>
      {lede ? <p style={{ ...styles.lede, marginTop: 14 }}>{lede}</p> : null}
    </div>
  );
}

/* ------------------------- 截图窗框 ------------------------- */
/** claude.com 式截图卡：浏览器窗框（三圆点）+ 大圆角 + 深阴影。frame=false 时退化为素净圆角卡。 */
export function Screenshot({
  src,
  alt,
  caption,
  maxWidth = 960,
  frame = true,
  ratio,
}: {
  src: string;
  alt: string;
  caption?: string;
  maxWidth?: number;
  frame?: boolean;
  /** 图片固有宽高比（如 "8 / 5"）：懒加载前预留盒高，避免 CLS 与锚点跳转落点偏移。 */
  ratio?: string;
}) {
  return (
    <figure style={{ margin: "0 auto", maxWidth, width: "100%" }}>
      <div
        style={{
          background: "#fff",
          border: `1px solid ${color.border}`,
          borderRadius: radius.modal,
          boxShadow: frame ? shadow.popover : shadow.card,
          overflow: "hidden",
        }}
      >
        {frame ? (
          <div
            style={{
              display: "flex",
              gap: 6,
              padding: "10px 14px",
              borderBottom: `1px solid ${color.borderInner}`,
              background: color.surfaceAlt,
            }}
          >
            {["#f57b71", "#f5bd4f", "#57c353"].map((c) => (
              <span key={c} style={{ width: 10, height: 10, borderRadius: "50%", background: c }} />
            ))}
          </div>
        ) : null}
        <img src={src} alt={alt} loading="lazy" style={{ display: "block", width: "100%", height: "auto", aspectRatio: ratio }} />
      </div>
      {caption ? (
        <figcaption style={{ textAlign: "center", fontSize: 12.5, color: color.textSubtle, marginTop: 12 }}>
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );
}

/* ------------------------- 滚动入场 ------------------------- */
/** 首次进入视口时 opacity/translateY 渐入；不支持 IntersectionObserver 时直接可见。 */
export function useReveal(delayMs = 0) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    // 后台/隐藏标签页不派发 IO 回调（Chrome 节流）：直接显示，放弃动画，避免内容卡在透明态。
    if (!el || typeof IntersectionObserver === "undefined" || document.visibilityState === "hidden") {
      setShown(true);
      return;
    }
    const ob = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setShown(true);
          ob.disconnect();
        }
      },
      { rootMargin: "0px 0px -60px 0px", threshold: 0.05 },
    );
    ob.observe(el);
    return () => ob.disconnect();
  }, []);
  const style: CSSProperties = {
    opacity: shown ? 1 : 0,
    transform: shown ? "none" : "translateY(16px)",
    transition: `opacity .55s ease ${delayMs}ms, transform .55s ease ${delayMs}ms`,
  };
  return { ref, style };
}

export function Reveal({ children, delay = 0, style }: { children: ReactNode; delay?: number; style?: CSSProperties }) {
  const r = useReveal(delay);
  return (
    <div ref={r.ref} style={{ ...r.style, ...style }}>
      {children}
    </div>
  );
}
