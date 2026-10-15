# Data Warden · invariantes de ingeniería y régimen de pruebas

> Las propiedades que el código no puede perder, cada una con el comando que la comprueba, y el régimen
> de pruebas de cada zona del código. Un cambio aquí es una decisión de diseño: si es estructural, exige
> un ADR en [`adr/`](adr/).

---

## 1. Invariantes verificables

Si una invariante no tiene comando, no es una invariante: es un deseo. La imposición está en el gate.

| id | Invariante | Verificación |
|---|---|---|
| **I-01** | **Ninguna regla del guard se borra ni se debilita.** Un `rule_id` no se reutiliza nunca. Retirar una regla exige un ADR, y el id queda como `RETIRED` con sus casos migrados | `python scripts/check_rules_registry.py` contra `.gate/rules-registry.json` (lo escribe el gate y nada más). Falla si desaparece un `rule_id`, si baja `n_reject_cases` o si un caso migra de `reject` a `accept` |
| **I-02** | **Lo que se ejecuta es el AST re-serializado, nunca la cadena de entrada.** `Engine.execute()` acepta `ValidatedQuery`, nunca `str` | `python scripts/check_no_raw_sql.py` (AST sobre `engines/`) + `mypy --strict` |
| **I-03** | **El guard es allowlist.** Un tipo de nodo o una función desconocidos se rechazan por defecto | `pytest tests/property/test_guard_allowlist.py --hypothesis-profile=gate`: `exp.Anonymous` con nombre aleatorio ⇒ rechazo |
| **I-04** | **Fail-closed.** `validate()` no propaga excepciones; `except Exception` solo se admite en `validator.validate` y termina en `Rejected`. Un timeout del guard es un rechazo | `pytest tests/property/test_guard_failclosed.py` + `python scripts/check_failclosed.py` (AST) |
| **I-05** | **El rol nunca viene de datos no autenticados.** `_meta` y los argumentos de tool son dato, no autoridad | `pytest tests/adversarial/test_role_spoofing.py` + `python scripts/check_role_source.py`: ningún módulo fuera de `principal/` lee `role` de un dict de petición |
| **I-06** | **Ninguna invocación sin auditoría.** `AuditedExecutor` es el único camino a `Engine.execute()`, y pasa por el enmascarado | `lint-imports` (contrato: `engines` solo importable desde `audit` y `tests`) + `python scripts/check_mask_path.py` + propiedad de contadores |
| **I-07** | **El catálogo se genera, nunca se escribe a mano.** Solo `docs/spec/policy.yaml` y `docs/spec/glossary.yaml` son manuales, y sus claves son subconjunto exacto del catálogo | `python scripts/check_catalog_fresh.py` |
| **I-08** | **`guard/`, `mask/`, `cost/`, `audit/`, `principal/` y `domain/` no importan nada de `nl2sql/`, `agent/`, `mcp/`, `http/` ni ningún SDK de LLM** | `lint-imports` con contrato `layers` |
| **I-09** | **Todo rechazo produce un mensaje accionable** conforme a `docs/spec/rejection.schema.json`, con sugerencia no vacía y sin revelar ningún valor de celda ni contenido de columna sensible | `tests/unit/guard/test_rule_cases.py` (recorre todos los casos `reject` del corpus) + test con fila PII sembrada |
| **I-10** | **`SELECT *` no sobrevive al guard.** Se expande contra el catálogo o se rechaza | Propiedad: `exp.Star` ausente del árbol renderizado de toda `ValidatedQuery` |
| **I-11** | **Todo prompt vive en `prompts/*.md` con frontmatter** (`id`, `version`, `modelo_destino`). Ninguna cadena de prompt en `.py` | `tests/unit/nl2sql/test_prompt.py` |
| **I-12** | **`LIMIT` y `max_rows` son del dominio.** Ningún engine aplica límites propios | `tests/property/test_budget_invariant.py`: un engine falso que devuelve 10.000 filas y el dominio recorta |
| **I-13** | **`tests/unit` no toca disco, red ni DuckDB.** Presupuesto ≤ 20 s | `tests/unit/conftest.py` envenena `socket.socket` y `duckdb.connect` |
| **I-14** | **Toda familia de ataque tiene una regla que la para, y toda regla tiene su familia de ataque.** Matriz sin filas ni columnas vacías | `python scripts/check_attack_coverage.py` |
| **I-15** | **`tools/list` devuelve orden determinista y todo resultado lleva `resultType`** | `pytest tests/contract` + `python scripts/mcp_conformance.py` |
| **I-16** | **`[tool.gate]` de `pyproject.toml` y `docs/GOALS.yaml` dicen el mismo número.** `cobertura_linea_min` == `G-COV-LINE`, `mutantes_muertos_min` == `G-MUTATION`, el 95 % de `guard/mask/audit/principal` == el umbral adicional de `G-COV-LINE`, el 85 % de `guard/rules` == `G-MUT-GUARD`, y `testable` tiene exactamente los 8 paquetes que cuenta `G-COV-FUNC` | `python scripts/check_gate_config.py`: exit 1 ante cualquier divergencia, en el gate B y en `make done` |
| **I-17** | **`docs/CONTRACTS/` es solo lectura y solo copias; `docs/spec/` es donde este proyecto escribe sus contratos** | `python scripts/check_contracts.py`: `diff` byte a byte de cada fichero de `docs/CONTRACTS/` contra su original compartido, y comprobación de que los 4 contratos propios existen y validan en `docs/spec/` |

