-- M1 example 2: due-period SLA by priority (CTE + JOIN + GROUP BY).
WITH eligible AS (
    SELECT w.priority,
           CASE WHEN w.resolved_at_us <= w.due_at_us THEN 1 ELSE 0 END AS met_sla
    FROM work_orders w
    JOIN active_snapshot active ON active.snapshot_id = w.snapshot_id
    WHERE w.due_at_us >= :start_us AND w.due_at_us < :end_us
      AND w.due_at_us <= :as_of_us
)
SELECT priority, SUM(met_sla) AS met_orders, COUNT(*) AS due_orders,
       ROUND(1.0 * SUM(met_sla) / COUNT(*), 6) AS sla_attainment
FROM eligible
GROUP BY priority
ORDER BY priority;
