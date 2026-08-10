-- 类别口径切内网 moType（2026-08-09 拍板）：PGSQL → PostgreSQL、ADS Docker → Docker。
-- 幂等可重跑；覆盖三张表：规则 match_json（categories[] 新形态与存量单值 category 两种）、
-- 事件 category、聚合单 category。
--
-- match_json 用 text 级替换：仅命中**带双引号的完整 token**（'"PGSQL"'），
-- 策略名如 "PGSQL 事务锁等待监控" 因引号内后随空格不受影响。
-- 残余风险：match_json.keywords / label_selectors 若恰好存了完整 "PGSQL" 值也会被换——
-- 本期模板 keywords 无此取值，Phase2 引入自定义 keywords 前此风险为零。

UPDATE sre_alert_rule
SET match_json = replace(replace(match_json::text, '"PGSQL"', '"PostgreSQL"'),
                         '"ADS Docker"', '"Docker"')::jsonb
WHERE match_json::text LIKE '%"PGSQL"%' OR match_json::text LIKE '%"ADS Docker"%';

UPDATE sre_alert_event    SET category = 'PostgreSQL' WHERE category = 'PGSQL';
UPDATE sre_alert_event    SET category = 'Docker'     WHERE category = 'ADS Docker';
UPDATE sre_alert_incident SET category = 'PostgreSQL' WHERE category = 'PGSQL';
UPDATE sre_alert_incident SET category = 'Docker'     WHERE category = 'ADS Docker';

-- ⚠ 可选段（默认注释，联调核对后由运维决定是否执行）：
-- 内网契约下 strategy_name ← metricName（指标名），与本地模板策略名（"MySQL 主从延迟监控"等）
-- 不是同一词表——存量勾选过 strategies 子集的规则在内网告警上将**永不命中**。
-- 置空 strategies = 该类型全部策略（两步向导起新建规则本就落空数组）。
-- 执行前先核对 29.11 的 metricName 取值域确认无法对齐再放开：
--
-- UPDATE sre_alert_rule
-- SET match_json = match_json - 'strategies'
-- WHERE jsonb_array_length(coalesce(match_json->'strategies', '[]'::jsonb)) > 0;
