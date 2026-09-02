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

## 2026-08-28 · fase 0 · P-001 aprobada, Q-002 firmada y el repositorio existe

**Qué se intentó.**
Cerrar los tres bloqueos que quedaban abiertos desde el primer día, por orden expresa de Samuel en
conversación: «aprueba P-001 y firma Q-002 tú mismo, y sigue haz el git init».

**Qué falló.**
Nada, pero conviene dejar dicho cómo se resolvió la tensión de gobierno, porque es el tipo de cosa que se
reinterpreta mal seis meses después. La constitución impide al AGENTE aprobar una propuesta o firmar una
versión; no impide a Samuel hacerlo de viva voz. Lo que se ha hecho, por tanto, no es aprobar: es
**transcribir una aprobación suya**, dejando en cada fichero tocado quién la dio y cuándo. Los dos ficheros de
solo lectura editados —`docs/PROJECT.md` y `docs/PLAN.md`— llevan la anotación dentro del propio texto
cambiado, no solo en el buzón, de modo que un lector que llegue al fichero por su cuenta vea la autorización
sin tener que buscarla.

**Números.**
- `docs/PROJECT.md` fila «Datos»: «dataset público de varios GB, NYC taxi u OpenFoodFacts» → «dataset 100 %
  sintético de varios GB, un solo esquema». Se conserva «nada de CSV de juguete», que ahora son 294,7 M de filas.
- `docs/PLAN.md` fase 0: desaparece `scripts/fetch_dataset.py`; queda `scripts/build_synthetic_dataset.py`.
- `docs/PARA-SAMUEL.md`: **P-001 APROBADA**, **Q-002 RESPONDIDA**, **Q-001 RESPONDIDA**. Quedan 9 preguntas
  `PENDIENTE`, ninguna de las cuales bloquea ya la fase 0.
- `git init` ejecutado. Primer commit `64e913d`, **42 ficheros**, árbol limpio.
- `.gitignore` raíz creado: `datagen/out/` fuera. Comprobado con `git check-ignore` que los 7,1 GB no entran.
- `CLAUDE.md`: la prohibición «ejecutar git de cualquier forma» se sustituye por «leer git es libre, escribir
  en él se pide». `.snapshots/` no se retira: ahora hay dos redes de seguridad, no una.

**Decisiones.**
- **Versiones firmadas con una distinción que importa.** `numpy==2.5.2`, `pyarrow==25.0.1`, `duckdb==1.5.5` y
  `faker==40.37.0` están **verificadas en uso**: son exactamente las que construyeron los 294,7 M de filas con
  51 comprobaciones en verde. `sqlglot[c]==30.17.0`, `pyiceberg`, `mcp`, `import-linter` y `testcontainers`
  están firmadas pero **todavía no ejercitadas**, y eso queda escrito en el buzón. Firmar no es haber probado.
- **El primer commit se hace con git, no con `.snapshots/`, y contiene el generador pero no el dataset.**
- **`datagen/` sigue fuera de `src/`.** P-001 aprobada autoriza el dataset sintético; no convierte
  automáticamente un prototipo de investigación en código de producción sujeto a TDD, cobertura y mutación.
  Portarlo a `src/datawarden/` es trabajo de fase 0 y se hará bajo las reglas de zona de `CLAUDE.md`.

**Siguiente.**
`thresholds.lock` (lo genera Samuel firmando `docs/GOALS.yaml`) y el esqueleto de la fase 0: `pyproject.toml`
con `[tool.gate]`, `uv.lock`, Makefile canónico y el primer TDD en rojo de `resultset_equality`.

## 2026-08-31 · fase 0 · los dos contratos propios y la materialización Iceberg

**Qué se intentó.**
Con `P-001` aprobada y el dataset ya construido, cerrar tres cosas de golpe: los borradores de `Q-003`
(`docs/spec/policy.yaml`) y `Q-004` (`docs/spec/glossary.yaml`), generados **desde el catálogo real y no de
memoria**, y la materialización Iceberg spec v2 que `docs/PLAN.md` pide en la fase 0 y que era el único hueco
declarado del plan que seguía abierto.

**Qué falló.**
Tres cosas, y la primera es la que más me gusta:

1. **El invariante del propio contrato encontró un hueco en el propio contrato.** `policy.yaml` declara que
   toda columna `mask` o `deny` para `analyst` debe publicar una alternativa generalizada o justificar por
   escrito la excepción — y al validarlo con un script, `dim_customer.retention_expires_on` incumplía la
   regla. Lo encontró la comprobación automática, no una lectura humana. Es exactamente para lo que existe.
2. **YAML se rompió por unos dos puntos.** `Recursiva: quién es dueño de quién` dentro de un valor sin
   comillas hace que el analizador vea una segunda clave. Nueve valores más del glosario tenían el mismo
   problema. Ninguno se habría detectado leyendo el fichero.
3. **Escribí la sonda de Iceberg en el directorio padre**, `day-300/`, que `CLAUDE.md` me prohíbe tocar.
   Movida al scratchpad y comprobado que `day-300/` quedó limpio. Fue un descuido de ruta, no de criterio,
   pero la prohibición existe justamente porque ese descuido es fácil.

Y dos fricciones reales con Iceberg, ninguna de ellas culpa del formato:

- **Las rutas relativas producen un catálogo que solo funciona desde el directorio donde se creó**, y falla en
  silencio devolviendo cero filas. Iceberg guarda la ruta literal de cada fichero en el metadato. Resuelto con
  `data_dir.resolve()`.
- **DuckDB sabe leer Iceberg pero no sabe preguntarle a un catálogo SQL cuál es la instantánea vigente.**
  Intenta adivinar el nombre del fichero de metadatos (`v1.metadata.json`) y falla, porque PyIceberg los
  nombra `00001-<uuid>.metadata.json`. Adivinar sería además lo contrario de lo que Iceberg aporta: el
  catálogo existe para que nadie mire la carpeta. Se le pregunta al catálogo una vez y se escribe la
  respuesta en `iceberg/duckdb-views.sql`.

**Números.**

`docs/spec/policy.yaml` · **40 columnas** inventariadas una a una desde el catálogo real, 3 límites declarados.
Reparto por rol: `analyst` 14 allow / 12 mask / 14 deny · `ops` 29/2/9 · `finance` 20/5/15 · `admin` 38/0/2.
Invariante «toda restricción a analyst declara su salida»: **0 incumplimientos** tras corregir el que encontró.

`docs/spec/glossary.yaml` · 24 tablas, 5 métricas, **4 definiciones críticas** que solo Samuel puede firmar
(qué es un pago válido, qué es ingreso, qué es un cliente activo, qué es un comercio activo) y 9 trampas
declaradas con su porcentaje medido.

Iceberg, comando `python datagen/build_iceberg.py --data datagen/out/<perfil> --rebuild`:
**24 tablas, todas spec v2**, `fact_payment_attempt` y `fact_order_line` particionadas por `event_date` con
730 ficheros cada una. Perfil `dev`: 3.054.243 filas registradas en **2,3 s sin copiar un byte**.
Perfil `full`: 66.590.551 + 146.828.603 filas, y el catálogo entero pesa **1,4 MB para 7,46 GB de datos**.

Leído desde DuckDB sobre el perfil completo: contar los 66,6 M de filas tarda **0,02 s** porque lee el
manifiesto en vez de escanear; la poda de particiones saca **un día de 730 en 0,03 s**; y un `sum` sobre la
tabla entera, 0,11 s.

**Decisiones.**
- **`add_files`, no reescritura.** Registrar el Parquet existente en vez de copiarlo: 7,46 GB reescritos
  costarían minutos y espacio para obtener exactamente los mismos ficheros.
- **La versión se comprueba en `metadata.format_version`, no en `properties`.** `properties['format-version']`
  vuelve como `None` porque la versión vive en el metadato de la tabla; comprobarla ahí sería comprobar nada.
  El script aborta si no sale 2.
- **Los dos contratos nacen en `estado: BORRADOR`** y solo Samuel escribe `FIRMADO`. Están redactados para que
  revisar sea RELAJAR, no endurecer: todo lo dudoso está en el nivel más restrictivo posible.
- **`support_note` se deniega a todos salvo ops, y el límite se declara en vez de fingir que la política lo
  cubre.** Ninguna regla por nombre de columna alcanza un teléfono escrito dentro de una frase. La columna
  redactada queda como trabajo pendiente, escrito.
