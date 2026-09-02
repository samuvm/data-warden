-- Consulta 2 de la fase 0. Las tres métricas de dinero con su nombre canónico.
--
-- G-2 de la firma de Q-004 partió «ingreso» en tres, porque llamar «ingreso» a dos
-- cosas distintas es lo que produce el error. Aquí salen las tres a la vez, sobre
-- el ÚNICO grano donde «cuánto nos han pagado» tiene una sola respuesta:
-- `fact_settlement_batch`, ya deduplicado, en euros y sin tráfico de pruebas.
--
-- El periodo se ancla a `max(settlement_date)` del propio dataset y NUNCA a
-- `current_date` (G-3 y G-4 de la firma): con un dataset congelado, `current_date`
-- vacía la métrica sola con el paso del tiempo.
WITH referencia AS (
    SELECT max(settlement_date) AS hasta FROM fact_settlement_batch
)
SELECT
    date_trunc('month', b.settlement_date)                       AS mes,
    sum(b.gross_eur_minor)                                       AS volumen_procesado,
    sum(b.fee_eur_minor)                                         AS ingreso_bruto,
    sum(b.fee_eur_minor - b.interchange_eur_minor - b.scheme_fee_eur_minor)
                                                                 AS margen_neto,
    round(100.0 * sum(b.fee_eur_minor) / nullif(sum(b.gross_eur_minor), 0), 3)
                                                                 AS mdr_efectivo_pct
FROM fact_settlement_batch b, referencia r
WHERE b.settlement_date >= date_trunc('month', r.hasta)
  AND b.settlement_date <  date_trunc('month', r.hasta) + INTERVAL 1 MONTH
GROUP BY 1
ORDER BY 1;
