# =============================================================================
# Data Warden · Makefile canónico
#
# COMPATIBLE CON GNU MAKE 3.81, que es el que trae macOS y es de 2006. Sin
# `.ONESHELL`, sin `$(file ...)`, sin `!=`. Comprobado en la línea base del
# 2026-08-28 y anotado en JOURNAL.md, porque descubrirlo a mitad de la fase 2
# habría costado una tarde.
#
# REGLA DE ESTE FICHERO: un target que todavía no se puede cumplir FALLA con un
# mensaje que dice por qué. No pasa en vacío. Un gate que aprueba porque no
# encuentra nada que medir es peor que no tener gate: da una señal verde falsa.
# =============================================================================

SHELL := /bin/bash
UV    := uv run
PKG   := src/datawarden
PROFILE ?= dev

.DEFAULT_GOAL := help
.PHONY: help up down warm lint typecheck imports test-fast test test-int \
        coverage secrets eval eval-refresh eval-recovery eval-toolchoice \
        bench bench-guard cost-calibration mutation \
        attack-dev attack-holdout attack-mut pii-suite \
        mcp-conformance test-parity gate-fast gate-full done report clean \
        dataset dataset-full dataset-traps contracts catalog arch-checks goals guard-property \
        statistics budget-invariant

# -----------------------------------------------------------------------------
help:
	@echo "Data Warden · fase 0"
	@echo ""
	@echo "  Funciona hoy:"
	@echo "    lint typecheck imports test-fast test coverage secrets"
	@echo "    contracts catalog arch-checks goals gate-fast gate-full done"
	@echo "    dataset dataset-full report clean"
	@echo ""
	@echo "  Declarado y todavía sin implementar (falla con su motivo):"
	@echo "    up down warm test-int eval eval-refresh eval-recovery eval-toolchoice"
	@echo "    bench bench-guard cost-calibration mutation attack-dev attack-holdout"
	@echo "    attack-mut pii-suite mcp-conformance test-parity"

# --- infraestructura ---------------------------------------------------------
up down warm:
	@echo "FASE 0: no hay compose.yaml todavía. DuckDB es embebido y no necesita"
	@echo "levantar nada; el contenedor de exploración está en datagen/docker/."
	@exit 1

# --- calidad estática --------------------------------------------------------
lint:
	$(UV) ruff check src/ tests/ scripts/ datagen/
	$(UV) ruff format --check src/ tests/ scripts/ datagen/

typecheck:
	$(UV) mypy src/

# Los límites entre capas del mapa de docs/RULES.md, ejecutables. Sin esto, "el
# dominio no depende del transporte" es una frase del README.
imports:
	$(UV) lint-imports

# --- pruebas -----------------------------------------------------------------
# `-p no:randomly` no hace falta; lo que sí importa es que NUNCA aparezcan aquí
# `-k`, `--deselect`, `-x`, `--no-cov`, `skip` ni `xfail`: están prohibidos por
# docs/RULES.md porque son las cuatro formas de esquivar la suite sin que se note.
test-fast:
	$(UV) pytest tests/unit tests/property -m "not slow and not integration" \
		-p no:cacheprovider --hypothesis-profile=dev

test:
	$(UV) pytest tests/unit tests/property tests/contract --hypothesis-profile=gate

test-int:
	@echo "FASE 0: no hay tests de integración todavía (necesitan el motor y el catálogo)."
	@exit 1

# `--cov-context=test` NO es opcional: es lo que hace medible "un test por
# función" (CONSTITUCION §2.6). Sin él, G-COV-FUNC no se puede calcular y el check
# sale rojo por falta de medida, que es lo correcto.
# `tests/integration` ENTRA aquí y no en el gate rápido. El motivo es honestidad:
# tres funciones del catálogo solo se pueden ejercitar contra DuckDB (I-13 prohíbe
# el motor en tests/unit), así que medir la cobertura sin ellas daría un número que
# solo se puede cumplir escribiendo tests que no tocan lo que importa.
coverage:
	$(UV) pytest tests/unit tests/property tests/contract tests/integration \
		--cov --cov-context=test --cov-report=term-missing \
		--cov-report=json:evals/reports/coverage-contexts.json
	$(UV) python scripts/check_gate_config.py
	$(UV) python scripts/check_function_coverage.py
	$(UV) python scripts/check_line_coverage.py

# Cero hallazgos NUEVOS. La línea base NO se regenera para silenciar un hallazgo:
# se resuelve el hallazgo. Los cuatro que hay dentro son falsos positivos
# auditados uno a uno y anotados en JOURNAL.md el 2026-09-02.
secrets:
	$(UV) python scripts/check_secrets.py

# --- contratos, catálogo y metas ---------------------------------------------
# Compila los YAML firmados de docs/spec/ a los JSON que consume src/. El dominio
# no parsea YAML: ver el encabezado de scripts/compile_contracts.py y P-002.
contracts:
	$(UV) python scripts/compile_contracts.py
	$(UV) python scripts/check_contracts.py

# El catálogo se GENERA (I-07). Nunca se escribe a mano.
catalog:
	$(UV) warden catalog build
	$(UV) python scripts/check_catalog_fresh.py

