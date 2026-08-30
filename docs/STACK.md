# Stack fijado · agosto 2026

> Matriz de versiones exactas para los cinco proyectos. **Se copia a cada proyecto**, no se importa.
> Ningún agente cambia una versión de aquí sin propuesta aprobada en `docs/PARA-SAMUEL.md`.
>
> Regla: **versiones exactas (`==`), nunca `^` ni `~`.** Es lo que hace que el repo siga compilando
> dentro de seis meses y lo que impide que un agente alucine la API de una minor futura.
> Imágenes Docker **por digest**, no por tag.

---

## 0. Hardware de referencia

**MacBook Pro M4 Max · 36 GB de memoria unificada · macOS 26.5 · 14 núcleos (10P/4E)**

Toda meta de latencia de cualquier proyecto se declara contra esta máquina y así se publica en
el README. Un p95 sin hardware declarado no es un número, es una anécdota.

Dos consecuencias que cambian decisiones:

- **Ollama tiene backend MLX para Apple Silicon desde 0.19 (marzo 2026), estable en 0.30.**
  Reporta +93 % de decode y +57 % de prefill frente a Metal/llama.cpp. **Requiere ≥ 32 GB de
  memoria unificada.** Con 36 GB cualificas. Es la diferencia entre cumplir el objetivo de
  p95 ≤ 1,5 s del proyecto 01 o no cumplirlo.
- **Docker en macOS no pasa la GPU.** Postgres y demás van en contenedor; **Ollama va en el
  host** y se accede por `host.docker.internal`. Meter Ollama en compose es el error que
  destruye la latencia y nadie entiende por qué.

---

## 1. Base común a los cinco

| Componente | Versión | Motivo |
|---|---|---|
| Python | `==3.12.*` | **Porque MWAA solo ofrece 3.12**, y por uniformidad con las wheels de torch/MPS. Existen 3.13 y 3.14: decir "3.12 porque lo pide la oferta" es transcripción; decir "porque MWAA no ofrece más" es ingeniería |
| Gestor | `uv` (0.9.x) | Estándar de facto en 2026, lockfile multiplataforma, órdenes de magnitud más rápido que poetry |
| Lint / formato | `ruff` | Reemplaza a black, isort y flake8 |
| Tipos | `mypy --strict` | Solo sobre los paquetes `[tool.gate].testable` |
| Seguridad | `bandit`, `detect-secrets` | En el gate estático |
| Tests | `pytest`, `pytest-cov`, `pytest-xdist`, `hypothesis` | `--cov-context=test` es obligatorio: es lo que hace medible "un test por función" |
| Mutación | `mutmut` | Solo en `make done`. Es lo único que distingue cobertura de verificación |
| Contenedores en test | `testcontainers` | Nivel 2. **Nunca** en el gate rápido: en macOS es el punto de fricción número uno |
| Modelos Pydantic | `pydantic==2.13.4` | |

---

## 2. Modelos locales

| Rol | Modelo | Tamaño | Motivo |
|---|---|---|---|
| Runtime | **Ollama 0.32.6** | — | Mejor DX y la historia de `compose up`. Con la abstracción de §2.1 su reemplazo es una variable de entorno |
| Generador | **`qwen3.5:9b-mlx`** | 8,9 GB | Apache-2.0, 256K contexto. `qwen2.5:7b` de los documentos está **dos generaciones obsoleto** (Qwen3 abr-2025 → Qwen3.5 feb-2026) |
| Perfil rápido (dev, CI) | `qwen3.5:4b-mlx` | 3,4 GB | Bucle de desarrollo |
| Perfil calidad (nocturno) | `qwen3.5:27b-mlx` | 17 GB | Evals nocturnas |
| **Juez LLM** | **`gemma4:12b-mlx`** | 7,6 GB | **Nunca el mismo modelo que genera.** Un modelo juzgando su propia salida infla faithfulness sistemáticamente. Los documentos no lo dicen y es un fallo metodológico clásico |
| Embeddings | **`Qwen3-Embedding-0.6B`** | — | Apache-2.0, 32K contexto, instruction-aware, dimensiones configurables 32–1024 (MRL) |
| Embeddings (retador) | `BAAI/bge-m3` | — | MIT. Su mejor argumento hoy no es la calidad dense sino que produce sparse + dense + multi-vector, lo que alimenta gratis la pata léxica del híbrido |
| Reranker | **`Qwen3-Reranker-0.6B`** | — | Apache-2.0, **instruction-aware**: le puedes dar "relevancia = el artículo que *tipifica* la conducta, no el que la menciona". Un cross-encoder clásico no puede recibir eso |
| Reranker (retador) | `bge-reranker-v2-m3` | — | Apache-2.0, opción segura pero checkpoint de junio de 2024 |

