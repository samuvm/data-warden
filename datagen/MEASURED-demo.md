<!-- generado por datagen/report.py desde el perfil `demo`; no editar a mano -->

| | |
|---|---:|
| Filas totales · 24 tablas | **29,597,199** |
| Intentos de autorización · líneas de cesta | 6,626,228 · 14,596,532 |
| Parquet en disco | **0.77 GB** |
| Aprobación · ticket mediano · ticket medio | 86.69 % · 43.98 € · 194.53 € |
| Transfronterizo | 26.7 % |
| Liquidado · comisión · lotes sin cerrar | 1013.4 M€ · 21260.2 k€ · 105,824 pagos, 1.28 % |
| Top 1 % / top 10 % de comercios, **medido sobre el tráfico** | **21.94% / 61.92%** |
| (el mismo objetivo sobre el vector de pesos, que **no** es lo publicado) | 45% / 80% |
| Clientes que nunca pagan (objetivo 4.3%) | 4.316% |
| Pagos por cliente pagador: media · mediana · p99 · máximo | 7.07 · 3 · 47 · 193 |
| Anillos de fraude · miembros | 128 · 955 |
| Factor de reintento | 1.0641 |
| Clientes con nota de soporte · con supresión pedida | 8.13 % · 3,191 |

**Las trampas, medidas sobre estos datos:**

| Trampa | Cuánto engaña |
|---|---:|
| Contar ingresos contando filas | **+25.1 %** |
| Unir por la clave natural del comercio en vez de la subrogada | **+40.4 %** |
| `JOIN` de divisa por igualdad de fecha | **−24.4 %** de los pagos no-euro |
| Duplicados de ingesta | 0.352 % de las filas |
| Sumar el contracargo en todas sus etapas | **+58.4 %** |

**Y lo que tiene que dar cero:**

| Invariante | Filas que lo violan |
|---|---:|
| Dinero movido en día no hábil | 0 |
| Interchange sobre el tope del Reglamento (UE) 2015/751 | 0 |
| Notas de soporte con un teléfono que no es el del cliente | 0 |

**Contracargo por tramo de riesgo** (el score tiene que predecirlo):

| Tramo | % de contracargo |
|---|---:|
| 000-199 | 0.347 % |
| 200-399 | 0.674 % |
| 400+ | 1.418 % |