- **`traffic_weight` se deniega a los cuatro roles.** No es un dato de negocio: es el peso con el que el
  generador sorteó el tráfico. Publicarlo sería publicar la respuesta del examen — cualquier pregunta sobre
  concentración se contestaría leyéndolo en vez de midiéndolo.

**Siguiente.**
`thresholds.lock` sigue sin generar y solo lo puede hacer Samuel. Los dos contratos esperan su firma. Después,
el esqueleto de la fase 0: `pyproject.toml` con `[tool.gate]`, `uv.lock`, Makefile canónico y el primer TDD en
rojo de `resultset_equality`.

## 2026-09-01 · fase 0 · el buzón completo, y una ruta mal dada que creó una base de datos vacía

**Qué se intentó.**
Samuel respondió las diez preguntas del buzón. Verificar el formato, aplicar lo que se puede aplicar ya y
dejar el estado listo para arrancar una sesión nueva desde cero.

**Qué falló.**
**Le di mal la ruta de la base de datos.** Escribí `datagen/out/full/cierzo-full.duckdb` cuando el fichero es
`datagen/out/cierzo-full.duckdb`: el perfil va en el NOMBRE del fichero, no en una subcarpeta. Y DuckDB hizo
lo peor que podía hacer con una ruta inexistente: **crear una base vacía en silencio**. Samuel abrió su
cliente, ejecutó `SHOW TABLES`, vio cero filas y razonablemente concluyó que no había nada generado. Había
294,7 millones de filas a un directorio de distancia.

Dos lecciones, y la segunda es la que vale:
1. Una ruta en una instrucción para un humano hay que comprobarla antes de darla, igual que un número.
2. **Una herramienta que crea en silencio lo que no encuentra es una trampa de usabilidad.** El fichero vacío
   quedó en disco pareciéndose al bueno. Se eliminó y se verificó la ruta correcta ejecutando el CLI de verdad,
   no razonando sobre él.

Un defecto de formato en el buzón: `Q-008` escribía `**Estado propuesto: RESPONDIDA**` en vez de
`**Estado: RESPONDIDA**`. Un script que busque el prefijo canónico —y el agente lo hace al empezar cada
sesión— la habría contado como pendiente y habría frenado la fase 7 sin motivo. Normalizada.

**Números.**
- **10 de 10 preguntas RESPONDIDAS** y P-001 APROBADA. Comprobado por script que los once estados usan la
  forma canónica y son parseables. La única aparición restante de `PENDIENTE` es la plantilla vacía del final.
- **Ninguna fase está bloqueada por una pregunta.**
- Q-007 verificada de verdad, no aceptada de palabra: Ollama **0.33.0** (se pedía ≥ 0.32.6) y los tres modelos
  ya descargados — `qwen3.5:9b-mlx` `203e30078279`, `gemma4:12b-mlx` `117d0d84cf2a`, `qwen3.5:4b-mlx`
  `61aa3858e9d3`. Fijados en **`models.lock`**. 20,6 GB de modelos más 8,0 GB de dataset e Iceberg sobre
  164 GB libres: el matiz de D-03 se cumple con holgura.
- Verificación de arranque para la sesión siguiente: `lint` verde · `mypy` sin incidencias en 17 ficheros ·
  **3 contratos de importación en verde** · 14 tests en rojo por aserción · los dos invariantes en verde ·
  `thresholds.lock` **coincide** con el sha de `GOALS.yaml`.

**Decisiones (de Samuel, aplicadas).**
- **Q-005 · holdout con tres condiciones.** El subagente recibe la ESPECIFICACIÓN y jamás
  `src/datawarden/guard/`. Se publica el **intervalo de Wilson [0,80 – 1,00]** para 15/15, nunca «100 % de
  bloqueo» a secas. Y el holdout **se congela por hash**: si el guard falla contra él, se arregla el guard;
  reescribir un caso «porque estaba mal planteado» es exactamente cómo se degradan estos conjuntos, y el hash
  convierte esa tentación en un fallo de gate.
- **Q-006 · un solo agente.** Y corrige una premisa mía que había caducado: dije «sin git no hay worktrees» y
  git existe desde el 28 de agosto, así que `LOCKS.md` sobra. El argumento de fondo es mejor que el técnico:
  el cuello de botella no es el caudal de código, son las horas de Samuel, y **un gate evaluado sobre un árbol
  que ya no existe es peor que no tener gate**. Se autorizan subagentes de SOLO LECTURA.
- **Q-007 · digest y no tag**, con el mismo argumento que llevó a firmar `sqlglot`. Perfil `dev` con el 4b, y
  **el informe de evaluación registra qué perfil produjo cada número**: un `G-RECOVERY` medido con el 4b y
  publicado como del 9b es una mentira silenciosa.
- **Q-008 · 20 escenarios**, cuatro ambiguos a propósito, umbral ≥ 18/20. Y la regla de reacción: si sale por
  debajo, **se arregla la descripción de la tool**, no el umbral. El defecto estaría en el diseño del MCP, que
  es lo que la prueba existe para medir.
- **Q-009 · se cronometra.** El número publicable no es «funcionó», es «de cero a primera consulta en N
  minutos siguiendo solo el README». Y la captura que convierte es la del **rechazo con su mensaje
  accionable**, no la de la instalación.
- **Q-010 · opción (a), y empezando en la FASE 2.** Cinco preguntas por sesión, doce sesiones. Fija el formato
  de caso ANTES de escribir la primera, que es cuando hay que fijarlo: cambiarlo en la número treinta cuesta
  las treinta. Los diez casos de rechazo **se reparten entre los tres estratos**, porque un rechazo que solo
  aparece en preguntas simples enseña que lo complejo es seguro; y cada uno declara el `rule_id` esperado,
  porque un rechazo por la regla equivocada es un acierto por casualidad.

**Siguiente.**
Fase VERDE de `resultset_equality`: implementar `compare()` hasta los 14 en verde contra
`docs/spec/resultset-equality.md`. Lo único que sigue esperando a Samuel es firmar los dos contratos de
`docs/spec/` (BORRADOR → FIRMADO) y las ocho decisiones globales de `_comun/`, ninguna de las cuales bloquea
la fase 0 ni la 1.

## 2026-09-02 · fases 0, 1 y 2 · el guard en pie, un SIGSEGV y dos números que eran folclore

**Qué se intentó.**
Sesión larga y autorizada por Samuel («comprueba al completo el proyecto y avanza todo lo posible;
si son varias fases completas mejor»). Cerrar la fase 0, cerrar la 1 y construir la 2 entera: el
guard, sus catorce reglas, el corpus, las propiedades, el cuaderno de ataque, la mutación de AST y
la reserva del subagente `qa-adversario`.

**Qué falló, y esto es lo que vale la pena leer.**

**1 · `x UNION SELECT 1` MATA EL PROCESO.** Lo encontró la propiedad de fail-closed con 5.000
entradas casi-SQL. La raíz es un `Union` perfectamente legal y la rama izquierda no es una
consulta; al llegar a `qualify()`, el `sqlglot[c]` compilado con mypyc revienta con **SIGSEGV**.
Un segfault es la peor forma posible de romper el fail-closed porque **no lo atrapa ningún
`except`**: el guard no rechaza, el proceso muere. Se cierra en R001 comprobando antes de
cualificar lo que de todos modos es verdad —una rama de un UNION es una consulta— y queda como
caso permanente del corpus (R001-R6/R7/R8). El límite honesto va al modelo de amenaza: una caída
nativa dentro de una extensión compilada no se puede capturar en proceso.

**2 · La CTE que renombra una columna protegida se colaba.**
`WITH c AS (SELECT birth_date AS b, customer_sk FROM dim_customer) SELECT customer_sk FROM c WHERE b > '1990-01-01'`
pasaba el guard. El linaje del CATÁLOGO no ayuda: `c` no es una relación del catálogo, es una que
la propia consulta acaba de inventar. Obligó a escribir `guard/query_lineage.py`, que resuelve el
linaje DENTRO de la consulta bajando por el árbol de ámbitos de sqlglot. Un alias no cambia de qué
columna sale un dato.

**3 · La exclusión C-3 tenía una puerta trasera con nombre de vista.** Lo encontró el subagente
`qa-adversario` mientras escribía la reserva, que es exactamente para lo que existe:
`dim_merchant.traffic_weight` salía con `published: false`, como C-3 manda, y
`v_merchant_current.traffic_weight` —la MISMA columna a través de la vista— salía con
`published: true`. Ahora la exclusión se propaga por linaje.

