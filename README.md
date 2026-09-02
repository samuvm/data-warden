# Data Warden

> Acceso conversacional a un lakehouse con las barreras que una empresa exige antes de
> dejar que un modelo se acerque a sus datos: SQL validado antes de ejecutar, límites de
> coste, enmascarado por rol y auditoría inmutable. Se expone como servidor MCP.

**La tesis:** traducir lenguaje natural a SQL es la parte fácil y ya resuelta. Lo que
impide que esto llegue a producción es todo lo que va después.

---

> ## ⚠️ Estado: EN CONSTRUCCIÓN · fases 0, 1 y 2 CERRADAS · la 3, construida y sin cerrar
>
> Este README dice hoy lo que hay hoy, y cada número lleva **el comando que lo
> reproduce y el artefacto del que sale**. Los que faltan se dicen también.
>
> **Todo lo que se publica aquí es AUTOEVALUADO.** El gobierno que aislaría la
> reserva de quien escribió las reglas (`~/.claude/gates/`, decisión D-09) todavía no
> está instalado, así que ese aislamiento es hoy **disciplina y no ejecución**, y el
> número del holdout sale con esa palabra al lado. Es la política de honestidad de los
> cinco proyectos, decidida por escrito antes de que doliera.

## Los números, medidos

Todos sobre **MacBook Pro M4 Max · macOS 26.5 · arm64**, con un solo proyecto
encendido (decisión D-03). Los artefactos están en `evals/reports/`.

| Qué | Umbral | Medido | Comando |
|---|---|---|---|
| **Bloqueo de evasiones · RESERVA** | 15/15 | **15/15 · Wilson 95 % [0,80 – 1,00]** | `make attack-holdout` |
| **Bloqueo de evasiones · mutación de AST** | ≥ 2.000 mutantes | **3.497 · cero evasiones** | `make attack-mut` |
| **Fail-closed del guard** | ≥ 5.000 entradas, 0 excepciones | **20.000 · 0** | `make guard-property` |
| **Latencia del guard** | p95 ≤ 25 ms | **p95 0,81 ms** · p99 1,1 · máx 3,7 | `make bench-guard` |
| **Consultas caras que llegan al motor** | 0 | **0** · rechazo de 4,1 GB en **1,2 ms** | `make budget-invariant` |
| **Calibración del estimador** | p95(real/est.) ≤ 1,5 | **1,077** · 0 casos > 3, n = 60 | `make cost-calibration` |
| **Cobertura de línea** | ≥ 90 % / ≥ 95 % en `guard/` | **98,0 % / 96,5 %** | `make coverage` |
| **Un test por función pública** | 0 sin test | **0 de 88** | `make coverage` |
| **Secretos nuevos** | 0 | **0** | `make secrets` |
| **Mutación · `guard/rules`** | ≥ 85 % | **50,73 %** ❌ | `make mutation` |
| **Mutación · resto** | ≥ 70 % | **66,62 %** ❌ | `make mutation` |

**Qué significa «15/15» y qué no.** Los quince casos de la reserva los escribió un
subagente que **no vio `src/datawarden/guard/`** ni el cuaderno de ataque de
desarrollo: escribió contra la especificación firmada. Con 15/15 el intervalo de
Wilson al 95 % es aproximadamente **[0,80 – 1,00]**, y **eso es lo que se publica, no
«100 % de bloqueo»**. Publicar el punto con n = 15 sería publicar un número sin
significado. Subir a 30 casos estrecharía el límite inferior a ~0,88 y es la mejor
compra de credibilidad por hora que le queda al proyecto.

**Y el cuaderno de desarrollo, que sale 25/25, NO se publica como número de
seguridad.** Las reglas se escribieron para parar esos casos: pasar el 100 % es medir
sobre el conjunto de entrenamiento.

### Las dos que están en rojo, y por qué

La **mutación** está por debajo de su umbral y la fase 3 no cierra por eso. El motivo
está medido: sobre una muestra de 80 mutantes supervivientes de `guard/rules`, **el
55 % son mutaciones que solo cambian el TEXTO de un mensaje de rechazo**, y casi todo
el resto solo altera cómo se compone ese texto. Matarlos exigiría asertar los mensajes
palabra por palabra, que es la misma fragilidad que este proyecto prohíbe en otro
sitio. La propuesta **P-005** pide sacar los textos del alcance de la medida **sin
bajar ningún umbral**; hasta que se decida, el número se publica como está.

