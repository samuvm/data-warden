"""Dónde está una columna dentro del árbol. Es la mitad de la tesis del proyecto.

`docs/spec/policy.yaml` lo dice en su encabezado: los tres niveles **no son una
escala de confianza, son una escala de POSICIÓN EN EL ÁRBOL**. Una columna `mask` es
legal en la proyección y prohibida en el `WHERE`, y esa asimetría es lo que cierra
el canal lateral por predicado:

    SELECT count(*) FROM dim_customer WHERE birth_date BETWEEN '1985-01-01' AND '1985-12-31'

Eso no devuelve ni una fecha de nacimiento y la reconstruye a base de preguntas. El
rango entre 1930 y 2010 son 29.220 días, así que **quince consultas con distinto
literal fijan la fecha exacta de una persona**. Una máscara esconde el valor; no
esconde la respuesta. Rechazar la posición sí.

Por eso este módulo existe aparte: R008 y R012 lo necesitan igual, y dos
implementaciones de «dónde está esta columna» serían dos criterios distintos sobre
la misma pregunta.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from datawarden.domain.types import Position


def position_of(column: exp.Expression) -> Position:
    """La posición de una columna, subiendo por sus ancestros.

    El orden de las comprobaciones ES la semántica. Una columna dentro de una
    función dentro de un `WHERE` está en el `WHERE`: las dos posiciones están
    prohibidas para una columna enmascarada, pero el mensaje tiene que nombrar la
    cláusula, porque es lo que la persona que preguntó puede cambiar.

    Y una columna dentro de una función EN LA PROYECCIÓN no está «en la
    proyección»: está dentro de una función, que es una posición distinta y
    prohibida. `SELECT substr(national_id, 1, 3)` devuelve tres dígitos de un NIF
    sin que la columna aparezca desnuda en ningún sitio.
    """
    node = column.parent
    inside_function = False
    while node is not None:
        if isinstance(node, exp.Where):
            return Position.WHERE
        if isinstance(node, exp.Having):
            return Position.HAVING
        if isinstance(node, exp.Qualify):
            return Position.QUALIFY
        if isinstance(node, exp.Group):
            return Position.GROUP_BY
        if isinstance(node, exp.Join):
            return Position.JOIN_ON
        if isinstance(node, exp.Window):
            return _window_position(column, node)
        if isinstance(node, exp.Order):
            # Un `ORDER BY` dentro de una ventana ordena la ventana, no el
            # resultado, y lo cubre `_window_position`. Aquí solo llega el de
            # verdad, el de la consulta.
            return Position.ORDER_BY
        if isinstance(node, exp.Func):
            inside_function = True
        node = node.parent
    return Position.FUNCTION_ARGUMENT if inside_function else Position.PROJECTION


def _window_position(column: exp.Expression, window: exp.Window) -> Position:
    """Dentro de una ventana, `PARTITION BY` es la posición peligrosa.

    Particionar por una columna sensible agrupa por ella: es un `GROUP BY` con otro
    nombre, y expone la columna por cardinalidad exactamente igual. Ordenar por ella
    dentro de la ventana también revela su orden, así que se trata igual de
    restrictivamente y se dice cuál de las dos fue.
    """
    for part in window.args.get("partition_by") or []:
        if column is part or _contains(part, column):
            return Position.WINDOW_PARTITION
    return Position.ORDER_BY


def _contains(haystack: exp.Expression, needle: exp.Expression) -> bool:
    return any(node is needle for node in haystack.walk())


def table_and_column(column: exp.Column) -> tuple[str, str]:
    """`(tabla, columna)` en minúsculas. Tras `qualify()`, la tabla siempre está.

    Si no estuviera —una columna que `qualify()` no supo resolver— se devuelve
    cadena vacía, y quien decide qué hacer con eso es R008: aquí no se inventa una
    tabla, porque inventarla es adjudicar una política que no le corresponde.
    """
    return (column.table or "").lower(), column.name.lower()