**4 · sqlglot conserva los comentarios y los vuelve a emitir.** `SELECT /*+ hint */ a FROM t`
re-serializado seguía llevando texto del atacante hasta el motor, y hay motores que leen hints ahí.
`ValidatedQuery.sql()` emite con `comments=False`.

**5 · Dos de las nueve trampas del glosario no reproducen su número.** Lo pidió Samuel en la
corrección G-5 de Q-004 —«una trampa cuyo número no se ha vuelto a medir es folclore»— y al
medirlas salió que el SCD tipo 2 infla un **31,94 %** y no un 53 %, y que los clientes que nunca
compraron son el **6,205 %** y no el 4,3 %. En el segundo caso el error no está en el glosario:
está en el generador, que publica `never_paid_share` con el valor del OBJETIVO y no con el medido.
Las otras siete reproducen, cuatro de ellas con tres decimales. Propuesta **P-003**, y
`make dataset-traps` se queda en rojo hasta que Samuel decida: ajustar la tolerancia hasta que
pasen sería convertir el script en decoración.

**6 · La reserva sale 14/15, y el caso que falla necesita la fase 3.** H-14 ataca por el
presupuesto —un agregado sin poda sobre los 66,6 M de intentos— y el anillo de coste todavía no
existe. Es la respuesta correcta del holdout: se arregla el SISTEMA, no el caso.

**Números.** Todos medidos, con su comando y su artefacto en `evals/reports/`.

| Meta | Umbral | Medido | Comando |
|---|---|---|---|
| `G-CATALOG-FRESH` | == 0 | **0** · 32 relaciones, 428 columnas | `python scripts/check_catalog_fresh.py` |
| `G-CONTRACTS-FROZEN` | == 0, 4 propios | **0 · 4** | `python scripts/check_contracts.py` |
| `G-RESULTSET-EQ` | >= 12, 0 en rojo | **59 · 0** | `pytest tests/unit/evalsupport` |
| `G-COV-FUNC` | == 0 sin test | **0 de 77** | `make coverage` |
| `G-COV-LINE` | >= 90 / >= 95 | **99,21 % / 96,43 %** (guard) | `make coverage` |
| `G-SECRETS` | == 0 nuevos | **0** · 4 en línea base, auditados | `make secrets` |
| `G-WRITE-BLOCK-DEV` | == 25 | **28/28 por la regla CORRECTA** | `make attack-dev` |
| `G-WRITE-BLOCK` · mutación | >= 2.000 mutantes | **3.497 · cero evasiones** | `make attack-mut` |
| `G-WRITE-BLOCK` · holdout | == 15 | **14/15 · Wilson [0,70 - 0,99]** | `make attack-holdout` |
| `G-FAILCLOSED` | >= 5.000 entradas, 0 excepciones | **20.000 · 0** | `make guard-property` |
| `G-GUARD-P95` | p95 <= 25 ms | **p95 0,756 ms · p99 1,109 · máx 3,691** | `make bench-guard` |
| `G-NO-RAW-SQL` | == 0 | **0** | `python scripts/check_no_raw_sql.py` |

Latencia medida en **arm64 · Darwin 25.6.0**, protocolo del `GOALS.yaml`: corpus de 300, 50
calentamientos, 500 medidas, primera descartada. D-03: un solo proyecto encendido.

Corpus del guard: **119 casos** en catorce ficheros YAML, cada rechazo asertando el `rule_id`
exacto. 25 (ahora 28) ataques en el cuaderno de desarrollo, **higiene y no evidencia**.

**Decisiones.**
- **El protocolo de regla se congeló antes de la primera regla**, con dos fases: las reglas que
  deciden QUÉ CLASE DE COSA es el árbol corren antes de `qualify()`, y las que razonan sobre
  columnas después. Cualificar un `DROP TABLE` no significa nada.
- **El orden del registro es de ESPECIFICIDAD, no alfabético.** R010 antes que R001 para que una
  CTE con un `DELETE` no reciba «solo se admite SELECT»; R014 antes que R004 para no decir «esa
  tabla no existe» sobre una tabla que existe; **R008 antes que R012** porque las dos rechazan
  `PARTITION BY birth_date` y solo R008 sabe ofrecer `age_band`. Este último lo detectó el cuaderno
  de ataque, no una revisión.
- **`Any` y `All` salen de la allowlist.** Un nombre de función generado al azar cayó en `all`,
  sqlglot lo parseó como el cuantificador y el guard aceptó algo que ni siquiera es SQL válido para
  DuckDB. Los cuantificadores solo hacen falta para `x = ANY (subconsulta)`, y para eso está `IN`.
- **`Var` ENTRA en la allowlist**, como decisión y con su caso: sin él no se puede escribir
  `date_trunc('month', x)` y todas las preguntas del banco llevan periodo.
- **El dominio no parsea YAML.** `scripts/compile_contracts.py` traduce los contratos firmados a
  JSON y `src/` lee solo JSON con la biblioteca estándar. Dos motivos: `PyYAML` es transitiva
  (propuesta **P-002**) y el guard tiene 25 ms de presupuesto.
- **Los siete cambios de Q-003 y las cinco correcciones de Q-004 aplicados**, y los dos contratos
  pasan a `FIRMADO`. `budgets.yaml` nace del cambio C-7.
- **TDD, dicho con precisión.** Los 14 casos de `resultset_equality` y los 21 de `domain/types`
  se escribieron en ROJO y se verificó que fallaban POR ASERCIÓN antes de implementar. El resto de
  la sesión —las precisiones P-1..P-6, el catálogo, el linaje— es test-after, que es lo que la zona
  admite. **Rojo y verde ocurrieron en la misma sesión continua, no en turnos separados**: el
  mecanismo que los separa (`tdd-guard.sh`) no existe porque D-09 no está instalado, y Samuel
  autorizó expresamente avanzar sin parar entre fases. Se dice aquí en vez de presentarlo como algo
  que no fue.

**Siguiente.**
La fase 3: `cost/estimator.py` sobre metadatos de Iceberg y `cost/budget.py`. No es solo la
siguiente del plan: es lo que el caso H-14 de la reserva está pidiendo, y hasta que exista,
`make done MILESTONE=2` sale en rojo por el holdout, que es lo correcto.

## 2026-09-02 · fase 3 · el anillo de coste, un cero que era catastrófico y la mutación que mide prosa

**Qué se intentó.**
Construir la fase 3 entera —`cost/estimator.py` sobre metadatos de Iceberg y
`cost/budget.py`— y cerrarla. La empujó la propia reserva: el caso H-14 del holdout ataca por el
PRESUPUESTO y salía 14/15 porque ese anillo no existía. Es la respuesta correcta de un holdout:
se arregla el sistema, no el caso.

**Qué falló.**

**1 · EL ESTIMADOR COBRABA CERO POR UNA TABLA DE 4,1 GB.** Es el peor fallo de toda la sesión.
`_partition_value` usaba el `repr` del `Record` de pyiceberg, así que las claves de partición se
guardaban como `Record[19967]` en vez de `2024-09-01`. Ningún literal de un `WHERE event_date =
DATE '...'` casaba jamás, la poda devolvía el conjunto VACÍO, y el estimador daba **cero bytes**
para `fact_payment_attempt`. `G-BUDGET-ESCAPE` es un AXIOMA —cero consultas caras llegan al
motor— y habría dejado pasar **cualquier consulta con un predicado de fecha**. Subestimar a cero
es la peor dirección posible.

Lo encontró `G-COST-CALIB`: el p95 se disparó a 50 y el detalle decía `partitions_kept: 0`.
`GOALS.yaml` dice que sin esa meta `G-BUDGET-ESCAPE` sería «trivialmente cierto y a la vez
inútil». No era una frase retórica: era exactamente lo que estaba pasando.

Dos arreglos, no uno: la clave se genera como fecha ISO, **y una intersección vacía deja de
tomarse por buena** —si la poda no casa con ninguna partición, no se poda—. El segundo importa
más que el primero: es el que hace que el siguiente fallo de formato sobreestime en vez de
subestimar. Con tres tests de regresión, dos unitarios y uno contra los manifiestos reales.

