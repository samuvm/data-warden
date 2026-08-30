<!-- generado por datagen/report.py desde el perfil `full`; no editar a mano -->

| | |
|---|---:|
| Filas totales · 24 tablas | **294,752,291** |
| Intentos de autorización · líneas de cesta | 66,590,551 · 146,828,603 |
| Parquet en disco | **7.46 GB** |
| Aprobación · ticket mediano · ticket medio | 86.93 % · 42.72 € · 173.38 € |
| Transfronterizo | 24.6 % |
| Liquidado · comisión · lotes sin cerrar | 9153.2 M€ · 195591.3 k€ · 728,643 pagos, 1.27 % |
| Top 1 % / top 10 % de comercios, **medido sobre el tráfico** | **30.78% / 71.79%** |
| (el mismo objetivo sobre el vector de pesos, que **no** es lo publicado) | 45% / 80% |
| Clientes que nunca pagan (objetivo 4.3%) | 4.313% |
| Pagos por cliente pagador: media · mediana · p99 · máximo | 7.12 · 3 · 47 · 258 |
| Anillos de fraude · miembros | 1,288 · 9,705 |
| Factor de reintento | 1.0631 |
| Clientes con nota de soporte · con supresión pedida | 8.09 % · 31,313 |

**Las trampas, medidas sobre estos datos:**

| Trampa | Cuánto engaña |
|---|---:|
| Contar ingresos contando filas | **+24.0 %** |
| Unir por la clave natural del comercio en vez de la subrogada | **+52.6 %** |
| `JOIN` de divisa por igualdad de fecha | **−24.4 %** de los pagos no-euro |
| Duplicados de ingesta | 0.349 % de las filas |
| Sumar el contracargo en todas sus etapas | **+60.5 %** |

**Y lo que tiene que dar cero:**

| Invariante | Filas que lo violan |
|---|---:|
| Dinero movido en día no hábil | 0 |
| Interchange sobre el tope del Reglamento (UE) 2015/751 | 0 |
| Notas de soporte con un teléfono que no es el del cliente | 0 |

**Contracargo por tramo de riesgo** (el score tiene que predecirlo):

| Tramo | % de contracargo |
|---|---:|
| 000-199 | 0.358 % |
| 200-399 | 0.682 % |
| 400+ | 1.39 % |