**Descartado por licencia:** `embeddinggemma-300m` (licencia Gemma con política de uso
prohibido, no Apache) y `jina-reranker-v3.5` (CC-BY-NC, no comercial). En un portfolio que
quizá enseñas a un empleador, Apache-2.0 o MIT es preferible, y es una frase que puedes decir
en una entrevista.

### 2.1 Dos reglas duras de transporte

1. **Ollama no tiene endpoint de rerank.** Ni en 0.32.6. Los modelos reranker están en su
   librería pero **no existe `/api/rerank`**. El reranker corre **en proceso** con
   `sentence-transformers==5.7.0` (`CrossEncoder`, backend MPS), detrás de un puerto `Reranker`
   con un `RecordedReranker` para tests. Si un agente asume "todo por Ollama", se estrella.
   Bonus: elimina un salto de red del presupuesto de p95.
2. **El adaptador es `OpenAICompatProvider`, no `OllamaProvider`.** Ollama, llama.cpp server,
   vLLM y LM Studio exponen todos `/v1/chat/completions`. Un solo adaptador cubre los cuatro y
   cambiar de runtime es una variable de entorno. Ollama 0.32 está pivotando hacia producto de
   consumo; esto es seguro barato.

**Bedrock:** el `model_id` vive en configuración, nunca en código. **Titan Text Embeddings V2
sigue disponible pero AWS ya no lo desarrolla** — los documentos lo citan y está datado. La
generación actual es **Nova 2**. Modelos Claude en Bedrock a agosto 2026:
`anthropic.claude-opus-5`, `claude-sonnet-5`, `claude-opus-4-8`, `claude-haiku-4-5`.

---

## 3. Datos y almacenamiento

| Componente | Versión | Notas |
|---|---|---|
| PostgreSQL | **18** | Los documentos dicen 16. PG18 no cuesta nada aquí (mismo SQL, misma imagen) y aporta I/O asíncrona |
| pgvector | **0.8.6** | Imagen `pgvector/pgvector:0.8.6-pg18-bookworm`. **0.8.2 parcheó CVE-2026-3172**, desbordamiento en construcción paralela de índices HNSW que puede filtrar datos de otras relaciones. Un `>=0.8` sin patch fijado es una vulnerabilidad real |
| Cliente pgvector | `pgvector==0.5.0` | |
| DuckDB | **1.5.5** | v2.0.0 previsto para otoño 2026: **riesgo de migración a mitad de proyecto**. Fijar y declararlo |
| duckdb-iceberg | ≥ 1.5.3 | Soporta Iceberg v3, pero ver la nota de abajo |
| Apache Iceberg | 1.11.0, **tablas en formato v2** | **Athena no soporta v3.** Si el dataset se escribe en v3, el criterio "el mismo caso funciona en DuckDB y en Athena" falla por el formato, no por tu abstracción |
| PyIceberg | última 0.1x | Sin escrituras: el 03 es solo lectura |
| ClickHouse | ≥ 25.12, recomendado **26.4** | Sigue siendo la elección correcta para trazas: carga analítica, columnar, ~90 % menos de almacenamiento que alternativas fila |

**El "BM25" del proyecto 01 no es BM25.** `tsvector` + `ts_rank_cd` no normaliza por longitud
de documento ni satura frecuencia de término. Llamarlo BM25 en el README es exactamente la
imprecisión que un revisor técnico detecta y que resta credibilidad al resto. Se le llama por
su nombre (`ts_rank_cd` con configuración `spanish` + `unaccent`), y se hace un spike de un día
con **`pg_textsearch` 1.3** (BM25 real, la opción más "solo Postgres") midiendo recall@5 sobre
el golden set. Sale un ADR excelente: *probamos BM25 real, mejoró/no mejoró X puntos, decidimos
Y*. Eso es señal; poner la etiqueta sin haberlo puesto es ruido negativo.

---

## 4. Orquestación y agentes

