# ADR-002 · Ejecutar el AST validado, verificado con `check_no_raw_sql.py` en lugar de semgrep

**Estado:** aceptado · 2026-09-01

## Contexto

Validar un texto y ejecutar otro abre la clase entera de ataques por diferencia de parser: lo que
sqlglot entiende no tiene por qué ser lo que DuckDB entiende. Si el motor acepta una cadena, basta con
que alguien llame a `execute(sql)` en vez de a `execute(validated)` para que los cinco anillos sean
decorativos. El diseño inicial listaba `semgrep` con reglas de inyección SQL como control estático.

## Opciones

| Opción | Pros | Contras |
|---|---|---|
| A · Ejecutar la cadena de entrada tras validarla | Sin coste de re-serialización | Diferencia de parser explotable; comentarios y formato llegan al motor |
| B · Ejecutar `ast.sql(dialect=...)` y vigilarlo con semgrep | Herramienta conocida | Sus reglas buscan concatenación de cadenas en cursores; aquí el SQL nunca se concatena, así que son un no-op con mantenimiento |
| C · Ejecutar `ast.sql(dialect=...)`; `Engine.execute()` solo acepta `ValidatedQuery`, verificado sobre el AST de Python | Comprueba la propiedad real, por tipo y por análisis estático | Script propio que mantener |

## Decisión

Se adopta C. Lo que llega al motor es el árbol validado re-serializado, sin comentarios.
`ValidatedQuery` solo se construye dentro del guard, `mypy --strict` impide pasar un `str`, y
`scripts/check_no_raw_sql.py` recorre `engines/` sobre su AST (no con `grep`) y falla si alguna firma
acepta texto. Se retira `semgrep`.

## Consecuencias

- No existe ninguna cadena que el guard lea de una manera y el motor de otra: el motor no lee nada que el
  guard no haya producido.
- Invariante I-02; meta axioma `G-NO-RAW-SQL` (0 hallazgos en cada gate).
- `AuditedExecutor` es el único camino a `Engine.execute()` (I-06), así que el tipo y la auditoría se
  refuerzan mutuamente.
- Señal de error: un adaptador de motor que necesite aceptar texto (por ejemplo, para SQL del sistema)
  obligaría a revisar esta decisión con otro ADR.