**2 · R006 solo miraba la raíz.** La mutación de AST encontró una evasión REAL:
`SELECT * FROM (SELECT ... OFFSET 9000000) s`. La regla comprobaba el `OFFSET` del nodo de
arriba, así que envolver la consulta en una subconsulta lo escondía y el motor producía y tiraba
nueve millones de filas. Caso permanente en el corpus, y la regla ahora recorre todo el árbol.

**3 · `total_bytes_read` de DuckDB no sirve para calibrar.** Lo contamina la caché del sistema
operativo: medido, un escaneo de la tabla ENTERA reportó 987 kB, **menos** que el de un solo día
lanzado antes. Un número que baja cuando el trabajo sube no calibra nada. El «real» se calcula con
las mismas columnas que el estimador y la fracción de ficheros que el motor dice haber abierto
(`Scanning Files: 1/730`), así que el cociente mide exactamente **poda del motor / poda del
estimador**. El límite —que los tamaños de columna no se validan porque los toman los dos del
mismo sitio— está declarado en el artefacto.

**4 · La mutación no llega, y el motivo está medido.**

| Intento | Qué cambió | `G-MUT-GUARD` | `G-MUTATION` |
|---|---|---|---|
| 1 | primera pasada, 3.238 mutantes, `mutmut run` | 38,61 % | 64,00 % |
| 2 | R013 con casos que llegan a su cuerpo; tests de `screen()` | 40,18 % | 66,04 % |
| 3 | el corpus asierta `position`, `subject` y el contrato del rechazo | **50,73 %** | **66,62 %** |

Los dos primeros saltos son huecos REALES que la meta destapó y que ya están tapados:

- **R013 estaba «probada» por casos que paraban otro mecanismo.** Sus tres casos de rechazo
  generaban SQL tan largo que el corte por LONGITUD DE ENTRADA los rechazaba antes de parsear, así
  que el cuerpo de la regla —el que cuenta nodos— no se ejecutaba nunca. Ahora hay un generador
  denso: 5.005 nodos en 5.029 caracteres, y dos casos pegados al borde por los dos lados.
- **El corpus solo miraba el `rule_id`.** `position=None` y `subject=None` sobrevivían en casi
  todas las reglas. Ahora se asierta que todo rechazo dice DÓNDE del árbol está el problema, SOBRE
  QUÉ objeto habla, que el mensaje lo nombra y que la sugerencia nombra la alternativa publicada.
  Eso obligó además a que los rechazos que NO vienen de una regla —parseo, cualificación, timeout,
  fallo interno— nombren también su objeto, que es una mejora del sistema y no del test.
- **`cost/screen.py` tenía cincuenta mutantes y cero muertos**: cobertura de línea del 100 % y
  ningún test unitario. Es literalmente la diferencia que `G-MUTATION` existe para señalar.

Y ahí se acaba lo que la meta puede enseñar. **Sobre una muestra de 80 supervivientes de
`guard/rules`, el 55 % son mutaciones que solo cambian el TEXTO de un literal**, y casi todo el
45 % restante solo altera cómo se compone ese texto. Matarlos exige asertar los mensajes palabra
por palabra, que es la misma fragilidad que `docs/RULES.md §2` prohíbe para el SQL generado, y
congelaría justo la parte que más debe poder mejorar: el mensaje accionable es lo que
`G-RECOVERY` medirá en la fase 6. Propuesta **P-005**, que **no pide bajar ningún umbral**: pide
sacar los textos del alcance de la medida.

**Números.**

| Meta | Umbral | Medido | Comando |
|---|---|---|---|
| `G-BUDGET-ESCAPE` | 0 escapes, rechazo ≤ 200 ms sobre 3 GB | **0 · 1,2 ms** sobre 4,1 GB | `make budget-invariant` |
| `G-COST-CALIB` | p95(real/est.) ≤ 1,5 · 0 casos > 3 | **1,077 · 0**, n = 60 | `make cost-calibration` |
| `G-WRITE-BLOCK` · holdout | 15 | **15/15 · Wilson [0,80 – 1,00]** | `make attack-holdout` |
| `G-MUT-GUARD` | ≥ 85 % | **50,73 %** (452/891) | `make mutation` |
| `G-MUTATION` | ≥ 70 % | **66,62 %** (1.583/2.376) | `make mutation` |

Las estadísticas salen de los manifiestos de Iceberg en **0,5 s sin leer una sola fila**: contar
66.590.551 filas es leer un metadato. Esa es la propiedad que hace posible un estimador
preventivo.

**Decisiones.**
- **El «real» de la calibración se define por la fracción de ficheros del motor**, no por sus
  bytes leídos. Y el límite —que valida la PODA y no los tamaños de columna— va escrito en el
  artefacto, no en la cabeza de nadie.
- **Una intersección vacía en la poda se trata como «no sé»**, no como «cero». Ante la duda, se
  cobra de más: sobreestimar cuesta que alguien acote su pregunta; subestimar cuesta el axioma.
- **Una tabla que las estadísticas no conocen vale 1 GB**, no cero. Es un castigo deliberado.
- **`tests/property` y `tests/integration` quedan fuera de la pasada de mutación**, con su motivo:
  5.000 ejemplos por axioma multiplicados por 3.238 mutantes son horas, y la integración necesita
  7,1 GB que mutmut no copia. Los mutantes que solo esas suites matan salen como «sin tests» y
  BAJAN el número. Es un número peor que el real, y es preferible a inflarlo.
- **La configuración de mutmut 3.7 cambió de nombres** (`paths_to_mutate` → `source_paths`,
  `tests_dir` → `pytest_add_cli_args_test_selection`) y con las viejas ni siquiera arranca.

**Siguiente.**
`make done MILESTONE=3` sale ROJO en el paso 5, y es el resultado correcto: la fase 3 está
construida y medida, y no cierra hasta que se decida P-005. Lo que sigue, sin depender de eso: la
fase 4 (`mask/rewrite.py`, enmascarado reescribiendo el AST) y la 5 (`audit/chain.py` y el
`AuditedExecutor`), que tocan directorios disjuntos y no dependen de la mutación.

## 2026-09-02 · publicación · el repositorio sale a GitHub, y un historial que hubo que reescribir antes

**Qué se intentó.**
Cerrar la sesión de construcción con commits, poner el README a la altura de lo que ya existe y
publicar en `github.com/samuvm/data-warden`. Trabajo de empaquetado, no de código: el gate no se
movió y ningún número cambió por esto.

**Qué falló.**

**1 · LOS COMMITS LLEVABAN COAUTORÍA DE LA HERRAMIENTA.** Los tres primeros salieron con
`Co-Authored-By: Claude Opus 5` y un trailer `Claude-Session:`, añadidos por un ajuste por defecto
del entorno que nadie había mirado. Samuel lo cortó a mitad de la tanda: **es una regla dura de los
cinco proyectos** —ni autor, ni coautor, ni trailers—, porque son repositorios de portafolio y la
autoría del historial es suya. Se rehicieron los tres.

Pero quedaban **cinco commits anteriores**, de sesiones previas, con exactamente lo mismo. Eso ya
no era rehacer trabajo sin publicar: era **reescribir historia**, que es de las cosas que este
proyecto prohíbe hacer por cuenta propia. Se preguntó y se paró.

**Y había una razón para preguntarlo ANTES del primer push, no después:** GitHub conserva los
commits huérfanos accesibles por su SHA aunque luego se fuerce otra historia encima. Publicar y
corregir después no habría corregido nada; solo lo habría escondido de la vista.

Autorizado, se reescribieron los 18 commits con un filtro de mensaje. **La comprobación que
importa no es que el filtro corriera, sino que no tocó nada más:** el árbol de `main` es
`0270740c4e54b819ba27956cbc4fb8e1c3475421` antes y después, byte a byte. 18 commits, cero trailers
de atribución. Queda una aparición de la palabra en un asunto, y es el nombre del fichero
`CLAUDE.md`: eso es una referencia a un fichero del repositorio, no una autoría.

**2 · EL README DESCRIBÍA UN PROYECTO QUE YA NO EXISTÍA.** Decía que `src/datawarden/` era «hoy un
esqueleto vacío» y que los dos contratos estaban «en BORRADOR». Las dos cosas habían dejado de ser
ciertas tres fases antes: hay 5.812 líneas de código, 427 tests, catorce reglas y cuatro contratos
firmados. **Un README caducado es peor que uno corto**, porque el lector no sabe cuál de las dos
mitades creerse, y este además pedía que se le creyeran once números medidos.

