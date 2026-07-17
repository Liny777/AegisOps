// 空会话预设问题 chips：点击把模版写入 composer 供用户编辑，绝不自动发送。
//
// 渲染在 OpenOpsChatMessageView 的空消息分支里（走正常文档流，首条消息落地即自动消失），
// 而不是 CopilotKit 官方 welcomeScreen/suggestions：前者的分支因面板恒传 threadId 不可达，
// 后者类型上传不进去且点击即发送——与「填入待编辑」相反。
import { createContext, useContext, useMemo, type ReactNode } from "react";

import { writeComposer } from "./composerBridge";
import { PRESET_QUESTIONS, presetSelection } from "./presetQuestions";

/**
 * readOnly / 连接态经 context 下传，不做 memo 组件工厂：messageView 走 vendor 的
 * useShallowStableRef，工厂会在入参变化时 mint 出新组件类型 → 整棵子树 remount。
 */
const ChatPresetsContext = createContext<{ enabled: boolean }>({ enabled: false });

export function ChatPresetsProvider({
  enabled,
  children,
}: {
  enabled: boolean;
  children: ReactNode;
}) {
  const value = useMemo(() => ({ enabled }), [enabled]);
  return <ChatPresetsContext.Provider value={value}>{children}</ChatPresetsContext.Provider>;
}

export function CopilotPresetQuestions() {
  const { enabled } = useContext(ChatPresetsContext);
  if (!enabled) return null;

  return (
    <div className="oa-preset-questions" data-testid="preset-questions">
      <div className="oa-preset-questions-hint">可以先从这些问题开始：</div>
      <div className="oa-preset-questions-row">
        {PRESET_QUESTIONS.map((question) => (
          <button
            key={question.id}
            type="button"
            className="oa-preset-chip"
            data-testid={`preset-${question.id}`}
            onClick={() => writeComposer(question.text, presetSelection(question.text))}
          >
            {question.text}
          </button>
        ))}
      </div>
    </div>
  );
}
