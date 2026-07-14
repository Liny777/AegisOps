import { useState } from "react";
import type { CSSProperties, ElementType, ReactNode } from "react";

/** 复刻设计稿的 `style-hover`：合并 base + hover 态内联样式。 */
export function useHover() {
  const [hovered, setHovered] = useState(false);
  return {
    hovered,
    bind: {
      onMouseEnter: () => setHovered(true),
      onMouseLeave: () => setHovered(false),
    },
  };
}

type InteractiveProps = {
  as?: ElementType;
  baseStyle?: CSSProperties;
  hoverStyle?: CSSProperties;
  children?: ReactNode;
  onClick?: () => void;
  title?: string;
  className?: string;
  href?: string;
  disabled?: boolean;
  [key: string]: unknown;
};

/** 通用可交互容器：hover 时叠加 hoverStyle。用于导航行、菜单项、卡片等。 */
export function Interactive({
  as,
  baseStyle,
  hoverStyle,
  children,
  onClick,
  title,
  className,
  disabled,
  ...rest
}: InteractiveProps) {
  const Tag = (as ?? "div") as ElementType;
  const { hovered, bind } = useHover();
  return (
    <Tag
      className={className}
      title={title}
      onClick={disabled ? undefined : onClick}
      {...(disabled ? {} : bind)}
      {/* button 须真禁用（DOM disabled）：仅样式禁用时 Playwright/读屏器认不出，
          自动化会在数据未就绪时点到空 onClick（编辑向导预填幕实测坑） */ ...(disabled && Tag === "button" ? { disabled: true } : {})}
      {...rest}
      style={{ ...baseStyle, ...(hovered && !disabled ? hoverStyle : {}) }}
    >
      {children}
    </Tag>
  );
}
