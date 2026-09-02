"""El registro de reglas. Explícito, ordenado e inmutable.

**Se importa regla a regla y no con un `glob`.** Un descubrimiento automático
tendría dos consecuencias que este proyecto no puede permitirse: el orden dependería
del sistema de ficheros —y el orden ES semántica, porque decide qué regla da el
mensaje— y una regla se caería del conjunto sin que nada lo dijera, con solo
renombrar un fichero. Aquí, quitar una regla es borrar una línea que se ve en el
diff, y `scripts/check_rules_registry.py` además lo prohíbe (I-01).

**EL ORDEN NO ES ALFABÉTICO, ES DE ESPECIFICIDAD.** La primera regla que rechaza es
la que da el mensaje, así que la que corre antes tiene que ser la que mejor explica
lo que pasó. Tres decisiones concretas:

- **R013 primero**: acota el tamaño del árbol que las otras trece van a recorrer. Si
  corriera después, una bomba de AST agotaría el presupuesto antes de llegar a ella.
- **R010 antes que R001**: un `DELETE` es un `DELETE`, esté arriba o escondido en una
  CTE. Si R001 corriera antes, la CTE que esconde una escritura recibiría el mensaje
  «solo se admite SELECT» sobre una consulta que empieza por SELECT.
- **R014 antes que R004**: `information_schema` existe. Decirle a alguien que «esa
  tabla no existe» cuando sí existe enseña a insistir; R014 dice lo que pasa.
- **R008 antes que R012**: las dos rechazan `PARTITION BY birth_date` para
  `analyst`, y las dos tienen razón. Pero R008 sabe además que la política publica
  `age_band` como salida, y lo dice; R012 solo puede decir «ese grupo es una
  persona». Lo detectó el cuaderno de ataque, que exige que cada caso lo pare la
  regla que lo explica mejor. Con este orden, `GROUP BY national_id` con rol ADMIN
  sigue cayendo en R012, que es lo correcto: R008 no tiene nada que objetar cuando
  el rol puede ver la columna, y aun así agregarla hasta aislarla es otra cosa.
- **R006 el último**: es el único que reescribe casi siempre, y recorta contra el
  `max_rows` del rol. Recortar antes habría hecho que las demás reglas razonaran
  sobre un árbol distinto del que se escribió.
"""

from __future__ import annotations

from typing import Final

from datawarden.guard.rule import POST_QUALIFY, PRE_QUALIFY, Rule
from datawarden.guard.rules import (
    r001_single_read_statement,
    r002_node_allowlist,
    r003_function_allowlist,
    r004_catalog_scope,
    r005_join_predicate,
    r006_row_limit,
    r007_nesting_depth,
    r008_column_policy,
    r009_star_expansion,
    r010_no_write_node,
    r011_set_operation_scope,
    r012_group_cardinality,
    r013_tree_size,
    r014_system_schema,
)

#: Las catorce, en el orden en que corren.
RULES: Final[tuple[Rule, ...]] = (
    r013_tree_size.RULE,
    r010_no_write_node.RULE,
    r001_single_read_statement.RULE,
    r014_system_schema.RULE,
    r004_catalog_scope.RULE,
    r002_node_allowlist.RULE,
    r003_function_allowlist.RULE,
    r009_star_expansion.RULE,
    r005_join_predicate.RULE,
    r007_nesting_depth.RULE,
    r011_set_operation_scope.RULE,
    r008_column_policy.RULE,
    r012_group_cardinality.RULE,
    r006_row_limit.RULE,
)

PRE_RULES: Final = tuple(r for r in RULES if r.phase == PRE_QUALIFY)
POST_RULES: Final = tuple(r for r in RULES if r.phase == POST_QUALIFY)

#: `rule_id` -> regla. Lo usan el corpus parametrizado y los checks de arquitectura.
BY_ID: Final[dict[str, Rule]] = {rule.rule_id: rule for rule in RULES}

#: Toda familia de ataque que alguna regla dice parar (I-14).
FAMILIES: Final[frozenset[str]] = frozenset(f for rule in RULES for f in rule.families)
