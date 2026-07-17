// 写入 CopilotChat composer 的单一入口（原 CopilotSkillSlash 内的 DOM 方案抽出共用）。
//
// composer 是 CopilotKit 内部的 CopilotChatInput：项目只经 slot 传 className，从不传
// inputValue/onInputChange，所以 textarea 非受控、state 在 vendor 内部——没有 setInput 可调，
// 只能原生 setter 写值 + 派发 input 事件让其受控态跟上。

const NATIVE_SETTER = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;

export function composerTextarea(): HTMLTextAreaElement | null {
  return document.querySelector<HTMLTextAreaElement>(".copilot-chat-panel textarea");
}

/**
 * 写入 composer 并让 vendor 的受控态更新；select 给出则选中该区间，否则光标落末尾。
 *
 * setSelectionRange 放在 dispatchEvent 之后是安全的：input 在 React 19 里是 discrete 优先级，
 * dispatchEvent 返回时已 re-render + commit 完，且 commit 见 node.value 已等于新值会跳过 DOM
 * 写入，选区得以保留。
 */
export function writeComposer(text: string, select?: readonly [number, number]): boolean {
  const ta = composerTextarea();
  if (!ta || !NATIVE_SETTER) return false;
  NATIVE_SETTER.call(ta, text);
  ta.dispatchEvent(new Event("input", { bubbles: true }));
  ta.focus();
  ta.setSelectionRange(select?.[0] ?? text.length, select?.[1] ?? text.length);
  return true;
}
