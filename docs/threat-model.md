# Data Warden · modelo de amenaza

> **Qué es este documento.** Lo que este sistema protege, contra quién, y —sobre todo— **qué NO
> protege**. La tercera parte es la que importa: una defensa cuyo límite no se publica se lee como
> una promesa que no es, y quien la despliegue creyendo esa promesa acabará confiando en algo que
> nunca prometió.
>
> Se escribe en la fase 5 porque es cuando la cadena de auditoría existe y su límite se puede
> declarar con precisión, y se completa en la fase 10 con las superficies de las fases 6 y 7.

---

## 1. Qué se protege

Un almacén analítico al que se le hacen preguntas en lenguaje natural, y del que un modelo genera
SQL. Concretamente:

| Activo | Por qué importa |
|---|---|
| **Los datos personales del almacén** | `national_id`, `birth_date`, `iban`, `phone_e164`, `email`, y las columnas de las que se derivan. Su exposición es el daño irreversible. |
| **La integridad del almacén** | Este sistema es de solo lectura por construcción. Una escritura que llegue al motor es una pérdida de datos, no una fuga. |
| **El presupuesto de cómputo** | Una consulta de coste no acotado es una denegación de servicio contra el resto de usuarios, y en un motor de pago por consulta es además dinero. |
| **El registro de auditoría** | Es la única evidencia de qué se preguntó y qué se ejecutó. Si se puede reescribir sin dejar rastro, no es evidencia. |

## 2. Contra quién

Tres actores, con capacidades distintas, y el diseño distingue entre ellos.

**A · El usuario legítimo que pregunta de más.** No es hostil: es un analista que escribe
`SELECT * FROM dim_customer` porque quiere ver qué hay. La defensa es la política por rol y
posición, y tiene que **redirigir** en vez de bloquear: cada rechazo publica su alternativa, porque
un guard que bloquea el trabajo se desactiva en tres semanas.

**B · El modelo, y quien controla su entrada.** El modelo genera SQL a partir de texto, y ese texto
puede venir de un tercero: una fila del propio dataset, el argumento de una herramienta, un campo
`_meta`. **El modelo no es de confianza y el sistema está construido asumiéndolo.** No se le pide
que se porte bien: se valida lo que produce.

**C · Quien tiene acceso al proceso o al disco.** Puede leer el almacén de auditoría, borrar sus
triggers y reescribir la cadena. **Contra este actor el sistema no defiende**, y la sección 4 lo
dice con todas sus letras.

## 3. Qué se defiende, y con qué

Cinco anillos. Ninguno es suficiente solo, y esa es la idea.

| # | Anillo | Contra qué | Evidencia |
|---|---|---|---|
| 1 | **Catálogo generado** | Que el modelo razone sobre un esquema que no es el real | `G-CATALOG-FRESH` |
| 2 | **Guard por AST, allowlist** | Escritura, funciones de motor, lectura de catálogo, columnas prohibidas | `G-WRITE-BLOCK`, `G-NO-RAW-SQL`, `G-FAILCLOSED` |
| 3 | **Presupuesto previo** | Consultas de coste no acotado | `G-BUDGET-ESCAPE` |
| 4 | **Enmascarado por reescritura de AST** | Exposición de datos personales | `G-PII-LEAK` |
| 5 | **Auditoría encadenada** | Que no quede rastro de lo ocurrido | `G-AUDIT-COV`, `G-AUDIT-TAMPER` |

**Tres decisiones de diseño hacen el trabajo pesado, y conviene nombrarlas:**

**Se ejecuta el árbol, nunca la cadena de entrada.** `Engine.execute()` acepta un `ValidatedQuery` y
jamás un `str`. Lo que llega al motor es el AST validado re-serializado, sin comentarios. Eso
elimina **por construcción** la clase entera de ataques por diferencia de parser: no hay ninguna
cadena que el guard lea de una manera y el motor de otra, porque el motor no lee ninguna cadena que
el guard no haya producido.

**El guard es una allowlist, no una lista negra.** Un nodo o una función que el guard no conoce se
rechaza. La diferencia importa el día que sqlglot añada un nodo nuevo o el motor una función nueva:
con lista negra, lo nuevo entra; con allowlist, lo nuevo se para hasta que alguien lo declare.

**El rol nunca viene de datos no autenticados.** `RoleSource` no tiene ningún valor que signifique
«lo dijo el cliente». `_meta` y los argumentos de herramienta son dato, no autoridad.

---

## 4. Qué NO se protege · el límite honesto

Esta sección es obligatoria y su contenido no es negociable.

### 4.1 · La cadena de auditoría

