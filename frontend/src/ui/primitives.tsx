import type { CSSProperties, ReactNode } from "react";
import { color, radius, font } from "../theme/tokens";
import type { Tone } from "../theme/tokens";
import { toneColor } from "../theme/tokens";
import { Icon } from "./Icon";
import { Interactive } from "./Interactive";

/* ----------------------------- Button ----------------------------- */
type BtnVariant = "primary" | "secondary" | "ghost";
export function Button({
  children,
  variant = "primary",
  icon,
  onClick,
  disabled,
  style,
  type = "button",
  title,
}: {
  children?: ReactNode;
  variant?: BtnVariant;
  icon?: string;
  onClick?: () => void;
  disabled?: boolean;
  style?: CSSProperties;
  type?: "button" | "submit";
  title?: string;
}) {
  const base: CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    fontSize: 13,
    fontWeight: 700,
    borderRadius: radius.md,
    cursor: disabled ? "not-allowed" : "pointer",
    whiteSpace: "nowrap",
    opacity: disabled ? 0.55 : 1,
    padding: "9px 16px",
    border: "none",
    transition: "background .12s, border-color .12s",
  };
  const variants: Record<BtnVariant, { base: CSSProperties; hover: CSSProperties }> = {
    primary: {
      base: { background: color.brand, color: "#fff" },
      hover: { background: color.brandStrong },
    },
    secondary: {
      base: { border: `1px solid ${color.borderInput}`, background: "#fff", color: color.textNav, fontWeight: 600 },
      hover: { background: color.pageBg },
    },
    ghost: {
      base: { border: `1px solid ${color.border}`, background: "#fff", color: color.textNav, fontWeight: 600, fontSize: 12, padding: "5px 10px" },
      hover: { background: color.brandTintBg},
    },
  };
  return (
    <Interactive
      as="button"
      type={type}
      title={title}
      disabled={disabled}
      onClick={onClick}
      baseStyle={{ ...base, ...variants[variant].base, ...style }}
      hoverStyle={disabled ? {} : variants[variant].hover}
    >
      {icon ? <Icon name={icon} size={15} /> : null}
      {children}
    </Interactive>
  );
}

/* --------------------------- IconButton --------------------------- */
export function IconButton({
  icon,
  onClick,
  active,
  title,
  size = 32,
}: {
  icon: string;
  onClick?: () => void;
  active?: boolean;
  title?: string;
  size?: number;
}) {
  return (
    <Interactive
      as="button"
      title={title}
      onClick={onClick}
      baseStyle={{
        width: size,
        height: size,
        borderRadius: radius.md,
        border: `1px solid ${active ? color.brandTintBorder : color.border}`,
        background: active ? color.brandTintBg : "#fff",
        color: active ? color.brand : "#697283",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        cursor: "pointer",
      }}
      hoverStyle={{ background: active ? color.brandTintBg : color.pageBg }}
    >
      <Icon name={icon} size={17} />
    </Interactive>
  );
}

/* ------------------------------ Dot ------------------------------- */
export function Dot({ tone = "neutral", running, size = 6 }: { tone?: Tone; running?: boolean; size?: number }) {
  return (
    <span
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: toneColor[tone].dot,
        flex: "0 0 auto",
        animation: running ? "omPulse 1.2s ease-in-out infinite" : undefined,
      }}
    />
  );
}

/* ---------------------------- StatusBadge -------------------------- */
/** dot + 标签 + 可选值，浅底 chip（如证据源状态 / 服务状态）。 */
export function StatusBadge({
  label,
  value,
  tone = "neutral",
  running,
}: {
  label: ReactNode;
  value?: ReactNode;
  tone?: Tone;
  running?: boolean;
}) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
        fontWeight: 600,
        color: color.textBody,
        background: "#f5f6f8",
        border: `1px solid ${color.border}`,
        padding: "5px 10px",
        borderRadius: radius.md,
      }}
    >
      <Dot tone={tone} running={running} />
      {label}
      {value != null ? <span style={{ color: color.textSubtle, fontWeight: 500 }}>{value}</span> : null}
    </span>
  );
}

/* ------------------------------ Pill ------------------------------ */
/** 状态胶囊（active/draft/blocked 等），tone 决定配色。 */
export function Pill({
  children,
  tone = "neutral",
  icon,
  solid,
  style,
}: {
  children: ReactNode;
  tone?: Tone;
  icon?: string;
  solid?: boolean;
  style?: CSSProperties;
}) {
  const c = toneColor[tone];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        fontSize: 10.5,
        fontWeight: 600,
        color: c.text,
        background: solid ? c.bg : c.bg,
        border: `1px solid ${c.border}`,
        padding: "2px 8px",
        borderRadius: radius.pill,
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {icon ? <Icon name={icon} size={12} color={c.text} /> : null}
      {children}
    </span>
  );
}

/* ---------------------------- Toggle ------------------------------ */
/** 开关（设计稿 is_approval_required 等）。 */
export function Toggle({ on, onChange }: { on: boolean; onChange?: (v: boolean) => void }) {
  return (
    <div
      onClick={() => onChange?.(!on)}
      style={{
        width: 38,
        height: 22,
        borderRadius: radius.pill,
        background: on ? color.brand : "#d7dae0",
        cursor: "pointer",
        padding: 2,
        transition: "background .15s",
        flex: "0 0 auto",
      }}
    >
      <div
        style={{
          width: 18,
          height: 18,
          borderRadius: "50%",
          background: "#fff",
          boxShadow: "0 1px 2px rgba(0,0,0,.2)",
          transform: on ? "translateX(16px)" : "translateX(0)",
          transition: "transform .15s",
        }}
      />
    </div>
  );
}

/* --------------------------- SegRadio ----------------------------- */
/** 分段单选（scope_mode / status 等），当前项高亮。 */
export function SegRadio<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { label: string; value: T }[];
  value: T;
  onChange?: (v: T) => void;
}) {
  return (
    <div style={{ display: "flex", gap: 6 }}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <span
            key={o.value}
            onClick={() => onChange?.(o.value)}
            style={{
              fontSize: 12,
              fontWeight: 600,
              padding: "5px 12px",
              borderRadius: radius.md,
              cursor: "pointer",
              border: `1px solid ${active ? color.brand : color.border}`,
              color: active ? color.brand : color.textNav,
              background: active ? color.brandTintBg : "#fff",
            }}
          >
            {o.label}
          </span>
        );
      })}
    </div>
  );
}

/* ---------------------------- TextInput --------------------------- */
export function TextInput({
  value,
  onChange,
  placeholder,
  icon,
  mono,
  style,
  onKeyDown,
}: {
  value: string;
  onChange?: (v: string) => void;
  placeholder?: string;
  icon?: string;
  mono?: boolean;
  style?: CSSProperties;
  onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
}) {
  return (
    <div style={{ position: "relative", width: "100%" }}>
      {icon ? (
        <Icon
          name={icon}
          size={15}
          color={color.textSubtle}
          style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)" }}
        />
      ) : null}
      <input
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        style={{
          width: "100%",
          height: 36,
          border: `1px solid ${color.borderInput}`,
          borderRadius: radius.md,
          padding: icon ? "0 11px 0 33px" : "0 11px",
          fontSize: 13,
          outline: "none",
          fontFamily: mono ? font.mono : font.sans,
          background: "#fff",
          color: color.ink,
          ...style,
        }}
      />
    </div>
  );
}
