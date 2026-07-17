import { color, radius } from "../theme/tokens";
import { Icon } from "./Icon";
import { Interactive } from "./Interactive";

/** 页码式分页器（对齐后端 /assets/* 与 29.3 §2.2 的 page/page_size + total）。
 *
 * 仓内第一个分页组件：此前前端一律「一次拉全量、全量渲染」。分页必须是**服务端**的——
 * 拿到 total 才能显示总数/末页，且分页 + 客户端过滤会让每页数量错乱。
 * total<=pageSize 时不渲染（单页无需分页器）。 */
export function Pagination({
  page,
  pageSize,
  total,
  onPage,
  compact = false,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  /** 紧凑态（如插件页 250px 左树）：省掉「共 N 条」只留页码与箭头。 */
  compact?: boolean;
}) {
  const pages = Math.max(1, Math.ceil(total / Math.max(1, pageSize)));
  if (total <= pageSize) return null;

  const step = (delta: number) => {
    const next = Math.min(pages, Math.max(1, page + delta));
    if (next !== page) onPage(next);
  };
  const arrow = (name: string, delta: number, disabled: boolean, title: string) => (
    <Interactive
      onClick={() => { if (!disabled) step(delta); }}
      baseStyle={{
        display: "flex", alignItems: "center", justifyContent: "center",
        width: 22, height: 22, borderRadius: radius.md,
        cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.4 : 1,
      }}
      hoverStyle={disabled ? {} : { background: "#f0f1f4" }}
    >
      <Icon name={name} size={13} color={color.textSubtle} title={title} />
    </Interactive>
  );

  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: compact ? "center" : "flex-end",
      gap: 8, padding: compact ? "8px 4px" : "10px 2px",
      fontSize: 12, color: color.textSubtle, fontVariantNumeric: "tabular-nums",
    }}>
      {compact ? null : <span>共 {total} 条</span>}
      {arrow("chevron-left", -1, page <= 1, "上一页")}
      <span>{page} / {pages}</span>
      {arrow("chevron-right", 1, page >= pages, "下一页")}
    </div>
  );
}
