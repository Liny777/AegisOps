import { createContext, useCallback, useContext, useEffect, useState } from "react";
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
    Promise.all([api.getMe(), api.listAgents()]).then(([m, a]) => {
      if (!alive) return;
      setMe(m);
      setAgents(a);
      if (m.recent_instance_id) setCurrentAgentId(m.recent_instance_id);
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [tick]);

  return (
    <Ctx.Provider value={{ me, agents, currentAgentId, setCurrentAgentId, loading, toggleRole, refresh }}>
      {children}
    </Ctx.Provider>
  );
}

export function useApp(): AppCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useApp must be used within AppProvider");
  return c;
}
