# Bitácora · Data Warden

**Qué es.** La memoria del proyecto contra repetir errores, y la **evidencia** que exige la constitución antes
de admitir cualquier propuesta de cambio de umbral. Lo que no está aquí, no ocurrió.

**Cómo se escribe.**

- **Append-only.** Nunca se edita ni se borra una entrada anterior: el hook rechaza cualquier escritura cuyo
  contenido nuevo no empiece exactamente por el viejo. Si algo estaba mal, se corrige en una entrada nueva.
- **Una entrada por sesión de trabajo**, encabezada por `## AAAA-MM-DD · fase N · <titular en una línea>`.
- Cinco apartados fijos: **Qué se intentó · Qué falló · Números · Decisiones · Siguiente**.
- **Todo número lleva su comando y su artefacto.** Un número sin comando que lo reproduzca no es un número.
- **Todo número de latencia lleva el hardware** y el protocolo (calentamientos, muestras, descarte).
- Lo que se decidió y es reversible se anota aquí; lo que es no obvio y estructural va además a un ADR y se
  referencia por su id.
- Se escribe **también cuando el día sale mal**. Las entradas de fracaso medido son las que permiten proponer
  bajar un umbral; sin ≥ 2 intentos medidos aquí, el gate rechaza la propuesta.

---

<!-- ================================================================== -->
<!-- ENTRADA DE EJEMPLO. No es trabajo real y el gate la ignora por el   -->
<!-- marcador `[EJEMPLO]` del encabezado. No la borres: es la referencia -->
<!-- de formato. La primera entrada real va debajo de la línea final.    -->
<!-- ================================================================== -->

## 2026-08-12 · fase 2 · [EJEMPLO] `qualify()` con esquema domina el p95 del guard

**Qué se intentó.**
Cerrar `G-GUARD-P95` (p95 ≤ 25 ms, p99 ≤ 60 ms, máx ≤ 250 ms) con las 14 reglas ya implementadas. Hipótesis de
partida: el coste está repartido entre las reglas. Se midieron tres configuraciones sobre el mismo corpus de
300 consultas: (A) `qualify()` completo en cada llamada; (B) `qualify()` con el esquema del catálogo
precargado y cacheado por `catalog_sha`; (C) B + reglas ordenadas por coste creciente, con corte en el primer
rechazo.

**Qué falló.**
La hipótesis. El desglose por fase muestra que **el 78 % del tiempo está en `qualify()`**, no en las reglas:
las 14 juntas suman 2,9 ms de mediana. La configuración A no alcanza el umbral y no lo iba a alcanzar tocando
reglas, que era donde iba a mirar primero.
Además, un intento previo de cachear el árbol cualificado por sha del SQL de entrada se descartó en 20 min: la
clave correcta es `(sql_normalizado, catalog_sha, dialect)`, y cachear por la cadena de entrada habría
reintroducido por la puerta de atrás justo lo que I-02 prohíbe.

**Números.**

| Config | p50 | p95 | p99 | máx | Comando |
|---|---|---|---|---|---|
| A · qualify por llamada | 21,4 ms | 48,7 ms | 96,2 ms | 310 ms | `make bench-guard CONFIG=a` |
| B · esquema cacheado | 7,1 ms | 19,3 ms | 41,8 ms | 133 ms | `make bench-guard CONFIG=b` |
| C · B + corte temprano | 6,8 ms | 18,9 ms | 40,1 ms | 129 ms | `make bench-guard CONFIG=c` |

Hardware: MacBook Pro M4 Max, 36 GB, macOS 26.5. Protocolo: corpus de 300 consultas, 50 calentamientos, 500
medidas, se descarta la primera. Artefacto: `evals/reports/guard-latency.json`.
`G-GUARD-P95` en **verde** con B (19,3 ≤ 25 · 41,8 ≤ 60 · 133 ≤ 250).

**Decisiones.**
- Se adopta **B**, no C. C mejora 0,4 ms en p95 (dentro del ruido: la diferencia entre C y B es menor que la
  desviación entre repeticiones de B) y a cambio hace que el orden de evaluación de las reglas importe, lo que
  vuelve el `rule_id` reportado dependiente del orden. Eso rompería la aserción de `rule_id` exacto que exige
  `check_rule_coverage.py` y haría que un caso "cubierto por R008" pasara a reportar R002. **Menos determinismo
  a cambio de ruido no se compra.** → ADR-017.
- La caché se indexa por `(sql_normalizado, catalog_sha, dialect)` y se invalida con el catálogo. Decisión
  reversible, sin ADR.
- No se toca el máximo absoluto de 250 ms: es el timeout de fail-closed, no un objetivo de rendimiento.

**Siguiente.**
Reejecutar `make attack-mut` con la caché activa para confirmar que no cambia ningún veredicto (la caché no
debe poder convertir un `reject` en `accept`); si algo cambia, es un bug de la clave de caché, no del guard.
Después, `make done MILESTONE=2`.

<!-- ==================== fin de la entrada de ejemplo ==================== -->

---

## 2026-08-28 · fase 0 · línea base del entorno, y tres decisiones de Samuel que cambian el arranque

**Qué se intentó.**
Primera sesión del proyecto. Se lee el gobierno (`CLAUDE.md`, `docs/PROJECT.md`, `docs/GOALS.yaml`,
`docs/PLAN.md`, `docs/RULES.md`, `docs/CONSTITUCION.md`, `docs/PARA-SAMUEL.md`,
`_comun/PARA-SAMUEL-GLOBAL.md`), se verifica el entorno y se contrasta con `citebound-01`, el primero de los
cinco proyectos, cerrado el 2026-08-28. No se escribe una línea de código de producción.

**Qué falló.**
Nada se rompió, pero la comparación con `citebound-01` destapa **cuatro divergencias entre lo escrito aquí y
lo que de hecho se hizo allí**, que estaban a punto de arrastrarse a este proyecto:

1. **Git.** `CLAUDE.md` de este proyecto lo prohíbe («ejecutar `git` de cualquier forma») y declara
   `.snapshots/` como único punto de retorno. `citebound-01` tiene repositorio git con historia real. No es
   una violación de la constitución: `CONSTITUCION §9 paso 10` ya lo contempla como
   «`git init` y migración del gate a `pre-commit` · cuando tú decidas», y §2.2 dice que el gate no depende
   de git y está listo para él. Lo que estaba mal era solo el `CLAUDE.md` local.
2. **Versiones.** `Q-002` pedía firmar la tabla entera del stack. Ocho de esos pines ya están probados en
   `citebound-01` y no hace falta volver a decidirlos; solo quedan seis paquetes nuevos.
3. **`~/.claude/gates/` sigue sin existir** y `~/.claude/settings.json` no tiene ni `permissions.deny` ni
   hooks: solo un `allow` de siete entradas y `defaultMode: auto`. D-09 sigue `PENDIENTE` después de un
   proyecto entero. Aquí pesa más que allí porque la métrica insignia es de seguridad y `tests/holdout/`
   tiene que ser ilegible para que `G-WRITE-BLOCK` valga algo.
4. **`PROJECT.md` §3 escribe `src/data_warden/`** y D-02 fija `datawarden`. Prevalece D-02, como ya anticipaba
   el propio buzón. No se pregunta.

**Números.**

| Qué | Valor | Comando |
|---|---|---|
| uv | 0.9.27 (b5797b2ab, 2026-01-26) | `uv --version` |
| Python del sistema | 3.14.6 | `python3 -V` |
| Python objetivo | 3.12.x — heredado de `citebound-01` (ADR-002 de aquel: MWAA no ofrece más) | `pyproject.toml` |
| DuckDB CLI | **no instalado** — no bloquea: se usa la librería de Python | `duckdb --version` |
| Docker | 29.7.2 (build a7dcaa6) | `docker --version` |
| GNU Make | **3.81** (el de macOS, de 2006) | `make --version` |
| RAM | 36 GB unificada | `sysctl -n hw.memsize` |
| CPU | Apple M4 Max, 14 núcleos | `sysctl -n machdep.cpu.brand_string` |
| macOS | 26.5.2 (25F84) | `sw_vers` |
| Disco libre | 178 GiB de 926 GiB (80 % usado) | `df -h` |
| Ollama · generador | `qwen3.5:9b-mlx` (8,9 GB) **ya descargado** | `ollama list` |
| Ollama · juez | `gemma4:12b-mlx` (7,7 GB) **ya descargado** | `ollama list` |

Coincidencia byte a byte de las copias de `_comun/`, que es el test de `docs/CONTRACTS/`:
`docs/STACK.md` y `docs/CONSTITUCION.md` con sha256 idéntico a los de `_comun/`
(`5540bb6c…5746ca` y `05037963…923ec`); `goals.schema.json`, `otel-genai.md` y `eval-report.schema.json`
sin diferencias (`diff -q`, salida vacía).

Versiones publicadas hoy leídas del índice de paquetes para los seis pines nuevos, propuestas en `Q-002` y
**sin fijar todavía**: `sqlglot` 30.17.0 · `duckdb` 1.5.5 · `pyiceberg` 0.11.1 · `mcp` 2.1.1 ·
`import-linter` 2.14 · `testcontainers` 4.15.0. Y tres que la decisión de dataset sintético añade:
`faker` 40.37.0 · `numpy` 2.5.2 · `pyarrow` 25.0.1.

**Decisiones.**
- **Q-001 · dataset totalmente sintético, una sola base de datos.** Respondida por Samuel en conversación el
  2026-08-28 y transcrita al buzón por el agente; queda pendiente de que él marque el `Estado:`. Descarta la
  opción (a) que recomendaba el agente (NYC TLC real + dimensiones sintéticas). **Contradice `PROJECT.md`**
  («dataset público de varios GB… nada de CSV de juguete»), que es de solo lectura, así que se abre la
  propuesta **P-001** y se espera. Riesgo declarado aquí para que no se descubra en la fase 4:
  `G-COST-CALIB` mide si el estimador acierta *antes* de ejecutar, y sobre datos uniformes acierta siempre;
  el generador tiene que inyectar desigualdad realista (Zipf en los identificadores, nulos, duplicados,
  outliers, estacionalidad horaria) o el número no significa nada y hay que declararlo en el README.
- **Git entra desde el día uno.** `git init` lo ejecuta Samuel (`CONSTITUCION §9`: «lo inicias tú»); el agente
  no commitea por su cuenta. `CLAUDE.md` —editable— se corrige en el turno siguiente. `.snapshots/` no se
  retira: sigue siendo el punto de retorno del gate hasta que exista `pre-commit`.
- **La tabla de versiones no se fija sin firma.** Se escribe en `Q-002` y se para, según lo que la propia
  pregunta exige para `sqlglot`. No se ejecuta `uv add` (está en `ask`).
- Reversibles, sin ADR: se hereda de `citebound-01` el conjunto de pines de herramienta ya probado
  (`pytest` 9.1.1, `pytest-cov` 7.1.0, `pytest-xdist` 3.8.0, `hypothesis` 6.165.2, `ruff` 0.16.2,
  `mypy` 2.3.0, `mutmut` 3.7.0, `pydantic` 2.13.4, `langgraph` 1.2.10 + `-prebuilt` 1.1.0 +
  `-checkpoint` 4.2.0) en vez de volver a decidirlos.

**Siguiente.**
Bloqueado a la espera de: `P-001` aprobada, `Q-002` firmada, `git init` y `thresholds.lock` generado. Lo único
que no depende de ninguna de las cuatro es el test de `evalsupport/resultset_equality.py` (fase 1, 12 casos de
`G-RESULTSET-EQ`) en fase ROJO, y es lo que se coge en el turno siguiente si Samuel no responde antes.
Dos avisos anotados para cuando toquen: **GNU Make 3.81** no tiene `.ONESHELL` ni `$(file …)`, así que el
Makefile canónico se escribe compatible con 3.81 o se declara `gmake` como requisito; y **D-03 sigue sin
responder**, lo que significa que cualquier número de `G-GUARD-P95` medido con otro proyecto encendido no vale.