**Por qué I-16.** Los umbrales de cobertura y mutación viven en dos sitios: `GOALS.yaml`, sellado por el
sha256 de `thresholds.lock`, y `[tool.gate]` de `pyproject.toml`, que no lo está porque se edita
legítimamente cada vez que cambia una dependencia. Sin este check, la forma más barata de pasar el gate
no sería escribir tests sino bajar `cobertura_linea_min` en un fichero que nada vigila, con
`thresholds.lock` en verde. **`GOALS.yaml` manda**: ante divergencia el fallo es de `pyproject.toml`.

**El guard crece con los ataques.** Toda regla nueva llega con su familia de ataque en
`attacks/dev-notebook.yaml` y sus casos en `tests/unit/guard/cases/` (I-14). Una evasión encontrada se
convierte en una regla o en un caso permanente; nunca en un `xfail`.

---

## 2. Regímenes por zona: qué se testea, qué se mide y qué está prohibido

| Zona | Módulos | Régimen |
|---|---|---|
| **Determinista · TDD obligatorio · cobertura 95 % · mutación 85 %** | `guard/`, `mask/`, `audit/`, `principal/` | Ciclo rojo → verde: el test se escribe primero y se comprueba que falla por aserción antes de implementar |
| **Determinista · TDD obligatorio · cobertura 90 %** | `cost/estimator.py`, `cost/budget.py`, `evalsupport/resultset_equality.py`, `domain/types.py` | Igual, con el umbral estándar |
| **Determinista · test-after aceptable · cobertura 90 %** | `catalog/`, `evalsupport/mutate.py`, `evalsupport/recovery.py` | Se testean contra fixtures; el orden test/código no se impone |
| **Contrato, no unitario** | `engines/`, `mcp/`, `http/`, `service/` | Tests de contrato (paridad entre transportes, adaptador de LangGraph, contratos firmados), snapshots de esquema JSON y conformidad MCP. **No se persigue cobertura de línea:** son adaptadores y el 100 % es ruido |
| **Se mide, no se testea · TDD prohibido** | `nl2sql/generator.py`, `prompts/`, `agent/`, y la calidad de las descripciones de tools | Evaluación: distribuciones e intervalos, nunca aserciones binarias sobre la salida de un modelo. Un test que asierta una cadena de prompt exacta es un fallo de gate, no un test frágil |
| **Adversarial** | `tests/adversarial/`, `tests/holdout/` | Cero es cero. Aquí no hay umbral estadístico |

