"""Las cuatro operaciones, y la forma de lo que devuelven. Transporte-neutro.

**Ni una decisión de política aquí.** El rol llega en el constructor y jamás por
llamada; al motor se llega solo por `AuditedExecutor`, que es el único camino (I-06) y
el único que atraviesa los cuatro anillos. Esto orquesta y da forma, nada más.

**Un rechazo es una RESPUESTA, no un error**, y esa decisión se toma aquí para que los
dos transportes la hereden igual: MCP lo devuelve con `is_error: false` y HTTP con un
200. Modelarlo como error haría que un cliente lo reintentara a ciegas o lo tratara como
una caída, en vez de leer la regla, el motivo y la alternativa — que es lo más valioso
que produce este sistema.
"""

from __future__ import annotations

from typing import Any

from datawarden.audit.executor import AuditedExecutor, RunResult
from datawarden.domain.types import Principal, RejectionReason
from datawarden.principal.budgets import Decision


def to_payload(result: RunResult) -> dict[str, Any]:
    """El resultado de una invocación, en la forma que publica el `outputSchema`.

    **`columns_masked` viaja al cliente a propósito.** Que una columna salga
    enmascarada no es un secreto: decirlo evita que quien lee la respuesta confunda
    un `***` con un dato real de la base, y le dice exactamente qué columna pedir de
    otra forma. Ocultarlo solo protegería del usuario, no del atacante.
    """
    if result.rejection is not None:
        return {"outcome": "rejected", "rejected": _rejection_dict(result.rejection)}
    rows = result.rows
    assert rows is not None
    return {
        "outcome": "rows",
        "result": {
            "columns": list(rows.columns),
            "rows": [list(row) for row in rows.rows],
            "row_count": len(rows.rows),
            "truncated": rows.truncated,
            "columns_masked": list(result.query.masked_columns) if result.query else [],
        },
    }


def _rejection_dict(rejection: RejectionReason) -> dict[str, Any]:
    return {
        "rule_id": rejection.rule_id,
        "code": rejection.code,
        "message": rejection.message,
        "suggestion": rejection.suggestion,
        "severity": rejection.severity.value,
        "position": rejection.position.value,
        "subject": rejection.subject,
        "alternative": rejection.alternative,
        "retryable": rejection.retryable,
    }


def _list_tables_payload(schema: Any) -> dict[str, Any]:
    """Las relaciones publicadas, con su grano. Es el punto de entrada al almacén."""
    published = schema.published()
    return {
        "outcome": "rows",
        "result": {
            "columns": ["table", "kind", "columns"],
            "rows": [[t.name, t.kind, len(t.columns)] for t in published.tables],
            "row_count": len(published.tables),
            "truncated": False,
            "columns_masked": [],
        },
    }


def _unknown_relation(table: str) -> dict[str, Any]:
    """El rechazo de una tabla que no está en el catálogo, con el mismo `rule_id`.

    Se responde con la forma de R004 y no con un error del protocolo porque para
    quien pregunta es exactamente el mismo suceso que si hubiera llegado por SQL, y
    darle dos formas distintas al mismo hecho obliga al cliente a tratarlo dos veces.
    """
    return {
        "outcome": "rejected",
        "rejected": {
            "rule_id": "R004",
            "code": "relation_out_of_scope",
            "message": f"relation {table.lower()} is not in the generated catalog",
            # LA SUGERENCIA NOMBRA ALGO QUE EL CLIENTE PUEDE HACER. Decir «lee el
            # recurso del catálogo» es inútil en un cliente sin lectura de recursos,
            # y así se quedó tirado un modelo real en Q-009.
            "suggestion": (
                "call describe_table with no arguments to see the tables that exist, "
                "then describe the one you need"
            ),
            "severity": "security",
            "position": "statement",
            "subject": table.lower(),
            "alternative": None,
            "retryable": True,
        },
    }