## 2026-08-28 · fase 0 · CIERZO: el generador sintético, seis bugs propios y una ley que no cabía

**Qué se intentó.**
Samuel eligió el dominio **CIERZO** (pasarela de pagos ficticia) de entre seis candidatos evaluados por un
panel de agentes con jueces independientes, y pidió construir el generador de datos sintéticos de forma
autónoma: diseñar, ejecutar, revisar e iterar hasta tener una base de datos completa, realista y con
suficiente riqueza para perfilar personas, seguir el dinero entre grupos empresariales y cruzar dispositivos
con direcciones IP. Se construye en `datagen/`, **fuera de `src/`**, sin tocar `pyproject.toml`: `Q-002` y
`P-001` siguen `PENDIENTE` y el generador no puede pre-empeñar ninguna de las dos. Dependencias pinadas
aparte en `datagen/requirements.txt` y ejecutadas con `uv run --with-requirements`, que no escribe en el
entorno del proyecto ni requiere `uv add`.

**Qué falló.** Seis defectos propios, todos encontrados por comprobaciones escritas antes de mirar los datos.

1. **Una ley de potencias de un solo parámetro no puede cumplir dos concentraciones.** Se publican dos
   —top 1 % = 45 % del tráfico y top 10 % = 80 %— y una Zipf por rango solo satisface la que se resuelva:
   daba 70,2 % en la segunda con n=12.400. Se sustituyó por **Zipf-Mandelbrot**, que tiene el parámetro `q`
   extra, con bisección anidada sobre los dos objetivos y detección del sentido de monotonía. Es exactamente
   el fallo que los jueces del panel cazaron en cinco de los seis candidatos de diseño.
2. **Suelo de entidades.** Con menos de ~300 comercios el «top 1 %» es un comercio y fijarlo en el 45 % fuerza
   al top 10 % por encima del 87 %: los dos objetivos son **conjuntamente inalcanzables**. El solucionador
   ahora lanza excepción con la ventana alcanzable en vez de errar en silencio, y el perfil `dev` subió de
   124 a 380 comercios. Se descartaron las «ballenas» explícitas: no son solubles en las tres escalas a la vez.
3. **`ingestion_id` se reiniciaba cada día**, duplicando el 99,6 % de las claves. Cascada: rompió también la
   comprobación de rangos IP, que parecía un fallo de geolocalización y no lo era.
4. **Los reintentos podían tener marca de tiempo anterior a su predecesor** en el 4 % de los intentos
   reintentados, porque cada fila sorteaba su propio segundo dentro de la hora. Una ventana ordenada por
   `attempt_seq` habría parecido correcta; una ordenada por `event_ts` habría devuelto el «último intento»
   equivocado.
5. **El bloque IP de casa se asignaba al azar**, no en el país del cliente: el 15 % de los pagos parecían
   transfronterizos por IP contra un 3,7 % declarado, y `datacenter` salía al 7,4 % contra un 0,9 % declarado.
   Ambos son números que un analista de fraude lee primero.
6. **Los anillos de fraude no compartían red.** Estaba en el diseño y no en el código, así que familias y
   anillos aparecían repartidos sobre las mismas 4.181 redes: el ejercicio no tenía respuesta.

Tres defectos más de **realismo**, no de corrección, que solo se ven mirando los datos:
`TRAVEL` salía como sector dominante en los 24 segmentos de cliente (los MCC se repartían al azar y a escala
reducida un comercio se lleva el 21 % del tráfico); el ticket medio de alimentación salía a **625 €** porque
el catálogo tenía tantos SKU de catering como de menú; y `expired_card` era el motivo de rechazo nº 1, por
delante de `insufficient_funds`, que es una ordenación que nadie del sector se cree.

**Números.**

| Perfil | Comercios | Clientes | Intentos | Líneas | Filas totales | Tiempo | Tamaño |
|---|---:|---:|---:|---:|---:|---:|---:|
| dev | 380 | 92 k | 0,68 M | 1,50 M | 3,1 M | 15 s | 84 MB |
| demo | 3.100 | 920 k | 6,60 M | 14,60 M | 29,6 M | ~3 min | ~800 MB |

24 tablas · 278 columnas · **tres granos distintos** (intento de autorización, línea de cesta, lote de
liquidación). Comando: `./datagen/run.sh <perfil>`. Artefacto: `datagen/out/<perfil>/MANIFEST.json`, que
guarda cada forma **medida** junto a su objetivo.

Calibración final medida sobre `dev`: aprobación **85,6 %** · mediana del ticket **40 €** · media 174 € ·
factor de reintento **1,113** · top 1 % de comercios **45,00 %** y top 10 % **80,00 %** (ambos exactos en las
tres escalas) · IP residencial 92,1 %, móvil 5,3 %, datacenter 2,2 %, VPN 0,5 %.

Trampas medidas, no supuestas: contar ingresos contando filas infla **+27 %**; unir por la clave natural del
comercio en vez de por la subrogada infla las filas **+60 %**; un `JOIN` de FX por igualdad de fecha pierde
**el 24 %** de los pagos no-euro en silencio (fines de semana y festivos no tienen cotización).

Señal de perfilado descubrible, medida por *lift* y no por `mode()` —que devolvía `GROCERY` en los 24
segmentos porque es la categoría más grande en todas partes—: 18-24 → Gaming (×2,5), 65+ → Donaciones (×2,56)
y Farmacia (×1,75), 45-54 → Vinos y licores (×1,84) y Combustible (×1,66).
Anillos frente a familias: 4-6 personas por dispositivo → riesgo 222 y 7 % de categorías de riesgo;
11-14 personas desde 2-3 redes → riesgo **470** y **46 %** en apuestas y cripto.