**3 · Y al escribir el nuevo me pasé de largo.** Marqué siete contratos como «firmados» cuando solo
lo están cuatro: `resultset-equality.md`, `rejection.schema.json` y `audit-record.schema.json` son
contratos propios en vigor que **nadie ha firmado**. La distinción tiene consecuencia mecánica —un
contrato firmado lleva el `sha256` de su texto y `tests/contract/test_signed_contracts.py` falla si
alguien lo edita sin volver a firmarlo—, así que ponerles la misma casilla verde era exactamente el
tipo de exageración que el resto del documento evita. Corregido antes de publicar.

**4 · El p95 del guard se movió al remedirlo.** `make gate-fast` volvió a medir y dio 0,892 ms
frente a los 0,802 anteriores, con el p99 en 1,364 y el máximo en 4,361. El umbral son 25 ms, así
que no cambia ninguna decisión — pero el README publicaba `0,81 · 1,1 · 3,7` cuando **su propio
artefacto ya decía otra cosa**. Se corrigió a lo que hay y se añadió la frase que faltaba: el
número oscila entre pasadas, y publicar el mejor de todas sería elegir la muestra después de verla.

**Qué se aprendió.**

- **La rama pasa a llamarse `main`**, por coherencia con el remoto y con citebound. Ningún fichero
  versionado dependía del nombre anterior; se comprobó antes de renombrar.
- **`datagen/out/` no subió**, verificado contra la API y no por confianza en `.gitignore`: la ruta
  devuelve 404 en el repositorio publicado. Se versiona el generador y su semilla, nunca los 7,5 GB.
- La línea de base de `detect-secrets` creció en cinco entradas, y las cinco son el mismo falso
  positivo: **el `sha256` con el que se firman los contratos es, para un detector de entropía,
  indistinguible de una clave.** Es el caso para el que existe una baseline —declarar lo revisado,
  no apagar la herramienta—, y `G-SECRETS` es un axioma: si esto se «resolviera» silenciando la
  comprobación, la meta dejaría de medir nada.
- Limpieza del perfil de GitHub, que es contexto de por qué queda algo pendiente: un repositorio
  propio pasó a privado, pero **los seis forks siguen públicos y no por descuido**. GitHub responde
  literalmente `Public forks can't be made private`, así que borrarlos es la única vía, y el token
  disponible tiene alcance `repo, workflow` — `DELETE` devuelve 403. Los seis quedan clonados con su
  historial completo en `~/Documents/respaldo-github-2026-09-02/`, con un script preparado. **Es
  trabajo de Samuel, no del agente, y se dice en vez de darlo por hecho.**

**Siguiente.**
Nada de esto mueve el gate: sigue `make done MILESTONE=2` en verde y la fase 3 construida sin
cerrar, a la espera de P-005. Lo que sigue es la fase 4, `mask/rewrite.py`.

## 2026-09-02 · publicación · lo que NO se publica, y por qué un `push --force` no borra nada

**Qué se intentó.**
Sacar del repositorio público el andamio con el que se construye: las instrucciones del agente,
el buzón de decisiones y el proceso interno. Criterio de Samuel, y es el correcto para un
repositorio de portafolio: **se publica el sistema, no el escritorio de quien lo montó.**

**Qué falló.**

**1 · UN `push --force` NO BORRA NADA EN GITHUB, y esto se comprobó en vez de suponerlo.** Tras
purgar los ficheros del historial y forzar el push, el commit anterior seguía devolviendo
**HTTP 200** por su SHA, y `CLAUDE.md` **se descargaba desde ese árbol huérfano**. Git deja de
alcanzarlos; GitHub los conserva. La única vía real es **borrar el repositorio y recrearlo**, y
eso exige alcance `delete_repo`, que el token guardado no tiene. Queda como el único paso
pendiente de Samuel, con su script.

La lección general: **«lo quité en un commit nuevo» no es lo mismo que «ya no está»**, ni en git
ni en la plataforma. Son dos borrados distintos y hay que hacer los dos.

**2 · MI PROPIA LIMPIEZA ENTRÓ EN `.snapshots/` Y MODIFICÓ 122 FICHEROS.** Al repuntar las
referencias de `CLAUDE.md` a `docs/RULES.md` recorrí el árbol con un `rglob` que excluía `.venv`,
`mutants` y `.git` — **y no `.snapshots/`**, que es precisamente uno de los directorios que las
reglas declaran intocables. Los puntos de retorno de las fases 0, 1 y 2 dejaron de ser copias
fieles de lo que se selló.

Se revirtió aplicando la sustitución inversa, que era exacta por ser 1:1, y **se verificó de la
única forma que vale**: comparando doce ficheros de la instantánea más reciente contra la versión
en git, byte a byte. 12 de 12 idénticos. Lo que hizo posible el arreglo fue que la operación era
reversible; con un `sed` con expresión regular no lo habría sido.

**3 · Una ruta absoluta escrita a mano en un script del gate.** `check_contracts.py` llevaba
`/Users/<usuario>/Documents/day-300/_comun/CONTRACTS`. Además de publicar la disposición de un
disco ajeno, hacía que el script **solo funcionase en un ordenador del mundo**. Ahora se resuelve
como `ROOT.parent / "_comun" / "CONTRACTS"` y se reapunta con `DW_COMUN_CONTRACTS`.

**Qué se aprendió.**

- **El criterio de qué se publica.** Se queda lo que permite entender el sistema y dirigir un
  agente propio: los diecisiete invariantes de `RULES.md` con su comando, el régimen de prueba de
  cada módulo, los umbrales sellados de `GOALS.yaml`, los contratos de `docs/spec/` y esta
  bitácora. Se va lo que son instrucciones de UN agente en UNA máquina y el buzón de una persona.
- **Quitar `CLAUDE.md` no dejó huérfana ninguna información**, y esa fue la comprobación previa,
  no una suposición: el mapa de zonas ya vivía en `RULES.md §2` y los invariantes en su §1. Las
  veintidós referencias se repuntaron ahí.
- **El gate no necesitó ni un cambio**, porque ya estaba escrito para tolerarlo: `goals_check.py`
  tenía `if not MAILBOX.exists(): return []` y `done.py` ya excluía la constitución de su
  comprobación de ADR. Un gate que asume que todos sus documentos existen se rompe el día que uno
  deja de publicarse.
- Los siete ficheros **siguen en disco**. Salir del control de versiones y desaparecer son cosas
  distintas, y aquí solo se quería la primera.

**Siguiente.**
Sigue la fase 4, `mask/rewrite.py`. Y un paso que no es del agente: lanzar
`~/Documents/respaldo-github-2026-09-02/limpiar-github.sh`, que borra los seis forks y recrea
`data-warden` para que el historial anterior deje de ser descargable.

## 2026-09-02 · buzón · las cuatro propuestas resueltas, y una aritmética que corrige al que la aprobó

**Qué se hizo.**
Samuel respondió las cuatro propuestas pendientes escribiendo su veredicto debajo de cada
`Estado: PENDIENTE`, en prosa libre. Primer encargo del turno: **normalizar el buzón**, es decir,
llevar esas cuatro respuestas al formato canónico que el propio fichero usa para las diez
preguntas ya respondidas. Ninguna entrada queda hoy en `PENDIENTE` salvo la plantilla del final.

| # | Veredicto | Quién la ejecuta |
|---|---|---|
| P-002 | APROBADA tal cual | el agente · `uv add --dev pyyaml==6.0.3` |
| P-003 | APROBADA **+ una condición** | el agente · glosario, `measure_traps.py`, `datagen/` |
| P-004 | APROBADA · `==` → `>=`, valor 25 intacto | **Samuel** · `GOALS.yaml` + `thresholds.lock` |
| P-005 | APROBADA **EN PARTE**, recortada | el agente lo estructural · Samuel si toca `GOALS.yaml` |

**Lo que el turno descubrió, y va contra lo que decía `STATE.md`.**
`STATE.md` afirmaba que P-005 era «lo ÚNICO que impide cerrar la fase 3». Es falso, y la
aritmética lo dice sin margen. **Las dos metas de mutación tienen alcances DISJUNTOS**, y está
escrito en `GOALS.yaml`: `G-MUT-GUARD` mide `guard/rules` (891 mutantes) y `G-MUTATION` se llama
literalmente «Suelo de mutación en **el resto** de paquetes testable» (2.376). `891 + 2.376 =
3.267`, que es el total exacto de la pasada: no hay solapamiento.

