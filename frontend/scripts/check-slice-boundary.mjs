#!/usr/bin/env node
/**
 * 切片边界检查（构建前置守卫，由 package.json 的 prebuild 钩子自动执行）。
 *
 * 防的是什么
 * ==========
 * Agent Studio 垂直切片重构把若干组件从 `src/admin/` 迁到了 `src/studio/`，并把它们依赖的
 * 类型（`lib/api/types.ts`）与 api 方法（`OpenOpsApi`）一并移走。若某人的工作区里**新文件到位、
 * 旧文件没被删掉**（切分支/合并残留），旧副本仍会引用已经搬走的符号 —— 而 `tsc -b` 编译
 * `src/` 下**每一个** .tsx，与是否被 import 无关 ⇒ 一个没人用的孤儿文件足以打挂整个构建。
 *
 * 失败现场长这样（内网实测）：7 条 TS2305/TS2339 全部指向 src/admin/AgentStudioPage.tsx，
 * 报「Module '"../lib/api/types"' has no exported member 'StudioRunsPage'」。报错指向的文件
 * 看着像正常业务代码，实际它本就不该存在 —— 极难自行定位，故加本检查把它变成一句人话。
 *
 * 为什么用文件系统而不是 git
 * ==========================
 * 残留文件很可能是**未追踪**的（切分支时留下），`git ls-files` 根本看不见它，但 tsc 照样编译。
 * 用 git 查会漏掉最常见的那一半场景，因此这里一律以磁盘为准。
 *
 * 维护
 * ====
 * 今后再有 admin → studio（或类似）的文件迁移，往 MOVED_TO_STUDIO 补一项即可。
 * 刻意**不做通配/启发式**：本脚本挂在 prebuild 上，一旦误报会阻塞所有人构建，
 * 代价远高于漏掉一个边缘场景 —— 宁可漏报，绝不误报。
 */
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** 已从 src/admin/ 迁往 src/studio/ 的文件（PR #32 / #34 / #38 陆续搬迁）。 */
const MOVED_TO_STUDIO = [
  "AgentStudioPage.tsx",
  "StudioAgentCard.tsx",
  "StudioTranscript.tsx",
  "RunDetailView.tsx",
  "ReplayPage.tsx",
];

// 以脚本自身位置定位 frontend/，不依赖调用方 cwd。
const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const adminDir = join(frontendDir, "src", "admin");

const stale = MOVED_TO_STUDIO.filter((name) => existsSync(join(adminDir, name)));

if (stale.length > 0) {
  const list = stale.map((n) => `frontend/src/admin/${n}`);
  console.error("");
  console.error("✗ 发现切片残留文件（构建已中止）：");
  for (const p of list) console.error(`    ${p}`);
  console.error("");
  console.error("  这些文件已迁至 frontend/src/studio/，磁盘上的旧副本仍引用已搬走的类型与 api，");
  console.error("  会被 tsc 编译并报「has no exported member 'StudioRunsPage'」之类的错误。");
  console.error("  它们不该存在，删除即可，不涉及任何代码改动。");
  console.error("");
  console.error("  修复（在仓库根目录执行）：");
  console.error(`    git rm -f ${list.join(" ")}`);
  console.error("");
  console.error("  若提示 pathspec 不匹配，说明是未追踪残留（git 看不见它），改用：");
  console.error(`    rm ${list.join(" ")}`);
  console.error("");
  process.exit(1);
}
