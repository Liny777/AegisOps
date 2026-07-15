import { useCallback, useEffect, useRef, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";

/**
 * Streamdown 1.6.11 的 fullscreen 会额外渲染第二张 Mermaid。这里在 markdown 边界截获操作，
 * 浏览器支持时让现有 mermaid-block 自身进入全屏；请求失败才放行 vendor 弹层作为降级。
 */
export function MermaidFullscreenBoundary({ children }: { children: ReactNode }) {
  const vendorFallback = useRef(new WeakSet<HTMLButtonElement>());

  useEffect(() => {
    const exitOnEscape = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        document.fullscreenElement?.matches('[data-streamdown="mermaid-block"]')
      ) {
        void document.exitFullscreen?.();
      }
    };
    document.addEventListener("keydown", exitOnEscape);
    return () => document.removeEventListener("keydown", exitOnEscape);
  }, []);

  const handleClickCapture = useCallback((event: ReactMouseEvent<HTMLDivElement>) => {
    const target = event.target instanceof Element ? event.target : null;
    const button = target?.closest<HTMLButtonElement>('button[title="View fullscreen"]');
    if (!button) return;
    if (vendorFallback.current.has(button)) {
      vendorFallback.current.delete(button);
      return;
    }

    const block = button.closest<HTMLElement>('[data-streamdown="mermaid-block"]');
    if (!block || typeof block.requestFullscreen !== "function") return;

    event.preventDefault();
    event.stopPropagation();
    const doc = block.ownerDocument;
    if (doc.fullscreenElement === block) {
      void doc.exitFullscreen?.();
      return;
    }

    try {
      void block.requestFullscreen().catch(() => {
        // 第二次合成 click 绕过本边界，触发 Streamdown 自带弹层；CSS 会保证原图隐藏且遮罩 fixed。
        vendorFallback.current.add(button);
        button.click();
      });
    } catch {
      vendorFallback.current.add(button);
      button.click();
    }
  }, []);

  return (
    <div className="oa-chat-rich-boundary" onClickCapture={handleClickCapture}>
      {children}
    </div>
  );
}
