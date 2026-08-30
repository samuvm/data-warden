<!-- generado por datagen/report.py desde el perfil `dev`; no editar a mano -->

| | |
|---|---:|
| Filas totales · 24 tablas | **3,054,243** |
| Intentos de autorización · líneas de cesta | 683,811 · 1,503,058 |
| Parquet en disco | **0.09 GB** |
| Aprobación · ticket mediano · ticket medio | 86.14 % · 56.23 € · 202.74 € |
| Transfronterizo | 35.5 % |
| Liquidado · comisión · lotes sin cerrar | 108.0 M€ · 2350.2 k€ · 23,107 pagos, 1.32 % |
| Top 1 % / top 10 % de comercios, **medido sobre el tráfico** | **18.42% / 49.40%** |
| (el mismo objetivo sobre el vector de pesos, que **no** es lo publicado) | 45% / 80% |
| Clientes que nunca pagan (objetivo 4.3%) | 4.345% |
| Pagos por cliente pagador: media · mediana · p99 · máximo | 7.28 · 3 · 49 · 172 |
| Anillos de fraude · miembros | 12 · 89 |
| Factor de reintento | 1.0667 |
| Clientes con nota de soporte · con supresión pedida | 8.25 % · 308 |

**Las trampas, medidas sobre estos datos:**

| Trampa | Cuánto engaña |
|---|---:|
| Contar ingresos contando filas | **+26.2 %** |
| Unir por la clave natural del comercio en vez de la subrogada | **+56.0 %** |
| `JOIN` de divisa por igualdad de fecha | **−24.4 %** de los pagos no-euro |
| Duplicados de ingesta | 0.349 % de las filas |
| Sumar el contracargo en todas sus etapas | **+62.5 %** |

**Y lo que tiene que dar cero:**

| Invariante | Filas que lo violan |
|---|---:|
| Dinero movido en día no hábil | 0 |
| Interchange sobre el tope del Reglamento (UE) 2015/751 | 0 |
| Notas de soporte con un teléfono que no es el del cliente | 0 |

**Contracargo por tramo de riesgo** (el score tiene que predecirlo):

| Tramo | % de contracargo |
|---|---:|
| 000-199 | 0.369 % |
| 200-399 | 0.75 % |
| 400+ | 1.515 % |