**Decisiones.**
- **Ningún número se escribe: se declara la concentración y se resuelve el exponente numéricamente**, y lo
  medido se publica junto al objetivo en `MANIFEST.json`. Es la regla que el panel identificó como el mayor
  riesgo transversal del repositorio y aquí es ejecutable, no una intención.
- **Se estratifica por entidad, jamás por filas.** Los tres perfiles conservan los 730 días y la forma
  completa. Muestrear filas destruiría las series por comercio y los duplicados de ingesta, que solo se
  detectan por pares.
- **Las tablas de dinero se derivan en SQL desde los hechos**, no se generan en paralelo: un lote de
  liquidación que no suma los pagos que contiene no es una trampa, es un bug. Aleatoriedad por `hash(clave)`,
  no por generador, así que no dependen del orden de las filas.
- **El contenedor construye su propio catálogo al arrancar**, en memoria y sin base de datos en disco. Una
  vista de DuckDB guarda la ruta literal con la que se creó y valida el glob al crearla, así que un catálogo
  construido en el host para un montaje de contenedor no se puede construir siquiera.
- **La pirámide de edad se declara por tramo** en vez de ajustarse. Cuatro parametrizaciones de gamma
  reprodujeron el cuerpo 25-44 y luego infrarrepresentaron a los mayores de 55 por un factor de dos: una
  pirámide de titulares de tarjeta no es un proceso, son dos.
- 29 comprobaciones bloqueantes en cada build (20 de integridad + 9 de conciliación). Reversible, sin ADR.

**Siguiente.**
Terminar el perfil `full` (68 M de intentos) y anotar sus números medidos. Sigue bloqueado lo mismo que ayer:
`P-001` sin aprobar, `Q-002` sin firmar, `git init` sin ejecutar y `thresholds.lock` sin generar. Nada de
`datagen/` entra en `src/` hasta que `P-001` esté `APROBADA`: hoy es un artefacto de investigación, no el
generador del proyecto.

## 2026-08-28 · fase 0 · CIERZO perfil completo: 295 millones de filas y 29 comprobaciones en verde

**Qué se intentó.**
Ejecutar el perfil `full` del generador CIERZO de extremo a extremo tras corregir los seis bugs de la entrada
anterior, y verificar que la forma declarada se cumple a escala real y no solo en los perfiles reducidos.
Comando: `./datagen/run.sh full`.

**Qué falló.**
Nada en esta pasada. Tres intentos previos se abortaron a propósito antes de llegar a los hechos, al detectar
en el perfil reducido defectos que habrían quedado congelados en 7 GB de Parquet: los anillos de fraude no
compartían red, `expired_card` salía como motivo de rechazo nº 1 por delante de `insufficient_funds`, y las
dimensiones tenían tres bucles de Python a nivel de fila que a escala ×100 eran decenas de millones de
iteraciones. Abortar tres veces salió cuatro veces más barato que descubrirlo después.

Queda una diferencia entre escalas que conviene tener anotada y no es un fallo: el **factor de reintento
medido baja de 1,113 en `dev` a 1,061 en `full`**, porque la mezcla de sectores y por tanto de motivos de
rechazo cambia con el número de comercios. `config.Profile.intents` usa 1,092 como divisor para estimar los
intentos a partir de los intentos objetivo, así que los 68,4 M pedidos salieron 66,5 M. El número publicable
es el medido, y está en el `MANIFEST.json`.

**Números.** Comando: `./datagen/run.sh full`. Artefacto: `datagen/out/full/MANIFEST.json`.

| Tabla | Filas | Cols | Parquet |
|---|---:|---:|---:|
| `fact_order_line` | 146.830.106 | 11 | 1,63 GB |
| `fact_payment_attempt` | 66.474.767 | 36 | 4,07 GB |
| `fact_settlement_batch` | 5.633.911 | 14 | — |
| `bridge_customer_device` | 23.616.203 | 6 | 0,27 GB |
| `dim_device` | 19.320.000 | 10 | 0,36 GB |
| `dim_card` | 18.418.905 | 13 | 0,40 GB |
| `dim_customer` | 9.200.000 | 23 | 0,49 GB |
| **Total** | **294.943.921** | 278 | **7,2 GB** |

Forma declarada frente a medida, toda desde `MANIFEST.json`:
top 1 % de comercios **45,0000000 %** (objetivo 45 %) · top 10 % **80,0000000 %** (objetivo 80 %) ·
Zipf-Mandelbrot con exponente 1,29513 y q=31,729 · mayor comercio 1,08 % del tráfico ·
clientes que nunca pagan **4,312 %** (objetivo 4,3 %) · media de 7,12 pagos por cliente pagador, mediana 3,
p90 18, p99 47, máximo 262 · 30,2 % con un único pago · top 1 % de clientes = 8,78 % de los pagos ·
1.288 anillos de fraude con 9.661 miembros (0,105 % de la base).

Calibración de negocio medida sobre los 65,7 M de intentos no-test: aprobación **85,15 %** · ticket mediano
**39,99 €** · media 164,93 € · 8.774 M € liquidados en 5,63 M de lotes · 207,5 M € de comisión (MDR efectivo
**2,37 %**) · 71.553 lotes sin cerrar (**1,27 %**), que es el trabajo semanal del rol `finance`.

Trampas medidas a escala completa: contar ingresos contando filas infla **+24,4 %** (10.964 M € frente a
8.816 M reales); unir por la clave natural del comercio en vez de por la subrogada infla las filas **+61,4 %**
(107,3 M frente a 66,5 M); un `JOIN` de FX por igualdad de fecha pierde **el 24,4 %** de los pagos no-euro,
idéntico en las trece divisas, porque el hueco es el calendario y no la divisa.

Señal a escala completa: 18-24 → In-Game/Games/Hardware con lift **×2,50**; 35-44 → Cursos ×1,98.
Anillos: 4 personas por dispositivo → riesgo 220 y 7,3 % de categorías de riesgo; **11 personas desde 2,9
redes → riesgo 463 y 50,0 %**.

