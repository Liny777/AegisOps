import assert from "node:assert/strict";
import test from "node:test";

import { normalizeAlertSeverity, parseAlertEntryParams } from "./alertEntry";

// 纯函数测试不碰 window/sessionStorage（capture/peek/consume 归浏览器链路手验）。

test("解析全参：entry_source=alert + 类别 + 英文级别", () => {
  assert.deepEqual(
    parseAlertEntryParams("?entry_source=alert&alert_category=MySQL&alert_severity=critical"),
    { source: "alert", category: "MySQL", severity: "critical" },
  );
});

test("解析数字级别与类别 trim（原样保留内容）", () => {
  assert.deepEqual(
    parseAlertEntryParams("?entry_source=alert&alert_category=%20EKS%20&alert_severity=1"),
    { source: "alert", category: "EKS", severity: "fatal" },
  );
});

test("他家参数（?q= 等）不干扰解析", () => {
  assert.deepEqual(
    parseAlertEntryParams("?q=hello&entry_source=alert&alert_category=Docker&alert_severity=warning"),
    { source: "alert", category: "Docker", severity: "warning" },
  );
});

test("缺 entry_source → null", () => {
  assert.equal(parseAlertEntryParams("?alert_category=MySQL&alert_severity=critical"), null);
});

test("entry_source 非 alert → null", () => {
  assert.equal(
    parseAlertEntryParams("?entry_source=webhook&alert_category=MySQL&alert_severity=critical"),
    null,
  );
});

test("缺/空白 alert_category → null", () => {
  assert.equal(parseAlertEntryParams("?entry_source=alert&alert_severity=critical"), null);
  assert.equal(parseAlertEntryParams("?entry_source=alert&alert_category=%20%20&alert_severity=critical"), null);
});

test("severity 非法/缺失 → 整体 null（宁缺勿错）", () => {
  for (const search of [
    "?entry_source=alert&alert_category=MySQL",
    "?entry_source=alert&alert_category=MySQL&alert_severity=",
    "?entry_source=alert&alert_category=MySQL&alert_severity=0",
    "?entry_source=alert&alert_category=MySQL&alert_severity=5",
    "?entry_source=alert&alert_category=MySQL&alert_severity=urgent",
  ]) {
    assert.equal(parseAlertEntryParams(search), null, search);
  }
});

test("normalizeAlertSeverity 表驱动：英文枚举 + 数字表 + 非法档", () => {
  const table: [string | null, ReturnType<typeof normalizeAlertSeverity>][] = [
    ["fatal", "fatal"],
    ["critical", "critical"],
    ["warning", "warning"],
    ["info", "info"],
    ["1", "fatal"],
    ["2", "critical"],
    ["3", "warning"],
    ["4", "info"],
    [" critical ", "critical"],  // trim 后归一
    ["0", null],  // SLO 档不进接管口径
    ["5", null],
    ["", null],
    ["FATAL", null],  // 大小写敏感（上游契约小写）
    [null, null],
  ];
  for (const [raw, expected] of table) {
    assert.equal(normalizeAlertSeverity(raw), expected, String(raw));
  }
});
