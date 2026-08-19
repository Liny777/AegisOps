-- 多规则命中同一告警按提示词分单（2026-08-19 拍板：提示词不同的规则分开建单各自诊断）。
-- 聚合单加 prompt 组标列；group_key 文本口径不动——改 key 格式会让部署切换瞬间
-- 旧格式未完结单对附着/冷却查询不可见，在途风暴双倍建单。
-- 幂等可重跑。存量行 prompt_group 恒 NULL，运行时按通配处理（附着/冷却对所有组生效），
-- 过渡期退化为旧「一组一单」语义，旧单完结后自然淡出。

ALTER TABLE sre_alert_incident ADD COLUMN IF NOT EXISTS prompt_group text;

COMMENT ON COLUMN sre_alert_incident.prompt_group IS 'prompt 组标：归一提示词（空白≡系统默认）的 sha256 前 12 位；同 group_key 下按组附着/冷却。NULL=本列上线前的存量单，查询按通配处理';
