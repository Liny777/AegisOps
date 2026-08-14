-- 迁移 2026-08-15：v3 并发治理落地（置顶列）+ 订阅下线（表删除）
-- 适用：已跑过 slices/alerts.sql 的存量库（内网升级顺序：先跑本文件再发新版后端）。
-- 全新库直接跑最新 slices/alerts.sql，无需本文件。

-- 1) 必跑：聚合单加管理员置顶列（§5.3 软插队；老版本后端不读此列，先加列后发版零风险）
ALTER TABLE sre_alert_incident ADD COLUMN IF NOT EXISTS manual_priority boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN sre_alert_incident.manual_priority IS '管理员置顶（§5.3 软插队）：有效优先级恒 -1 排队首；仅 queued 可置顶/取消，不打断在跑诊断';

-- 2) 可选：订阅表下线（2026-08-15 拍板去掉实例总开关，匹配面只看 白名单+规则启用）。
--    新版后端不再读写此表，不跑也无害（表躺着无人读写）；要清理时执行：
-- DROP TABLE IF EXISTS sre_alert_subscription;
