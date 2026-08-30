# Data Warden · reglas específicas del proyecto

> **Solo lectura para el agente.** Complementa `docs/CONSTITUCION.md`; no la repite y no puede contradecirla.
> Todo lo que aquí no se dice, lo dice la constitución. Cambios: propuesta en `docs/PARA-SAMUEL.md`.

---

## 1. Invariantes verificables

Cada una con el comando que la comprueba. Si una invariante no tiene comando, no es una invariante: es un
deseo. La redundancia pedagógica de las más importantes está en `CLAUDE.md`; la imposición está en el gate.

| id | Invariante | Verificación |
|---|---|---|
| **I-01** | **Ninguna regla del guard se borra, se debilita ni se retira jamás.** Un `rule_id` no se reutiliza nunca. Retirarla exige ADR + propuesta aprobada, y el id queda como `RETIRED` con sus casos migrados | `python scripts/check_rules_registry.py` contra `.claude/state/rules-registry.json` (lo escribe el gate; `deny` para el agente). Falla si desaparece un `rule_id`, si baja `n_reject_cases`, o si un caso migra de `reject` a `accept` |
| **I-02** | **Lo que se ejecuta es el AST re-serializado, nunca la cadena de entrada.** `Engine.execute()` acepta `ValidatedQuery`, jamás `str` | `python scripts/check_no_raw_sql.py` (AST sobre `engines/`) + `mypy --strict` |
| **I-03** | **El guard es allowlist.** Un tipo de nodo o una función desconocidos se rechazan por defecto | `pytest tests/property/test_guard_allowlist.py --hypothesis-profile=gate`: `exp.Anonymous` con nombre aleatorio ⇒ rechazo, 5.000 ejemplos |
| **I-04** | **Fail-closed.** `validate()` nunca propaga excepción; `except Exception` solo permitido en `validator.validate` y debe terminar en `Rejected` | `pytest tests/property/test_guard_failclosed.py` + `python scripts/check_failclosed.py` (AST) |
| **I-05** | **El rol nunca viene de datos no autenticados.** `_meta` y los argumentos de tool son dato, no autoridad | `pytest tests/adversarial/test_role_spoofing.py` + `scripts/check_role_source.py`: ningún módulo fuera de `principal/` lee `role` de un dict de request |
| **I-06** | **Ninguna invocación sin auditoría.** `AuditedExecutor` es el único camino a `Engine.execute()` | `lint-imports` (contrato: `engines` solo importable desde `audit` y `tests`) + propiedad de contadores |
| **I-07** | **El catálogo se genera; nunca se escribe a mano.** Solo `docs/spec/policy.yaml` y `docs/spec/glossary.yaml` son manuales, y sus claves son subconjunto exacto del catálogo | `python scripts/check_catalog_fresh.py` |
| **I-08** | **`guard/`, `mask/`, `cost/`, `audit/`, `principal/` y `domain/` no importan nada de `nl2sql/`, `agent/`, `mcp/`, `http/` ni ningún SDK de LLM** | `lint-imports` con contrato `layers` |
| **I-09** | **Todo rechazo produce un mensaje accionable** conforme a `docs/spec/rejection.schema.json`, con `sugerencia` no vacía, **y que no revela ningún valor de celda ni contenido de columna sensible** | `pytest tests/unit/guard/test_rejection_contract.py` (recorre todos los casos `reject` del corpus) + test con fila PII sembrada |
| **I-10** | **`SELECT *` no sobrevive al guard.** Se expande contra el catálogo o se rechaza | Propiedad: `exp.Star` ausente del árbol renderizado de toda `ValidatedQuery` |
| **I-11** | **Todo prompt vive en `prompts/*.md` con frontmatter.** Cero cadenas de prompt en `.py` | `python scripts/check_no_inline_prompts.py` (literal `str` > 200 chars en `nl2sql/` ⇒ fallo) + validación de frontmatter (`id`, `version`, `modelo_destino`, `temperatura`, `cambios`) |
| **I-12** | **`LIMIT` y `max_rows` son del dominio.** Ningún engine aplica límites propios | Test con engine falso que devuelve 10.000 filas: el dominio recorta |
| **I-13** | **`tests/unit` no toca disco, ni red, ni DuckDB.** Presupuesto ≤ 20 s | `conftest.py` que envenena `socket.socket` y `duckdb.connect`; presupuesto medido en el gate B |
| **I-14** | **Toda familia de ataque tiene una regla que la para, y toda regla tiene su familia de ataque.** Matriz sin filas ni columnas vacías | `python scripts/check_attack_coverage.py` |
| **I-15** | **`tools/list` devuelve orden determinista y todo resultado lleva `resultType`** | `pytest tests/contract/mcp` + `python scripts/mcp_conformance.py` |
| **I-16** | **`[tool.gate]` de `pyproject.toml` y `docs/GOALS.yaml` dicen el mismo número.** `cobertura_linea_min` == `G-COV-LINE`, `mutantes_muertos_min` == `G-MUTATION`, el 95 % de `guard/mask/audit/principal` == el umbral adicional de `G-COV-LINE`, el 85 % de `guard/rules` == `G-MUT-GUARD`, y `testable` tiene exactamente los 8 paquetes que cuenta `G-COV-FUNC` | `python scripts/check_gate_config.py`: exit 1 ante cualquier divergencia, corre en el gate B y en `make done` |
| **I-17** | **`docs/CONTRACTS/` es solo lectura y solo copias; `docs/spec/` es donde este proyecto escribe sus contratos.** Ningún entregable de ninguna fase cae dentro de `docs/CONTRACTS/` | `python scripts/check_contracts.py`: `diff` byte a byte de cada fichero de `docs/CONTRACTS/` contra `_comun/CONTRACTS/`, más la comprobación de que los 4 contratos propios existen y validan en `docs/spec/` |

