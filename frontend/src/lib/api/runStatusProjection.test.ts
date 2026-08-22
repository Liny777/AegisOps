import assert from "node:assert/strict";
import test from "node:test";

import { IDLE_DORMANT_REASON, isDormantRun, projectRunStatus } from "./projection";

/**
 * 守「会话过一会就锁住」这个回归：idle 回收关掉的 run 只是休眠，后端下次发消息会自动
 * 复开同一条 run，所以前端一切可用性判定都必须把它当 active。若有人把这里改回
 * 「closed 一律只读」，输入框会重新被锁死。
 */

test("idle 回收关闭 = 休眠：投影成 active（输入框不能锁）", () => {
  assert.equal(projectRunStatus("closed", IDLE_DORMANT_REASON), "active");
  assert.equal(isDormantRun("closed", IDLE_DORMANT_REASON), true);
});

test("用户主动关闭（reason 为空）= 真只读", () => {
  for (const reason of [null, undefined, ""]) {
    assert.equal(projectRunStatus("closed", reason), "closed");
    assert.equal(isDormantRun("closed", reason), false);
  }
});

test("管理员终止等其它 reason 是真只读", () => {
  for (const reason of ["admin_terminated", "blocked", "user_closed"]) {
    assert.equal(projectRunStatus("closed", reason), "closed");
  }
});

test("alert_idle 也算系统关闭——口径必须与后端复开守卫一致", () => {
  // 后端 reopen_alert_run 认 ('idle_timeout','alert_idle')；前端只认前者的话，
  // 等 alert_run_idle_ttl_minutes 旋钮实现，能复开的告警会话会被前端白白锁死。
  assert.equal(projectRunStatus("closed", "alert_idle"), "active");
  assert.equal(isDormantRun("closed", "alert_idle"), true);
});

test("active 的 run 恒 active，不受 reason 干扰", () => {
  assert.equal(projectRunStatus("active", null), "active");
  assert.equal(projectRunStatus("active", IDLE_DORMANT_REASON), "active");
  assert.equal(isDormantRun("active", IDLE_DORMANT_REASON), false);
});

test("缺字段不崩：未知/缺失 run_status 按可用处理，不误锁", () => {
  assert.equal(projectRunStatus(undefined, undefined), "active");
  assert.equal(projectRunStatus(null, null), "active");
});
