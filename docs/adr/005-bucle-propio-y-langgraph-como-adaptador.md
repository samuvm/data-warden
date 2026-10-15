# ADR-005 · Bucle de corrección propio; LangGraph como adaptador delgado

**Estado:** aceptado · 2026-09-02

## Contexto

El anillo de generación tiene un ciclo generar → validar → corregir con dos reintentos como máximo. El
diseño inicial fijaba LangGraph para ese ciclo. `G-RECOVERY` mide justo ese ciclo: la fracción de
rechazos de los que el modelo se recupera leyendo el mensaje del guard.

## Opciones

| Opción | Pros | Contras |
|---|---|---|
| A · El ciclo dentro de un grafo de LangGraph | Trazas, checkpoints e `interrupt` incluidos | La política de reintentos queda dentro del framework, y es lo que se mide; el dominio se acopla a una dependencia que cambia rápido |
| B · PydanticAI | Buen encaje con `mypy --strict` | Pensado para agentes lineales; misma objeción sobre los reintentos |
| C · Bucle propio y puro (`nl2sql/loop.py`) y un grafo de tres nodos en `agent/` que lo envuelve | El número no depende del framework; el bucle se prueba con TDD contra proveedores deterministas | Dos caminos que deben dar lo mismo |

## Decisión

Se adopta C. `run_loop` es código propio, determinista dado un `Provider`, y es lo que usan la
evaluación y los transportes. `agent/langgraph_graph.py` lo envuelve para quien quiera trazas,
checkpoints o una interrupción humana, y no decide nada. `langchain-*` no entra en `pyproject.toml` y
el dominio no importa LangGraph (I-08).

## Consecuencias

- `G-RECOVERY` mide el bucle, no la política de un framework: 0,857 (24/28), Wilson 95 % [0,69 – 0,94].
- Un test de contrato (`tests/contract/test_langgraph_adapter.py`) exige que el grafo y el bucle den el
  mismo resultado.
- El grafo termina en un `ValidatedQuery` o en un rechazo; al motor solo se llega por
  `AuditedExecutor`, y el contrato de imports lo impide desde `agent/`.
- Si se borrara `agent/`, ninguna métrica cambiaría. Señal de error: necesitar dentro del bucle algo
  que solo el framework ofrece (estado persistente entre peticiones, por ejemplo).
