import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api";
import type { AgentInstance, Me } from "./api/types";

interface AppCtx {
  me: Me | null;
  agents: AgentInstance[];
  currentAgentId: string;
  setCurrentAgentId: (id: string) => void;
  loading: boolean;
  /** demo：切换 user / platform_admin 身份（驱动后端 mock 头 + 侧栏模式）。 */
  toggleRole: () => void;
  refresh: () => void;
}

const Ctx = createContext<AppCtx | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [agents, setAgents] = useState<AgentInstance[]>([]);
  const [currentAgentId, setCurrentAgentId] = useState<string>("agt_pay_fast_recovery");
  const [loading, setLoading] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);
  const toggleRole = useCallback(() => {
    // demo：user ↔ admin 身份切换（角色事实在后端 DB）
    setMe((m) => {
      api.switchRole(!(m?.role === "platform_admin"));
      return m;
    });
    setTick((t) => t + 1);
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setBootError(null);
    (async () => {
      try {
        const m = await api.getMe();
        if (!alive) return;
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
        setBootError(err.message || "无法连接后端服务");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [tick]);

  return (
    bootError ? (
      <div style={{ height: "100vh", display: "flex", alignItems: "center", justifyContent: "center", flexDirection: "column", gap: 14, background: "#f7f8fa" }}>
        <div style={{ fontSize: 17, fontWeight: 700 }}>服务暂时不可用</div>
        <div style={{ fontSize: 13, color: "#788192", maxWidth: 420, textAlign: "center", lineHeight: 1.7 }}>{bootError}</div>
        <button onClick={refresh} style={{ padding: "8px 18px", borderRadius: 8, border: "1px solid #dfe3ea", background: "#fff", cursor: "pointer", fontSize: 13 }}>重试</button>
      </div>
    ) : (
    <Ctx.Provider value={{ me, agents, currentAgentId, setCurrentAgentId, loading, toggleRole, refresh }}>
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
