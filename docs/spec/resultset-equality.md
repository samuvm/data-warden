# Cuándo dos resultsets son «el mismo»

**Contrato propio del proyecto.** Meta `G-RESULTSET-EQ`, bloqueante desde la fase 1.

## Por qué este documento existe antes que el código

`G-EXEC-ACC` promete «exactitud de ejecución ≥ 0,80». Ese número compara el resultado
de la consulta que генera el modelo contra el de un SQL de referencia. Pero **«dan el
mismo resultado» no significa nada hasta que alguien decide qué cuenta como el mismo**:

- ¿`[(1,'a'), (2,'b')]` es igual a `[(2,'b'), (1,'a')]`? Depende de si hubo `ORDER BY`.
- ¿`0.1 + 0.2` es igual a `0.3`? En coma flotante, no.
- ¿`NULL` es igual a `''`? En SQL no, y confundirlos oculta un error real.
- ¿Un rechazo del guard cuenta como fallo, o no cuenta?

Sin estas decisiones escritas, dos personas miden la misma cosa y publican dos cifras
distintas con el mismo nombre. **Y la comparación de cadenas de SQL no vale**: dos
consultas escritas de forma completamente distinta pueden dar el mismo resultado, que
es exactamente lo que importa. Comparar SQL como texto es una métrica que engaña.

---

## Las doce decisiones

### 1 · Orden de filas: **se ignora, salvo que la referencia tenga `ORDER BY`**

Sin `ORDER BY`, SQL no garantiza orden y exigirlo penalizaría una respuesta correcta.
Con `ORDER BY` en la referencia, el orden **es** parte de la respuesta y se compara.
La presencia de `ORDER BY` se detecta sobre el AST de la referencia, no con `grep`.

### 2 · Orden de columnas: **se ignora**

`SELECT a, b` y `SELECT b, a` responden lo mismo. Las columnas se emparejan por nombre.

### 3 · Nombres de columna: **se ignoran, se emparejan por posición tras ordenar por tipo**

`count(*)`, `n` y `total` son la misma columna con tres alias. Exigir el nombre mediría
la elección de alias del modelo, que no es lo que `G-EXEC-ACC` quiere medir.
**Excepción:** si la pregunta pide explícitamente un nombre («llámalo `ingresos`»), el
caso lo declara y entonces sí se compara.

### 4 · Flotantes: **tolerancia relativa 1e-9, absoluta 1e-12**

`math.isclose(rel_tol=1e-9, abs_tol=1e-12)`. Un `sum()` sobre 66 millones de filas
acumula error de redondeo distinto según el orden de agregación, y ese orden depende
del plan de ejecución, no de la corrección de la consulta.

### 5 · `Decimal` frente a `float`: **iguales si el valor coincide dentro de la tolerancia**

DuckDB devuelve `DECIMAL` para agregaciones sobre enteros y `DOUBLE` para divisiones.
La misma respuesta llega con dos tipos según cómo se escriba. Se compara el **valor**.

### 6 · `NULL` frente a `''`: **DISTINTOS. Nunca se equiparan**

Es la decisión que más se discute y la que más importa aquí. Este almacén tiene NULLs
que significan cosas concretas —`last_name_2` es NULL porque ese sistema de nombres no
tiene segundo apellido; `fx_rate` es NULL porque no hubo conversión— y equipararlos a
la cadena vacía borraría precisamente la distinción que el dataset enseña.
`None == None` sí es igualdad.

### 7 · `int(1)` frente a `"1"`: **DISTINTOS**

El otro caso discutible. Un tipo distinto es una respuesta distinta: si el modelo
devuelve texto donde la referencia devuelve un entero, la consulta no es equivalente
aunque «se lea igual». Excepción única: `Decimal` frente a `float` (decisión 5), donde
la diferencia la impone el motor y no la consulta.

### 8 · Resultset vacío: **igual a otro vacío, con la misma forma**

Dos consultas que no devuelven filas son equivalentes **si además coinciden en número
de columnas**. Un vacío de tres columnas no es un vacío de una: la segunda perdió una
columna y da la casualidad de que no había filas para notarlo.

### 9 · Duplicados: **multiset, no conjunto**

`[(1,), (1,)]` **no** es igual a `[(1,)]`. Este almacén tiene duplicados de ingesta
declarados al 0,35 % y filas legítimamente repetidas; tratar el resultado como conjunto
haría invisible la diferencia entre deduplicar y no deduplicar, que es una de las
trampas centrales del dataset.

### 10 · Temporales: **normalizados a UTC antes de comparar**

`TIMESTAMP` sin zona se interpreta como UTC. `DATE` se compara como fecha civil, sin
convertir. Un desfase de zona horaria es un error del sistema, no de la consulta, y
mezclarlo con la exactitud contamina la métrica.

### 11 · Timeout: **cuenta como FALLO en el numerador y sí entra en el denominador**

Una consulta que no termina no es una respuesta. Excluirla del denominador subiría la
exactitud escondiendo el problema, que es la definición de una métrica que engaña.

### 12 · Rechazo del guard: **depende de lo que la referencia diga**

- Si el caso esperaba **rechazo** (10 de las 60 preguntas del banco lo esperan, porque
  piden datos que ese rol no puede ver): el rechazo es **acierto**.
- Si el caso esperaba **filas**: el rechazo es **fallo**, y entra en el denominador.

Nunca se saca del denominador. La tasa de rechazo se publica aparte, siempre.

---

## Contrato de la función

```python
compare(actual, expected, *, ordered=False, strict_names=False) -> Comparison
```

Devuelve un `Comparison` con `equal: bool` y `reason: str`. **`reason` es tan
importante como `equal`**: sin él, un fallo de 60 casos no dice en qué se diferencian,
y depurar la métrica cuesta más que calcularla.

Motivos posibles: `equal`, `row_count`, `column_count`, `cell_value`, `cell_type`,
`null_vs_empty`, `row_order`, `column_names`, `timeout`, `rejected`, `expected_rejection`.