Rendimiento de consulta sobre los 295 M de filas, DuckDB embebido, M4 Max: las nueve consultas del cuaderno
tardan entre **0,1 s y 3,9 s**. La más cara es la unión geográfica por rango (`ip BETWEEN inicio AND fin`),
3,9 s, que es lo que la hace la prueba honesta del estimador de coste del anillo 4.

**Verificación.** 29 comprobaciones bloqueantes, **0 fallos** sobre los 295 M de filas: 20 de integridad y 9 de
conciliación contable.

**Decisiones.**
- El divisor 1,092 de `Profile.intents` se deja como está y **no se ajusta al 1,061 medido**. Es una estimación
  para dimensionar, no una meta; ajustarlo por escala haría que el mismo perfil produjera un número distinto
  de filas según el tamaño y rompería la comparación entre builds. Reversible, sin ADR.
- `datagen/out/` y `datagen/docker/catalog.sql` van a `.gitignore`. Lo versionado es el generador y su
  semilla, que reproducen el dataset byte a byte; 7 GB de Parquet en el repositorio lo harían inservible para
  quien tiene que descargarlo, y D-07 ya lo anticipaba.

**Siguiente.**
Auditoría adversarial del dataset por seis revisores independientes con lentes distintas (pagos, estadística,
modelado, fraude, privacidad, y las necesidades del propio proyecto), cada hallazgo verificado por un segundo
agente. Sigue bloqueado lo mismo: `P-001` sin aprobar, `Q-002` sin firmar, `git init` sin ejecutar y
`thresholds.lock` sin generar. **Nada de `datagen/` entra en `src/` hasta que `P-001` esté `APROBADA`.**

## 2026-08-28 · fase 0 · la auditoría del dataset: 22 defectos, y por qué el generador estaba mintiendo

**Qué se intentó.**
Someter el dataset CIERZO ya generado a una auditoría adversarial antes de darlo por bueno. Seis agentes
independientes con lentes distintas —ingeniero de pagos con diez años de oficio, estadístico, arquitecto de
datos, analista de fraude, delegado de protección de datos, y el arquitecto del propio Data Warden— consultando
DuckDB de verdad, con la instrucción de no afirmar nada sin la consulta que lo demuestre. Cada hallazgo pasó
después por un verificador adversarial cuyo trabajo era refutarlo con una consulta distinta.

**Qué falló.** Casi todo lo que importaba, y lo peor es que la mayoría era invisible desde dentro.
De 22 hallazgos verificados, **20 confirmados como «arreglar ya»**. Comprobé personalmente los diez principales
antes de tocar nada: los auditores tenían razón en los diez.

Lo grave, por orden de vergüenza:

1. **`fx_rate` era una columna corrupta.** Doble indexado: el vector ya estaba expandido por fila y se volvía a
   indexar por divisa, así que cada fila llevaba el tipo de cambio de OTRO pago. Rango medido dentro de la
   libra esterlina: de 0,77 a 385,88. Cuatro de las seis lentes lo encontraron por caminos distintos.
2. **Los importes no estaban denominados en moneda local.** La cesta se valoraba en euros y se etiquetaba con
   la divisa del comercio, y luego se DIVIDÍA por el tipo en vez de multiplicar. Ticket mediano en Hungría:
   **0,19 €**. Y `ref_currency.minor_units` se ignoraba por completo, así que 40 € en Hungría se escribían como
   1,9 millones de forintos.
3. **La tabla FX publicada no era la que se aplicó.** `generate.py` reconstruía la tabla con otra semilla: dos
   paseos aleatorios independientes, hasta un 29 % de diferencia. El tipo publicado tiene que ser el usado.
4. **El 86,7 % del tráfico era transfronterizo** (Europa real: 15-25 %). El país del comercio se sorteaba con
   independencia del cliente, así que el resultado era exactamente Σp², el de emparejar al azar. Y ese único
   flag alimenta a la vez la aprobación, el interchange y el riesgo.
5. **El interchange rompía el tope del Reglamento (UE) 2015/751 en el 55,5 % de las aprobadas.** El
   multiplicador transfronterizo se aplicaba dentro del EEE, donde el tope rige igual.
6. **El motor de riesgo estaba muerto:** 1 bloqueo y 134 revisiones en 6,6 millones de filas. Y a la vez
   **18.588 filas se contradecían a sí mismas**: `decline_reason = blocked_by_risk_engine` con
   `risk_decision = allow`.
7. **La trampa de duplicados de ingesta del 0,35 % NO EXISTÍA.** Declarada en `config.py`, documentada en el
   README, jamás implementada. Cero filas duplicadas en 6,6 millones. Una trampa que solo existe en la
   documentación es peor que ninguna, porque es una afirmación.
8. **El 51,5 % de los dispositivos «compartidos» eran colisión aleatoria.** La separación familia/anillo, que es
   el ejercicio entero, no tenía respuesta: el ruido y la señal medían lo mismo.
9. **El 19,3 % de los clientes pagaba antes de darse de alta**, hasta 698 días antes.
10. **`risk_score` no predecía las disputas.** El contracargo dependía solo del MCC; dentro del 7995 la relación
    incluso se invertía. Un score de riesgo que no predice lo que existe para predecir es lo primero que mira
    un analista de fraude y lo primero que le hace cerrar la pestaña.
11. **Yo publicaba el objetivo del solucionador como si fuera el resultado.** `MANIFEST.json` decía
    «top 1 % = 45,00 %, exacto en las tres escalas». Medido sobre el tráfico real: **19,9 % / 52,2 %**. La
    afinidad, la fidelidad al comercio y el sesgo doméstico mueven el tráfico DESPUÉS de sortear los pesos.
    Es exactamente la regla que este generador dice imponer, incumplida por su propio autor.
