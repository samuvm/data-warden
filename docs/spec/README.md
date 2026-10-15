# Contratos propios de Data Warden

Aquí viven los contratos que **este proyecto define y escribe**. No confundir con `docs/CONTRACTS/`, que son
copias literales e inmutables del directorio de contratos compartidos, y donde un `diff` contra el original es
un test (invariante I-17 de `docs/RULES.md`).

| Fichero | Qué fija | Naturaleza |
|---|---|---|
| `policy.yaml` | Matriz rol × columna × nivel (`allow` / `mask` / `deny`). Sus claves son subconjunto exacto de `catalog/generated/schema.json` | **Decisión de negocio**, firmada por una persona (`firmado_por`) |
| `glossary.yaml` | Semántica de negocio de tablas y métricas. Se compila a JSON y entra en el prompt de generación | **Decisión de negocio**, revisada por una persona (`revisado_por`) |
| `resultset-equality.md` | Cuándo dos resultsets son iguales: orden de filas, orden y nombres de columna, tolerancia en flotantes, `Decimal` vs `float`, `NULL` vs `''`, vacío, duplicados como multiset, temporales en UTC, timeout y rechazo en el denominador | Especificación técnica |
| `audit-record.schema.json` | Forma del registro de auditoría, con canonicalización JCS (RFC 8785) declarada | Especificación técnica |
| `rejection.schema.json` | Forma del mensaje de rechazo accionable: `rule_id`, motivo y `sugerencia` no vacía, sin revelar valores de celda | Especificación técnica |
| `budgets.yaml` | Presupuesto de coste por rol: ejecutar, pedir confirmación o rechazar, y límite de filas | Decisión de negocio, recalibrada con `make cost-calibration` |
| `tools.yaml` | Las cuatro herramientas MCP y la descripción exacta que ve el cliente | Contrato de interfaz, medido por `G-TOOL-CHOICE` |
| `catalog-overlay.yaml` | Columnas deprecadas que el motor no puede declarar. Solo anota columnas que ya existen | Decisión de negocio |
| `catalog.schema.json` | Forma del catálogo generado (`catalog/generated/schema.json`) | Especificación técnica |

`G-CONTRACTS-FROZEN` cuenta **cuatro contratos** aquí —`policy.yaml`, `resultset-equality.md`,
`audit-record.schema.json` y `rejection.schema.json`—; `glossary.yaml` lo verifica `G-CATALOG-FRESH` junto con
`policy.yaml`, porque lo que se comprueba de él es que sus claves salgan del catálogo generado.
`domain/types.py` **no es un contrato**: es código congelado, y lo verifican `mypy --strict` y su snapshot.

Nada de este directorio es copia de otro sitio. Si alguna vez hace falta cambiar un contrato **transversal**,
se edita en su origen compartido, se sube su versión, se copia a `docs/CONTRACTS/` y se anota en el
`CHANGELOG.md`; nunca al revés ni desde aquí.