**Por qué TDD está prohibido en el generador y en los prompts.** Un test binario sobre la salida de un LLM
o falla de forma intermitente y se acaba desactivando, o se escribe tan laxo que no prueba nada. Las dos
salidas son peores que medir. Lo que sí se testea con TDD es el **bucle** (`nl2sql/loop.py`) contra
`ScriptedProvider` y `RecordedProvider`, que son deterministas: la lógica de reintento, el conteo de
intentos, la construcción del mensaje accionable y la condición de parada son código normal.

**Ningún test asierta sobre la cadena de SQL generada.** La verdad se establece sobre el **AST**
(comparación estructural) o sobre el **resultset normalizado** (`docs/spec/resultset-equality.md`).
[`DESIGN.md`](DESIGN.md) lo exige para las evaluaciones; aquí se extiende a los unitarios, donde el error
es mucho más fácil de cometer y produce una suite que se rompe con cada cambio de formateo de sqlglot.

**Formas prohibidas de esquivar la suite:** `skip`, `xfail`, `--no-cov`, `-k`, `--deselect` y `-x`.
`make done` busca las marcas en `tests/` y falla si encuentra alguna.

---

## 3. «Un test por función», instanciado aquí

El `[tool.gate]` de `pyproject.toml`:

```toml
[tool.gate]
testable = [
  "src/datawarden/guard", "src/datawarden/mask", "src/datawarden/cost",
  "src/datawarden/audit", "src/datawarden/principal", "src/datawarden/catalog",
  "src/datawarden/evalsupport", "src/datawarden/domain",
]
tdd_obligatorio = [
  "src/datawarden/guard", "src/datawarden/mask",
  "src/datawarden/audit", "src/datawarden/principal",
]
excluido = [
  "src/datawarden/nl2sql/generator.py", "src/datawarden/agent",
  "src/datawarden/mcp", "src/datawarden/http", "src/datawarden/engines", "prompts",
]
cobertura_linea_min = 90          # == G-COV-LINE. 95 en guard/, mask/, audit/, principal/: umbral
                                  #    adicional de la misma meta, medido aparte
mutantes_muertos_min = 70         # == G-MUTATION. 85 en guard/rules: G-MUT-GUARD, medido aparte
```

**Estos números son los de `docs/GOALS.yaml`, no una segunda opinión.** `G-COV-FUNC`, `G-COV-LINE`,
`G-MUTATION`, `G-MUT-GUARD` y `G-SECRETS` los declaran allí, donde `thresholds.lock` los protege; aquí y
en `pyproject.toml` solo se instancian para que los lea un script. La coherencia entre los dos sitios es
I-16.

La definición ejecutable general (`scripts/check_function_coverage.py` con `--cov-context=test`) se
aplica a los paquetes `testable`. **En `guard/` la unidad no es la función, es el caso.** Una regla tiene
una única función pública (`check`), así que «un test por función» quedaría satisfecho por un solo test
y no probaría nada. Regla específica, verificada por `python scripts/check_rule_coverage.py`:

> **Toda regla registrada tiene ≥ 3 casos `accept` y ≥ 3 casos `reject` en
> `tests/unit/guard/cases/<rule_id>.yaml`, y cada caso `reject` asierta el `rule_id` exacto que disparó,
> no solo que hubo rechazo.**

Lo segundo impide el fallo silencioso más común: un caso que se cree cubierto por R008 y que en realidad
para R002 por accidente. El día que R002 cambie, R008 tiene un agujero y nadie se entera. Por la misma
razón los casos asiertan también la posición y el sujeto del rechazo.

**Los casos son datos, no código.** YAML más un único test parametrizado. Añadir una evasión nueva son
cinco líneas de YAML, no un fichero de test nuevo.

---

## 4. Capas del gate

