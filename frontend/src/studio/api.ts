/** Agent Studio 切片的 API 客户端（与 core 的 lib/api 门面平级但独立）。
 *
 * 只依赖 core 传输层的公开物：`lib/api/client` 的 `apiFetch` + `API_MODE`
 * （envelope 解析 / 401→IAM 跳转 / mock 身份头都由它统一处理）。切片新增端点只改本文件，
 * **不用再去动 core 那个 130 方法的 OpenOpsApi 大接口**。
 *
 * ⚠ `realStudioApi` / `mockStudioApi` 都必须显式标注 `: StudioApi`——这是 mock/real
 *   方法集不漂移的**唯一编译期保障**（core 的 realApi/mockApi 同做法）。
 */
import { API_MODE, apiFetch } from "../lib/api/client";
import * as M from "./mockData";
import type { StudioMessage, StudioRunDetail, StudioRunsPage } from "./types";

export interface StudioApi {
  /** 管理员：某用户的 run 列表（含软删）+ span 聚合指标。 */
  adminStudioRuns(userId: string, params?: { page?: number; pageSize?: number }): Promise<StudioRunsPage>;
  /** 管理员：run 详情（span 按 agent 实例分组 + 交接账本 + rollup）。 */
  adminStudioRunDetail(runId: string): Promise<StudioRunDetail>;
  /** 管理员：run 对话记录（用户↔助手全文；含软删 run）。mock runtime 不落对话 → 空数组。 */
  adminStudioRunMessages(runId: string): Promise<StudioMessage[]>;
  /** 用户回放：owner-only；LLM 输入 messages 由**服务端**置空（平台提示词不外露）。 */
  replayRunDetail(runId: string): Promise<StudioRunDetail>;
  replayRunMessages(runId: string): Promise<StudioMessage[]>;
}

const realStudioApi: StudioApi = {
  async adminStudioRuns(userId, params) {
    const s = new URLSearchParams({ user_id: userId });
    s.set("page", String(params?.page ?? 1));
    s.set("page_size", String(params?.pageSize ?? 20));
    return apiFetch<StudioRunsPage>(`/openops/v1/admin/studio/runs?${s.toString()}`);
  },
  async adminStudioRunDetail(runId) {
    return apiFetch<StudioRunDetail>(`/openops/v1/admin/studio/runs/${encodeURIComponent(runId)}`);
  },
  async adminStudioRunMessages(runId) {
    return apiFetch<StudioMessage[]>(`/openops/v1/admin/studio/runs/${encodeURIComponent(runId)}/messages`);
  },
  async replayRunDetail(runId) {
    return apiFetch<StudioRunDetail>(`/openops/v1/agent-runs/${encodeURIComponent(runId)}/replay`);
  },
  // ⚠ 这个打的是 **core** 的 /agent-runs/{id}/messages（api/routers/runs.py），不是 studio 端点：
  //    后端 service 层已与 core 共用 project_transcript，再造一个 studio 端点是重复实现。
  //    代价是 core 改这个响应结构时本切片要跟着改——见 src/studio/README.md 的协调清单。
  async replayRunMessages(runId) {
    return apiFetch<StudioMessage[]>(`/openops/v1/agent-runs/${encodeURIComponent(runId)}/messages`);
  },
};

const delay = <T,>(v: T, ms = 120): Promise<T> => new Promise((r) => setTimeout(() => r(v), ms));

const mockStudioApi: StudioApi = {
  adminStudioRuns: (userId) => delay(M.mockStudioRuns(userId)),
  adminStudioRunDetail: (runId) => delay(M.mockStudioRunDetail(runId)),
  adminStudioRunMessages: (runId) => delay(M.mockStudioRunMessages(runId)),
  // 镜像后端语义：用户回放不下发 LLM 输入。**不是样板代码**——是安全语义的 mock 侧镜像，
  // 简化成 delay(d) 会让 mock 档看起来「用户也能看到平台提示词」，误导评审。
  replayRunDetail: (runId) => {
    const d = M.mockStudioRunDetail(runId);
    for (const a of d.agents) for (const c of a.calls) if (c.kind === "llm") c.input = "";
    return delay(d);
  },
  replayRunMessages: (runId) => delay(M.mockStudioRunMessages(runId)),
};

export const studioApi: StudioApi = API_MODE === "real" ? realStudioApi : mockStudioApi;
