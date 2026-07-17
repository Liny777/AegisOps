import assert from "node:assert/strict";
import test from "node:test";

import { PRESET_QUESTIONS, PRESET_VARIABLE, presetSelection } from "./presetQuestions";

test("selects the variable so the next keystroke overwrites it", () => {
  const text = `帮我诊断 ${PRESET_VARIABLE} 告警`;
  const [start, end] = presetSelection(text);

  // 断言切片而非字面偏移量：改文案时这条会报警，硬编码的 [5, 9] 只会无声腐烂。
  assert.equal(text.slice(start, end), PRESET_VARIABLE);
});

test("puts an empty caret at the end when there is no variable", () => {
  for (const text of ["我有哪些告警？", "我看护的范围有哪些应用？"]) {
    assert.deepEqual(presetSelection(text), [text.length, text.length]);
  }
});

test("indexOf offsets agree with setSelectionRange's UTF-16 units", () => {
  const text = `帮我诊断 ${PRESET_VARIABLE} 告警`;
  // 全部字符在 BMP 内 → 无代理对，code point 数与 UTF-16 code unit 数相等，
  // 故 indexOf 算出的偏移量可以直接喂给 setSelectionRange。
  assert.equal([...text].length, text.length);
  assert.deepEqual(presetSelection(text), [5, 5 + PRESET_VARIABLE.length]);
});

test("ships three questions with unique ids and exactly one variable", () => {
  assert.equal(PRESET_QUESTIONS.length, 3);
  assert.equal(new Set(PRESET_QUESTIONS.map((q) => q.id)).size, PRESET_QUESTIONS.length);
  assert.equal(PRESET_QUESTIONS.filter((q) => q.text.includes(PRESET_VARIABLE)).length, 1);
});
