-- analysis_queries.sql
--
-- Analysis queries for the cleaned CRM pipeline data in data/pipeline.db.
-- Run against tables: leads(lead_id, company_name, lead_source, rep_id,
-- stage, deal_value, is_member, created_date, close_date),
-- reps(rep_id, rep_name, region, hire_date),
-- activities(activity_id, lead_id, rep_id, activity_date, activity_type).
--
-- Canonical stage values: New, Contacted, Qualified, Proposal, Negotiation,
-- Won, Lost. ('Won','Lost') = closed; everything else = open.


-- 1. Pipeline value by stage
SELECT
    stage,
    COUNT(*)              AS num_deals,
    SUM(deal_value)        AS total_pipeline_value,
    ROUND(AVG(deal_value), 2) AS avg_deal_value
FROM leads
GROUP BY stage
ORDER BY total_pipeline_value DESC;


-- 2. Win rate (won / all closed deals)
SELECT
    SUM(CASE WHEN stage = 'Won' THEN 1 ELSE 0 END)                       AS deals_won,
    SUM(CASE WHEN stage = 'Lost' THEN 1 ELSE 0 END)                      AS deals_lost,
    SUM(CASE WHEN stage IN ('Won', 'Lost') THEN 1 ELSE 0 END)            AS deals_closed,
    ROUND(
        100.0 * SUM(CASE WHEN stage = 'Won' THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN stage IN ('Won', 'Lost') THEN 1 ELSE 0 END), 0),
        2
    ) AS win_rate_pct
FROM leads;


-- 3. Conversion rate by lead source (won / total leads from that source)
SELECT
    lead_source,
    COUNT(*)                                              AS total_leads,
    SUM(CASE WHEN stage = 'Won' THEN 1 ELSE 0 END)        AS won_leads,
    ROUND(100.0 * SUM(CASE WHEN stage = 'Won' THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                            AS conversion_rate_pct
FROM leads
GROUP BY lead_source
ORDER BY conversion_rate_pct DESC;


-- 4. Average sales cycle length, in days (created_date -> close_date, Won deals)
SELECT
    ROUND(AVG(julianday(close_date) - julianday(created_date)), 1) AS avg_sales_cycle_days,
    COUNT(*) AS won_deals_with_close_date
FROM leads
WHERE stage = 'Won'
  AND close_date IS NOT NULL
  AND created_date IS NOT NULL;


-- 5. Stalled deals: still open, and no activity in the last 30+ days
--    (or no activity at all -> treated as stalled since day 0)
SELECT
    l.lead_id,
    l.company_name,
    l.stage,
    l.rep_id,
    l.deal_value,
    MAX(a.activity_date) AS last_activity_date,
    CASE
        WHEN MAX(a.activity_date) IS NULL THEN NULL
        ELSE CAST(julianday('now') - julianday(MAX(a.activity_date)) AS INTEGER)
    END AS days_since_last_activity
FROM leads l
LEFT JOIN activities a ON a.lead_id = l.lead_id
WHERE l.stage NOT IN ('Won', 'Lost')
GROUP BY l.lead_id, l.company_name, l.stage, l.rep_id, l.deal_value
HAVING last_activity_date IS NULL
    OR julianday('now') - julianday(last_activity_date) >= 30
ORDER BY days_since_last_activity IS NOT NULL, days_since_last_activity DESC;


-- 6. Rep performance: deal count, win rate, and won value per rep
SELECT
    r.rep_id,
    r.rep_name,
    r.region,
    COUNT(l.lead_id)                                                  AS total_deals,
    SUM(CASE WHEN l.stage = 'Won' THEN 1 ELSE 0 END)                  AS deals_won,
    ROUND(
        100.0 * SUM(CASE WHEN l.stage = 'Won' THEN 1 ELSE 0 END)
        / NULLIF(SUM(CASE WHEN l.stage IN ('Won', 'Lost') THEN 1 ELSE 0 END), 0),
        2
    ) AS win_rate_pct,
    SUM(CASE WHEN l.stage = 'Won' THEN l.deal_value ELSE 0 END)       AS won_value
FROM reps r
LEFT JOIN leads l ON l.rep_id = r.rep_id
GROUP BY r.rep_id, r.rep_name, r.region
ORDER BY won_value DESC;


-- 7. Member vs. non-member revenue (won deals only)
SELECT
    CASE WHEN is_member = 1 THEN 'Member' ELSE 'Non-Member' END AS customer_type,
    COUNT(*)                    AS won_deals,
    SUM(deal_value)              AS total_revenue,
    ROUND(AVG(deal_value), 2)    AS avg_deal_value
FROM leads
WHERE stage = 'Won'
GROUP BY customer_type
ORDER BY total_revenue DESC;