**Por qué I-16 no es burocracia.** Los umbrales de cobertura y mutación viven en dos sitios: `GOALS.yaml`, que
está en `deny` y bajo el sha256 de `thresholds.lock`, y `[tool.gate]` de `pyproject.toml`, que **no está en
ninguno de los dos** —el agente lo edita legítimamente cada vez que añade una dependencia—. Sin este check, la
forma más barata de pasar el gate no es escribir tests: es bajar `cobertura_linea_min` de 90 a 60 en un
fichero que nadie vigila, y `thresholds.lock` sigue en verde porque `GOALS.yaml` no se ha tocado. Es
exactamente el agujero del mecanismo 2 de anti-gaming (CONSTITUCION §2.5), y se cierra comparando los dos
sitios en cada gate. **`GOALS.yaml` manda**: ante divergencia el fallo es de `pyproject.toml`, nunca al revés.

**Regla de oro del repo, adaptada a la ausencia de git.** `PROJECT.md` dice: *"ningún PR que toque `guard/` se
fusiona sin añadir al menos un caso al cuaderno de ataque"*. Sin PRs se convierte en: **`make done` falla si el
sha de cualquier fichero de `guard/rules/` cambió desde el último snapshot verde y `attacks/` no ganó ningún
caso.** Mismo efecto, mismo comando, cero dependencia de git.

---

## 2. Regímenes por módulo: qué se testea, qué se mide, qué está prohibido

| Zona | Módulos | Régimen |
|---|---|---|
| **Determinista · TDD obligatorio · cobertura 95 % · mutación 85 %** | `guard/`, `mask/`, `audit/`, `principal/` | Ciclo rojo→verde **en turnos separados** (`tdd-guard.sh`). El paso ROJO termina en PARAR |
| **Determinista · TDD obligatorio · cobertura 90 %** | `cost/estimator.py`, `cost/budget.py`, `evalsupport/resultset_equality.py`, `domain/types.py` | Igual, con umbral estándar |
| **Determinista · test-after aceptable · cobertura 90 %** | `catalog/`, `cost/explain_parser.py`, `evalsupport/mutate.py` | Se testean contra fixtures; el orden test/código no se impone |
| **Contrato, no unitario** | `engines/`, `mcp/`, `http/` | Suite de contrato compartida (`test_engine_contract.py` parametrizada por engine), snapshots de esquema JSON, conformidad MCP. **Prohibido perseguir cobertura de línea aquí:** son adaptadores y el 100 % es ruido |
| **Se MIDE, no se testea · TDD PROHIBIDO** | `nl2sql/generator.py`, `prompts/`, `agent/`, y la calidad de las descripciones de tools | Nivel 4: distribuciones e intervalos, nunca aserciones binarias sobre la salida de un modelo. **Escribir un test que asierte una cadena de prompt exacta es un fallo de gate**, no un test frágil |
| **Adversarial** | `tests/adversarial/`, `tests/holdout/` | Cero es cero. Aquí no hay umbral estadístico |

