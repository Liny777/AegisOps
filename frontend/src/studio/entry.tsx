/** Agent Studio 切片对 core 的**唯一**入口。
 *
 * App.tsx / Sidebar.tsx / AdminConsole.tsx 只准 import 本文件；切片内部的组件、api、
 * mockData、types 由 studio owner 随时改，core 不认识它们。
 *
 * ⚠ 本文件必须保持「轻」：**只做 `lazy()` 与常量声明，禁止顶层 import 任何切片组件/api/mockData**。
 *   App.tsx 顶层 import 本文件——一旦变重，studio 全部代码会被打进主 chunk，
 *   App.tsx 里那句「路由级 code splitting（S1）……主 chunk 曾 >500KB」就白做了。
 *   验证方式：`npm run build` 后 dist/assets 里必须仍有独立的 ReplayPage / AgentStudioPage chunk。
 */
import { lazy } from "react";
import { Route } from "react-router-dom";
import type { ReactElement } from "react";
import type { NavItem } from "../layout/navTypes";

const ReplayPage = lazy(() => import("./ReplayPage").then((m) => ({ default: m.ReplayPage })));
const AgentStudioPage = lazy(() => import("./AgentStudioPage").then((m) => ({ default: m.AgentStudioPage })));

/** 用户侧路由：core 在 <Routes> 的鉴权区里展开本数组即可，路径由切片自己决定。
 *  （React Router 的 createRoutesFromChildren 走 React.Children.forEach，会展开数组。） */
export const studioRoutes: ReactElement[] = [
  <Route key="studio-replay" path="/replay/:runId?" element={<ReplayPage />} />,
];

/** 侧栏项：**位置**（插在哪两项之间）是 core 的信息架构决策，**内容**（label/icon/路径）归切片。 */
export const STUDIO_USER_NAV: NavItem = { key: "replay", label: "回放", icon: "history", to: "/replay" };
export const STUDIO_ADMIN_NAV: NavItem = { key: "studio", label: "Agent Studio", icon: "telescope", to: "/admin/studio" };

/** 用户侧侧栏高亮：命中切片自己的路由就返回 nav key，否则 null（core 继续走它自己的分支）。 */
export const studioActiveKey = (pathname: string): string | null =>
  pathname.startsWith("/replay") ? STUDIO_USER_NAV.key : null;

/** 管理台子页：key / 标题 / 渲染三件一起给，AdminConsole 里不再散落字面量。 */
export const STUDIO_ADMIN_PAGE = {
  key: "studio",
  title: "Agent Studio",
  render: () => <AgentStudioPage />,
} as const;
