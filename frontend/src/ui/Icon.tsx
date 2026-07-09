import type { CSSProperties } from "react";

/** Tabler 图标（webfont，`<i class="ti ti-xxx">`），与设计稿一致。 */
export function Icon({
  name,
  size = 16,
  color,
  style,
  className,
  spin,
  pulse,
  title,
  onClick,
}: {
  name: string;
  size?: number;
  color?: string;
  style?: CSSProperties;
  className?: string;
  spin?: boolean;
  pulse?: boolean;
  title?: string;
  onClick?: () => void;
}) {
  const anim = spin
    ? { animation: "omSpin 1s linear infinite" }
    : pulse
    ? { animation: "omPulse 1.2s ease-in-out infinite" }
    : undefined;
  return (
    <i
      className={`ti ti-${name}${className ? " " + className : ""}`}
      aria-hidden
      title={title}
      onClick={onClick}
      style={{ fontSize: size, color, lineHeight: 1, flex: "0 0 auto", cursor: onClick ? "pointer" : undefined, ...anim, ...style }}
    />
  );
}
