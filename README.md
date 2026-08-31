# Data Warden

> Acceso conversacional a un lakehouse con las barreras que una empresa exige antes de
> dejar que un modelo se acerque a sus datos: SQL validado antes de ejecutar, límites de
> coste, enmascarado por rol y auditoría inmutable. Se expone como servidor MCP.

**La tesis:** traducir lenguaje natural a SQL es la parte fácil y ya resuelta. Lo que
impide que esto llegue a producción es todo lo que va después.

---

> ## ⚠️ Estado: EN CONSTRUCCIÓN, fase 0 de 10
>
> Este README dice hoy lo que hay hoy. **Ningún número de este repositorio está
> publicado como resultado todavía**, porque el sistema que los produciría aún no
> existe. Lo que sí existe está abajo, con su comando para comprobarlo.
>
> Cuando haya métricas, se publicarán **con su `n`, su intervalo de confianza y el
> artefacto del que salen**, salgan como salgan. Es la política de honestidad que
> gobierna los cinco proyectos, decidida por escrito antes de que doliera.

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