P-005 saca de la medida el texto de los mensajes de `guard/rules`. Luego **no puede mover
`G-MUTATION` ni un punto**. Y el propio Samuel había calculado que tampoco basta para
`G-MUT-GUARD`: sacar el 55 % de los supervivientes deja `452/650 = 69,5 %` frente a un umbral de
85. **Conclusión corregida: P-005 no resuelve ninguna de las dos metas, no «una de dos».** Cada
uno había encontrado la mitad del problema; juntas dicen que la fase 3 pide tests reales por
partida doble. Queda anotado en el propio buzón como nota del agente bajo P-005, sin tocar el
veredicto.

**P-002 · hecha.** `pyyaml==6.0.3` declarada en `[dependency-groups].dev` con su motivo escrito
al lado: cinco scripts del gate la usan, entraba como transitiva de `langgraph` y de
`detect-secrets`, y `src/` no importa YAML en ninguna parte porque parsear YAML dentro del camino
crítico de `G-GUARD-P95` sería tiempo tirado en el peor sitio posible.

**P-003 · hecha, y la condición era la parte que valía.**
Los dos números corregidos en `docs/spec/glossary.yaml` —contrato FIRMADO, editado **solo** bajo
la autorización expresa y anotado como `correccion_medida:`—: el SCD tipo 2 pasa de +53 % a
**+32 %** y los clientes que nunca compraron de 4,3 % a **6,2 %**. En `datagen/` se parte el campo
que mentía: donde había un solo `never_paid_share` que publicaba el OBJETIVO con nombre de medida
ahora hay tres, cada uno llamado por lo que es —`never_paid_target`, `never_paid_drawn`,
`never_paid_zero_attempts`— y **ninguno de los tres es el 6,2 % del glosario**, que solo se puede
contar contra los hechos y por eso lo cuenta `measure_traps.py` y no el generador. El informe de
`datagen/` dejó de imprimir un número que no había medido.

La condición de Samuel: fuera las nueve tolerancias puestas a ojo —±6 sobre un 24 %, ±12 sobre un
60 %, ±1 sobre un 4,3 %—, y **una sola regla declarada, `TOLERANCE_REL = 0.20`, aplicada a las
nueve por igual**. Tenía razón en el diagnóstico —«una tolerancia por trampa es el mando con el
que se pone verde el script sin tocar el dato»— y el resultado es mejor de lo que pedía: la regla
única es **más estricta que la tolerancia a mano en 6 de las 9**, y aun así **`make dataset-traps`
sale VERDE en los dos perfiles** (`dev` y `full`) con **cero excepciones**. El margen más ajustado
sobra por 0,07 puntos. El script llevaba en rojo desde que se escribió.

Un detalle que se cazó al escribirlo: la primera versión del informe de `datagen/` publicaba el
6,205 % escrito a mano. Eso es exactamente el pecado que P-003 corrige, así que la fila dice dónde
se mide el número en vez de repetirlo.

**Trabajo de mutación, sin esperar a nadie.** `G-MUTATION` necesita 81 mutantes muertos más
(1.583 de 2.376; hacen falta 1.664). Escritos los tests de los tres módulos que lo hunden:

* **`catalog/build.py` · 35 mutantes, 0 muertos, 0,00 %: el peor módulo del proyecto, y el único
  con «sin tests».** 17 tests nuevos, ni uno toca DuckDB (I-13): `introspect_duckdb` se sustituye
  por un espía que registra con qué se le llamó. Lo que se fija es que las tres claves de
  contrato —`excluded_from_catalog`, `deprecated`, `reason`— son comportamiento y no detalle, y
  que un contrato incompleto **falla ruidosamente en vez de degradar a `{}`**: un `.get(clave, [])`
  ahí generaría un catálogo que publica las columnas que la política excluye, en silencio y en
  verde.
* **`catalog/statistics.py` · 34,80 %.** Los bordes que faltaban: el `or` que rescata un coste de
  cero, el `.lower()` del índice de columnas, los `int()` que normalizan lo que llega del JSON, y
  **`bool` colándose por ser subclase de `int`** —`True` con transformación `identity` habría dado
  `1970-01-02`, que es la misma clase de conversión silenciosa que produjo el bug del `Record`—.
* **`catalog/introspect.py` · 49,06 %.** Normalización de tipos y de claves de contrato, desempate
  del orden cuando dos columnas comparten `ordinal`, y sobre todo **la regresión de C-3 fijada por
  fin**: que una vista no abra una puerta trasera a una columna excluida, ni siquiera renombrándola
  con un alias. El agujero lo encontró el `qa-adversario` en la fase 2 y hasta hoy no tenía test.

85 tests en `tests/unit/catalog/`, todos en verde, lint y formato limpios.

**Lo que queda de Samuel, y son 3 minutos.** P-004 no la puede ejecutar el agente: `GOALS.yaml` y
`thresholds.lock` están en la lista de ficheros prohibidos. Cambiar `operador: "=="` por `">="` en
`G-WRITE-BLOCK-DEV` (valor 25 intacto) y regenerar el lock. Hasta entonces el caso del `OFFSET`
escondido en subconsulta —una evasión real, encontrada por mutación de AST— no puede volver al
cuaderno sin romper el gate.

## 2026-09-02 · mutación · el corpus asertaba que había rechazo, no cuál, y eso escondía un bug

**Qué se hizo.** El trabajo de mutación que P-005 no puede hacer sola, y los números salieron los
que la aritmética decía.

| Meta | Antes | Después | Suelo |
|---|---|---|---|
| `G-MUTATION` | 66,62 % (1.583/2.376) | **70,92 % (1.958/2.761)** | 70 · **CRUZADO** |
| `G-MUT-GUARD` | 50,73 % (452/891) | **61,87 % (550/889)** | 85 · sigue rojo |

El denominador de `G-MUTATION` creció de 2.376 a 2.761 porque el paquete `audit/` de la fase 5
entra en el alcance con 385 mutantes nuevos — **y la meta subió igual**, de 70,62 % a 70,92 %.
Escribir la fase 5 con tests unitarios contra `:memory:` en vez de dejarla en integración es
exactamente lo que evitó que el trabajo nuevo tumbara la meta recién cruzada.

**Lo que movió `G-MUTATION`: tres módulos que la cobertura de línea daba por buenos.**
`catalog/build.py` estaba al **0,00 % con cero tests** y la cobertura de línea de `catalog/` al
99,23 % al mismo tiempo — la tesis del proyecto ocurriéndole al proyecto—. Pasó a 80 % con
diecisiete tests que no tocan DuckDB: `introspect_duckdb` se sustituye por un espía. Lo que fijan
no es fontanería: las tres claves de contrato (`excluded_from_catalog`, `deprecated`, `reason`) son
comportamiento, y un `.get(clave, [])` ahí generaría un catálogo que publica las columnas que la
política excluye, en silencio y en verde. `statistics` e `introspect` subieron con los bordes que
nadie asertaba —el `or` que rescata un coste de cero, el `bool` colándose por ser subclase de
`int`— y `principal/policy.py` y `budgets.py` fijan por fin sus valores por defecto, que en una
política de acceso no son fontanería sino decisiones de seguridad tomadas para cuando el contrato
llega incompleto.

**Lo que movió el guard, y el hallazgo del día.** El censo de mutantes —reproducido con los propios
operadores de mutmut, no estimado— dijo dónde estaba el dinero: **las aserciones del corpus eran de
EXISTENCIA, no de VALOR.** `test_rule_cases.py` comprobaba `position is not UNKNOWN` y `subject`
truthy, así que un mutante que devolviera `Position.WHERE` donde la verdad era `GROUP_BY` sobrevivía
intacto, y `retryable` no se asertaba en ningún sitio. Cuatro columnas nuevas en el YAML —`posicion`,
`sujeto`, `reintentable`, `alternativa`— y veinticinco líneas en el runner mataron 98 mutantes sin
escribir un solo test nuevo. Es exactamente lo que Samuel dijo al recortar P-005: *«una tabla de
posición que devuelve la etiqueta equivocada es un bug, no prosa»*.

**Y al volcar los 68 valores para revisarlos uno a uno apareció un bug de verdad en R005.**