**Por qué TDD está prohibido en `nl2sql/generator.py` y en `prompts/`.** Un test binario sobre la salida de un
LLM o falla de forma intermitente y se acaba desactivando, o se escribe tan laxo que no prueba nada. Las dos
salidas son peores que medir. Lo que sí se testea con TDD es el **bucle** (`nl2sql/loop.py`) contra
`RecordedProvider`, que es determinista: la lógica de reintento, el conteo de intentos, la construcción del
mensaje accionable y la condición de parada son código normal y se prueban como tal.

**Prohibición propia de este proyecto:** ningún test unitario puede asertar sobre la **cadena** de SQL
generada. La verdad se establece sobre el **AST** (comparación estructural) o sobre el **resultset
normalizado**. `PROJECT.md` ya lo dice para las evals; aquí se extiende a los unitarios, donde el error es
mucho más fácil de cometer y donde produce una suite que se rompe con cada cambio de formateo de sqlglot.

---

## 3. "Un test unitario por función", instanciado aquí

El `[tool.gate]` literal de `pyproject.toml`, listo para copiar:

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

**Estos cuatro números son los de `docs/GOALS.yaml`, no una segunda opinión.** `G-COV-FUNC`, `G-COV-LINE`,
`G-MUTATION`, `G-MUT-GUARD` y `G-SECRETS` los declaran allí, donde `thresholds.lock` los protege; aquí y en
`pyproject.toml` solo se instancian para que los lea un script. La coherencia entre los dos sitios es **I-16**
y la comprueba `scripts/check_gate_config.py` en cada gate. Cambiar uno sin el otro es un fallo de gate, no
una discrepancia menor.

La definición ejecutable de la constitución (`check_function_coverage.py` con `--cov-context=test`) se aplica
tal cual a los paquetes `testable`. **Pero en `guard/` la unidad no es la función: es el caso.** Una regla
tiene una única función pública (`check`), así que "un test por función" quedaría satisfecho por un solo test
y no probaría nada. Regla específica, verificada por `python scripts/check_rule_coverage.py`:

> **Toda regla registrada tiene ≥ 3 casos `accept` y ≥ 3 casos `reject` en
> `tests/unit/guard/cases/<rule_id>.yaml`, y cada caso `reject` asierta el `rule_id` exacto que disparó,
> no solo que hubo rechazo.**

Lo segundo es lo que impide el fallo silencioso más común del proyecto: un caso que se cree cubierto por R008
y que en realidad para R002 por accidente. El día que R002 cambie, R008 tiene un agujero y nadie se entera.

**Los casos son datos, no código.** YAML + un único test parametrizado. Añadir una evasión nueva son cinco
líneas de YAML, no un fichero de test nuevo. Eso es lo que hace que la suite crezca de forma natural en vez
de por disciplina.

---

## 4. Presupuestos por capa del gate (CONSTITUCION §2.1, instanciada)

- **Gate A** (≤ 5 s, informa, no bloquea): solo los casos YAML de la regla tocada.
- **Gate B** (≤ 25 s): `ruff` + `mypy --strict` sobre `testable` + `pytest tests/unit tests/contract -n auto`
  + Hypothesis perfil `dev` + `lint-imports` + los `scripts/check_*.py` de arquitectura, **incluidos
  `check_gate_config.py` (I-16) y `check_contracts.py` (I-17)**, que cuestan milisegundos.
  **No se activa hasta que `make test-fast` baje de 20 s** (paso 6 del orden de activación).
- **Gate C** (≤ 3 min): B + `tests/property` perfil `gate` + `tests/adversarial/dev` + `goals-check` +
  verificación de `thresholds.lock`.
- **`make done`**: todo + `tests/integration` (testcontainers / DuckDB) + `tests/holdout` + `make coverage`
  (puntos 4 y 5 de la DoD: `G-COV-FUNC` y `G-COV-LINE`) + `make mutation` + `make secrets` (`G-SECRETS`) +
  evals desde caché grabada.