| Componente | Versión | Notas |
|---|---|---|
| LangGraph | **`langgraph==1.2.10`** | 1.0 GA en oct-2025 con compromiso público de cero cambios rompedores hasta 2.0. **Fijar exactamente `langgraph-prebuilt` y `langgraph-checkpoint`**: hubo un cambio rompedor en `langgraph-prebuilt==1.0.2` publicado sin restricción de versión. Un `>=` ahí rompe CI un día aleatorio |
| Airflow | **`apache-airflow==3.2.1`** | Ver §4.1 |
| MLflow | **`mlflow==3.15.1`** | |
| Docling | **`docling==2.118.1`** | Publican features cada semana: fijar exacto, no `^2` |
| Pandera | **`pandera==0.32.1`** | Ver §4.2 |

**Regla dura sobre LangGraph:** se usa **solo como máquina de estados**. El retrieval es SQL
propio, el acceso al LLM es el `LLMProvider`, y **`langchain-*` no aparece en `pyproject.toml`**.
El riesgo no es LangGraph, es arrastrar LangChain entero y romper la regla que los propios
documentos establecen ("ningún módulo de dominio importa el SDK de un proveedor").

*Descartado con motivo, para el ADR:* **PydanticAI 2.27** es el rival serio de 2026 y encaja
mejor con `mypy --strict`; para un agente lineal sería mejor opción. Para un ciclo con reintento
y abstención acotados, LangGraph gana.

### 4.1 Airflow: el conflicto que los documentos no ven

| Dónde | Versión | Estado |
|---|---|---|
| Upstream actual | 3.3.0 (jul-2026) | |
| **MWAA máximo** | **3.2.1** | Desde mayo 2026 |
| Airflow 3.2 upstream | | **EOL el 06-jul-2026** |
| Airflow 2.x | | EOL abril 2026 |

El documento del 04 promete *"el mismo DAG en ambos"*. **Eso no se cumple si desarrollas en
3.3.0 y MWAA solo ofrece 3.2.1.** Decisión tomada: **fijar `apache-airflow==3.2.1` en local**,
con el fichero de constraints oficial. Se cumple la promesa literal. El coste es renunciar al
particionado avanzado y a las tareas con estado de 3.3, y eso va escrito.

Tres cosas de Airflow 3 que los documentos no incorporan y son obligatorias:

1. **`Dataset` → `Asset`.** `from airflow.sdk import Asset`, no `from airflow.datasets import
   Dataset`. **Si el agente escribe DAGs de memoria (Airflow 2), producirá código roto.** Regla:
   todo import en `dags/` viene de `airflow.sdk`, y el gate ejecuta
   `ruff check --preview --select AIR30` (detecta y autocorrige los rompedores 2→3) más
   `airflow config lint`.
2. **Los sensores son mentalidad Airflow 2.** En Airflow 3 hay `AssetWatcher` y scheduling
   dirigido por eventos. Para un pipeline que descubre documentos, eso es lo correcto y
   demuestra que conoces la versión, no la anterior.
3. **AIP-103 (tareas con estado) queda rechazado**, y con motivo: el proyecto exige que la
   lógica se pruebe sin Airflow, y atarse a su state store rompe eso. Ese ADR de
   "considerado y rechazado por esta razón" es de los que se leen en entrevista.

### 4.2 Pandera está bien elegido pero mal encajado

Los contratos que lista el documento del 04 —idioma, ratio de caracteres útiles, PDF escaneado
sin capa de texto, longitud mínima, codificación— son **validaciones por documento, no por
DataFrame**. Meterlas en Pandera obliga a construir un DataFrame de una fila por documento solo
para validarlo: torpe, lento y con peores mensajes de error.

Diseño correcto, en dos niveles:

- **Nivel documento → Pydantic v2.** Cada regla es un `field_validator` con un motivo tipado que
  va literal al registro de cuarentena.
- **Nivel lote/tabla → Pandera.** Aquí sí brilla: sin nulos, longitud de tokens en cotas, IDs
  únicos, distribución de tamaños, tasa de cuarentena por fuente entre ejecuciones.

---

## 5. Evaluación

| Herramienta | Versión | Rol |
|---|---|---|
| **Suite propia** | — | **Gate primario.** Ahí está la tesis: precisión de cita exacta, alucinación de artículo con tolerancia cero. Eso no lo da ninguna librería |
| **DeepEval** | 4.1.5 | Métricas estándar (faithfulness, context recall) sin ser punto único de fallo |
| **promptfoo** | activo | Nivel 5 adversarial: 40+ plugins de red-team en config YAML. Cubre "documentos con instrucciones incrustadas" mejor que escribirlo a mano |
| **scipy** | ≥ 1.17 | `scipy.stats.bootstrap` y `permutation_test`. API madura |
| ~~Ragas~~ | 0.4.3 | **Fuera del gate.** Ver abajo |

