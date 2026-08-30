# Contratos transversales

Estos ficheros son las **dependencias reales** entre los cinco proyectos. El mapa de conjunto
declara cinco contratos, pero ninguno de los documentos individuales los especifica lo bastante
como para implementarlos, y hay uno —el esquema del informe de evaluación— que ni siquiera está
declarado y sin embargo existe.

## La regla

**Se copian. Nunca se importan.**

Cada proyecto tiene su propia copia en `docs/CONTRACTS/`. Cambiar un contrato es un evento
consciente: se edita aquí, se sube su versión, se propaga a mano a los repos afectados, y se
anota en el CHANGELOG de cada uno.

Duplicar unos ficheros es barato. Crear un paquete común del que dependan los cinco es caro:
rompe la independencia, obliga a publicarlo, y convierte cada repositorio en "uno de esos
cinco" en lugar de un proyecto que se sostiene solo.

## Qué hay aquí

| Contrato | Entre | Por qué existe |
|---|---|---|
| `goals.schema.json` | **los cinco** | Forma canónica de `docs/GOALS.yaml`. Sin él, cada proyecto inventa su convención —y lo hicieron: `clase` significaba dos cosas incompatibles, la cobertura iba unas veces en ratio y otras en porcentaje, y algunos umbrales estaban en prosa. Sin esquema común no se puede escribir un solo `goals-check.sh` que sirva a los cinco, y ese script es el punto 7 de la Definition of Done |
| `chunks-ddl.sql` | 01 ↔ 04 | El mapa lo resume en cinco nombres de columna. Faltan dimensión, métrica de distancia, hiperparámetros del índice, esquema de metadata, y la columna de búsqueda léxica que el 01 necesita. Sin esto el 04 construye una tabla que el 01 no puede consultar |
| `retrieval-metrics.md` | 01, 02, 04 | Hoy el 01 y el 04 medirían "recall" de dos formas distintas y sus README no serían comparables. Incluye el esquema del golden set, el bootstrap pareado y la corrección por comparaciones múltiples |
| `otel-genai.md` | 01, 02, 03, y 04 en su fase de ampliación | El mapa lo declara como estándar asentado. Nada del namespace `gen_ai.*` es estable en 2026. Sin versión pineada y capa de traducción, la integración se rompe sola |
| `eval-report.schema.json` | 01, 02, 03, 04 | El sexto contrato, no declarado en el mapa. El 02 pretende ser la puerta de calidad de los demás y no puede comparar informes sin esquema común. El 04 lo usa para sus informes de calidad de ingesta |
| `pricing-table.md` | 02 → 04 | El 02 la construye; el 04 la necesita para su métrica de ahorro en euros. Dependencia oculta |

**El 05 no consume ninguno** salvo `goals.schema.json`: no produce informes de evaluación ni
toca el índice. Una copia huérfana de un contrato en un proyecto que no lo usa es ruido, y el
gate la detecta.

### La distinción que evita el error más fácil

`docs/CONTRACTS/` son **copias literales de aquí, inmutables, en `deny`**. Los contratos propios
de un proyecto —una política de validación, el formato de un registro de auditoría— viven en
`docs/spec/`, y ahí sí se escribe con normalidad. Confundirlos hace inejecutable cualquier fase
cuyo entregable sea un esquema propio.

## Contratos del mapa que NO están aquí, y por qué

- **API compatible con OpenAI** (01, 03, 05 → 02). No necesita fichero: el contrato es la
  especificación pública de OpenAI, y el test del 02 valida contra ella, no contra una
  interpretación propia.
- **Iceberg / Parquet sobre S3** (04, 02 → 03). **Es ficticio en ambos extremos.** El 02
  almacena en ClickHouse y no menciona Iceberg ni S3; el 04 escribe en pgvector y guarda estado
  en Postgres. El "bucle bonito" —el 03 consultando las trazas del 02— violaría además el
  fuera-de-alcance del propio 03 ("más de un esquema"). Degradado a trabajo futuro en el mapa
  corregido. Si algún día se hace, es una fase de ampliación del 02 con su coste declarado, no
  una integración que se da por hecha.
- **Módulos Terraform versionados** (05 → 01, 03). Temporalmente imposible tal como está: el
  mapa dice que 01 y 03 consumen módulos del 05, y a la vez que el 05 se extrae de lo que ya
  escribieron 01 y 04. Además el 01 no tiene ni directorio de infraestructura y declara que
  corre sin cuenta AWS. Corregido: cada uno lleva su Terraform mínimo o solo su `compose.yaml`;
  el 05 extrae, generaliza y publica; la importación por versión se documenta como posible, no
  como hecha.
