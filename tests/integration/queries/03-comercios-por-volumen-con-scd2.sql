-- Consulta 3 de la fase 0. La trampa del SCD tipo 2, esquivada a propósito.
--
-- `dim_merchant` es una dimensión de cambio lento tipo 2: un comercio tiene VARIAS
-- filas, una por versión, y `fact_settlement_batch` guarda la clave NATURAL
-- (`merchant_id`), no la subrogada. Unir sin acotar a la versión vigente multiplica
-- las filas un 53 % y reescribe la historia: es la trampa 2 del glosario, y es la
-- que convierte un ranking de comercios en un ranking inventado.
--
-- Aquí se une contra `v_merchant_current`, que ya filtra `is_current`.
SELECT
    m.merchant_id,
    m.trade_name,
    m.category,
    sum(b.gross_eur_minor)  AS volumen_procesado,
    sum(b.fee_eur_minor)    AS ingreso_bruto
FROM fact_settlement_batch b
JOIN v_merchant_current   m ON m.merchant_id = b.merchant_id
GROUP BY m.merchant_id, m.trade_name, m.category
ORDER BY volumen_procesado DESC
LIMIT 10;
