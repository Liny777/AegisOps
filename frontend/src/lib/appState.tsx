import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  api,
  invalidateConversationHistory,
  isAbortError,
  subscribeConversationHistory,
} from "./api";
import type { AgentInstance, Conversation, Me } from "./api/types";

interface AppCtx {
  me: Me | null;
  agents: AgentInstance[];
  conversations: Conversation[];
  conversationsLoading: boolean;
  currentAgentId: string;
  setCurrentAgentId: (id: string) => void;
  loading: boolean;
  refresh: () => void;
  /** 显式失效历史列表；新建/改名/关闭/删除成功时 API facade 会自动调用。 */
  refreshConversations: () => void;
}

const Ctx = createContext<AppCtx | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [agents, setAgents] = useState<AgentInstance[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(false);
  const [currentAgentId, setCurrentAgentId] = useState<string>("agt_pay_fast_recovery");
  const [loading, setLoading] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const hasLoadedIdentity = useRef(false);

  const refresh = useCallback(() => setTick((t) => t + 1), []);
  const refreshConversations = useCallback(() => invalidateConversationHistory(), []);

  useEffect(() => {
    let alive = true;
    // 首次身份加载阻塞路由；后续切角色/刷新 Agent 列表在后台完成，避免 WhitelistGuard
    // 临时用 Loading 替换 AppShell，导致正在运行的 Workbench/CopilotKit 被卸载。
    if (!hasLoadedIdentity.current) setLoading(true);
    setBootError(null);
    (async () => {
      try {
        const m = await api.getMe();
        if (!alive) return;
        hasLoadedIdentity.current = true;
        setMe(m);
        if (m.recent_instance_id) setCurrentAgentId(m.recent_instance_id);
        // 未开通用户 listAgents 必 403——跳过拉取，守卫按 whitelisted=false 引到开通引导页；
        // 已开通但列表瞬断也不阻塞进壳（各页面自兜底）
        if (m.whitelisted) {
          try {
            const a = await api.listAgents();
            if (alive) setAgents(a);
          } catch { /* 列表失败不阻塞 */ }
        }
      } catch (e) {
        if (!alive) return;
        const err = e as { code?: string; message?: string };
        if (err.code === "AUTH_REDIRECT") return; // 正在跳 IAM 登录页，保持加载态
        if (hasLoadedIdentity.current) {
          console.warn("[OpenOps] 后台刷新身份失败，保留当前会话：", err.message || e);
        } else {
          setBootError(err.message || "无法连接后端服务");
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [tick]);

  useEffect(() => {
    if (!me?.whitelisted) {
      setConversations([]);
      setConversationsLoading(false);
      return;
    }

    let alive = true;
    let controller: AbortController | null = null;
    const load = () => {
      controller?.abort();
      controller = new AbortController();
      const signal = controller.signal;
      setConversationsLoading(true);
      api.listConversations({ signal }).then((next) => {
        if (alive && !signal.aborted) setConversations(next);
      }).catch((error) => {
        if (!isAbortError(error)) {
          console.warn("[OpenOps] 历史会话刷新失败，保留现有列表：", error);
        }
      }).finally(() => {
        if (alive && controller?.signal === signal && !signal.aborted) {
          setConversationsLoading(false);
        }
      });
    };

    const unsubscribe = subscribeConversationHistory(load);
    load();
    return () => {
      alive = false;
      controller?.abort();
      unsubscribe();
    };
  }, [me?.user_id, me?.whitelisted]);

  return (
    bootError ? (
      <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 14, background: "#f7f8fa" }}>
        <div style={{ fontSize: 17, fontWeight: 700 }}>服务暂时不可用</div>
        <div style={{ fontSize: 13, color: "#788192", maxWidth: 420, textAlign: "center", lineHeight: 1.7 }}>{bootError}</div>
        <button onClick={refresh} style={{ padding: "8px 18px", borderRadius: 8, border: "1px solid #dfe3ea", background: "#fff", cursor: "pointer", fontSize: 13 }}>重试</button>
      </div>
    ) : (
    <Ctx.Provider value={{
      me,
      agents,
      conversations,
      conversationsLoading,
      currentAgentId,
      setCurrentAgentId,
      loading,
      refresh,
      refreshConversations,
    }}>
      {children}
    </Ctx.Provider>
    )
  );
}

export function useApp(): AppCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useApp must be used within AppProvider");
  return c;
}

/** 让 URL 指向的实例成为「当前 Agent」并确保它在全局列表里。
 *
 * 全局 agents 只在应用挂载时拉一次；向导激活新实例后是 SPA 内导航（不重载页面），列表里没有新实例
 * → 侧栏 picker 兜底显示旧 Agent、设置页也看不到它（实测 bug）。缺失即 refresh 重拉，幂等收敛。 */
export function useSyncCurrentAgent(instanceId?: string) {
  const { agents, loading, setCurrentAgentId, refresh } = useApp();
  const lastPulled = useRef<string | null>(null); // 每个 instanceId 只兜底重拉一次：坏 id 不进刷新循环
  useEffect(() => {
    if (!instanceId) return;
    setCurrentAgentId(instanceId);
    // 列表缺当前实例即重拉。不能加 agents.length>0 门——全部删光后新建的场景列表就是空的，
    // 有门会拦死重拉，侧栏 picker 一直「选择 Agent」直到手动刷新（实测 bug）。
    if (!loading && !agents.some((a) => a.instance_id === instanceId) && lastPulled.current !== instanceId) {
      lastPulled.current = instanceId;
      refresh();
    }
  }, [instanceId, agents, loading, setCurrentAgentId, refresh]);
}