- **Gate A** (≤ 5 s, informa, no bloquea): solo los casos YAML de la regla tocada.
- **Gate B** (`make gate-fast`, ≤ 30 s): `ruff` + `mypy --strict` + `lint-imports` + `tests/unit` y
  `tests/property` con Hypothesis en perfil `dev`.
- **Gate C** (`make gate-full`): B + `make arch-checks` (los `scripts/check_*.py`, incluidos I-16 e I-17)
  + `tests/contract` y `tests/adversarial` + cobertura + secretos + cuaderno de ataque y mutación de AST.
- **`make done`**: todo lo anterior + `tests/integration` contra DuckDB + `tests/holdout` +
  `make mutation` + metas de `GOALS.yaml` + verificación de `thresholds.lock` + evaluaciones desde caché
  grabada. Deja un punto de restauración en `.snapshots/`.

Perfiles de Hypothesis: `dev` = 25 ejemplos, `gate` = 100, `nightly` = 1.000.

---

## 5. Decisiones de herramientas

1. **LangGraph `1.2.10`, con `langgraph-prebuilt` y `langgraph-checkpoint` fijados exactamente.** Hubo un
   cambio rompedor en `langgraph-prebuilt` publicado sin restricción de versión.
2. **MCP: la línea es `mcp>=2,<3` (spec 2026-07-28) y el pin exacto vive en `pyproject.toml` y
   `uv.lock`.** [`STACK.md`](STACK.md) explica la línea; el fichero de bloqueo fija el número.
3. **sqlglot con el extra `[c]`** (compilado con mypyc). La versión exacta se fija a mano y se revisa
   antes de cada cambio: el AST de sqlglot cambia entre minors sin semver estricto y aquí es la pieza
   crítica.
4. **`semgrep` fuera; `scripts/check_no_raw_sql.py` en su lugar** (ADR-002). Las reglas de inyección SQL
   de semgrep buscan concatenación de cadenas en cursores; aquí el SQL nunca viene de concatenación, viene
   de renderizar un AST validado. Se sustituye por un check que verifica la propiedad real (I-02).
5. **`langchain-*` no aparece en `pyproject.toml`.** LangGraph se usa solo como máquina de estados, en
   `agent/`, y el dominio no lo importa (I-08, ADR-005).

---

## 6. Lo que este proyecto no construye, y por qué

- **No consume trazas de un sistema externo vía Iceberg/Parquet sobre S3.** Un almacén de trazas en
  ClickHouse no produce Iceberg, y además violaría el fuera de alcance de [`DESIGN.md`](DESIGN.md) §2
  («más de un esquema»). Es trabajo futuro.
- **No importa módulos Terraform versionados de un repositorio de infraestructura compartida.** Si el
  motor en la nube necesita infraestructura, lleva su propio Terraform mínimo.
- **No implementa escrituras, gestión de identidades reales, optimización de consultas ni más de un
  esquema** (fuera de alcance de [`DESIGN.md`](DESIGN.md) §2).
- **No usa el FastMCP externo.** Su propuesta es hacer funcionar aplicaciones con estado sobre un
  protocolo sin estado, y la premisa del proyecto es precisamente que el servidor sin sesiones es el
  único guardián posible.
- **No implementa HTTP+SSE**, deprecado en la spec 2026-07-28. stdio para MCP y una capa HTTP propia.

---

## 7. Errores típicos de este dominio

Cada uno cuesta días si se descubre tarde.

1. **Validar por lista de palabras prohibidas.** Se salta con comentarios entre tokens, mayúsculas raras o
   codificación. Se valida el **AST**, y sobre el árbol ya pasado por `sqlglot.optimizer.qualify.qualify()`
   con el esquema del catálogo: con el árbol cualificado, alias, CTEs anidadas y `SELECT *` ya están
   resueltos, y no hay que «buscar» el alias porque ya no existe.
2. **Usar `sqlparse`.** Solo tokeniza; no construye un AST utilizable para validación (ADR-001).
3. **Denylist de funciones peligrosas.** Se queda corta el día que DuckDB añade una extensión. Allowlist
   (ADR-001).