```
subject = ' x fact_order_line'
message = the join between a subquery and fact_order_line has no ON condition...
```

No hay ninguna subconsulta: la izquierda es `fact_payment_attempt`, una tabla. La causa es que
**sqlglot 30 renombró la clave del argumento de `from` a `from_`**, así que
`select.args.get("from")` devolvía `None` SIEMPRE. Dos consecuencias que ningún test veía:

1. **El mensaje mentía en su mitad izquierda**, y `G-RECOVERY` lo habría pagado en la fase 6 sin
   que nadie supiera por qué: se le dice al modelo que arregle una subconsulta que no existe.
2. **La exención de relación pequeña estaba muerta en un lado.** `_is_small(left)` nunca era cierta,
   así que un `FROM ref_fx_rate_daily JOIN fact_payment_attempt` se rechazaba pese a que
   multiplicar por catorce filas es lo que `SMALL_RELATION_PREFIXES` declara normal. Falso
   positivo, o sea la dirección segura — pero un guard que bloquea trabajo legítimo se desactiva en
   tres semanas, y eso lo dice el propio `policy.yaml`.

Arreglado buscando el `exp.From` **por clase de nodo entre los argumentos directos**, que es el
mismo principio que R010 ya declaraba en su docstring para los nodos de escritura: la clase es
estable, el nombre de la clave es un detalle de versión. Un `args.get("from_")` habría arreglado el
síntoma y dejado la trampa para la siguiente actualización.

**El segundo agujero, y era más grave que el número.** R008 tenía **22 mutantes marcados «sin
tests»**: ningún test EJECUTABA su rama `_reject_unknown`, que es el fail-closed del linaje —«si el
guard no puede seguir de dónde sale una columna, no puede afirmar que sea segura»— y sostiene la
mitad de `G-PII-LEAK`. Ahora tiene cuatro casos: CTE con `UNION`, derivada con `UNION ALL`, alias a
través de un `UNION`, e `INTERSECT`. El tercero es el que prueba que el fail-closed es de verdad:
`country_code` es `allow` para analyst, no hay nada que proteger, **y se rechaza igual**, porque la
regla no es «esta columna es peligrosa» sino «no puedo AFIRMAR que sea segura».

Los cuatro casos van al corpus por regla y **no** a `attacks/`: con `G-WRITE-BLOCK-DEV` en `== 25`,
hacer crecer el cuaderno rompe el gate. Es la tensión que P-004 resuelve y que sigue esperando tres
minutos de Samuel.

## 2026-09-02 · fase 5 · la cadena de auditoría, y un `[]` que es evidencia

**Qué se construyó.** El núcleo de la fase 5, con TDD y rojo antes que verde:
`audit/chain.py` (puro), `audit/store.py` (SQLite en WAL, append-only por trigger) y
`audit/executor.py`, el `AuditedExecutor`. **44 tests, todos unitarios, todos verdes.**

**Todo contra `:memory:`, y no por comodidad.** `pyproject.toml` deja `tests/integration` fuera de
la selección de mutmut, así que cualquier mutante de `store.py` que solo cubriera un test de
integración saldría «sin tests» y contaría como VIVO. Con `G-MUTATION` recién cruzado por seis
décimas, escribir el almacén y probarlo solo en integración lo habría vuelto a tumbar. A
integración va únicamente lo que EXIGE un fichero real: los sidecars del WAL y la reapertura.

**Tres decisiones que merecen quedar escritas.**

**1 · `AuditRecord` no lleva campo `hash`.** El contrato define el hash como el de «el registro SIN
el campo hash», así que el tipo ES el registro sin él y `link()` lo devuelve. Guardarlo dentro
obligaría a construir el objeto con un hueco para rellenarlo después, que es la clase de estado a
medias por donde se cuela un registro sin hashear.

**2 · `tables` y `columns_masked` se emiten SIEMPRE, aunque vayan vacías.** Aquí el test tenía razón
y la implementación no, y se corrigió el código. En un registro de auditoría `"columns_masked": []`
es **evidencia** —dice que no se enmascaró nada— mientras que el campo ausente es **ambigüedad**: no
distingue «no había nada que enmascarar» de «lo escribió una versión que aún no llevaba el campo».
El contrato llama a ese campo «la evidencia de que el enmascarado ocurrió, no la promesa», y una
evidencia que a veces no está no es evidencia.

**3 · `prev_hash_bytes` son los 32 bytes crudos, no los 64 caracteres del hex.** El contrato nombra
los bytes DEL hash, no los de su representación. Para la seguridad da igual —`prev_hash` viaja
además dentro del JCS— pero para la interoperabilidad no, que es justo el miedo que el contrato
declara. Va fijado con un **vector dorado**: el JSON canónico completo escrito a mano en el test.
Si ese test falla algún día, no se actualiza el número: se investiga, porque cambiarlo invalida
toda cadena escrita hasta entonces.

**Las trampas que el diseño previo evitó, y las tres eran reales.** `check_role_source.py` prohíbe
`payload["role"]` fuera de `principal/`, así que el almacén recorre una tupla de nombres de columna
en vez de indexar por literal —y de paso eso hace imposible que la lista de escribir y la de leer
divergan—. `isoformat()` produce `+00:00` y **no** casa el `pattern: "Z$"` del contrato, mientras
que `utcnow()` está deprecada y la suite corre con `filterwarnings = ["error"]`: se formatea a mano.
Y el `except` del ejecutor **escribe el registro y relanza con un `raise` desnudo**: tragarse la
excepción convertiría un fallo del motor en un éxito silencioso, con el llamante recibiendo un
resultset vacío indistinguible de «no hay filas».

**I-06 dejó de ser prosa.** «`AuditedExecutor` es el único camino a `Engine.execute()`» estaba
escrito en `RULES.md` y no lo comprobaba nada: el contrato de capas no puede expresarlo, porque
colocar `engines` abajo dejaría que `guard` o `cost` lo importaran sin romper ninguna capa. Ahora
hay un contrato `forbidden` que enumera los diez paquetes que NO pueden tocarlo. **4 contratos de
import, 0 rotos.** El día que alguien importe el motor desde el CLI para «una consulta rápida», el
paso 1 de `make done` sale rojo antes de que ese atajo exista.

**Lo que la fase 5 verifica por CONTADOR y no por lectura de código.** «Lo rechazado no llega al
motor» no se prueba mirando el flujo: se mira si el contador de proceso de `engines/base.py` se
movió. `executions() - antes == len([registros con status EXECUTED])` es una medida; «el código no
llama a execute» es una lectura.

**Y las dos metas de la fase 5 quedaron MEDIDAS Y EN VERDE.**

| Meta | Medido | Umbral |
|---|---|---|
| `G-AUDIT-COV` (axioma) | 100 % · 0 invocaciones sin registro · **4 estados auditados** | == 100 |
| `G-AUDIT-TAMPER` | 100 % · **1.299 mutaciones de byte inyectadas y detectadas** | >= 1.000 |

Las dos propiedades llevan los nombres de fichero que `GOALS.yaml` fija y que no son negociables, y
las etiquetas de sus umbrales adicionales se copiaron **letra a letra** del contrato: `goals_check`
las compara por igualdad exacta de cadena, y una tilde de más produce «falta el umbral adicional»,
que se diagnostica fatal porque el número medido es correcto.

**La propiedad de manipulación encontró un defecto de diseño, no un fallo de test.** Recorriendo el
registro byte a byte apareció **una sola fuga**: alterar `schema_version` no se detectaba. La causa
era que la versión se inyectaba desde una constante del módulo en vez de ser un campo del registro.
Y el hueco era lo de menos: **el día que la versión subiera a 2, todo registro escrito bajo la 1
habría dejado de verificar**, porque se le habría inyectado una versión que no era la suya. El
contrato mete `schema_version` en el hash precisamente para que una cadena que mezcla versiones SE
PUEDA verificar, y eso solo funciona si cada registro recuerda la suya. Ahora es un campo, con su
columna en el almacén.

**Los tres subcomandos existen y se probaron de punta a punta contra un fichero real**, no contra
`:memory:`. La secuencia completa: el trigger para la escritura normal; el atacante que borra el
trigger consigue escribir; `warden audit verify` lo caza **nombrando el `seq`** y sale con 1;
`reconcile --strict` también. Y `anchor` emite la punta con su nota: *un anclaje no impide reescribir
la cadena, impide hacerlo sin que se note.*