12. **920.000 NIF con letra de control correcta**, el 55,6 % dentro del rango ya emitido. Un identificador
    sintético no debe poder pertenecer a nadie.
13. **Nombres de empresas reales con etiquetas de riesgo inventadas.** Google Cloud, AWS y Azure marcados
    `is_anonymizer = TRUE` con un `risk_weight` que me inventé. La misma disciplina que mantuvo todas las IP
    dentro de 10.0.0.0/8 no se aplicó a los ASN.
14. **Faltaba la columna donde de verdad se fuga la PII:** cero texto libre, cero ciclo de vida RGPD, cero KYC.

**Qué se hizo.** Los 20 «arreglar ya», más cuatro bugs adicionales que destaparon los checks nuevos.
Añadidas **8 comprobaciones bloqueantes** (de 29 a **37**), y cuatro de ellas cazaron defectos el mismo día que
se escribieron: el muestreador de motivos podía inventar un bloqueo del motor de riesgo para un pago que el
motor había permitido; 98.769 pagos se hacían con una tarjeta que aún no existía; los duplicados de ingesta
recién implementados rompían dos invariantes que no los contemplaban; y las tablas de dinero liquidaban dos
veces el mismo pago porque leían la tabla cruda en vez de la vista deduplicada.

Cambios estructurales, no parches:
- **Los puntos de venta tienen país propio.** Modelar un comercio con un solo país era la causa raíz del
  transfronterizo: en un mercado pequeño un cliente no tiene dónde comprar en casa. En la realidad una
  plataforma grande tiene entidad local en cada mercado, y el número de sedes escala con el volumen.
- **La asignación de pagos se calcula ANTES de crear los clientes**, porque la fecha de alta tiene que preceder
  al primer pago y eso solo se sabe con la asignación hecha.
- **Los dispositivos se reparten en bloques disjuntos.** Compartir es siempre deliberado.
- **La línea de pedido se valora en la moneda de la transacción antes de sumar el importe**, no al revés.
- **Las tablas derivadas leen `v_attempt_dedup`**, nunca la tabla cruda.
- **`v_customer` lista sus columnas explícitamente.** Un `SELECT *` es un agujero recto a través de cualquier
  política que funcione por nombre de columna: la columna sensible nueva aparece en la vista al crearla y en la
  política cuando alguien se acuerda.
- **Añadido `support_note`:** texto libre en el 8,1 % de los clientes, con nombre, teléfono, NIF o últimos
  cuatro dígitos DENTRO. Es la columna que ninguna política por nombre protege y donde la PII se escapa de
  verdad. Más `kyc_status`, `kyc_verified_on`, `erasure_requested_on` y `retention_expires_on`.

**Números medidos tras la iteración**, perfil `dev`, comando `./datagen/run.sh dev`:

| | antes | después |
|---|---:|---:|
| `fx_rate` dentro de GBP | 0,77 – 385,88 | 0,777 – 0,845 |
| Tipo del hecho vs tabla publicada | hasta 29 % de error | **desviación 0,0** |
| Ticket mediano en Hungría | 0,19 € | 59,63 € (22.019 HUF) |
| Transfronterizo | 86,7 % | 37,1 % |
| Interchange sobre el tope EEE | 55,5 % | **0,55 %**, y luego 0 al topar prepago |
| Cola de riesgo (bloqueo / revisión) | 1 y 134 filas | 0,32 % y 3,01 % |
| Duplicados de ingesta | 0 % (declarado 0,35 %) | **0,353 %** |
| Dispositivos compartidos | 51,55 % (colisión) | 6,51 % (deliberado) |
| Pagos antes del alta | 19,3 % | **0** |
| Comprobaciones bloqueantes | 29 | **37** |

**Decisiones.**
- **La concentración se publica MEDIDA SOBRE EL TRÁFICO**, no sobre el vector de pesos, y el manifiesto lleva
  las tres cifras: objetivo, vector y medida. La diferencia entre 45 % y 20 % no es un error del solucionador:
  es el efecto real de la afinidad y la fidelidad, y esconderlo habría sido el mismo pecado que el generador
  dice combatir.
- **La letra de control del NIF usa la tabla rotada**, de modo que todo identificador generado falla la
  verificación real conservando forma, longitud y distribución.
- **Los ASN e ISP son inventados**, con números del rango de uso privado.
- Tres reconstrucciones abortadas del perfil `full` antes de llegar a los hechos. Salió cuatro veces más barato
  que congelar el defecto en 7 GB de Parquet.
- **Incidente propio:** un parche mal escrito (`open(path,'w')` que trunca antes de que falle la escritura)
  dejó `generate.py` en cero bytes. No había copia porque el fichero se creó con heredoc y no con la
  herramienta de edición, que sí guarda historial. Reconstruido íntegro con todas las correcciones acumuladas.
  Regla para el futuro: escribir a temporal y renombrar, o usar la herramienta que versiona.

**Siguiente.**
Reconstruir `demo` y `full` con el generador corregido y volver a auditar. Sigue bloqueado lo mismo: `P-001`
sin aprobar, `Q-002` sin firmar, `git init` sin ejecutar, `thresholds.lock` sin generar.

## 2026-08-28 · fase 0 · dos acantilados en la señal de fraude, y por qué una separación demasiado limpia también es un defecto

**Qué se intentó.**
Cerrar la iteración del generador tras la auditoría: reconstruir `demo` y `full` con las 24 correcciones,
verificar el cuaderno de exploración a 285 millones de filas y comprobar que las señales que el dataset promete
siguen siendo encontrables.

**Qué falló.**
Una cosa, y es interesante porque es el defecto contrario al que se busca normalmente. A escala completa, la
separación entre una familia y un anillo de fraude salió **demasiado limpia**: 4-5 personas por dispositivo daban
riesgo 187 y un 7,4 % de categorías de riesgo; 6 o más daban riesgo 320 y un **51 %**. Un corte perfecto en el
número seis.