### Ragas: el hallazgo que obliga a cambiar los documentos

- Última release **0.4.3, 13-ene-2026**. Último commit en `main`: **24-feb-2026**.
- El repo cambió de organización (`explodinggradients` → `vibrantlabsai`), 537 issues abiertos,
  y los PRs de la comunidad se cierran sin fusionar.
- Varios artículos de 2026 dicen que "está activamente mantenido": están mirando actividad de
  issues y PRs, no merges. Nada entra en `main` desde hace casi seis meses.

El documento del 01 pone *"Faithfulness (Ragas) ≥ 0,90"* **en la primera línea del README** y
como criterio de bloqueo. **El riesgo es de credibilidad, no técnico:** en una entrevista con
alguien que conozca el ecosistema, "¿sabías que Ragas lleva medio año parado?" deja al proyecto
sin respuesta. Ragas se conserva como referencia histórica y para generar corpus inicial;
**nunca como gate**.

---

## 6. Observabilidad

| Componente | Versión | Notas |
|---|---|---|
| OTel semconv GenAI | Repo `semantic-conventions-genai`, **versión exacta pineada en `otel-semconv.lock`** | Ver abajo |
| Instrumentación | OpenLLMetry o SDK OTel puro | **No OpenInference** (Arize): usa convenciones propias, no `gen_ai.*`, y te saca del estándar que el 02 dice defender |
| Exportador ClickHouse | `clickhouseexporter` de collector-contrib | **traces y logs beta, metrics alpha.** No basar el panel de coste en el exportador de métricas: derivarlo de spans. `create_schema: false` |
| Grafana | **13.1** + Foundation SDK + Git Sync (GA) | Mejora sobre el documento: dashboards tipados en Go/TS/Python en vez de JSON a mano |
| LiteLLM | `1.90.x`, imagen `-stable`, **pin por digest** | Ver abajo |

**Ningún atributo `gen_ai.*` es estable.** Todo está en estado *Development*, y las convenciones
se movieron a un repo propio (eliminadas del principal en v1.43.0). Rupturas ya ocurridas que
hay que absorber: `gen_ai.system` → `gen_ai.provider.name`; `prompt_tokens`/`completion_tokens`
→ `input_tokens`/`output_tokens`. Las consultas analíticas deben hacer `coalesce()` de ambas
generaciones.

**Regla de gobierno:** mantener un **modelo de datos interno propio** desacoplado del esquema
externo, con una capa de traducción. Es lo que recomienda la comunidad y es un ADR de mucho valor.

**LiteLLM sufrió un ataque de cadena de suministro confirmado en marzo de 2026** (versiones
1.82.7 y 1.82.8 en PyPI con robo de credenciales). Obliga a `--require-hashes`, escaneo de
dependencias y jamás usar `latest`.

---

## 7. MCP · ruptura de generación

| Componente | Versión |
|---|---|
| Spec | **`2026-07-28`** |
| SDK Python | **`mcp>=2,<3`** (v2.0.0, 28-jul-2026) |
| sqlglot | Última minor de agosto 2026, extra `[c]` (compilado con mypyc), pin exacto |

La spec de julio de 2026 **eliminó las sesiones** (`Mcp-Session-Id`), el handshake `initialize`,
`ping` y la resumibilidad SSE. `sampling`, `roots` y `logging` están deprecados. En el SDK,
`FastMCP` → **`MCPServer`**. **El código de ejemplo de 2025 no compila.**

Lo que cambia en el diseño del proyecto 03:

| Cambio | Efecto |
|---|---|
| Protocolo sin estado | **El rol de quien pregunta (anillo 5, enmascarado) ya no puede venir de la sesión de protocolo.** Debe venir de handles explícitos acuñados por el servidor, o de OAuth. Sin esto, el anillo 5 es ficción |
| `server/discover` obligatorio | Endpoint nuevo que el servidor debe implementar |
| **MRTR** sustituye a sampling/elicitation | El ciclo "el modelo recibe el motivo y reintenta" encaja **muy bien** con `InputRequiredResult` + `requestState`. Es un punto a favor, con patrón nuevo |
| `resultType` obligatorio | Cambia el contrato de todas las tools |
| JSON Schema 2020-12 completo | Mejora: los esquemas de las cuatro tools pueden ser mucho más expresivos |
| `ttlMs` + `cacheScope` obligatorios en los `list` | El catálogo como recurso debe declarar TTL |
| `tools/list` con orden determinista | Check de CI barato |
| Trace context OTel en `_meta` | **Oportunidad excelente:** encadena 03 con 02 de forma estándar |
| HTTP+SSE deprecado | Usar Streamable HTTP. stdio sigue válido para clientes de escritorio |