---

## 5. Correcciones al stack respecto de la hoja de investigación

Se declaran aquí porque el agente las va a encontrar y no debe "arreglarlas" por su cuenta.

1. **LangGraph: `1.2.10`, no `1.2.7`.** `docs/STACK.md` fija `langgraph==1.2.10` y manda sobre cualquier otra
   fuente. Además se pinean **exactamente** `langgraph-prebuilt` y `langgraph-checkpoint`: hubo un cambio
   rompedor en `langgraph-prebuilt==1.0.2` publicado sin restricción de versión.
2. **MCP: se pinea `mcp==2.0.0` en `pyproject.toml`.** Que `STACK.md` escriba `mcp>=2,<3` **no es una
   contradicción**: la constitución §7.2 fija que los rangos de `STACK.md` son la *investigación* —de qué
   línea hay que coger, y aquí la línea es la generación v2, spec 2026-07-28— mientras que el `==` exacto vive
   en `pyproject.toml` y en `uv.lock`. El agente traduce el rango a un `==` concreto por su cuenta y **anota
   la versión elegida en `docs/JOURNAL.md`**; no hace falta preguntar. Lo mismo vale para cualquier otro rango
   de `STACK.md`.
3. **sqlglot: extra `[c]`** (compilado con mypyc), como dice `STACK.md`, no "extra de tokenizer nativo".
   La versión exacta la propone el agente y **la firma Samuel** (Q-002): el AST de sqlglot cambia entre
   minors sin semver estricto y aquí es la pieza crítica.
4. **`semgrep` fuera; `scripts/check_no_raw_sql.py` en su lugar.** Las reglas de inyección SQL de semgrep
   buscan concatenación de cadenas en cursores; aquí el SQL nunca viene de concatenación, viene de renderizar
   un AST validado. Ese conjunto de reglas es un no-op con coste de mantenimiento. Se sustituye por un check
   que verifica la propiedad real (I-02). `PROJECT.md` §4 lo listaba: es un cambio consciente, con ADR.
5. **`langchain-*` no aparece en `pyproject.toml`.** LangGraph se usa solo como máquina de estados, en
   `agent/`, y el dominio no lo importa (I-08).

---

## 6. Lo que este proyecto NO construye, y por qué

Se escribe para que el agente no lo "descubra" y lo construya por iniciativa propia.

- **No consume trazas del proyecto 02 por Iceberg/Parquet sobre S3.** El mapa de conjunto lo declaraba, pero
  el 02 almacena en ClickHouse y no produce Iceberg: el contrato es ficticio en ambos extremos, y además
  violaría el fuera-de-alcance de `PROJECT.md` ("más de un esquema"). Es trabajo futuro, no una integración
  dada por hecha.
- **No importa módulos Terraform versionados del proyecto 05.** El 05 se construye después y se declara
  extracción de lo ya escrito: la dependencia está invertida. Si la fase 9 necesita infraestructura, lleva su
  propio Terraform mínimo, y la importación por versión se documenta como posible, no como hecha.
- **No implementa escrituras, gestión de identidades reales, optimización de consultas ni más de un esquema**
  (fuera de alcance de `PROJECT.md` §2, que sigue vigente).
- **No usa el FastMCP externo de PrefectHQ.** Su propuesta es hacer funcionar aplicaciones con estado sobre un
  protocolo sin estado: es una capa de compatibilidad de la que no conviene depender cuando la tesis del
  proyecto es precisamente que el servidor sin sesiones es el único guardián posible.
- **No implementa HTTP+SSE** (reclasificado a *Deprecated* en la spec 2026-07-28). stdio + Streamable HTTP.

---

## 7. Errores típicos de este dominio que el agente debe evitar

Salieron todos en la investigación previa. Cada uno cuesta días si se descubre tarde.

1. **Validar por lista de palabras prohibidas.** Se salta con comentarios entre tokens, mayúsculas raras o
   codificación. Se valida el **AST**, y sobre el árbol ya pasado por `sqlglot.optimizer.qualify.qualify()`
   con el esquema del catálogo: con el árbol cualificado, alias, CTEs anidadas y `SELECT *` ya están
   resueltos y no hay que "buscar" el alias, porque ya no existe.