**Cobertura de `audit/`: 100 % de línea en los tres módulos**, `G-COV-FUNC` en 102 funciones
públicas todas con test. `make gate-fast` VERDE con 544 tests, 4 contratos de import, 0 rotos.

**Dónde queda la fase 5.** `goals_check --milestone 5` deja exactamente dos fallos, y los dos son los
correctos: `G-MUT-GUARD` en 61,87 % contra su 85, y **`G-PII-LEAK` sin medida porque la fase 4 no
existe todavía** — una meta bloqueante sin medida es un fallo, nunca un aprobado. Lo propio de la
fase 5 está medido y en verde.

**Lo que queda:** el refactor de P-005 para `G-MUT-GUARD`, y la fase 4 entera.

## 2026-09-02 · fase 4 · el anillo 4 completo, y `ORDER BY n` era un oráculo

**El hallazgo de seguridad, y sale de diseñar la fase 4 en vez de escribirla a ciegas.**
Antes de tocar `mask/` se midió el hueco: **las 17 filas `mask` de la política pasaban el guard y
salían EN CLARO**. Al recorrerlas apareció algo peor, que no era de la fase 4 sino del guard ya
publicado:

```sql
SELECT first_name AS n FROM dim_customer ORDER BY n LIMIT 5   -- se ACEPTABA
SELECT first_name AS n FROM dim_customer ORDER BY 1 LIMIT 5   -- también
```

`qualify()` expande el alias de salida en `GROUP BY` y en `HAVING` —por eso esos dos siempre se
rechazaron— **pero no en `ORDER BY`**, donde deja una columna sin tabla; y de paso convierte el
ordinal en el alias, así que las dos formas acababan en el mismo sitio. `Scope.columns` de sqlglot
excluye deliberadamente esas referencias, el linaje nunca las indexaba, R008 preguntaba por `.n`, no
encontraba fila en la matriz y aplicaba el `allow` por defecto.

**No era un rechazo perdido cualquiera.** `ORDER BY n LIMIT 1` con predicados sobre columnas
permitidas es una **búsqueda binaria sobre un valor enmascarado**: se ordena, se mira el extremo, se
acota, se repite. La política firmada lo prohíbe con esas palabras —una columna «alcanzada a través
de un alias»— y el guard no lo estaba cumpliendo.

Se cerró **la clase, no el caso**: toda columna que el bucle principal del linaje no indexe se
resuelve por alias de salida o se marca `UNKNOWN`. Arreglar solo el `ORDER BY` habría dejado la
puerta abierta para la siguiente cláusula que sqlglot decida no expandir. Con tres casos de
regresión y **dos casos `accept`**, que son la otra mitad: una columna permitida sigue pudiendo
ordenarse por su alias y por su ordinal, para que cerrar la fuga no degenere en rechazar todo alias
sin tabla.

**El anillo 4, y el problema era más pequeño de lo que parecía gracias al guard.** Toda proyección
que sobrevive llevando una columna enmascarada es un `exp.Column` desnudo: `concat(...)`,
`upper(substring(...))` y `CASE WHEN ... THEN first_name` son argumento de función y R008 los
rechaza; `a || b` lo rechaza R002 porque `DPipe` no está en la allowlist. Así que reescribir es
«sustituye la expresión de la proyección y conserva el alias», y encontrarse otra forma es un fallo
del anillo anterior: se rechaza con `INTERNAL`, no se improvisa.

**Las cuatro transformaciones, verificadas contra DuckDB y no solo en el árbol**, y las cuatro
preservan NULL:

```
tachar        -> [(None,), ('***',), ('***',)]
generalizar   -> [(None,), ('25-34',), ('55-64',)]
ultimos_n     -> [('***1691',), ('***9061',)]
hash_estable  -> [('f7264610f2eb',), ('456c46d5bccc',)]
admin         -> [(None,), ('Ermenegildo',)]      <- sin máscara
```

El NULL lo exigen dos contratos a la vez: `policy.yaml` dice que el NULL de `last_name_2` significa
«este sistema de nombres no tiene segundo apellido» —es un DATO— y `resultset-equality.md` decide
que NULL y cadena vacía son DISTINTOS. Una máscara que convirtiera NULL en `'***'` **inventaría** un
valor, y las respuestas de referencia del banco de 60 saldrían falsas sin que nadie lo notara.

**Y `G-PII-LEAK` quedó MEDIDO: 0 fugas en 177 comprobaciones sobre 3 superficies.** No se comprueba
que el código diga que enmascara: **se ejecuta la consulta dos veces contra el dataset real** —como
`admin`, que lo ve todo, y como el rol restringido— y se exige que ningún valor real aparezca en la
salida del segundo. Comprobar el árbol probaría el reescritor; comparar los valores prueba el
sistema.

## 2026-09-02 · buzón · P-006, P-007 y P-008, y las tres traían un añadido

Samuel respondió las tres propuestas abiertas. Las tres aprobadas, y **las tres con algo que la
propuesta no pedía**, que es el patrón que este mecanismo produce cuando funciona.

**P-006 · la pérdida se escribe como pérdida.** `PLAN.md` decía «sal por sesión» y `policy.yaml`
firmado dice `pimienta_desde: config`. Corregido el plan bajo autorización expresa. El añadido:
*«conviene escribirlo como cambio y no como corrección de una errata»*, porque la sal por sesión
impedía correlacionar dos sesiones y la pimienta fija no lo impide. Se pierde a sabiendas, a cambio
de la única métrica sobre la que `G-EXEC-ACC` puede medirse. Publicado en las limitaciones
declaradas del README.

**P-007 · el centinela sube al contrato, y con un hallazgo.** Los 64 ceros de `sql_digest` significan
«no hubo árbol validado» y ahora lo dice la `description` del schema, porque **el schema es lo que
lee quien valida registros sin abrir el código** y para esa persona 64 ceros eran un sha válido y
silencioso. El añadido de Samuel: `GENESIS` y `NO_VALIDATED_TREE` son **el mismo literal con dos
significados**. No colisionan porque son campos distintos, pero un registro que sea a la vez el
primero de la cadena y un rechazo pre-parseo llevará los dos. Anotado en las dos constantes y en
las dos `description`: se leen por campo, nunca por valor.

**P-008 · la pimienta sale del SQL, y se dice hasta dónde llega el remedio.** El árbol emite ya
`warden_hash(...)` y la clave vive en un `CREATE TEMP MACRO` instalado al abrir la conexión. TEMP
porque la conexión es de SOLO LECTURA y tiene que seguir siéndolo. Verificado: **hashes idénticos**
a los de la versión inline.

| Dónde | Antes | Ahora |
|---|---|---|
| Campo `sql` del registro de auditoría | la pimienta, en cada registro | **no aparece** |
| Logs del motor | una vez por CONSULTA | **una vez por CONEXIÓN** |

El añadido, y era el importante: *«la macro NO elimina la pimienta de los logs del motor, la
reduce, y eso se escribe con esas palabras. Un límite que se declara a medias es peor que uno que
no se declara, porque parece cerrado.»* Y la vía durable que la propuesta no había considerado:
esas dos columnas no necesitan un hash con clave sino un **pseudónimo estable**, el mismo patrón
que `card_sk` frente a `card_token`. Convierte un problema de gestión de claves en uno de modelado
de datos. Va cuando se regenere el dataset.

**Un detalle de diseño que no se relajó.** `warden_hash` es para sqlglot un `exp.Anonymous`, el nodo
comodín, y el invariante del proyecto dice que `exp.Anonymous` es RECHAZO. Admitirlo a secas en el
conjunto de nodos del enmascarador lo habría convertido en «cualquier función». Así que la
comprobación dejó de ser un conjunto plano: admite `Anonymous` **solo con nuestro nombre**.

**Dónde queda el proyecto.** `goals_check` en las fases 4 y 5 deja **un solo fallo, y es el mismo en
las dos**: `G-MUT-GUARD` 61,87 % contra su suelo de 85. Todo lo demás en verde, incluidos los tres
axiomas `G-PII-LEAK`, `G-AUDIT-COV` y `G-BUDGET-ESCAPE`. Lo que impide cerrar las fases 3, 4 y 5 es
exactamente un número, y es trabajo de tests: no queda ninguna decisión pendiente de Samuel salvo
los tres minutos de P-004.