4. **Ejecutar la cadena de entrada porque «ya está validada».** Ahí vive la clase entera de ataques por
   diferencia de parser: lo que sqlglot ve no es lo que DuckDB ve. Se ejecuta `ast.sql(dialect=...)`
   (I-02, ADR-002).
5. **Enmascarar post-procesando el resultado por nombre de columna.** Se rompe con el primer alias. El
   enmascarado es **reescritura del AST antes de ejecutar** (ADR-004).
6. **Olvidar el canal lateral por predicado.** `WHERE iban LIKE 'ES91%'` no devuelve el IBAN y filtra por
   él: es exfiltración bit a bit. Por eso el nivel `mask` de `docs/spec/policy.yaml` solo admite la columna
   en **proyección directa** y la rechaza en `WHERE`, `JOIN ON`, `GROUP BY`, `ORDER BY`, `HAVING` y dentro
   de cualquier función.
7. **Olvidar la agregación de grupo único.** `SELECT count(*) … GROUP BY national_id` expone la columna
   por cardinalidad. Se exige `k` mínimo de grupo.
8. **Escribir un generador gramatical de SQL válido para Hypothesis.** Es un subproyecto de una a dos
   semanas. Se sustituye por dos estrategias que cubren el mismo invariante a una fracción del coste:
   `st.builds()` sobre nodos de `sqlglot.expressions` (válido por construcción, sin parsing) y **mutación
   de AST sobre un corpus semilla** (envolver en CTE, insertar subconsulta, añadir `UNION`, renombrar
   alias, inyectar comentario entre tokens, cambiar mayúsculas, sustituir función por sinónimo de
   dialecto, colar la columna sensible en un `HAVING`).
9. **Creer que el SQL generado y registrado da gratis el conjunto de evaluación.** Da entradas, no verdad
   de referencia. Etiquetarlo cuesta lo mismo que escribirlo.
10. **Publicar una exactitud a secas con n = 60.** Sin intervalo, el número no significa nada. Se publica
    el intervalo de Wilson.
11. **Estimar coste con `EXPLAIN ANALYZE`.** Ejecuta la consulta: inútil para un control preventivo. Y
    `EXPLAIN` de DuckDB da cardinalidad pero no bytes escaneados; Athena solo reporta
    `DataScannedInBytes` después de ejecutar. El estimador se construye desde los metadatos de Iceberg, y
    por eso mismo sirve para los dos motores (ADR-003).
12. **Escribir tablas Iceberg en spec v3.** Athena no la soporta, y el criterio de aceptación nº 5 fallaría
    por el formato de tabla, no por la calidad de la abstracción de motor. **spec v2.**
13. **Copiar código MCP de 2025.** No compila contra el SDK v2: `FastMCP` → `MCPServer`, sin sesiones, sin
    `initialize`, sin `ping`, `resultType` obligatorio, sampling/elicitation → MRTR.
14. **Deducir el rol de la sesión de protocolo.** Ya no hay sesión. El rol viene del arranque del servidor
    (un proceso por rol), nunca de `_meta` ni de un argumento (I-05).
15. **Confiar la integridad de la auditoría al hash encadenado a secas.** Quien puede escribir en el
    almacén puede recalcular la cadena entera. Se declara en [`threat-model.md`](threat-model.md) §4.1 y
    se ofrece anclaje externo (`warden audit anchor`).
16. **Inventar atributos dentro de `gen_ai.*`.** Lo propio va en `app.*`. Y toda consulta sobre trazas
    históricas hace `coalesce()` de las dos generaciones de nombres (`gen_ai.system` /
    `gen_ai.provider.name`, `prompt_tokens` / `input_tokens`).
17. **Meter testcontainers en el gate rápido.** En macOS es la principal fuente de fricción. La
    integración corre en `make done`, nunca en A, B ni C.