> **Quien tiene escritura sobre el almacén puede recalcular la cadena entera.**

El hash encadenado —`h_n = sha256(h_{n-1} ‖ jcs(registro_n))`— hace que reescribir un registro
obligue a reescribir **todos** los posteriores. Eso detecta a quien manipula el almacén sin poder
rehacerlo entero: el proceso que edita una fila y arregla su hash queda delatado por la siguiente.

**No detecta a quien sí puede rehacerlo entero.** Quien tenga permiso de escritura sobre el fichero
SQLite puede borrar los triggers `audit_log_no_update` y `audit_log_no_delete`, reescribir el
registro que quiera y recalcular la cadena desde ahí hasta el final. El resultado verificaría
correctamente y no habría forma de distinguirlo de la historia real.

Lo que reduce ese hueco, y **no lo cierra**:

- **`warden audit anchor`**, que publica el hash de la punta de la cadena en un medio que el
  atacante no controla. A partir de un anclaje, reescribir el pasado exige además falsificar el
  anclaje. Entre dos anclajes, la ventana sigue abierta.
- **Permisos del sistema de ficheros**: que el proceso que escribe y el que audita no sean el mismo
  usuario. Es una defensa del despliegue, no de este código, y por eso se nombra aquí en vez de
  darse por hecha.

**Un subproceso arbitrario del mismo usuario puede leer el almacén entero.** El registro guarda
`question_preview` solo cuando se pide explícitamente, y la pregunta va hasheada por defecto
justamente por esto; pero el SQL ejecutado y las tablas sí están en claro, porque sin ellos la
auditoría no auditaría nada.

### 4.2 · El enmascarado

El enmascarado protege contra la lectura del valor, **no contra la inferencia estadística**. Un rol
que puede agrupar por una columna hasheada puede contar cuántas filas comparten cada valor, y con
suficientes consultas cruzadas eso reduce el espacio de candidatos. Las defensas presentes —prohibir
la columna enmascarada en predicado, en `GROUP BY` de grupo único y en `HAVING count(*)=1`— cierran
los canales laterales más directos. **No hay presupuesto de privacidad diferencial**, y por tanto no
se afirma resistencia frente a un adversario que consulta muchas veces.

El hash es **determinista con pimienta desde configuración**. Eso es deliberado: permite agrupar sin
revelar, y permite que el banco de preguntas de referencia tenga respuestas comparables entre
ejecuciones. El precio es que **el mismo valor produce el mismo hash siempre**, así que quien
conozca un valor puede reconocerlo en el resultset. Quien tenga acceso a la pimienta puede además
construir un diccionario y revertir cualquier columna de cardinalidad baja.

### 4.3 · El modelo

El modelo puede generar SQL correcto y **semánticamente equivocado**: la consulta pasa los cinco
anillos y responde a otra pregunta. Ningún anillo de este sistema defiende contra eso. Es lo que
mide `G-EXEC-ACC` en la fase 8, y es la razón de la tesis del proyecto: **el valor no está en la
tasa de acierto, está en la garantía sobre el fallo.**

### 4.4 · Lo que queda fuera del alcance

- **Autenticación.** Este sistema recibe un `Principal` ya construido. Quién lo acuña y cómo se
  protege esa emisión es del sistema que lo integra.
- **El motor.** Se asume que DuckDB no tiene una escalada de privilegios explotable desde una
  consulta `SELECT` válida. Es un supuesto, y se declara como tal.
- **Cifrado en reposo y en tránsito.** Es del despliegue.
- **Disponibilidad.** El presupuesto acota el coste de una consulta; no hay control de tasa.

---

## 5. Un fallo real, y por qué está aquí

Un modelo de amenaza que solo enumera defensas se lee como publicidad. Este es un fallo que el
proyecto tuvo, medido y corregido:

**El estimador de coste cobraba CERO por una tabla de 4,1 GB.** El `repr` de un `Record` de
pyiceberg tiene la forma `Record[19967]`, y usarlo como clave de partición producía particiones
contra las que ningún literal de fecha casaba jamás. La poda devolvía el conjunto vacío, el coste
salía cero, y **`G-BUDGET-ESCAPE` —que es un axioma— habría dejado pasar cualquier consulta con un
predicado de fecha**. Subestimar a cero es la peor dirección posible.

No lo encontró una revisión: lo encontró la calibración, porque `p95(real/estimado)` se disparó. Por
eso `G-COST-CALIB` existe y por eso `GOALS.yaml` dice que sin ella `G-BUDGET-ESCAPE` sería
«trivialmente cierto y a la vez inútil».

La lección que este documento hereda: **un anillo que no se mide contra la realidad no es un
anillo.**
