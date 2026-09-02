# Cuándo dos resultsets son «el mismo»

**Contrato propio del proyecto.** Meta `G-RESULTSET-EQ`, bloqueante desde la fase 1.

## Por qué este documento existe antes que el código

`G-EXEC-ACC` promete «exactitud de ejecución ≥ 0,80». Ese número compara el resultado
de la consulta que genera el modelo contra el de un SQL de referencia. Pero **«dan el
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

### Precisiones que la implementación obligó a escribir

Se añaden el 2026-09-02, **antes** de implementar `compare()`. Son huecos de la
especificación que solo aparecen al escribir el código; escribirlos aquí en vez de
resolverlos dentro de la función es la diferencia entre un contrato y una costumbre.

**P-1 · «Tipo» significa CLASE de tipo, no tipo de Python.** La decisión 7 opone un
entero a su representación en texto, y la 5 opone `Decimal` a `float`. Las dos son la
misma regla vista desde dos sitios: lo que no se puede confundir es **texto con
número**, no `INTEGER` con `DOUBLE`. Se fijan seis clases y la comparación es dentro
de la clase:

| Clase | Tipos Python | Cómo se comparan dos valores de la clase |
|---|---|---|
| `number` | `int`, `float`, `Decimal` | `math.isclose(rel_tol=1e-9, abs_tol=1e-12)` |
| `bool` | `bool` | Identidad. **No es un número**, aunque Python lo herede de `int` |
| `text` | `str` | Igualdad exacta |
| `bytes` | `bytes`, `bytearray`, `memoryview` | Igualdad exacta de bytes |
| `null` | `None` | `None` es igual a `None` |
| `date` | `date` | Fecha civil, sin convertir (decisión 10) |
| `datetime` | `datetime` | Normalizado a UTC antes de comparar (decisión 10) |
| `time` | `time` | Igualdad exacta |
| `other` | lo demás | `==`, y el tipo exacto tiene que coincidir |

Los tres temporales son clases separadas y no una sola: un `DATE` frente a un
`TIMESTAMP` es un tipo distinto, y decirlo con `cell_type` en vez de con
`cell_value` es la diferencia entre un informe que orienta y uno que despista.
`bool` fuera de `number` es deliberado: DuckDB devuelve `BOOLEAN` donde una consulta
mal escrita devuelve `1`, y equipararlos borraría un error real. Dos clases distintas
producen `cell_type`; dos valores distintos de la misma clase producen `cell_value`.

**P-2 · La forma de un vacío solo se compara si el resultset la lleva.** La decisión 8
exige que dos vacíos coincidan en número de columnas, y una `list[tuple]` vacía **no
tiene esa información**: no hay ninguna fila de la que leer la aridad. Por eso el
contrato admite dos formas de resultset:

```python
compare([], [])                                   # sin forma: no se puede comparar, y no se finge
compare(Table((), []), Table(("a","b"), []))      # con forma: column_count
```

`Table(columns, rows)` lleva los nombres de columna, que son además lo único que hace
verificable `strict_names`. Una lista pelada sigue siendo válida y es lo que usan los
casos donde la forma no está en duda; cuando falta, la comparación **no inventa** una
aridad, y eso se dice aquí en vez de descubrirse en la fase 8.

**P-3 · El orden de columnas se canonicaliza por CONTENIDO, no por nombre.** La
decisión 2 dice «se emparejan por nombre» y la 3 dice que los nombres se ignoran: son
incompatibles tal cual, porque `count(*)` y `total` son la misma columna con dos
nombres. Manda la 3. Cada lado se transpone a columnas, cada columna recibe una clave
derivada de su **multiconjunto de valores** —independiente por tanto del orden de
filas— y las columnas se ordenan por esa clave en los dos lados. Las filas siguen
enteras: la reordenación permuta columnas, nunca desempareja una fila.

**P-4 · Un timeout gana a todo lo demás.** Se comprueba antes que el rechazo y antes
que las filas, en cualquiera de los dos lados. Una consulta que no termina no es una
respuesta y no hay nada que comparar (decisión 11).

**P-5 · `expected_rejection` es el motivo del CASO, no del veredicto.** Cuando la
referencia esperaba un rechazo, `reason` vale `expected_rejection` tanto si el rechazo
ocurrió —y entonces `equal` es cierto— como si llegaron filas, y entonces es falso.
`rejected` es el otro caso: rechazo que nadie esperaba, siempre fallo.

**P-6 · `null` frente a cadena vacía tiene motivo propio; `null` frente a cualquier
otra cosa es `cell_type`.** La decisión 6 existe porque confundir `NULL` con `''` es un
error de significado, y merece que el informe lo nombre. `NULL` frente a `42` es
sencillamente otra clase de tipo y no necesita un motivo especial.
