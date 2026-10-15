# ADR-004 · Enmascarado por reescritura del AST, decidido por rol y posición

**Estado:** aceptado · 2026-09-02

## Contexto

La política (`docs/spec/policy.yaml`) dice, por rol, qué columnas son `allow`, `mask` o `deny`. Las
columnas sensibles pueden salir por la proyección, por un alias, dentro de una función, a través de una
vista (`v_customer.full_name` concatena tres columnas protegidas), por un predicado
(`WHERE iban LIKE 'ES91%'` extrae el valor sin imprimirlo) o por una agregación de grupo único.

## Opciones

| Opción | Pros | Contras |
|---|---|---|
| A · Post-procesar el resultado por nombre de columna | Simple, independiente del SQL | Se rompe con el primer alias; no ve predicados ni agregaciones |
| B · Vistas enmascaradas por rol en el motor | El motor hace el trabajo | Una vista por rol y tabla; los predicados siguen viendo el valor real |
| C · Decidir sobre el AST cualificado por rol, columna y posición, y reescribir la proyección antes de ejecutar | Sigue el linaje por alias, CTEs y vistas; cierra los canales laterales por predicado y agregación | El enmascarado depende del árbol validado y del orden de los anillos |

## Decisión

Se adopta C. R008 admite una columna `mask` solo en proyección directa y la rechaza en `WHERE`,
`JOIN ON`, `GROUP BY`, `ORDER BY`, `HAVING` y dentro de cualquier función; el rechazo publica la
alternativa (`age_band` en lugar de `birth_date`). Lo que sobrevive es un `exp.Column` desnudo, y
`mask/rewrite.py` sustituye su expresión conservando el alias. Se enmascara después de presupuestar,
para no cambiar el árbol que el estimador tarificó. Las transformaciones preservan `NULL`.

## Consecuencias

- Meta axioma `G-PII-LEAK`: 0 fugas en 177 comprobaciones sobre tres superficies, ejecutando cada
  consulta como `admin` y como el rol restringido y exigiendo que ningún valor real aparezca en la
  segunda salida.
- La medida tiene que ir por el camino que se ejecuta: el anillo llegó a estar fuera de
  `AuditedExecutor` con la suite en verde. Hoy `scripts/check_mask_path.py` lo impide; ver
  [`threat-model.md`](../threat-model.md) §5.2.
- `hash_estable` se calcula en el motor con una pimienta fija: las respuestas son comparables entre
  ejecuciones a cambio de poder correlacionar sesiones ([`threat-model.md`](../threat-model.md) §4.2).
- Señal de error: cualquier forma de proyección con columna `mask` que no sea un `exp.Column`; hoy se
  rechaza como `INTERNAL` en lugar de improvisar.
