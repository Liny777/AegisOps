-- 告警清单排序键索引（2026-08-20）：067de40 把清单排序从 last_seen_at（有索引）改为
-- (creation_date DESC, alert_event_id DESC)（稳定排序）时未补索引，清单页 count/翻页
-- 全表排序是「打开要 1-3s」的主因之一。幂等可重跑。
-- 表仅存 30 天滚动数据（expire_at 硬删），普通 CREATE INDEX 秒级即可；
-- 如在内网写入高峰执行，可改用 CREATE INDEX CONCURRENTLY（注意其不能包在事务里）。

CREATE INDEX IF NOT EXISTS ix_alert_event_created
  ON sre_alert_event (creation_date DESC, alert_event_id DESC);
ANALYZE sre_alert_event;
