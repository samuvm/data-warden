# ADR-003 · Estimar el coste desde los metadatos de Iceberg, no con `EXPLAIN`

**Estado:** aceptado · 2026-09-02

## Contexto

El anillo de coste tiene que rechazar una consulta cara antes de que el motor la toque. `EXPLAIN ANALYZE`
ejecuta la consulta. `EXPLAIN` de DuckDB da cardinalidades pero no bytes escaneados, y Athena solo
reporta `DataScannedInBytes` después de ejecutar, cuando ya ha facturado. La tabla de hechos tiene
66,6 M de filas y 4,1 GB.

## Opciones

| Opción | Pros | Contras |
|---|---|---|
| A · `EXPLAIN` del motor | Sin código propio | Sin bytes; distinto por motor; no existe un equivalente previo en Athena |
| B · Ejecutar con límite de tiempo | Coste real | Ya es tarde: el motor ha trabajado y, en la nube, cobrado |
| C · Estadísticas desde los manifiestos de Iceberg (filas y bytes por columna y partición) con poda por predicado | Sin leer una fila; los mismos metadatos sirven para DuckDB y Athena | Es una estimación: hay que calibrarla y decidir hacia dónde redondea |

## Decisión

Se adopta C. Las estadísticas se construyen desde los manifiestos (0,5 s para el perfil completo, sin
leer datos) y se versionan. Todo lo dudoso se redondea hacia arriba: una intersección vacía en la poda
se trata como «no sé» y no como «cero», y una tabla sin estadísticas vale 1 GB. Sobreestimar cuesta que
alguien acote su pregunta; subestimar cuesta el axioma.

## Consecuencias

- Meta axioma `G-BUDGET-ESCAPE`: 0 consultas caras llegan al motor; una consulta de 3 GB se rechaza en
  1,5 ms. Meta `G-COST-CALIB`: p95(real/estimado) = 1,077 sobre 60 consultas.
- La calibración es la que hace útil al axioma: un estimador que devuelve cero nunca deja escapar nada.
  Así se encontró que el estimador cobraba 0 bytes por la tabla de 4,1 GB (la clave de partición se
  serializaba mal y la poda quedaba vacía); ver [`threat-model.md`](../threat-model.md) §5.1.
- Las vistas derivadas no tienen manifiesto: su coste sale del linaje publicado en el catálogo.
- Señal de error: un p95 de calibración que se acerque a 1,5, o cualquier caso con ratio > 3.