class WardenTools:
    """Las cuatro herramientas, sobre el ejecutor auditado. **No deciden nada.**

    El `principal` se recibe en el constructor y NUNCA se lee de los argumentos de
    una llamada: es la mitad ejecutable de `G-ROLE-SPOOF`. Un método que aceptara un
    `role=` en su firma sería un agujero con forma de comodidad.
    """

    def __init__(self, *, executor: AuditedExecutor, principal: Principal) -> None:
        self._executor = executor
        self._principal = principal

    def run_query(
        self,
        question_sql: str,
        question: str | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        """Valida, presupuesta, enmascara, ejecuta si procede — y audita pase lo que pase.

        `trace_id` viene del `traceparent` de `_meta`, ya validado. **Es dato y no
        autoridad**: encadena la traza con la de quien llama y no concede nada. Va al
        registro porque un evento de seguridad que no se puede correlacionar con la
        petición que lo produjo vale la mitad.
        """
        return to_payload(
            self._executor.run(
                question_sql,
                principal=self._principal,
                question=question,
                trace_id=trace_id,
            )
        )

    def budget_decision(self, question_sql: str) -> Decision | None:
        """Qué diría el presupuesto, SIN ejecutar y SIN auditar. `None` si el guard para.

        Es lo que necesita MRTR para decidir si preguntar antes de gastar. Se separa de
        `explain_cost` porque aquello devuelve una respuesta para un humano y esto un
        dato para una decisión, y mezclarlos obligaría a leer filas para tomarla.

        Usa `screen()` a propósito, que es lo que `check_mask_path.py` prohíbe en el
        ejecutor: **esto no devuelve ni una fila**, así que no hay nada que enmascarar.
        La regla no es «llama siempre a `screen_and_mask`», es «lo que devuelve datos
        pasa por el anillo 4».
        """
        from datawarden.cost.screen import screen

        result = screen(
            question_sql,
            principal=self._principal,
            schema=self._executor.schema,
            policy=self._executor.policy,
            budgets=self._executor.budgets,
            stats=self._executor.stats,
        )
        return result.decision

    def explain_cost(self, question_sql: str) -> dict[str, Any]:
        """Lo que COSTARÍA, sin ejecutar nada. Anillos 2 y 3, y ahí se para.

        Usa `screen()` a propósito, que es justo lo que `check_mask_path.py` prohíbe
        en el ejecutor. Aquí es correcto y allí no, y la diferencia es exactamente la
        que importa: **esto no devuelve ni una fila**, así que no hay nada que
        enmascarar; el ejecutor sí las devuelve, y por eso tiene que pasar por el
        anillo 4. La regla no es «llama siempre a `screen_and_mask`», es «lo que
        devuelve datos pasa por el anillo 4».

        **Límite declarado, y prefiero escribirlo que descubrirlo luego:** esto NO
        deja registro de auditoría, porque el invariante I-06 habla del camino al
        motor y esto no llega al motor. Aun así, repetir `explain_cost` con distintos
        predicados revela cómo está particionado el almacén, que es información. Si
        se decide auditarlo, hace falta un estado nuevo en
        `docs/spec/audit-record.schema.json` y `G-AUDIT-COV` pasa de cuatro estados a
        cinco: es una decisión de contrato, no un añadido, y va al buzón antes que al
        código.
        """
        from datawarden.cost.screen import screen

        result = screen(
            question_sql,
            principal=self._principal,
            schema=self._executor.schema,
            policy=self._executor.policy,
            budgets=self._executor.budgets,
            stats=self._executor.stats,
        )
        if result.rejection is not None and result.cost is None:
            # Rechazo del GUARD: ni siquiera se llegó a estimar, así que no hay coste
            # que publicar. Devolver un cero aquí sería el mismo error que cobrar
            # cero por una tabla de 4,1 GB.
            return {"outcome": "rejected", "rejected": _rejection_dict(result.rejection)}

        cost = result.cost
        assert cost is not None
        budget = self._executor.budgets.for_role(self._principal.role)
        return {
            "outcome": "rows",
            "result": {
                "columns": ["estimated_bytes", "estimated_rows", "files", "budget", "decision"],
                "rows": [
                    [
                        cost.estimated_bytes,
                        cost.estimated_rows,
                        cost.files_scanned,
                        budget.hard_bytes,
                        (result.decision or Decision.EXECUTE).value,
                    ]
                ],
                "row_count": 1,
                "truncated": False,
                "columns_masked": [],
            },
        }

    def sample_table(self, table: str, limit: int = 10) -> dict[str, Any]:
        """Unas filas sin condición. **Lo que se interpola sale del CATÁLOGO.**

        Aquí el servidor compone SQL, y componer SQL con un nombre que viene de una
        petición es la forma clásica de comerse una inyección. Se cierra por
        construcción y en dos pasos, no escapando cadenas:

        1. **El nombre de la tabla se RESUELVE contra el catálogo generado antes de
           componer nada.** Lo que acaba dentro del `SELECT` no es la cadena que
           mandó el cliente: es `found.name`, un identificador que ya estaba en
           `catalog/generated/schema.json`. Una tabla desconocida —o una con una
           subconsulta dentro— no resuelve y se va por el rechazo, sin llegar a
           componerse.
        2. Y aunque lo anterior fallara, **lo que se ejecuta es el árbol que el guard
           validó**, nunca esta cadena: R004 rechaza toda relación fuera del catálogo
           y la allowlist rechaza todo nodo desconocido. La defensa es estructural.
        """
        found = self._executor.schema.table(table.lower())
        if found is None:
            return _unknown_relation(table)
        columns = [c.name for c in found.columns if c.published]
        projection = ", ".join(columns) if columns else "*"
        # `found.name` y `columns` salen del catálogo; `limit` pasa por `int()` y el
        # `inputSchema` ya lo acota entre 1 y 100. Ningún trozo viene de la petición.
        sql = f"SELECT {projection} FROM {found.name} LIMIT {int(limit)}"  # noqa: S608
        return to_payload(self._executor.run(sql, principal=self._principal))

    def _list_tables(self) -> dict[str, Any]:
        """Las relaciones publicadas. El punto de entrada cuando no se conoce nada."""
        return _list_tables_payload(self._executor.schema)

    def describe_table(self, table: str | None = None) -> dict[str, Any]:
        """La ficha de una tabla — o, SIN ARGUMENTO, la lista de las que hay.

        **Sin el caso «sin argumento» este servidor era indescubrible**, y se vio en
        Q-009 con un cliente real: el catálogo se servía solo como recurso MCP, el
        cliente no tenía herramienta para leer recursos, y el modelo acabó adivinando
        nombres a ciegas —`customers`, `clientes`, `payments`, `pagos`— hasta rendirse.

        Lo peor no era el callejón: era que el rechazo decía *«read the catalog
        resource»*, **una acción que ese cliente no podía ejecutar**. Un mensaje
        accionable que nombra algo que no se puede hacer no es accionable, y este
        proyecto se sostiene precisamente sobre esa promesa.

        Los recursos MCP son OPCIONALES para un cliente; las herramientas no. Así que
        el camino de descubrimiento tiene que existir como herramienta. El recurso se
        queda: para quien sí lo lee, es el catálogo entero de una vez.
        """
        if table is None or not table.strip():
            return self._list_tables()
        found = self._executor.schema.table(table.lower())
        if found is None:
            return _unknown_relation(table)
        return {
            "outcome": "rows",
            "result": {
                "columns": ["column", "type", "derives_from"],
                "rows": [
                    [c.name, c.engine_type, ", ".join(c.derives_from)]
                    for c in found.columns
                    if c.published
                ],
                "row_count": sum(1 for c in found.columns if c.published),
                "truncated": False,
                "columns_masked": [],
            },
        }
