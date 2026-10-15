# ADR-001 · Validación por AST con allowlist

**Estado:** aceptado · 2026-09-01

## Contexto

El SQL lo escribe un modelo cuya entrada puede controlar un tercero. Hay que decidir qué consultas
llegan al motor. Una lista negra de palabras (`DROP`, `DELETE`, `read_csv`…) se salta con comentarios
entre tokens, mayúsculas, codificación o una CTE que oculta la escritura. Una lista negra de funciones
se queda corta el día que DuckDB añade una extensión o sqlglot un nodo nuevo.

## Opciones

| Opción | Pros | Contras |
|---|---|---|
| A · Denylist de palabras sobre el texto | Trivial | Se evade por construcción |
| B · `sqlparse` + reglas | Ligero | Solo tokeniza: no hay árbol sobre el que razonar alias, CTEs ni subconsultas |
| C · AST de `sqlglot` con denylist de nodos y funciones | Árbol real | Lo nuevo entra por defecto |
| D · AST de `sqlglot` cualificado contra el catálogo, con allowlist | Lo desconocido se rechaza; alias, CTEs y `*` ya resueltos al validar | Hay que declarar cada nodo y función admitidos; algunas consultas legítimas se rechazan hasta declararlas |

## Decisión

Se adopta D. La consulta se parsea, se cualifica con `qualify()` contra el catálogo generado y pasa por
14 reglas (`R001`…`R014`). `guard/allowlist.py` declara 165 tipos de nodo admitidos; `exp.Anonymous`
solo se admite con los nombres internos del sistema. Se renuncia a aceptar SQL válido que use una
construcción no declarada: el coste es un rechazo de más, y el rechazo publica una alternativa.

## Consecuencias

- Un nodo o una función nuevos en sqlglot o en DuckDB se rechazan hasta que alguien los declare con su
  caso. Ejemplo: `Any` y `All` salieron de la allowlist cuando un nombre de función aleatorio se parseó
  como cuantificador y el guard aceptó algo que ni siquiera era SQL válido para DuckDB.
- Invariantes I-03 (allowlist) e I-04 (fail-closed); metas `G-WRITE-BLOCK`, `G-FAILCLOSED`, `G-GUARD-P95`.
- Medido: 0 evasiones en el holdout (15/15, Wilson 95 % [0,80 – 1,00]) y en 3.388 mutantes de AST;
  0 excepciones en 20.000 entradas arbitrarias; p95 de 0,85 ms por consulta.
- El pin de sqlglot es exacto: su AST cambia entre minors y una subida cambia qué se considera seguro.
- Señal de error: una tasa alta de rechazos de consultas legítimas por nodo no declarado en el banco
  de evaluación.
