# Contratos propios de Data Warden

Aquí viven los contratos que **este proyecto define y escribe**. No confundir con `docs/CONTRACTS/`, que son
copias literales e inmutables de `_comun/CONTRACTS/`, están en `permissions.deny`, y donde un `diff` contra el
original es un test (CONSTITUCION §1.1 e invariante I-17 de `docs/RULES.md`).

| Fichero | Fase | Qué fija | Quién lo escribe |
|---|---|---|---|
| `policy.yaml` | 1 | Matriz rol × columna × nivel (`allow` / `mask` / `deny`). Sus claves son subconjunto exacto de `catalog/generated/schema.json` | **Samuel** (Q-003). Es decisión de negocio |
| `glossary.yaml` | 1 | Semántica de negocio de tablas y métricas. Se expone como recurso MCP | **Samuel** (Q-004) |
| `resultset-equality.md` | 1 | Cuándo dos resultsets son iguales: orden de filas, orden y nombres de columna, tolerancia en flotantes, `Decimal` vs `float`, `NULL` vs `''`, vacío, duplicados como multiset, temporales en UTC, timeout y rechazo en el denominador | Agente |
| `audit-record.schema.json` | 1 | Forma del registro de auditoría, con canonicalización JCS (RFC 8785) declarada | Agente |
| `rejection.schema.json` | 1 | Forma del mensaje de rechazo accionable: `rule_id`, motivo y `sugerencia` no vacía, sin revelar valores de celda | Agente |

`G-CONTRACTS-FROZEN` cuenta **cuatro contratos** aquí —`policy.yaml`, `resultset-equality.md`,
`audit-record.schema.json` y `rejection.schema.json`—; `glossary.yaml` lo verifica `G-CATALOG-FRESH` junto con
`policy.yaml`, porque lo que se comprueba de él es que sus claves salgan del catálogo generado.
`domain/types.py` **no es un contrato**: es código congelado, y lo verifican `mypy --strict` y el snapshot de
la fase 1.

Nada de este directorio es copia de `_comun/`. Si alguna vez hace falta cambiar un contrato **transversal**,
se edita en `_comun/CONTRACTS/`, se sube su versión, se copia a `docs/CONTRACTS/` y se anota en el
`CHANGELOG.md` — nunca al revés, y nunca desde aquí.
