import type { ReactNode } from "react";
import { color, radius, shadow } from "../theme/tokens";
import { Icon } from "./Icon";

/** 居中模态（如模板编辑器）。点遮罩关闭，内容区阻止冒泡。 */
export function Modal({
  open,
  onClose,
  children,
  maxWidth = 720,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  maxWidth?: number;
}) {
  if (!open) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 60,
        background: "rgba(20,24,31,.42)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
        animation: "omFade .16s ease",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%",
          maxWidth,
          maxHeight: "86vh",
          background: "#fff",
          borderRadius: radius.modal,
          boxShadow: shadow.modal,
          display: "flex",
          flexDirection: "column",
          animation: "omPop .22s ease",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/** 右侧抽屉（如 Tool 标注）。 */
export function SlideIn({
  open,
  onClose,
  children,
  width = 460,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  width?: number;
}) {
  if (!open) return null;
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 60,
        background: "rgba(20,24,31,.34)",
        display: "flex",
        justifyContent: "flex-end",
        animation: "omFade .16s ease",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width,
          height: "100vh",
          background: "#fff",
          boxShadow: shadow.slideIn,
          display: "flex",
          flexDirection: "column",
          animation: "omPop .22s ease",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/** 模态/抽屉通用头部（标题 + 副标题 + 关闭 X）。 */
export function OverlayHeader({
  title,
  sub,
  onClose,
  right,
}: {
  title: ReactNode;
  sub?: ReactNode;
  onClose: () => void;
  right?: ReactNode;
}) {
  return (
    <div
      style={{
        flex: "0 0 auto",
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "16px 20px",
        borderBottom: `1px solid ${color.border}`,
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 15, fontWeight: 700 }}>{title}</div>
        {sub != null ? (
          <div style={{ fontSize: 12, color: color.textSubtle, fontFamily: "ui-monospace, monospace" }}>{sub}</div>
        ) : null}
      </div>
      <div style={{ flex: 1 }} />
      {right}
      <Icon name="x" size={18} color="#697283" style={{ marginLeft: 4 }} onClick={onClose} />
    </div>
  );
}
