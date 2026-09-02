<h1 align="center">Data Warden</h1>

<p align="center">
  <strong>Un agente que traduce lenguaje natural a SQL sobre un lakehouse — y cinco anillos de
  control que se aseguran de que lo que salga de ahí no pueda hacer daño.</strong>
</p>

<p align="center">
  <img alt="estado" src="https://img.shields.io/badge/estado-en%20construcci%C3%B3n-orange">
  <img alt="fases" src="https://img.shields.io/badge/fases-0%C2%B71%C2%B72%20cerradas-brightgreen">
  <img alt="python" src="https://img.shields.io/badge/python-3.12-blue">
  <img alt="licencia" src="https://img.shields.io/badge/licencia-Apache--2.0-lightgrey">
  <img alt="tests" src="https://img.shields.io/badge/tests-427-informational">
</p>

---

**La tesis.** Traducir lenguaje natural a SQL es la parte fácil, y hace tiempo que está
resuelta. Lo que impide que esto llegue a producción en una empresa que tiene datos de
verdad es todo lo que va después: quién puede ver qué columna, qué pasa si el modelo
escribe un `DELETE`, cuánto cuesta la consulta antes de lanzarla, y quién responde
cuando alguien pregunta qué se ejecutó el martes a las tres.

Data Warden es el «después». El modelo propone; **el sistema decide**, y decide con
reglas que no viven en el prompt.

```
    lenguaje natural
          │
          ▼
    ①  CONTEXTO      el catálogo y el glosario se sirven como recurso MCP
          │          el modelo no adivina la estructura: la lee
          ▼
    ②  GENERACIÓN    NL → SQL  ·  la única parte donde hay un LLM
          │
          ▼
    ③  VALIDACIÓN    sqlglot → AST → guard de ALLOWLIST · 14 reglas
          │          nodo o función desconocidos ⇒ rechazo
          ▼
    ④  COSTE         se estima desde los manifiestos de Iceberg, sin leer datos
          │          si supera el presupuesto, no se ejecuta
          ▼
    ⑤  SALIDA        enmascarado por rol reescribiendo el AST · límite de filas
          │          auditoría append-only con hash encadenado
          ▼
    resultado, o un rechazo que explica cómo acotar
```

Y el punto que lo sostiene todo: **da igual qué cliente se conecte o qué modelo use,
no puede saltarse las reglas, porque no viven en su lado.**

---

> ### ⚠️ Estado: en construcción
>
> **Fases 0, 1 y 2 cerradas en verde. La 3, construida y medida, sin cerrar.** Los
> anillos 1, 3 y 4 funcionan; el 2 y el 5 todavía no existen.
>
> Cada número de este documento lleva **el comando que lo reproduce y el artefacto del
> que sale**, y los que faltan se dicen también. **Todo lo publicado aquí es
> AUTOEVALUADO**: el gobierno que aislaría la reserva de quien escribió las reglas
> todavía no está instalado, así que ese aislamiento es hoy disciplina y no ejecución.

## Empezar

