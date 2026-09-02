"""La función de enmascarado que vive en el MOTOR, para que la pimienta no viaje.

**Qué problema resuelve, y cuál NO.** `hash_estable` se calcula en el motor —es la
consecuencia directa de que el enmascarado sea una reescritura de AST y no un
post-proceso—, así que para hashear hay que darle la clave. Con la pimienta escrita
en cada consulta, acababa en dos sitios: los logs del motor **y el campo `sql` del
registro de auditoría**. El segundo es el que duele, porque `docs/threat-model.md
§4.1` declara que cualquier subproceso del mismo usuario puede leer ese almacén.

Con la macro, el árbol dice `warden_hash(...)` y la pimienta **desaparece del
registro de auditoría**. Sigue apareciendo en los logs del motor, dentro del
`CREATE TEMP MACRO`, pero **una vez por conexión en vez de una vez por consulta**.

Es una reducción grande y **no es una eliminación**, y Samuel pidió expresamente que
se dijera con esas palabras al aprobar P-008: *un límite que se declara a medias es
peor que uno que no se declara, porque parece cerrado.*

**La solución de verdad no es esta, y también está decidida.** Estas dos columnas no
necesitan un hash con clave: necesitan un **pseudónimo estable**, que es el patrón que
el proyecto ya usa con `dim_card.card_token` (denegada) y su alternativa `card_sk`.
Generar `device_fingerprint_sk` e `ip_address_sk` en tiempo de construcción conserva
entera la capacidad de contar sesiones distintas y hace que la pimienta **deje de
existir** para ellas. Va después porque regenerar una columna derivada invalida el
`MANIFEST` contra el que cerraron las fases 0, 1 y 2.

**Por qué `TEMP`.** La conexión del motor es de SOLO LECTURA, y tiene que seguir
siéndolo. Una macro temporal vive en el esquema de sesión y no toca el fichero, así
que no hay que abrir en escritura para instalarla. Comprobado contra el DuckDB de
`dev`: instala, ejecuta, y da exactamente el mismo resultado que la versión con la
pimienta escrita en la consulta.
"""

from __future__ import annotations

from typing import Final

from datawarden.mask.config import MaskConfig

#: El nombre de la función. Es parte del contrato entre `mask/` y el motor: el
#: reescritor la emite y el adaptador la instala, así que cambiarla aquí sin
#: cambiarla allí produce un `Function not found` en tiempo de ejecución.
MACRO_NAME: Final = "warden_hash"

#: Caracteres del hash que se publican. Vive aquí y no en `rewrite.py` porque el
#: truncado ocurre DENTRO de la macro: si los dos números discreparan, el mismo valor
#: daría dos hashes según por dónde pasara.
HASH_HEX: Final = 12


def macro_ddl(config: MaskConfig, *, dialect: str = "duckdb") -> str:
    """El `CREATE TEMP MACRO` que hay que ejecutar al abrir la conexión.

    **La pimienta va dentro de esta cadena.** Es el precio de que el hash se calcule
    en el motor, y por eso esta cadena no debe registrarse en la auditoría ni
    imprimirse en un log de la aplicación: no es una consulta del usuario, es la
    instalación de una clave.
    """
    if dialect != "duckdb":
        message = (
            f"no hay macro de enmascarado para el dialecto {dialect!r}. La fase 9 "
            "(Athena) tiene que declarar la suya con una UDF equivalente, o publicar "
            "`G-ENGINE-PARITY` como NO VERIFICADO para las columnas con "
            "`hash_estable`. Inventar aquí un dialecto que no se ha probado sería "
            "exactamente lo que este proyecto no hace."
        )
        raise ValueError(message)
    escaped = config.pepper.replace("'", "''")
    return (
        f"CREATE OR REPLACE TEMP MACRO {MACRO_NAME}(v) AS "
        f"substring(sha256(v || '{escaped}'), 1, {HASH_HEX})"
    )