# Las estadísticas salen de los MANIFIESTOS de Iceberg, sin leer una sola fila:
# contar 66,6 M de filas tarda 0,5 s porque se lee el metadato. Perfil `full`, que es
# el dataset publicado y el que calibra los presupuestos.
statistics:
	$(UV) python -c "import pathlib; from datawarden.catalog import statistics as S; \
	  st = S.build_from_iceberg(pathlib.Path('datagen/out/full/iceberg'), 'full'); \
	  pathlib.Path('src/datawarden/catalog/generated/statistics.json').write_text(S.to_json(st)); \
	  print(f'estadisticas: {len(st.tables)} tablas')"

# Los checks de arquitectura que cuestan milisegundos y entran en el gate B.
arch-checks:
	$(UV) python scripts/check_gate_config.py
	$(UV) python scripts/check_no_raw_sql.py
	$(UV) python scripts/check_contracts.py
	$(UV) python scripts/check_catalog_fresh.py
	$(UV) python scripts/check_resultset_eq.py
	$(UV) python scripts/check_failclosed.py
	$(UV) python scripts/check_role_source.py
	$(UV) python scripts/check_rule_coverage.py
	$(UV) python scripts/check_rules_registry.py
	$(UV) python scripts/check_attack_coverage.py

goals:
	$(UV) python scripts/goals_check.py --milestone $(MILESTONE)

# --- evaluación --------------------------------------------------------------
eval eval-refresh eval-recovery eval-toolchoice:
	@echo "FASE 8: la evaluación necesita el banco de 60 preguntas (Q-010) y el"
	@echo "ciclo NL->SQL, que es fase 6. Nada de esto existe."
	@exit 1

# --- rendimiento -------------------------------------------------------------
# AVISO: D-03 dice UN proyecto encendido cada vez. Un p95 medido con otro
# proyecto compitiendo por la memoria NO vale y hay que repetirlo.
bench bench-guard:
	$(UV) python scripts/bench_guard.py

cost-calibration:
	$(UV) python scripts/cost_calibration.py --profile $(PROFILE)

# `G-BUDGET-ESCAPE`: el invariante por contador y el número por reloj.
budget-invariant:
	$(UV) python scripts/check_budget_invariant.py

# Lo único que distingue cobertura de verificación. `mutmut run` tarda ~40 s con
# ocho hijos; el check lee sus resultados y los compara con los dos umbrales.
mutation:
	$(UV) mutmut run --max-children 8
	$(UV) python scripts/check_mutation.py

# --- seguridad ---------------------------------------------------------------
# HIGIENE, NO EVIDENCIA. Las reglas se escribieron para parar estos casos: el
# número de `attack-dev` NO se publica como métrica de seguridad.
attack-dev:
	$(UV) python scripts/attack_dev.py

# La otra mitad del número publicable: >= 2.000 mutantes de AST, cero evasiones.
attack-mut:
	$(UV) python scripts/attack_mut.py

# La RESERVA. El agente NO la lee: la ejecuta y mira el veredicto.
attack-holdout:
	$(UV) python scripts/attack_holdout.py

# El guard, ejercitado por sus propiedades: fail-closed y allowlist.
guard-property:
	$(UV) python scripts/check_guard_property.py

# G-PII-LEAK, y es un AXIOMA. Se EJECUTA contra el dataset: la fuga se mide con datos
# delante, no razonando sobre el arbol.
pii-suite:
	$(UV) python scripts/pii_suite.py

mcp-conformance test-parity:
	@echo "FASE 7 y 9: no hay servidor MCP ni segundo motor todavía."
	@exit 1

# Las nueve trampas del glosario, MEDIDAS. Nace de la corrección G-5 de la firma
# de Q-004: «una trampa cuyo número no se ha vuelto a medir desde que se escribió
# es folclore». Hoy salen 7 de 9; las dos que discrepan están en la propuesta P-003.
dataset-traps:
	$(UV) python scripts/measure_traps.py --profile $(PROFILE)

# --- el dataset sintético ----------------------------------------------------
dataset:
	./datagen/run.sh $(PROFILE)

dataset-full:
	./datagen/run.sh full

# --- puertas -----------------------------------------------------------------
# gate-fast: lo que tiene que pasar en cada turno. Segundos, no minutos.
gate-fast: lint typecheck test-fast
	@echo ""
	@echo "gate-fast VERDE"

# gate-full: lo que tiene que pasar para cerrar una fase.
# gate-full: lo que tiene que pasar para cerrar una fase, sin el peso de `done`.
# No incluye mutación ni snapshot: eso es `done`, y mezclarlos haría que nadie
# corriera gate-full por lo que tarda.
gate-full: lint typecheck imports arch-checks test coverage secrets attack-dev attack-mut
	@echo ""
	@echo "gate-full VERDE"

# La ÚNICA definición de "hecho" (CONSTITUCION §5). Doce condiciones, parando en
# la primera que falla, y el resultado en .claude/state/gate-status.json, que lo
# escribe el GATE y no el agente.
done:
	@test -n "$(MILESTONE)" || { echo "falta MILESTONE=N"; exit 2; }
	$(UV) python scripts/done.py --milestone $(MILESTONE)

# --- varios ------------------------------------------------------------------
report:
	$(UV) python datagen/report.py --data datagen/out/$(PROFILE) \
		--db datagen/out/cierzo-$(PROFILE).duckdb \
		--out datagen/MEASURED-$(PROFILE).md

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache .hypothesis htmlcov \
	       coverage.xml .coverage mutants
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} + 2>/dev/null || true
	@echo "limpiado (datagen/out/ NO se toca: son horas de generación)"
