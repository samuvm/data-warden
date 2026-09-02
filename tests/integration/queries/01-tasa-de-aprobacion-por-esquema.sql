-- Consulta 1 de la fase 0, escrita a mano y sin una sola línea de IA.
--
-- Tasa de aprobación POR INTENTO y por esquema de tarjeta. Toca las dos trampas
-- declaradas que más se cometen contra esta tabla y las esquiva a propósito:
--
--   · `v_attempt_dedup` en vez de `fact_payment_attempt`: el 0,35 % son
--     duplicados de ingesta (trampa 9 del glosario).
--   · `NOT is_test`: el 1,2 % del tráfico son pruebas (trampa 6).
--
-- Y dice CUÁL de las dos tasas legítimas está calculando —por intento, no por
-- pago—, que es lo que el glosario exige y lo que evita dos números con el mismo
-- nombre.
SELECT
    c.card_scheme,
    count(*)                                                    AS intentos,
    count(*) FILTER (WHERE a.auth_status = 'approved')          AS aprobados,
    round(100.0 * count(*) FILTER (WHERE a.auth_status = 'approved') / count(*), 2)
                                                                AS tasa_aprobacion_pct
FROM v_attempt_dedup a
JOIN dim_card c ON c.card_sk = a.card_sk
WHERE NOT a.is_test
GROUP BY c.card_scheme
ORDER BY intentos DESC;