**El argumento del proyecto sale reforzado:** *"da igual qué cliente se conecte, no puede
saltarse las reglas"* es **más** cierto con un protocolo sin estado, porque el servidor es aún
más claramente el único guardián. Cambia el mecanismo, no la tesis.

**sqlglot es la elección correcta y no tiene competidor real en Python.** `sqlparse` solo
tokeniza y **no sirve** para validación por AST — mencionarlo en el ADR como el error a evitar.
Su API interna del AST cambia entre minors sin semver estricto: pin exacto y tests de regresión
en cada subida, que el diseño "una regla = un fichero = un test" ya cubre.

---

## 8. Infraestructura

| Componente | Versión | Notas |
|---|---|---|
| Terraform | **1.15.x** | `terraform test` es el diferencial del 05 y 1.15 cierra el último hueco (valores mock conformes al formato) |
| Provider AWS | **`~> 6.57.1`** | ⚠️ **La 6.57.0 fue retirada por un bug grave.** Ir directo a 6.57.1 |
| `configure-aws-credentials` | **v6.2.3** | v6.0.0 fue breaking (Node 24): exige runner ≥ 2.327.1 |
| Checkov | 3.2.526 | La principal. Políticas basadas en grafo desde 3.0 |
| Trivy | 0.70.0 | Complementa. Absorbió toda la librería de checks de tfsec |
| tflint | — | Linter, **no** escáner de seguridad. No presentarlo como tal |
| ~~tfsec~~ | — | Cerrado por Aqua. No usar |
| ~~Terrascan~~ | — | Archivado por Tenable en nov-2025. No usar |
| LocalStack | `2026.06.x` | **Bedrock se emula desde 4.0, pero es Pro.** El documento dice que no se emula: el matiz es erróneo, la conclusión sigue siendo correcta |

**ADR obligatorio sobre BUSL.** Terraform está bajo BUSL 1.1 desde 1.6 y HashiCorp es de IBM.
Es la pregunta número uno que hará cualquier empresa que mire el repo. Recomendación: fijar
Terraform 1.15, **verificar en CI que `tofu plan` también pasa**, y documentar la ruta de salida.
OpenTofu 1.11.6 (MPL 2.0) tiene features que Terraform no tiene en 2026: cifrado de estado,
valores efímeros, `for_each` en providers.

### Dos cosas que reescriben ADRs del 05

1. **El ADR "Function URL vs API Gateway" ya no es "streaming sí o no".** API Gateway REST
   soporta response streaming nativo desde noviembre de 2025, y Lambda response streaming está
   en todas las regiones comerciales desde abril de 2026. Ahora el ADR va de coste,
   autorizadores, rate limiting, validación en el borde, WAF, y del umbral sin cap (10 MB vía
   API Gateway, 6 MB vía Function URL; después, 2 MB/s en ambos). Esto **mejora** el proyecto:
   `api-gateway-llm` deja de ser el módulo incómodo.
2. **`sub` inmutable en OIDC, desde el 15 de julio de 2026.** Los repos nuevos emiten un `sub`
   que añade el ID numérico permanente de organización y repositorio tras cada nombre. **Las
   condiciones `StringLike` escritas al estilo antiguo dejan de casar, en silencio.** El módulo
   `least-privilege-role` debe soportar ambos formatos y tener un test negativo que lo
   demuestre. Hoy es el detalle más vendible de todo el proyecto 05. (Bonus: el thumbprint del
   proveedor OIDC ya no hace falta y se ignora si se especifica; casi todos los tutoriales de
   2026 lo siguen copiando.)

### Lo que el informe de permisos debe documentar para no ser falso

IAM Access Analyzer genera política desde CloudTrail, y ese es exactamente el flujo del
documento. Pero:

1. La salida es una **propuesta**, no una política final: hay que rellenar ARNs de recurso,
   volver a añadir lo que se ejecuta con menos frecuencia que la ventana de observación, y
   quitar lo que aparece solo porque fue intentado y denegado.
2. **CloudTrail no captura todo**: las llamadas de plano de datos de S3 y KMS requieren
   configuración explícita de data events. Un informe que no lo diga es un informe falso.
3. El ciclo real es **conceder aproximadamente bien y podar con datos de uso**, trimestralmente.
   No "escribir la política perfecta el día 1".

Escribir esas tres limitaciones es lo que eleva el proyecto de demo a criterio.