Requiere **Python 3.12** y [`uv`](https://docs.astral.sh/uv/). No hay servidor que
levantar, ni puerto, ni credenciales: DuckDB es embebido y los datos son ficheros
Parquet.

```bash
git clone https://github.com/samuvm/data-warden.git
cd data-warden
uv sync                      # instala con versiones exactas desde uv.lock

make gate-fast               # lint · typecheck · unit · property · contratos  (~40 s)
make test                    # la suite completa
make help                    # todos los comandos, con lo que mide cada uno
```

El dataset **no se versiona** — son 7,5 GB. Se genera, y sale idéntico byte a byte:

```bash
./datagen/run.sh dev         # 15 s  ·   3,1 M filas ·  91 MB   ← suficiente para todo
./datagen/run.sh full        # 8 min · 294,8 M filas · 7,46 GB
```

Y para mirar los datos con las manos:

```bash
duckdb datagen/out/dev/cierzo-dev.duckdb
```

## Los números, medidos

Sobre **MacBook Pro M4 Max · macOS 26.5 · arm64**. Los artefactos, en `evals/reports/`.

Cada fila sale del JSON que deja su comando, y **se reescribe cuando se vuelve a
medir**: la latencia, por ejemplo, oscila entre 0,80 y 0,89 ms de p95 entre ejecuciones.
Publicar el mejor número de todas las pasadas sería elegir la muestra después de verla.

| Qué se mide | Umbral | Medido | Comando |
|---|---|---|---|
| Bloqueo de evasiones · **reserva** | 15/15 | **15/15** · Wilson 95 % [0,80 – 1,00] | `make attack-holdout` |
| Bloqueo de evasiones · **mutación de AST** | ≥ 2.000 mutantes | **3.497** · cero evasiones | `make attack-mut` |
| Fail-closed del guard | ≥ 5.000 entradas, 0 excepciones | **20.000 · 0** | `make guard-property` |
| Latencia del guard | p95 ≤ 25 ms | **p95 0,89 ms** · p99 1,4 · máx 4,4 | `make bench-guard` |
| Consultas caras que llegan al motor | 0 | **0** · rechazo de 4,1 GB en **1,2 ms** | `make budget-invariant` |
| Calibración del estimador | p95(real/est.) ≤ 1,5 | **1,077** · 0 casos > 3, n = 60 | `make cost-calibration` |
| Cobertura de línea · global / `guard/` | ≥ 90 % / ≥ 95 % | **98,0 % / 96,5 %** | `make coverage` |
| Funciones públicas sin un solo test | 0 | **0 de 88** | `make coverage` |
| Secretos nuevos | 0 | **0** | `make secrets` |
| Mutación · `guard/rules` | ≥ 85 % | **50,73 %** 🔴 | `make mutation` |
| Mutación · resto | ≥ 70 % | **66,62 %** 🔴 | `make mutation` |

### Qué significa «15/15» y qué no

Los quince casos de la reserva los escribió **un subagente que no vio
`src/datawarden/guard/`** ni el cuaderno de ataque de desarrollo: escribió contra la
especificación firmada. Trece esperan rechazo y **dos esperan aceptación**, que
importan igual — un guardián que rechazara todo pasaría un holdout de solo rechazos, y
eso no mediría nada.

Con n = 15, el intervalo de Wilson al 95 % es **[0,80 – 1,00]**, y **eso es lo que se
publica, no «100 % de bloqueo»**. El punto solo, con quince casos, es un número sin
significado. Subir a 30 estrecharía el límite inferior a ~0,88.

Y el cuaderno de desarrollo, que sale 25/25, **no se publica como número de
seguridad**: las reglas se escribieron para parar esos casos, así que pasarlo al 100 %
es medir sobre el conjunto de entrenamiento.

### Las dos que están en rojo, y por qué siguen en rojo

La **mutación** está por debajo de su umbral, y la fase 3 no cierra por eso. El motivo
está medido: sobre 80 mutantes supervivientes de `guard/rules`, **el 55 % son
mutaciones que solo cambian el texto de un mensaje de rechazo**, y casi todo el resto
solo altera cómo se compone ese texto. Matarlos exigiría asertar los mensajes palabra
por palabra, que es exactamente la fragilidad que este proyecto prohíbe en otro sitio.

La propuesta **P-005** pide sacar los textos del alcance de la medida **sin bajar
ningún umbral**. Hasta que se decida, el número se publica como está — bajar el umbral
para verlo verde es, según la constitución del proyecto, un fallo de gate por sí mismo.

Lo que la mutación **sí** encontró, y ya está arreglado: que el corpus del guard no
asertaba `position` ni `subject` de los rechazos, y que R013 estaba «probada» por casos
que en realidad paraban otro mecanismo. Subió del 38,61 % al 50,73 % tapando huecos
reales.

## El guard

El corazón. **Es una allowlist, no una denylist**: un nodo o una función que no estén
declarados se rechazan, y esa asimetría es el diseño entero. Se valida el **AST**, no
se buscan cadenas de texto, y lo que se ejecuta es `ast.sql(dialect=...)` del árbol ya
validado — **nunca la cadena que entró**.

| Regla | Qué para |
|---|---|
| `R001` | Una sola sentencia, y de lectura |
| `R002` | Allowlist de nodos — lo desconocido se rechaza |
| `R003` | Allowlist de funciones · `exp.Anonymous` ⇒ rechazo |
| `R004` | Solo tablas del catálogo generado · nada de funciones de tabla |
| `R005` | Todo `JOIN` lleva condición — nada de productos cartesianos |
| `R006` | `LIMIT` obligatorio, y se inyecta si falta |
| `R007` | Profundidad máxima de anidamiento |
| `R008` | **Rol × columna × posición.** Aquí vive la tesis |
| `R009` | `SELECT *` se expande contra el catálogo o se rechaza |
| `R010` | Ninguna escritura, en ninguna parte del árbol |
| `R011` | Las ramas de un `UNION` son consultas, y no son muchas |
| `R012` | Agregación de grupo único — el ataque que casi nadie contempla |
| `R013` | Tamaño acotado del árbol |
| `R014` | Nada de esquemas de sistema |

**`R008` es la que distingue este proyecto de un linter de SQL.** Una columna no es
«visible» o «invisible»: tiene un nivel por rol *y por posición en la consulta*. `iban`
puede aparecer enmascarada en la proyección y estar **prohibida en un `WHERE`** — porque
`WHERE iban LIKE 'ES91 2100 0418 45%'` extrae el dato aunque la columna nunca se
imprima. El linaje se resuelve dentro de la propia consulta, así que un alias, un `CTE`
o una vista que la renombre dos veces no la esconden.

Cada regla es **un fichero + un corpus** en `tests/unit/guard/cases/RNNN.yaml`, con un
mínimo de tres casos de aceptación y tres de rechazo. Hoy son **125 casos**: 57 que
deben pasar y 68 que deben caer, y cada rechazo asierta el `rule_id` exacto, la
posición, el sujeto y que la sugerencia proponga una alternativa real.

<details>
<summary><strong>Tres cosas que encontraron los tests, y no la revisión</strong></summary>

<br>

**Un `SIGSEGV` que ningún `except` podía atrapar.** `x UNION SELECT 1` mataba el
proceso dentro de `qualify()`, en el `sqlglot` compilado con mypyc. Una caída nativa no
es una excepción: no hay `try` que valga. Se cierra en R001, **antes** de cualificar, y
los casos viven en el corpus para siempre.

**Un `OFFSET` escondido en una subconsulta.** Lo encontró la mutación de AST: R006 solo
miraba la raíz del árbol. Ahora recorre todos los `Select` y `Union`.

**Una vista que se saltaba la exclusión del catálogo.** Lo encontró un subagente
adversario: una columna excluida por política reaparecía publicada a través de
`v_merchant_current`. La exclusión ahora se propaga por el linaje, no por el nombre.

</details>

## El coste

El anillo 4 estima **antes** de ejecutar, y lo hace desde los manifiestos de Iceberg,
que llevan `column_sizes` por fichero y el valor de partición de cada uno **sin leer una
sola fila**: 24 tablas y 294,8 M de filas contadas en medio segundo.

Esto no es un detalle de implementación, es la única opción: `EXPLAIN ANALYZE` **ejecuta
la consulta**, y Athena solo reporta bytes cuando ya te ha cobrado. Los dos son inútiles
para un guardián preventivo. El manifiesto sirve para los dos motores.

<details>
<summary><strong>El fallo más grave del proyecto hasta ahora</strong></summary>

<br>

El estimador cobraba **cero bytes** por una tabla de 4,1 GB.

`_partition_value` usaba el `repr` del `Record` de pyiceberg, así que las claves de
partición se guardaban como `Record[19967]` en vez de `2024-09-01`. Ningún literal de un
`WHERE event_date = DATE '...'` casaba jamás, la poda devolvía el conjunto **vacío**, y
el presupuesto habría dejado pasar **cualquier consulta con un predicado de fecha**.
Subestimar a cero es la peor dirección posible.

Lo encontró la calibración, no una revisión de código: el p95 se disparó a 50 y el
detalle decía `partitions_kept: 0`. Se arreglaron **dos cosas, y la segunda importa
más**: la clave se genera como fecha ISO, **y una intersección vacía dejó de tomarse por
buena**. Eso es lo que hace que el siguiente fallo de formato sobreestime en vez de
subestimar.

</details>

## El dataset · CIERZO

Una pasarela de pagos europea que no existe: **12.400 comercios, 9,2 M de clientes y
66,6 M de intentos de autorización** en dos años. Sintético al 100 %, reproducible byte
a byte desde su generador y su semilla. **Se publica el generador; los datos no.**

**24 tablas · 278 columnas · tres granos distintos · 51 comprobaciones bloqueantes.**
Materializado también como tablas **Apache Iceberg spec v2**, que es lo que hace posible
el anillo 4.

Las cifras están en [`datagen/MEASURED-full.md`](datagen/MEASURED-full.md) y **no están
escritas a mano**: las genera `datagen/report.py` en cada build. Una cifra que ese
script no sepa producir no entra en la documentación — regla que nació de haber
publicado una vez el objetivo de un solucionador bajo el título «medido».

El detalle completo, incluidas las dos auditorías adversariales que encontraron 52
defectos entre las dos, en [`datagen/README.md`](datagen/README.md).

## Los contratos

Lo que el sistema puede hacer no está en el código: está en YAML **firmado**, y el
código lo compila a JSON en tiempo de build. El dominio nunca parsea YAML.

| Fichero | Qué fija | Estado |
|---|---|---|
| [`docs/spec/policy.yaml`](docs/spec/policy.yaml) | Matriz **rol × columna × posición** sobre 40 columnas reales: `allow`, `mask`, `deny` | firmado por Samuel |
| [`docs/spec/glossary.yaml`](docs/spec/glossary.yaml) | Qué significa cada tabla y cada métrica en lenguaje de negocio | firmado por Samuel |
| [`docs/spec/budgets.yaml`](docs/spec/budgets.yaml) | Límites de coste por rol: ejecutar, confirmar o rechazar | firmado por Samuel |
| [`docs/spec/catalog-overlay.yaml`](docs/spec/catalog-overlay.yaml) | Lo que la introspección no puede saber: qué está obsoleto, qué no se publica | firmado por Samuel |
| [`docs/spec/resultset-equality.md`](docs/spec/resultset-equality.md) | Cuándo dos resultsets son «el mismo» — y sus doce decisiones | en vigor |
| [`docs/spec/rejection.schema.json`](docs/spec/rejection.schema.json) | La forma de un rechazo: motivo, posición, sujeto, alternativa | en vigor |
| [`docs/spec/audit-record.schema.json`](docs/spec/audit-record.schema.json) | La forma de un registro de auditoría | en vigor |

**«Firmado» significa firmado por una persona**, y es una distinción con consecuencia:
un contrato firmado lleva el `sha256` de su propio texto, y
`tests/contract/test_signed_contracts.py` falla si alguien lo edita sin volver a
firmarlo. Los tres últimos son contratos propios en vigor que nadie ha firmado
todavía — decirlo importa más que la casilla verde.

El catálogo **se genera**, no se escribe: 32 relaciones y 428 columnas introspectadas
del motor, cada columna con su linaje (`derives_from`) resuelto a través de las vistas.

## Estructura

```
src/datawarden/
  domain/        tipos congelados y puros · TDD obligatorio
  guard/         el corazón · allowlist + 14 reglas · TDD puro · cobertura 95 %
  principal/     rol, política y presupuesto · el rol NUNCA viene de datos
  catalog/       introspección, linaje y estadísticas de Iceberg
  cost/          estimador, presupuesto y el filtro previo a ejecutar
  engines/       adaptadores (DuckDB) · contrato y snapshot, no cobertura
  mask/  audit/  anillo 5 · pendientes
  nl2sql/ agent/ se MIDEN, no se testean · TDD prohibido a propósito

tests/           unit · property · contract · integration · adversarial · holdout
attacks/         el cuaderno de evasiones, que crece con cada una encontrada
evals/           golden · suites · reports  ← todo número publicado sale de aquí
docs/spec/       los contratos propios, firmados
scripts/         26 comprobaciones · una por meta de GOALS.yaml
```

**Cada zona tiene su régimen de prueba, y son distintos a propósito.** El guard exige
TDD puro y mutación al 85 %; los adaptadores solo contrato; y en el generador de SQL
**el TDD está prohibido** — un componente no determinista se mide con evaluaciones, y
fingir que se prueba con asserts es engañarse.

## Gobierno

El proyecto se construye bajo reglas ejecutables, no bajo buenas intenciones:

- **[`docs/GOALS.yaml`](docs/GOALS.yaml)** — cada meta con su umbral y su comando,
  sellada por `thresholds.lock`. Ocho de ellas son **axiomas**: su umbral no admite
  propuesta de rebaja, y pedirla es en sí misma un fallo de gate.
- **[`docs/PLAN.md`](docs/PLAN.md)** — once fases, cada una con su criterio de cierre.
- **`make done MILESTONE=N`** — trece pasos, salida `0` o nada. **Es la única
  definición de «hecho»** que existe en este repositorio. Deja un snapshot como punto
  de retorno.
- **[`docs/JOURNAL.md`](docs/JOURNAL.md)** — la bitácora, **con los errores dentro**.

## Limitaciones declaradas

Se dicen aquí porque no decirlas sería el fallo:

- **Los números son autoevaluados.** El aislamiento entre quien escribe las reglas y
  quien escribe la reserva es hoy disciplina, no ejecución.
- **El gate es persuasión, no ejecución.** Los ganchos que lo harían obligatorio no
  están instalados.
- **`n = 15` en la reserva** es poco. Por eso se publica el intervalo y no el punto.
- **La mutación no llega a su umbral**, y la fase 3 no cierra por ello.
- **La calibración del coste** usa la fracción de ficheros escaneados que reporta el
  motor, no `total_bytes_read`: ese contador mide E/S y lo falsea la caché — medido, un
  escaneo de la tabla entera reportó menos bytes que uno de un solo día lanzado antes.
- Los anillos **2 (generación) y 5 (enmascarado y auditoría)** todavía no existen.

## Licencia

[Apache-2.0](LICENSE).