2. **Usar `sqlparse`.** Solo tokeniza; no construye un AST utilizable para validación. Mencionarlo en el ADR
   como el error a evitar, no cometerlo.
3. **Denylist de funciones peligrosas.** Se queda corta el día que DuckDB añade una extensión. Allowlist.
4. **Ejecutar la cadena de entrada porque "ya está validada".** Ahí vive la clase entera de ataques por
   diferencia de parser: lo que sqlglot ve ≠ lo que DuckDB ve. Se ejecuta `ast.sql(dialect=...)` (I-02).
5. **Enmascarar post-procesando el DataFrame por nombre de columna.** Se rompe con el primer alias, y es
   literalmente el ataque que `PROJECT.md` describe. El enmascarado es **reescritura del AST antes de
   ejecutar**.
6. **Olvidar el canal lateral por predicado.** `WHERE iban LIKE 'ES91%'` no devuelve el IBAN y filtra por él:
   es exfiltración bit a bit. Por eso el nivel `mask` de `docs/spec/policy.yaml` solo admite la columna en **proyección
   directa** y la rechaza en `WHERE`, `JOIN ON`, `GROUP BY`, `ORDER BY`, `HAVING` y dentro de cualquier
   función.
7. **Olvidar la agregación de grupo único.** `SELECT count(*) … GROUP BY national_id` expone la columna por
   cardinalidad. Se exige `k` mínimo de grupo.
8. **Escribir un generador gramatical de SQL válido para Hypothesis.** Es un subproyecto de 1-2 semanas.
   Se sustituye por dos estrategias que cubren el mismo invariante a una décima parte del coste:
   `st.builds()` sobre nodos de `sqlglot.expressions` (válido por construcción, cero parsing) y **mutación de
   AST sobre corpus semilla** (envolver en CTE, insertar subconsulta, añadir `UNION`, renombrar alias,
   inyectar comentario entre tokens, cambiar mayúsculas, sustituir función por sinónimo de dialecto, colar la
   columna sensible en un `HAVING`).
9. **Creer que el SQL generado y registrado "produce gratis" el conjunto de evaluación.** Es falso: da
   entradas, no verdad de referencia. Etiquetarlo cuesta lo mismo que escribirlo (Q-010).
10. **Publicar `0,80` a secas con n = 60.** Sin intervalo, ese número no significa nada. Se publica Wilson.
11. **Estimar coste con `EXPLAIN ANALYZE`.** Ejecuta la consulta: inútil para un guardián preventivo. Y
    `EXPLAIN` de DuckDB da cardinalidad pero no bytes escaneados; Athena solo reporta `DataScannedInBytes`
    **después** de ejecutar. El estimador se construye desde los metadatos de Iceberg, y por eso mismo sirve
    para los dos motores.
12. **Escribir tablas Iceberg en spec v3.** Athena no la soporta, y el criterio de aceptación nº 5 fallaría
    por el formato de tabla, no por la calidad de la abstracción de motor. **spec v2.**
13. **Copiar código MCP de 2025.** No compila contra el SDK v2: `FastMCP` → `MCPServer`, sin sesiones, sin
    `initialize`, sin `ping`, `resultType` obligatorio, sampling/elicitation → MRTR.
14. **Deducir el rol de la sesión de protocolo.** Ya no hay sesión. El rol viene del arranque del servidor
    (un proceso por rol) o de un `PrincipalToken` opaco firmado con HMAC y con TTL acuñado por el servidor.
    Nunca de `_meta` ni de un argumento (I-05).
15. **Confiar la integridad de la auditoría al hash encadenado a secas.** Quien puede escribir en el almacén
    puede recalcular la cadena entera. Se declara en `threat-model.md` con esas palabras y se ofrece anclaje
    externo. Publicar un control que no resiste el propio modelo de amenaza es lo primero que detecta un
    arquitecto en una entrevista.
16. **Inventar atributos dentro de `gen_ai.*`.** Lo propio va en `app.*`. Y toda consulta sobre trazas
    históricas hace `coalesce()` de las dos generaciones de nombres (`gen_ai.system` /
    `gen_ai.provider.name`, `prompt_tokens` / `input_tokens`).
17. **Meter testcontainers en el gate rápido.** En macOS es el punto de fricción número uno. Nivel 2 corre en
    `make done`, nunca en A, B ni C.
