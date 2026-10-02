-- M1 example 3: repeat events by fault (correlated history + CTE + window rank).
WITH eligible AS (
    SELECT w.fault_code,
           EXISTS (
               SELECT 1 FROM work_orders prior
               WHERE prior.snapshot_id = w.snapshot_id
                 AND prior.asset_id = w.asset_id
                 AND prior.fault_code = w.fault_code
                 AND prior.resolved_at_us >= w.opened_at_us - 720 * 3600000000
                 AND prior.resolved_at_us < w.opened_at_us
           ) AS is_repeat
    FROM work_orders w
    JOIN active_snapshot active ON active.snapshot_id = w.snapshot_id
    WHERE w.opened_at_us >= :start_us AND w.opened_at_us < :end_us
), grouped AS (
    SELECT fault_code, SUM(is_repeat) AS repeat_orders,
           COUNT(*) AS opened_orders,
           ROUND(1.0 * SUM(is_repeat) / COUNT(*), 6) AS repeat_rate
    FROM eligible
    GROUP BY fault_code
)
SELECT fault_code, repeat_orders, opened_orders, repeat_rate,
       DENSE_RANK() OVER (ORDER BY repeat_rate DESC) AS rate_rank
FROM grouped
ORDER BY rate_rank, fault_code;
