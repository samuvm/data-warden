# Data Warden · stack

> Versiones, motivo de cada elección y hardware de referencia. Las versiones exactas (`==`) viven en
> `pyproject.toml` y `uv.lock`; los modelos locales, por digest, en `models.lock`; la versión de las
> convenciones OpenTelemetry, en `otel-semconv.lock`. Este documento explica el porqué y fija la línea
> de cada dependencia; si discrepa de un fichero de bloqueo en un número concreto, manda el fichero.
>
> Regla: versiones exactas, nunca `^` ni `~`, y modelos e imágenes por digest, no por tag. Es lo que
> hace que el repositorio siga compilando y midiendo lo mismo dentro de seis meses.

---

## 0. Hardware de referencia

**MacBook Pro M4 Max · 36 GB de memoria unificada · macOS 26.5 · 14 núcleos (10P/4E)**

Toda meta de latencia se declara contra esta máquina, y así se publica en el README. Un p95 sin
hardware declarado no es un número.

Dos consecuencias prácticas:

- **Ollama corre con backend MLX**, que requiere ≥ 32 GB de memoria unificada y mejora de forma
  notable el decode y el prefill frente al backend Metal/llama.cpp.
- **Docker en macOS no pasa la GPU.** Ollama corre en el host, nunca en un contenedor; meterlo en
  compose destruye la latencia sin un error visible.

---

## 1. Base

| Componente | Versión | Motivo |
|---|---|---|
| Python | `==3.12.*` | Línea con wheels para todas las dependencias fijadas, incluido `sqlglot[c]`, y la más alta que ofrecen algunos runtimes gestionados de AWS |
| Gestor | `uv` 0.9.x | Lockfile multiplataforma y resolución rápida; `uv run --with-requirements` aísla el generador del dataset |
| Lint / formato | `ruff` 0.16.2 | Sustituye a black, isort y flake8 |
| Tipos | `mypy` 2.3.0, `--strict` | Sobre todo `src/` |
| Capas | `import-linter` 2.14 | Los límites entre capas del dominio, ejecutables (`make imports`) |
| Seguridad | reglas `S` de `ruff` (bandit), `detect-secrets` 1.5.0 | Gate estático; cero secretos nuevos contra `.secrets.baseline` |
| Tests | `pytest` 9.1.1, `pytest-cov` 7.1.0, `pytest-xdist` 3.8.0, `hypothesis` 6.165.2 | `--cov-context=test` es obligatorio: es lo que hace medible «toda función pública tiene un test» |
| Mutación | `mutmut` 3.7.0 | Solo en `make done`. Es lo que distingue cobertura de verificación |
| Contenedores en test | `testcontainers` 4.15.0 | Solo en integración, nunca en el gate rápido: en macOS es la principal fuente de fricción |
| Modelos de datos | `pydantic` 2.13.4 | |

---

## 2. Modelos locales

| Rol | Modelo | Motivo |
|---|---|---|
| Runtime | Ollama (≥ 0.32) | En el host, por la GPU. Versión verificada en `models.lock` |
| Generador | `gemma4:26b-mlx` | Con el mismo banco y el mismo sistema, `G-EXEC-ACC` pasó de 0,193 con `qwen3.5:9b-mlx` a 0,421 con este modelo: los fallos dejaron de ser de aterrizaje en el esquema |
| Generador de desarrollo | `qwen3.5:4b-mlx` | Bucle de desarrollo rápido |
| Juez | `gemma4:12b-mlx` | Solo para `G-TOOL-CHOICE`, que mide la elección de herramienta y no puntúa ninguna salida del generador. Nunca es el mismo modelo que genera |

Los cuatro se fijan **por digest** en `models.lock`: un tag de Ollama es móvil y cambiaría las
métricas sin tocar una línea de código. El generador anterior se conserva en el fichero para que los
informes que produjo sigan siendo reproducibles.

**El acceso al modelo es un puerto (`Provider`)** con tres implementaciones: `LocalProvider`
(Ollama), `RecordedProvider` (casetes grabadas, determinista, sin modelo) y `ScriptedProvider`
(tests). Ningún módulo de dominio importa el SDK de un proveedor. `make eval` usa las casetes; solo
`make eval-refresh` llama al modelo, con temperatura 0 y semilla fija.

---

## 3. Datos y almacenamiento

| Componente | Versión | Notas |
|---|---|---|
| DuckDB | `1.5.5` | Motor local embebido. La 2.0 está anunciada: fijar y declarar el riesgo de migración |
| PyIceberg | `0.11.1` (`[sql-sqlite,pyarrow]`) | Para materializar las tablas y leer sus metadatos. El sistema no escribe: es de solo lectura |
| Apache Iceberg | **tablas en formato v2** | **Athena no soporta v3.** Con v3, el criterio de aceptación nº 5 («el mismo caso funciona en DuckDB y en Athena») fallaría por el formato de tabla y no por la abstracción de motor |
| PyArrow | `25.0.1` | Lectura de Parquet |
| NumPy | `2.5.2` | Generador del dataset |
| Faker | `40.37.0` | Generador del dataset |

El generador del dataset (`datagen/`) fija sus dependencias aparte, en `datagen/requirements.txt`,
con los mismos números.

---

## 4. Orquestación

