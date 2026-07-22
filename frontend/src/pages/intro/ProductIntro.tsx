import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { color, radius } from "../../theme/tokens";
import { Icon, Interactive, Button } from "../../ui";
import { Hero, Capabilities, HowItWorks, SubAgentMatrix, Tutorial, Safety, FooterCta } from "./sections";
import { CityScene } from "./CityScene";

/** 滚动分帧：7 个区块各对应 3D 背景的一个镜头帧，视口 40% 线扫过哪个区块就切到哪一帧 */
const FRAME_SECTION_IDS = ["intro-hero", "intro-caps", "intro-how", "intro-agents", "intro-tutorial", "intro-safety", "intro-cta"];

function useScrollStep(): number {
  const [step, setStep] = useState(0);
  useEffect(() => {
    let rafId = 0;
    const update = () => {
      rafId = 0;
      const line = window.innerHeight * 0.4;
      let active = 0;
      FRAME_SECTION_IDS.forEach((id, i) => {
        const el = document.getElementById(id);
        if (el && el.getBoundingClientRect().top <= line) active = i;
      });
      setStep(active);
    };
    const onScroll = () => {
      if (!rafId) rafId = requestAnimationFrame(update);
    };
    update(); // 支持带锚点/中途刷新进入
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);
  return step;
}

/** 窄屏判定（内联样式无媒体查询）：header 锚点链接在窄屏放不下，需要条件渲染 */
function useNarrow(maxWidth = 719): boolean {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const query = window.matchMedia(`(max-width: ${maxWidth}px)`);
    const update = () => setNarrow(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, [maxWidth]);
  return narrow;
}

/**
 * 产品介绍页（/intro，守卫外全屏路由：无需白名单/初始化即可看）。
 * 页面自身不调任何业务 API / useApp；后端不可用时 AppProvider 对 /intro 放行渲染
 * （见 appState.tsx isIntroPath）。未登录用户仍会被全局 SSO（IAM 401 跳转）拦截——内网口径。
 * 「开始使用」一律 nav("/")，由 HomeRedirect 按用户状态三态分流。
 */
export function ProductIntro() {
  const nav = useNavigate();
  const narrow = useNarrow();
  const step = useScrollStep();
  const start = () => nav("/");
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });

  return (
    <div style={{ minHeight: "100vh", position: "relative" }}>
      {/* 固定背景层：渐变底 + 3D 城市，滚动时保持不动，内容在上层滑过 */}
      <div
        aria-hidden
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 0,
          pointerEvents: "none",
          background: `
            radial-gradient(720px 380px at 18% -10%, rgba(79,157,255,.14), transparent 62%),
            radial-gradient(820px 420px at 88% 4%, rgba(22,131,255,.10), transparent 60%),
            ${color.pageBg}`,
        }}
      >
        <CityScene step={step} />
      </div>

      {/* 内容层：轻微底色面纱压暗城市，保证长文可读性 */}
      <div style={{ position: "relative", zIndex: 1, background: "rgba(247,248,250,.45)" }}>
      {/* sticky 细条 header：离开 AppShell 后的返回通道 */}
      <header
        style={{
          position: "sticky",
          top: 0,
          zIndex: 20,
          height: 56,
          background: "rgba(255,255,255,.86)",
          backdropFilter: "blur(10px)",
          WebkitBackdropFilter: "blur(10px)",
          borderBottom: `1px solid ${color.border}`,
          display: "flex",
          alignItems: "center",
          padding: "0 clamp(16px, 3vw, 28px)",
          gap: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
          <div
            style={{
              width: 30,
              height: 30,
              borderRadius: radius.md,
              background: color.brandGrad,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flex: "0 0 30px",
            }}
          >
            <Icon name="robot" size={17} color="#fff" />
          </div>
          <span style={{ fontSize: 15, fontWeight: 800, color: color.textStrong, whiteSpace: "nowrap" }}>
            感知快恢 Agent
          </span>
        </div>
        <nav style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 4 }}>
          {(narrow
            ? []
            : [
                { label: "工作方式", id: "intro-how" },
                { label: "子 Agent", id: "intro-agents" },
                { label: "使用教程", id: "intro-tutorial" },
              ]
          ).map((l) => (
            <Interactive
              key={l.id}
              as="button"
              onClick={() => scrollTo(l.id)}
              baseStyle={{
                border: "none",
                background: "transparent",
                padding: "7px 12px",
                borderRadius: radius.md,
                fontSize: 13,
                fontWeight: 600,
                color: color.textNav,
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
              hoverStyle={{ background: color.brandTintBg, color: color.brandStrong }}
            >
              {l.label}
            </Interactive>
          ))}
          <Button icon="arrow-right" onClick={start} style={{ marginLeft: 8 }}>
            进入工作台
          </Button>
        </nav>
      </header>

      <Hero onStart={start} onTutorial={() => scrollTo("intro-tutorial")} />
      <Capabilities />
      <HowItWorks />
      <SubAgentMatrix />
      <Tutorial />
      <Safety />
      <FooterCta onStart={start} />

      {/* 页脚：OpenOps 署名（命名口径：产品主体叫感知快恢 Agent，OpenOps 只在此署名） */}
      <footer
        style={{
          borderTop: `1px solid ${color.border}`,
          padding: "26px 24px 34px",
          textAlign: "center",
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 700, color: color.textNav }}>
          感知快恢 Agent <span style={{ color: color.textFaint, fontWeight: 400 }}>· 由 OpenOps 提供支持</span>
        </div>
        <div style={{ fontSize: 12, color: color.textSubtle }}>AI 可能出错，请核对关键操作与生产风险。</div>
      </footer>
      </div>
    </div>
  );
}
