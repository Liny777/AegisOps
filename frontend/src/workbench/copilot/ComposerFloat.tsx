// composer 锚定浮层的共享骨架：rAF 测 `.copilot-chat-panel textarea` rect，portal 到 body。
// 从 CopilotHitlFloat 提取（审批卡与假设 checkpoint 卡共用）——锚定数学与层叠上下文的
// 权衡见 CopilotHitlFloat 的注释，这里只保留机制本体。
import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

const MAX_W = 760;
const SAFE_INSET = 12;
const ANCHOR_GAP = 10;

function composerTextarea(): HTMLTextAreaElement | null {
  return document.querySelector<HTMLTextAreaElement>(".copilot-chat-panel textarea");
}

export function ComposerAnchoredFloat({ active, className, zIndex = 1000, children }: {
  /** false 时整体不渲染并停掉 rAF 追踪。 */
  active: boolean;
  className: string;
  /** 多浮层并存时的层级（审批卡 1000 恒最上；checkpoint 999）。 */
  zIndex?: number;
  children: ReactNode;
}) {
  const [anchor, setAnchor] = useState<{ left: number; top: number; width: number } | null>(null);

  useEffect(() => {
    if (!active) {
      setAnchor(null);
      return;
    }
    let raf = 0;
    const track = () => {
      const ta = composerTextarea();
      if (ta) {
        const r = ta.getBoundingClientRect();
        // 同值时返回 prev → React bail-out，不空转重渲染
        setAnchor((prev) => {
          const next = { left: r.left, top: r.top, width: r.width };
          return prev && prev.left === next.left && prev.top === next.top && prev.width === next.width
            ? prev : next;
        });
      }
      raf = requestAnimationFrame(track);
    };
    raf = requestAnimationFrame(track);
    return () => cancelAnimationFrame(raf);
  }, [active]);

  if (!active || !anchor) return null;
  const viewportWidth = window.innerWidth;
  const width = Math.max(0, Math.min(anchor.width, MAX_W, viewportWidth - SAFE_INSET * 2));
  const idealLeft = anchor.left + (anchor.width - width) / 2;
  const left = Math.min(Math.max(SAFE_INSET, idealLeft), viewportWidth - SAFE_INSET - width);
  const bottom = Math.max(SAFE_INSET, window.innerHeight - anchor.top + ANCHOR_GAP);
  const maxHeight = Math.max(0, window.innerHeight - bottom - SAFE_INSET);
  // 经 portal 挂到 body：CopilotChat 虚拟消息列表容器会建独立层叠上下文，作为其兄弟的浮层
  // 会被内联工具卡盖住。挂 body 后彻底脱离该子树，position:fixed 仍按视口坐标定位。
  return createPortal(
    <div className={className} style={{ position: "fixed", left, bottom, width, maxHeight, zIndex,
                  overflowX: "hidden", overflowY: "auto", overscrollBehavior: "contain",
                  boxShadow: "0 12px 32px rgba(20,24,31,.16)", borderRadius: 14 }}>
      {children}
    </div>,
    document.body,
  );
}
