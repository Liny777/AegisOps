/** 7x24 告警接管切片对 core 的**唯一**入口。
 *
 * App.tsx / Sidebar.tsx / SettingsPage.tsx 只准 import 本文件；切片内部的组件、api、
 * mockData、types 由 alerts owner 随时改，core 不认识它们。
 *
 * ⚠ 本文件必须保持「轻」：**只做 `lazy()` 与常量声明，禁止顶层 import 任何切片组件/api/mockData**。
 *   App.tsx 顶层 import 本文件——一旦变重，alerts 全部代码会被打进主 chunk，
 *   路由级 code splitting（S1）就白做了（studio/entry.tsx 同律）。
 *   验证方式：`npm run build` 后 dist/assets 里必须仍有独立的 AlertsPage / AlertRulesPane chunk。
 */
import { lazy } from "react";
import { Route } from "react-router-dom";
import type { ReactElement } from "react";
import type { NavItem } from "../layout/navTypes";

const AlertsPage = lazy(() => import("./AlertsPage").then((m) => ({ default: m.AlertsPage })));

/** 「设置 → 告警接管配置」的懒组件：SettingsHome（/settings/alerts）从本入口拿，配 <Suspense> 使用。 */
export const AlertRulesPane = lazy(() => import("./AlertRulesPane").then((m) => ({ default: m.AlertRulesPane })));

/** 用户侧路由：core 在 <Routes> 的鉴权区里展开本数组即可，路径由切片自己决定。 */
export const alertsRoutes: ReactElement[] = [
  <Route key="alerts-list" path="/alerts/:incidentId?" element={<AlertsPage />} />,
];

/** 侧栏项：**位置**（插在哪两项之间）是 core 的信息架构决策，**内容**（label/icon/路径）归切片。 */
export const ALERTS_USER_NAV: NavItem = { key: "alerts", label: "告警接管清单", icon: "bell-bolt", to: "/alerts" };

/** 用户侧侧栏高亮：命中切片自己的路由就返回 nav key，否则 null（core 继续走它自己的分支）。 */
export const alertsActiveKey = (pathname: string): string | null =>
  pathname.startsWith("/alerts") ? ALERTS_USER_NAV.key : null;