La causa: los hogares recibían de 1 a 4 propietarios extra (máximo cinco personas) y los anillos tenían de 4 a
11 miembros, todos enganchados a todos los dispositivos del anillo. Los rangos no se solapaban, así que
`WHERE people >= 6` era un detector perfecto y el ejercicio entero —mirar red, categoría y riesgo a la vez—
se volvía innecesario. **Una señal que se lee en una sola columna no es una señal: es una etiqueta.**

El primer arreglo (hogares hasta ocho personas) movió el acantilado a nueve en vez de eliminarlo, porque un
dispositivo de anillo seguía teniendo exactamente `size` propietarios. El arreglo real fue modelar el
comportamiento: **un miembro de un anillo usa ALGUNOS de los dispositivos del anillo, no todos.** Un grupo de
nueve personas no comparte un solo portátil.

**Números.** Perfil `dev`, dispositivos con 4 o más propietarios:

| Personas | Dispositivos | Redes por disp. | Riesgo | % categorías de riesgo |
|---:|---:|---:|---:|---:|
| 4 | 1.471 | 0,28 | 191 | 7,2 |
| 6 | 605 | 0,69 | 192 | 7,5 |
| 8 | 212 | 1,84 | 193 | 8,1 |
| 10 | 75 | 4,19 | 192 | 7,4 |
| 11 | 147 | 2,71 | 192 | 8,4 |

El recuento ya no dice nada: riesgo plano en 187-193 y categorías planas en 7-8 % en todos los tamaños. La
señal aparece al hacer el join correcto —dispositivo **y** red—:

| Perfil (≥6 personas) | Dispositivos | Personas medias | Riesgo | % categorías de riesgo |
|---|---:|---:|---:|---:|
| Red compartida (≤2 bloques) | 64 | 6,5 | **210** | **17,4** |
| Redes dispersas (>2) | 1.483 | 7,5 | 191 | 7,5 |

Los anillos son de media **más pequeños** que los hogares, así que un umbral sobre el número de personas no solo
es inútil: apunta al revés.

Perfil `full` reconstruido: **285.333.814 filas · 24 tablas · 278 columnas · 7,2 GB**, hechos generados en 411 s
(la mitad que antes de optimizar los bucles de dimensión), **37 comprobaciones en verde**. Concentración medida
sobre el tráfico real: **30,90 % / 71,57 %** (vector 45 % / 80 %, suelo declarado 15 %). Aprobación 86,1 % y
estable en los nueve trimestres. Interchange sobre el tope del reglamento: **0 filas de 44,5 millones**.
Contracargo por tramo de riesgo: 0,397 % → 0,758 % → 1,495 %. Las catorce consultas del cuaderno tardan entre
0,0 s y 3,6 s.

**Decisiones.**
- **Un solape deliberado entre hogares y anillos.** Los rangos de tamaño tienen que pisarse; si no, el
  ejercicio se resuelve con un `WHERE` sobre una columna y el dataset deja de enseñar nada.
- La nota de soporte cita la **fecha de nacimiento, el correo y el NIF reales del cliente**, no un centinela.
  Una fuga con un `1900-01-01` fijo sería una fuga falsa: el valor expuesto tiene que ser el mismo que la
  política protege tres columnas más a la izquierda.
- Las tarjetas caducan **fuera** de la ventana salvo la cohorte declarada del 2,2 %. Sortear la caducidad entre
  2026 y 2031 metía un sexto de la cartera dentro del último año y hacía caer la aprobación 3,3 puntos: un
  analista lo lee como deterioro de cartera y en realidad es un dataset que olvidó que las tarjetas se reemiten.

**Siguiente.**
Segunda auditoría adversarial sobre el dataset corregido. Sigue bloqueado lo mismo: `P-001` sin aprobar,
`Q-002` sin firmar, `git init` sin ejecutar, `thresholds.lock` sin generar.

## 2026-08-28 · fase 0 · la segunda auditoría: 23 de 24 arreglos aguantan, y treinta defectos donde nadie miró

**Qué se intentó.**
Someter el dataset ya corregido a una segunda auditoría adversarial, con dos encargos explícitos: verificar los
24 arreglos de la primera ronda **uno a uno con consulta propia distinta de la usada para arreglarlos**, y
buscar defectos nuevos en las zonas que la primera no tocó —la cesta y el catálogo, el dinero después de la
autorización, y la coherencia temporal—, con atención especial a lo que hubieran podido romper los propios
arreglos. Cuatro lentes independientes.

**Qué falló.**
**23 de los 24 arreglos aguantan.** Los cuatro que yo temía fáciles de fingir resistieron medidos de forma
independiente: 0 filas intra-EEE por encima del tope de interchange, 0 NIF válidos sobre 920.000, 0 lotes con
`net <> gross - fee` y 449.759 pagos que cuadran exactos.

Pero encontró **tres regresiones mías** y unos treinta defectos nuevos. Los que más duelen:

1. **El teléfono de `support_note` no era el del cliente.** Se volvía a sortear: 0 coincidencias con
   `phone_e164` en 18.374 notas. Es la columna que sostiene la tesis entera del proyecto, y la demostración
   «enmascara la columna y el mismo número aparece en el texto libre» solo funciona si **es** el mismo número.
   Las cuatro lentes lo encontraron por separado.
2. **El README publicaba el objetivo del solucionador bajo el título «medido».** 45 %/80 % donde el tráfico
   real daba 30,8 %/71,6 %. Exactamente el pecado que `config.py` declara que este generador existe para
   evitar, cometido en la documentación en vez de en los datos.
3. **El 30,9 % del dinero se liquidaba y se pagaba en día no hábil** —139.035 pagos, 271,0 M €, incluidos 653
   con fecha valor el 25 de diciembre— mientras `dim_date.is_business_day` estaba en cada fila y `refs.py`
   documentaba por escrito la regla de traslado que no existía en el código.
