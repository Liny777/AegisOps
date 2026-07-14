// 审批卡（HITL/ASK）· CopilotChat 路径浮层——锚在 composer 正上方（操作点附近）。
//
// 此前审批卡与 RCA 同在面板**顶部**横条：审批是"等你按按钮"的强交互动作项，出现在
// 对话区顶部远离输入焦点，还把消息流往下压（用户实测反馈）。改为 SkillSlash 同款
// DOM 锚定：rAF 测 `.copilot-chat-panel textarea` rect，CopilotChat 重挂/输入框长高
// 时自动跟随；不碰 CopilotChat 内部（input slot 方案在外部状态注入时组件引用不稳，
// 会整段重建输入框丢焦点）。RCA 决策面板保持顶部横条（分析上下文，非动作项）。
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { HitlCard } from "../HitlCard";
import type { HitlCardData } from "../../lib/api/types";

const MAX_W = 760;

function composerTextarea(): HTMLTextAreaElement | null {
  return document.querySelector<HTMLTextAreaElement>(".copilot-chat-panel textarea");
}

export function CopilotHitlFloat({ hitl, onDecide }: {
  hitl: HitlCardData | undefined;
  onDecide: (d: "approved" | "rejected") => void;
}) {
  const [anchor, setAnchor] = useState<{ left: number; bottom: number; width: number } | null>(null);

  useEffect(() => {
    if (!hitl) {
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
          const next = { left: r.left, bottom: window.innerHeight - r.top + 10, width: r.width };
          return prev && prev.left === next.left && prev.bottom === next.bottom && prev.width === next.width
            ? prev : next;
        });
      }
      raf = requestAnimationFrame(track);
    };
    raf = requestAnimationFrame(track);
    return () => cancelAnimationFrame(raf);
  }, [hitl]);

  if (!hitl || !anchor) return null;
  const width = Math.min(anchor.width, MAX_W);
  const left = anchor.left + (anchor.width - width) / 2; // 比输入框宽时水平居中对齐
  // 经 portal 挂到 body：CopilotChat 虚拟消息列表容器会建独立层叠上下文，作为其兄弟的浮层
  // （原 zIndex:50）会被内联工具卡盖住。挂 body 后彻底脱离该子树，position:fixed 仍按视口坐标
  // 定位（锚定数学不变），zIndex 压过一切内联渲染，审批卡稳定置顶。
  return createPortal(
    <div style={{ position: "fixed", left, bottom: anchor.bottom, width, zIndex: 1000,
                  boxShadow: "0 12px 32px rgba(20,24,31,.16)", borderRadius: 14 }}>
      <HitlCard key={hitl.approval_request_id + hitl.status} hitl={hitl} onDecide={onDecide} />
    </div>,
    document.body,
  );
}
