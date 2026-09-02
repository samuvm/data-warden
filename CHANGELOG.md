# Changelog · Data Warden

Formato: [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Una entrada por
**fase cerrada**, escrita al pasar `make done MILESTONE=N` y no antes. El punto 11 de
la Definition of Done la exige: una fase cerrada sin entrada es una fase que nadie
puede leer después.

Los números que aparecen aquí están **medidos**, con su comando al lado, y viven en
`evals/reports/`. Un número sin comando que lo reproduzca no es un número.

## [0.5.0] · fase 5 · 2026-09-02 · **cerrada**

La cadena de auditoría. `audit/chain.py` (pura), `audit/store.py` (SQLite en WAL,
append-only por TRIGGER) y `audit/executor.py`, el `AuditedExecutor`: **el único
camino a `Engine.execute()`**.

### Añadido

- **Se audita TODA invocación, no solo la que ejecuta.** Los cuatro estados del
  contrato dejan registro, y el que más importa es el que no ejecuta nada: un rechazo
  del guard es un evento de seguridad, y quien sondea la política sistemáticamente es
  precisamente el que no dejaría ni una línea si solo se registraran los éxitos.
- **El append-only lo impone el motor, no el código.** Un trigger viaja con los datos:
  quien quiera reescribir un registro tiene que borrarlo antes, y eso es un acto
  deliberado. Si viviera en Python, el primer script que abriera el fichero con
  `sqlite3` se lo saltaría sin enterarse.
- **`warden audit verify | reconcile | anchor`**, probados contra un fichero real: el
  trigger para la escritura normal, quien lo borra consigue escribir, y `verify` lo
  caza **nombrando el `seq`**.
- **I-06 dejó de ser prosa.** Un contrato de import-linter prohíbe `datawarden.engines`
  a los diez paquetes que no son `audit`. El contrato de capas no podía expresarlo.

### Corregido

- **`schema_version` se guarda, no se inyecta.** Lo encontró la propiedad de
  manipulación recorriendo el registro byte a byte: era el único hueco, y el problema
  de fondo era peor que el hueco — **el día que la versión subiera a 2, todo registro
  escrito bajo la 1 habría dejado de verificar**, porque se le habría inyectado una
  versión que no era la suya.
- **`tables` y `columns_masked` se emiten siempre, aunque vayan vacías.** `[]` es
  evidencia de que no se enmascaró nada; el campo ausente es ambigüedad.

### Medido

| Meta | Umbral | Medido | Comando |
|---|---|---|---|
| `G-AUDIT-COV` *(axioma)* | == 100 %, 4 estados | **100 % · 4** | `pytest tests/property/test_audit_coverage.py` |
| `G-AUDIT-TAMPER` | >= 1.000 mutaciones de byte | **1.299, todas detectadas** | `pytest tests/property/test_audit_chain.py` |

### Límite declarado

**Quien tiene escritura sobre el almacén puede recalcular la cadena entera.** El hash
encadenado detecta a quien NO puede reescribir todo lo posterior; por eso existe
`warden audit anchor`. Está en `docs/threat-model.md §4.1` con esas palabras.

## [0.4.0] · fase 4 · 2026-09-02 · **cerrada**

El enmascarado por rol, **reescribiendo el AST antes de ejecutar** y nunca
post-procesando el DataFrame. El valor real no sale de la base de datos.

### Añadido

- **Las cuatro transformaciones del contrato firmado**, verificadas contra DuckDB y no
  solo en el árbol: `tachar` da `'***'`, `generalizar` da la columna que declara la
  política, `ultimos_n` conserva los `n` de la fila, y `hash_estable` da 12 hex.
- **Las cuatro preservan NULL.** Lo exigen dos contratos: `policy.yaml` dice que el
  NULL de `last_name_2` significa «este sistema de nombres no tiene segundo apellido»
  —es un DATO— y `resultset-equality.md` decide que NULL y cadena vacía son distintos.
  Una máscara que inventara un valor daría respuestas de referencia falsas en la fase 8.
- **`mask/pipeline.py`**, la costura de los cuatro anillos. Sin ella `mask/` era código
  muerto: el contrato de capas impide que `cost/screen.py` importe el enmascarador.

### Corregido · dos fugas reales

- **`SELECT first_name AS n FROM dim_customer ORDER BY n` se ACEPTABA.** `qualify()`
  expande el alias de salida en `GROUP BY` y `HAVING` pero **no en `ORDER BY`**, y
  `Scope.columns` de sqlglot excluye esas referencias. Era una **búsqueda binaria sobre
  un valor enmascarado**: se ordena, se mira el extremo, se acota. Cerrada la CLASE, no
  el caso.
- **`HAVING count(*) <= N` devolvía los grupos de UNA persona.** La comprobación era
  «rechaza si el literal está bajo `K_MIN`», correcta para una igualdad y falsa para un
  techo: `<= 1000` devolvía todos los grupos pequeños del almacén. Acotar por arriba el
  tamaño de grupo *es* la consulta de k-anonimato al revés.

### Medido

| Meta | Umbral | Medido | Comando |
|---|---|---|---|
| `G-PII-LEAK` *(axioma)* | == 0 fugas, 3 superficies | **0 en 177 comprobaciones · 3** | `make pii-suite` |

Se mide **ejecutando contra el dataset dos veces** —como `admin`, que lo ve todo, y
como el rol restringido— y exigiendo que ningún valor real aparezca en la salida del
segundo. Comprobar el árbol probaría el reescritor; comparar los valores prueba el
sistema.

### Límite declarado

**La pimienta del hash llega al motor.** Vive en un `CREATE TEMP MACRO` que se instala
al abrir la conexión, así que **no** aparece en el registro de auditoría y **sí** en los
logs del motor, una vez por conexión en vez de una por consulta. Es una reducción, no
una eliminación (`threat-model.md §4.2.1`). Y el enmascarado es determinista con
pimienta fija: se pierde la resistencia a correlacionar dos sesiones, a cambio de que
los resultsets sean comparables entre ejecuciones — sin eso, ninguna pregunta del banco
de 60 que toque una columna enmascarada podría medirse.

## [0.3.0] · fase 3 · 2026-09-02 · **cerrada**

El anillo de coste. Estuvo semanas en `-rc` con `make done MILESTONE=3` en **ROJO** por
mutación, y esa entrada se mantuvo mientras tanto: una fase que no cierra también deja
evidencia. Cierra hoy, cuando la mutación cruza sus dos suelos:
**`G-MUT-GUARD` 85,46 %** (desde 50,73) y **`G-MUTATION` 71,60 %** (desde 66,62).

### Añadido

- **`catalog/statistics.py`**: bytes por columna y por partición, leídos de los
  MANIFIESTOS de Iceberg. 24 tablas y 66,6 M de filas contadas en **0,5 s sin leer una
  sola fila** — que es lo que hace posible un estimador preventivo.
- **`cost/estimator.py`**: poda por proyección y por partición, desde el predicado del
  árbol validado. **Regla que gobierna el módulo: ante la duda, se cobra de más.**
- **`cost/budget.py`**: `soft` avisa, `hard` no ejecuta, `max_rows` recorta en el
  dominio (I-12). El rechazo mira el DETALLE del estimador para sugerir qué acotar.
- **`cost/screen.py`**: los dos anillos encadenados. Valida y solo después cuesta.
- **`engines/base.py`** con el CONTADOR de proceso que hace comprobable
  `G-BUDGET-ESCAPE`, y **`engines/duckdb_engine.py`**.
- **`evals/suites/cost-calibration.yaml`**: 60 consultas contra el dataset completo.

### Corregido — el más grave de todo el proyecto hasta ahora

- **El estimador cobraba CERO por una tabla de 4,1 GB.** Las claves de partición se
  guardaban como `Record[19967]` en vez de `2024-09-01`, así que ningún literal de
  fecha casaba, la poda salía vacía y el coste era cero. `G-BUDGET-ESCAPE` es un
  axioma y habría dejado pasar **cualquier consulta con un predicado de fecha**.
  Lo encontró `G-COST-CALIB`. Arreglado por dos vías: la clave se genera como fecha
  ISO, **y una intersección vacía ya no se toma por buena**.
- **R006 solo miraba la raíz**: `SELECT * FROM (SELECT … OFFSET 9000000) s` escondía
  el desplazamiento. Lo encontró la mutación de AST.
- **Los rechazos que no vienen de una regla no nombraban su objeto.** Ahora el parseo,
  la cualificación, el timeout y el fallo interno dicen todos de qué hablan.

### Medido

| Meta | Umbral | Medido |
|---|---|---|
| `G-BUDGET-ESCAPE` | 0 escapes · ≤ 200 ms sobre 3 GB | **0 · 1,2 ms** sobre 4,1 GB |
| `G-COST-CALIB` | p95 ≤ 1,5 · 0 casos > 3 | **1,077 · 0**, n = 60 |
| `G-MUT-GUARD` | ≥ 85 % | **50,73 %** ❌ |
| `G-MUTATION` | ≥ 70 % | **66,62 %** ❌ |

### Por qué no cierra

La mutación. Y el motivo está medido, no supuesto: **sobre 80 supervivientes de
`guard/rules`, el 55 % son mutaciones que solo cambian el texto de un mensaje.**
Subió de 38,61 % a 50,73 % tapando huecos reales —el corpus no asertaba `position` ni
`subject`; R013 estaba «probada» por casos que paraban otro mecanismo; `screen()` no
tenía ni un test unitario— y ahí se agotó lo que la meta puede enseñar sobre este
código. Propuesta **P-005**, que **no pide bajar ningún umbral**.

## [0.2.0] · fase 2 · 2026-09-02

El guard. **El corazón del proyecto**, y la fase donde vive su tesis: *el valor no
está en la tasa de acierto, está en la garantía sobre el fallo.*

### Añadido

- **Catorce reglas**, R001 a R014, una por fichero, con su protocolo congelado
  ANTES de la primera. Dos fases: las que deciden qué clase de cosa es el árbol
  corren antes de `qualify()`; las que razonan sobre columnas, después.
- **`guard/query_lineage.py`**: resuelve de qué columna base sale cada columna DE
  ESTA CONSULTA, bajando por el árbol de ámbitos. Es lo que cierra el alias que
  oculta: `WITH c AS (SELECT birth_date AS b …) … WHERE b > '1990-01-01'`.
- **`guard/position.py`**: dónde está una columna en el árbol. Es la mitad de la
  tesis: `mask` no es una escala de confianza, es una escala de POSICIÓN.
- **119 casos de corpus** en catorce YAML, cada rechazo asertando el `rule_id`
  exacto que disparó, no solo que hubo rechazo.
- **`attacks/dev-notebook.yaml`**: 25 evasiones. **Higiene, no evidencia**, y el
  script lo repite cada vez que corre.
- **`evalsupport/mutate.py`** y `make attack-mut`: nueve mutaciones de AST sobre
  corpus semilla, 3.497 mutantes, cero evasiones.
- **`make bench-guard`**, **`make guard-property`**, **`make attack-holdout`** y seis
  checks de arquitectura nuevos.

### Corregido — todo lo encontró una máquina, no una revisión

- **SIGSEGV en `sqlglot[c]`.** `x UNION SELECT 1` mataba el proceso al cualificar.
  Un segfault no lo atrapa ningún `except`: el guard no rechazaba, moría. Cerrado en
  R001 antes de `qualify()`. Lo encontró la propiedad de fail-closed.
- **El alias que oculta**, y **la vista que reexpone**: los dos, linaje.
- **`OFFSET` escondido en una subconsulta.** R006 solo miraba la raíz, así que
  `SELECT * FROM (SELECT … OFFSET 9000000) s` hacía que el motor produjera y tirara
  nueve millones de filas. Lo encontró la mutación de AST y es caso permanente.
- **La exclusión C-3 se saltaba por la vista.** Lo encontró el subagente
  `qa-adversario` escribiendo la reserva, que es exactamente para lo que existe.
- **`Any` y `All` fuera de la allowlist**; **`Var` dentro**, como decisión y con caso.

### Medido

| Meta | Umbral | Medido |
|---|---|---|
| `G-WRITE-BLOCK` · holdout | 15 casos | **15/15 · Wilson 95 % [0,80 - 1,00]** |
| `G-WRITE-BLOCK` · mutación | >= 2.000 mutantes | **3.497 · cero evasiones** |
| `G-WRITE-BLOCK-DEV` | 25 | **25/25 por la regla CORRECTA** |
| `G-FAILCLOSED` | >= 5.000 entradas | **20.000 · cero excepciones** |
| `G-GUARD-P95` | p95 <= 25 ms | **0,81 ms** · p99 1,1 · máx 3,7 |
| `G-NO-RAW-SQL` | == 0 | **0** |
| `G-COV-LINE` guard | >= 95 % | **96,5 %** |

**El número del holdout se publica como intervalo y etiquetado como AUTOEVALUADO**,
porque D-09 no está instalado y el aislamiento de `tests/holdout/` es hoy disciplina
y no ejecución. Nunca «100 % de bloqueo» a secas.

### Pendiente de Samuel

- **P-004**: `G-WRITE-BLOCK-DEV` tiene umbral `== 25` y la regla de oro del repo dice
  que el cuaderno crece con cada evasión. Un `==` convierte encontrar una evasión en
  un fallo de gate. Se propone `>= 25`, sin bajar el número.

## [0.1.0] · fase 0 · 2026-09-02

Cimientos, dataset, catálogo generado, contratos firmados y tipos congelados.
Cierra la **fase 0** y la **fase 1**, que el plan declara paralelas.
**Cero IA en todo lo que hay aquí dentro**, que es lo que la fase 0 exige.

### Añadido

- **Catálogo generado** (`src/datawarden/catalog/`, meta `G-CATALOG-FRESH`).
  32 relaciones y 428 columnas introspectadas desde el esquema vivo de DuckDB.
  Nunca escrito a mano: `scripts/check_catalog_fresh.py` lo regenera y lo compara
  byte a byte, y falla si difiere.
- **Linaje de columnas** (`catalog/lineage.py`). Resuelve con sqlglot de qué
  columna base sale cada columna de cada vista. Cierra el agujero que el propio
  catálogo abría: `v_customer.full_name` es `concat_ws(' ', first_name,
  last_name_1, last_name_2)` sobre columnas protegidas —el ataque por expresión
  derivada que describe `PROJECT.md`— y una política que casa por `tabla.columna`
  no lo veía. 424 de 428 columnas resueltas; las 4 restantes salen de una CTE
  recursiva, se declaran, y se les atribuye el cierre de dependencias en vez de un
  `deny` a ciegas.
- **`docs/spec/resultset-equality.md`** completo y su implementación
  (`evalsupport/resultset_equality.py`), 59 casos en verde y 100 % de cobertura.
- **`domain/types.py` congelado**: `Principal`, `RejectionReason`, `ValidatedQuery`,
  `CostEstimate`, `ResultSet`, `Role`, `RoleSource`, `Severity`, `Position`.
- **`principal/policy.py` y `principal/budgets.py`**: la matriz de acceso y los
  presupuestos, indexados desde los contratos firmados.
- **Contratos nuevos en `docs/spec/`**: `audit-record.schema.json`,
  `rejection.schema.json`, `catalog.schema.json`, `catalog-overlay.yaml` y
  `budgets.yaml`.
- **`scripts/`**: `compile_contracts.py`, `check_contracts.py`,
  `check_catalog_fresh.py`, `check_function_coverage.py`, `check_line_coverage.py`,
  `check_resultset_eq.py`, `check_secrets.py`, `goals_check.py`, `measure_traps.py`
  y `done.py`, que implementa las doce condiciones de la Definition of Done.
- **Tres consultas SQL escritas a mano** (`tests/integration/queries/`) que además
  comprueban los números del glosario: tasa de aprobación 86,2 % (declarado 86-87 %)
  y MDR efectivo 2,183 % (declarado 2,1-2,4 %).

### Firmado

- **`docs/spec/policy.yaml`**: FIRMADO con los siete cambios de la respuesta a Q-003
  (C-1 a C-7), aplicados uno a uno y anotados en la fila que tocan.
- **`docs/spec/glossary.yaml`**: FIRMADO con las cinco correcciones de Q-004
  (G-1 a G-5).
- **`docs/spec/budgets.yaml`**: nace del cambio C-7, que saca los presupuestos de la
  matriz firmada porque tienen ritmos de cambio distintos.

### Corregido

- **`ValidatedQuery.sql()` emite con `comments=False`.** sqlglot CONSERVA los
  comentarios en el árbol y los vuelve a emitir, así que
  `SELECT /*+ hint */ a FROM t` re-serializado seguía llevando texto del atacante
  hasta el motor, y hay motores que leen hints ahí. Lo encontró un test, no una
  revisión.
- **`compare()` no revienta con `Decimal('sNaN')`** —que lanza al compararse— ni da
  por iguales dos importes distintos fuera del rango de `float`, que ambos
  convierten a `inf`. Los dos los encontraron tests.
- **`dim_merchant.traffic_weight` declara su excepción a «admin lo ve todo»**. Era
  la segunda, no estaba marcada, y la encontró el test que Samuel pidió en C-2
  exactamente para eso.

### Medido

| Meta | Umbral | Medido |
|---|---|---|
| `G-CATALOG-FRESH` | == 0 divergencias | **0** |
| `G-CONTRACTS-FROZEN` | == 0, 4 contratos propios | **0 · 4** |
| `G-RESULTSET-EQ` | >= 12 casos, 0 en rojo | **59 · 0** |
| `G-COV-FUNC` | == 0 funciones sin test | **0 de 41** |
| `G-COV-LINE` | >= 90 % / >= 95 % críticos | **99,23 % / 100 %** |
| `G-SECRETS` | == 0 hallazgos nuevos | **0** |

### Pendiente de Samuel

- **P-002**: declarar `PyYAML` como dependencia de desarrollo. Hoy entra por vía
  transitiva. `src/` no la importa en ninguna parte.
- **P-003**: dos de las nueve trampas del glosario no reproducen su número medido.
  El SCD tipo 2 infla un 31,9 % y no un 53 %; los clientes que nunca compraron son
  el 6,2 % y no el 4,3 %, y ahí el error está en el generador, que publica el
  objetivo con nombre de medida. Las otras siete reproducen, cuatro de ellas con
  tres decimales.
