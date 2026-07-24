/** 侧栏导航项类型（原定义在 Sidebar.tsx 内）。
 *
 * 单独成文件是为了打断一个类型层的环：垂直切片（src/studio/entry.tsx）要用 NavItem 声明
 * 自己的导航项，而 Sidebar.tsx 要 import 切片的导航常量。若 NavItem 留在 Sidebar.tsx，
 * 两个文件互指——`import type` + isolatedModules 下运行时无环，但工具链会告警，
 * 且哪天有人把 `import type` 手滑写成 `import` 就变真环。
 */
export interface NavItem {
  key: string;
  label: string;
  icon: string;
  to?: string;
  locked?: boolean;
}
