-- M1 example 1: resolved-period MTTR by model (JOIN + GROUP BY).
WITH eligible AS (
    SELECT a.model, w.resolved_at_us - w.opened_at_us AS duration_us
    FROM work_orders w
    JOIN assets a ON a.snapshot_id = w.snapshot_id AND a.asset_id = w.asset_id
    JOIN active_snapshot active ON active.snapshot_id = w.snapshot_id
    WHERE w.status = 'resolved'
      AND w.resolved_at_us >= :start_us AND w.resolved_at_us < :end_us
)
SELECT model, COUNT(*) AS resolved_orders,
       ROUND(SUM(duration_us) / 3600000000.0 / COUNT(*), 6) AS mttr_hours
FROM eligible
GROUP BY model
ORDER BY model;