| Componente | Versión | Notas |
|---|---|---|
| LangGraph | `langgraph==1.2.10`, `langgraph-prebuilt==1.1.0`, `langgraph-checkpoint==4.2.0` | Los tres fijados exactamente: un cambio rompedor en `langgraph-prebuilt` se publicó una vez sin restricción de versión, y un `>=` rompe CI un día cualquiera |

**Regla dura sobre LangGraph:** se usa solo como máquina de estados. El bucle de corrección es propio
y puro (`nl2sql/loop.py`); LangGraph lo envuelve como adaptador delgado en `agent/` (ADR-005). El
acceso al modelo es el `Provider` y **`langchain-*` no aparece en `pyproject.toml`**: el riesgo no es
LangGraph, es arrastrar LangChain entero y romper la regla de que ningún módulo de dominio importa el
SDK de un proveedor.

Descartado: **PydanticAI**, que encaja bien con `mypy --strict` y sería mejor opción para un agente
lineal; para un ciclo con reintento acotado y un número que medir, el bucle propio es más simple.

---

## 5. Evaluación

| Herramienta | Rol |
|---|---|
| Suite propia (`scripts/eval_*.py`, `evalsupport/`) | Gate primario. Exactitud de ejecución por comparación de resultsets normalizados, recuperación tras rechazo y elección de herramienta |
| Intervalo de Wilson | Calculado en `scripts/gatelib.py`. Todo número de evaluación se publica con su intervalo al 95 % |

No hay juez LLM sobre la salida del generador: la exactitud compara resultsets, que es determinista.
Tampoco se usan librerías genéricas de métricas RAG: no aplican a NL→SQL y no deben ser un punto
único de fallo del gate.

---

## 6. Observabilidad

| Componente | Versión | Notas |
|---|---|---|
| Convenciones OTel GenAI | `v1.42.0` del repositorio principal, fijada en `otel-semconv.lock` | La última versión del repositorio principal que todavía contiene las convenciones GenAI |
| Modelo interno | `telemetry/model.py` | Desacoplado del esquema externo; `telemetry/otel.py` es el único módulo que conoce los nombres `gen_ai.*` |

**Ningún atributo `gen_ai.*` es estable.** Todos están en estado *Development* y ya ha habido
renombrados (`gen_ai.system` → `gen_ai.provider.name`; `prompt_tokens`/`completion_tokens` →
`input_tokens`/`output_tokens`). Por eso el dominio usa un modelo propio y una capa de traducción, lo
propio va en `app.*`, y toda consulta sobre trazas históricas hace `coalesce()` de las dos
generaciones de nombres. Hoy los atributos se traducen y se verifican, pero no hay colector conectado.

---

## 7. MCP · ruptura de generación

| Componente | Versión |
|---|---|
| Spec | **`2026-07-28`** |
| SDK Python | Línea `mcp>=2,<3`; fijado `mcp==2.1.1` |
| sqlglot | `sqlglot[c]==30.17.0` (compilado con mypyc) |

La spec de julio de 2026 **eliminó las sesiones** (`Mcp-Session-Id`), el handshake `initialize`,
`ping` y la resumibilidad SSE. `sampling`, `roots` y `logging` quedan deprecados. En el SDK, `FastMCP`
pasa a ser `MCPServer`. El código de ejemplo de 2025 no compila contra el SDK v2.

Lo que cambia en el diseño de `data-warden`, y lo que comprueba `scripts/mcp_conformance.py`:

| Cambio | Efecto |
|---|---|
| Protocolo sin estado | El rol de quien pregunta (enmascarado) no puede venir de la sesión de protocolo. Viene del arranque del servidor, un proceso por rol |
| `server/discover` obligatorio | Endpoint que el servidor implementa, con la versión de la spec soportada |
| **MRTR** sustituye a sampling/elicitation | La confirmación del presupuesto blando se pide devolviendo un `InputRequiredResult`; el cliente responde y vuelve a llamar. El servidor no abre peticiones hacia el cliente |
| `resultType` obligatorio | En todos los resultados de todas las tools |
| JSON Schema 2020-12 completo | El `outputSchema` distingue filas de rechazo con `oneOf` |
| `ttlMs` + `cacheScope` obligatorios en los `list` | El catálogo como recurso declara TTL |
| `tools/list` con orden determinista | Check de CI barato |
| Trace context OTel en `_meta` | Se lee como correlación, nunca como autoridad |
| HTTP+SSE deprecado | stdio para clientes de escritorio; la capa HTTP propia es un transporte aparte |

**La tesis sale reforzada:** *da igual qué cliente se conecte, no puede saltarse las reglas* es más
cierto con un protocolo sin estado, porque el servidor es aún más claramente el único guardián.

**sqlglot no tiene competidor real en Python** para validación por AST. `sqlparse` solo tokeniza y no
sirve para esto (ADR-001). Su API interna de AST cambia entre minors sin semver estricto: pin exacto,
y cada subida es una decisión explícita con la suite de casos del guard como regresión, porque una
subida silenciosa cambia cómo se parsea una consulta, es decir, qué se considera seguro.

---

## 8. Transporte HTTP

| Componente | Versión | Notas |
|---|---|---|
| FastAPI | `0.141.1` | Extra opcional `http` |
| Uvicorn | `0.52.1` (`[standard]`) | |

La capa HTTP sirve las mismas cuatro operaciones que MCP desde `service/`, y un test de contrato
exige el mismo resultset normalizado por los dos transportes.
