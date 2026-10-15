# Decisiones de arquitectura

Un ADR recoge una decisión estructural y no obvia: el contexto, las opciones consideradas, lo que se
eligió y a qué se renunció. Lo reversible y ordinario va a [`JOURNAL.md`](../JOURNAL.md).

| ADR | Decisión | Estado |
|---|---|---|
| [001](001-validacion-por-ast-con-allowlist.md) | Validación por AST con allowlist, no por lista negra | aceptado |
| [002](002-ejecutar-el-ast-validado.md) | Ejecutar el AST validado; `check_no_raw_sql.py` en lugar de semgrep | aceptado |
| [003](003-coste-desde-metadatos-iceberg.md) | Coste desde los metadatos de Iceberg, no con `EXPLAIN` | aceptado |
| [004](004-enmascarado-por-reescritura-de-ast.md) | Enmascarado por reescritura del AST, por rol y posición | aceptado |
| [005](005-bucle-propio-y-langgraph-como-adaptador.md) | Bucle de corrección propio; LangGraph como adaptador | aceptado |

## Formato

`NNN-titulo-en-kebab-case.md`, con el siguiente número libre y un máximo de unas 40 líneas. Secciones:
**Contexto** (con los números medidos que acotan el problema), **Opciones** (incluida la de no hacer
nada cuando aplica), **Decisión** (qué se elige y a qué se renuncia) y **Consecuencias** (invariantes y
metas afectadas, y qué señal indicaría que la decisión fue equivocada).

Un ADR aceptado no se reescribe: si la decisión cambia, se escribe otro que lo supersede y en el viejo
solo cambia la línea de estado (`supersedido por ADR-NNN`).