Lo que la mutación **sí** encontró, y ya está arreglado: que el corpus del guard no
asertaba `position` ni `subject` de los rechazos, y que R013 estaba «probada» por
casos que en realidad paraban otro mecanismo. Subió de 38,61 % a 50,73 % tapando
huecos reales.

## Lo que ya funciona

### El dataset · CIERZO

Una pasarela de pagos europea que no existe: 12.400 comercios, 9,2 M de clientes y
66,6 M de intentos de autorización en dos años. **Sintético al 100 %**, reproducible
byte a byte desde su generador y su semilla. Se publica el generador; los datos no.

```bash
./datagen/run.sh dev     # 15 s  ·   3,1 M filas ·  91 MB
./datagen/run.sh demo    # 1 min ·  28,0 M filas · 762 MB
./datagen/run.sh full    # 8 min · 294,8 M filas · 7,46 GB
```

**24 tablas · 278 columnas · tres granos distintos · 51 comprobaciones bloqueantes.**
Materializado además como tablas **Apache Iceberg spec v2**.

Las cifras medidas están en [`datagen/MEASURED-full.md`](datagen/MEASURED-full.md), y
no están escritas a mano: las genera `datagen/report.py` en cada build. Una cifra que
ese script no sepa producir no entra en la documentación — regla que nació de haber
publicado una vez el objetivo de un solucionador bajo el título «medido».

Todo el detalle, incluidas las dos auditorías adversariales que encontraron 52
defectos entre las dos, en [`datagen/README.md`](datagen/README.md).

### Los contratos propios

- [`docs/spec/policy.yaml`](docs/spec/policy.yaml) — la matriz **rol × columna × nivel**
  sobre 40 columnas reales. Tres niveles: `allow`, `mask` (solo en proyección; rechazo
  si aparece en un `WHERE`) y `deny`. En **BORRADOR**, a la espera de firma.
- [`docs/spec/glossary.yaml`](docs/spec/glossary.yaml) — qué significa cada tabla y cada
  métrica en lenguaje de negocio. En **BORRADOR**.

## Lo que todavía NO existe

`src/datawarden/` es hoy un esqueleto vacío. No hay guard, ni enmascarado, ni auditoría,
ni servidor MCP, ni un solo número de seguridad publicable. La fase 0 son los cimientos
y va **sin una línea de IA**, a propósito.

## Cómo mirarlo

No hay servidor, ni puerto, ni credenciales: **DuckDB es embebido**. Los datos son
ficheros Parquet y la base de datos es un catálogo de vistas sobre ellos.

```bash
# Consola SQL en un contenedor de solo lectura
PROFILE=full docker compose -f datagen/docker/compose.yaml run --rm sql
cierzo> .read sql/01-explora.sql

# O con el cliente que prefieras, apuntando al fichero
duckdb datagen/out/full/cierzo-full.duckdb
```

## Los cinco anillos

| | | Estado |
|---|---|---|
| 1 · Contexto | El catálogo y el glosario se exponen como recurso MCP. El modelo no adivina la estructura: la lee | contratos en borrador |
| 2 · Generación | Traducción a SQL con ejemplos del dominio | pendiente |
| 3 · Validación | La consulta se parsea a AST con `sqlglot` y pasa un guard de **allowlist**. Se valida el árbol, no se buscan cadenas de texto | pendiente |
| 4 · Coste | `EXPLAIN` antes de ejecutar. Si supera el presupuesto no se ejecuta, y el mensaje explica cómo acotar | pendiente |
| 5 · Salida | Enmascarado por rol reescribiendo el AST, límite de filas y auditoría append-only con hash encadenado | pendiente |

Y sobre todo: **da igual qué cliente se conecte o qué modelo use, no puede saltarse las
reglas**, porque no viven en su lado.

## Gobierno

El proyecto se construye bajo un conjunto de reglas ejecutables:
[`docs/GOALS.yaml`](docs/GOALS.yaml) fija metas con umbral y comando, sellado por
`thresholds.lock`; [`docs/PLAN.md`](docs/PLAN.md) las reparte en once fases;
[`docs/JOURNAL.md`](docs/JOURNAL.md) es la bitácora, **con los errores dentro**.

Hoy ese gate es **persuasión, no ejecución**: los ganchos que lo harían obligatorio
todavía no están instalados, y mientras sea así se dice aquí con estas palabras.

## Licencia

Apache-2.0. Ver [`LICENSE`](LICENSE).