4. **`fact_refund` era una tabla de dinero que no se podía sumar:** catorce monedas locales sin columna en
   euros. La suma ingenua daba 55,71 M € de los que solo 22,06 M eran euros; el resto, öre suecos y haléřů
   checos sumados entre sí.
5. **Los 449.759 pagos iban a un IBAN español** con código de banco 0000, fuera cual fuera el país del
   beneficiario: 52.350 a grupos británicos, 21.927 a suizos, 13.472 a estadounidenses. Contradice la única
   historia que el grafo societario existe para contar.
6. **`v_money_flow` seguía leyendo la tabla cruda:** el arreglo de la deduplicación llegó a `build_derived.py`
   y no a las vistas, sobrestimando lo recaudado en 3.117.222 €.
7. **La cohorte de tarjetas caducadas declarada al 2,2 % se entregaba al 0,98 %**, porque
   `early = early & ~is_primary` recortaba a la mitad una tasa que ya estaba aplicada.
8. **Cascada de hashes independientes** en `build_derived.py`: cada rama de un `CASE` usaba una sal distinta,
   así que los umbrales no eran una función de distribución acumulada sino sorteos independientes. **Todos**
   los repartos declarados salían mal.
9. **`popularity_weight` era monótono en `product_sk`:** la clave subrogada ERA el ranking de ventas. Un solo
   SKU se llevaba el 51,8 % de su categoría y `ORDER BY product_sk` era una lista de más vendidos.
10. **Cuatro consejeros delegados y cuatro raíces** en un organigrama que decía tener uno.
11. Y una lista larga: tráfico de pruebas liquidándose (11,0 M € de dinero falso en el libro mayor);
    devoluciones y disputas fechadas hasta el 2027 en un almacén que acaba en agosto de 2026; 9.626 líneas de
    alcohol, tabaco y apuestas compradas por menores de 18 años **en la fecha de compra**; `is_test` cambiando
    de valor entre los intentos de un mismo pago; la reserva rodante que se retenía y nunca se liberaba;
    `payout_sk` no determinista porque el `ORDER BY` de su ventana no era la clave de agrupación; las etapas
    de una disputa separadas **exactamente** 21 días, sin una sola excepción; el plazo de devolución uniforme
    en [1,45] con muro duro al día 45; y el puente de gestores sin vigencia, con 955 asignaciones anteriores a
    la contratación del gestor.

**Qué se hizo.** Los tres arreglos regresivos y los defectos de gravedad alta y media. **14 comprobaciones
nuevas (de 37 a 51)**, y cuatro de ellas cazaron defectos el mismo día que se escribieron.

Dos lecciones de método:
- **Mi primer arreglo de la venta a menores no podía funcionar.** Volvía a sortear el PRODUCTO, y en apuestas,
  alcohol y tabaco todos los SKU están restringidos, así que el remuestreo caía en el mismo sitio. La
  restricción va en la elección de COMERCIO: un menor no entra en un sector con puerta de edad.
- **Un invariante que falla es una afirmación sobre los datos, así que tiene que estar bien antes de culpar a
  los datos.** El check de mod-97 del IBAN reportó 16 IBAN válidos que una reimplementación en Python no
  encontraba: plegaba el número en bloques de nueve dígitos con `a*10^9 + b`, que solo es correcto si todos
  los bloques miden nueve, y el último nunca mide nueve. El defecto era el check.

Y una decisión estructural: **las cifras del README ya no se escriben, se generan.** `datagen/report.py`
produce `MEASURED-<perfil>.md` desde el manifiesto y la base de datos en cada build. Una cifra que ese script
no sepa producir no entra en la documentación.

**Números.** Perfil `full` reconstruido, `./datagen/run.sh full`, artefacto `datagen/MEASURED-full.md`:

**294.752.291 filas · 24 tablas · 7,46 GB · 51 comprobaciones en verde.**
Aprobación 86,93 % · ticket mediano 42,72 € · medio 173,38 € · **transfronterizo 24,6 %** (dentro del rango
europeo real por primera vez; venía del 86,7 %) · 9.153,2 M € liquidados · 195,6 M € de comisión ·
728.643 pagos a comercios con un 1,27 % de lotes sin cerrar · concentración **medida sobre el tráfico**
30,78 % / 71,79 % · 1.288 anillos con 9.705 miembros · 8,09 % de clientes con nota de soporte y 31.313 con
supresión pedida.

Trampas medidas: contar filas **+24,0 %** · clave natural del comercio **+52,6 %** · `JOIN` de FX por fecha
**−24,4 %** · duplicados de ingesta 0,349 % · sumar el contracargo en todas sus etapas **+60,5 %**.
Invariantes que dan cero: dinero en día no hábil **0** · interchange sobre el tope **0** · notas con un
teléfono ajeno **0**. Contracargo por tramo de riesgo: 0,358 % → 0,682 % → 1,390 %.
Las catorce consultas del cuaderno corren sobre los 294,7 M de filas en menos de 3,9 s la más lenta.

**Decisiones.**
- **El libro mayor está en euros y lo dice.** La divisa preferida del comercio se conserva como información,
  no como etiqueta sobre un importe que nunca se convirtió.
- **El IBAN lleva el país del beneficiario y la longitud correcta de ese país (ISO 13616), con el dígito de
  control deliberadamente equivocado.** Mismo criterio que el NIF: un identificador sintético no debe poder
  pertenecer a nadie, y la garantía más barata es que falle la validación del propio estándar.
- **El importe de una disputa pertenece al CASO, no a cada etapa.** Se añaden `dispute_case_id` e
  `is_final_stage` para que la trampa sea navegable en vez de silenciosa.
- **Nada se fecha después del último día del almacén.** Censura por la derecha en devoluciones y disputas.

**Siguiente.**
El generador está terminado y auditado dos veces. Sigue bloqueado lo mismo desde el primer día: **`P-001` sin
aprobar** —y nada de `datagen/` entra en `src/` hasta entonces—, `Q-002` sin firmar, `git init` sin ejecutar y
`thresholds.lock` sin generar.
