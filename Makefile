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
        dataset dataset-full

# -----------------------------------------------------------------------------
help:
	@echo "Data Warden · fase 0"
	@echo ""
	@echo "  Funciona hoy:"
	@echo "    lint typecheck imports test-fast test coverage secrets gate-fast"
	@echo "    dataset dataset-full report clean"
	@echo ""
	@echo "  Declarado y todavía sin implementar (falla con su motivo):"
	@echo "    up down warm test-int eval eval-refresh eval-recovery eval-toolchoice"
	@echo "    bench bench-guard cost-calibration mutation attack-dev attack-holdout"
	@echo "    attack-mut pii-suite mcp-conformance test-parity gate-full done"

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

# Los límites entre capas del mapa de CLAUDE.md, ejecutables. Sin esto, "el
# dominio no depende del transporte" es una frase del README.
imports:
	$(UV) lint-imports

# --- pruebas -----------------------------------------------------------------
# `-p no:randomly` no hace falta; lo que sí importa es que NUNCA aparezcan aquí
# `-k`, `--deselect`, `-x`, `--no-cov`, `skip` ni `xfail`: están prohibidos por
# CLAUDE.md porque son las cuatro formas de esquivar la suite sin que se note.
test-fast:
	$(UV) pytest tests/unit tests/property -m "not slow and not integration" \
		-p no:cacheprovider --hypothesis-profile=dev

test:
	$(UV) pytest tests/unit tests/property tests/contract --hypothesis-profile=gate

test-int:
	@echo "FASE 0: no hay tests de integración todavía (necesitan el motor y el catálogo)."
	@exit 1

coverage:
	$(UV) pytest tests/unit tests/property tests/contract \
		--cov --cov-report=term-missing --cov-report=xml
	$(UV) python scripts/check_gate_config.py

secrets:
	$(UV) detect-secrets scan --baseline .secrets.baseline

# --- evaluación --------------------------------------------------------------
eval eval-refresh eval-recovery eval-toolchoice:
	@echo "FASE 8: la evaluación necesita el banco de 60 preguntas (Q-010) y el"
	@echo "ciclo NL->SQL, que es fase 6. Nada de esto existe."
	@exit 1

# --- rendimiento -------------------------------------------------------------
bench bench-guard:
	@echo "FASE 2: el guard no existe todavía, así que no hay p95 que medir."
	@echo "AVISO cuando exista: D-03 dice UN proyecto encendido cada vez. Un p95"
	@echo "medido con otro proyecto compitiendo por la memoria NO vale."
	@exit 1

cost-calibration:
	@echo "FASE 3: el estimador de coste no existe."
	@exit 1

mutation:
	@echo "FASE 3: la mutación se mide cuando hay reglas del guard que mutar."
	@exit 1

# --- seguridad ---------------------------------------------------------------
attack-dev attack-mut pii-suite:
	@echo "FASE 2: el cuaderno de ataque necesita el guard."
	@exit 1

attack-holdout:
	@echo "FASE 2: el holdout lo escribe el subagente qa-adversario, y Q-005 sigue"
	@echo "PENDIENTE. Recuerda: el agente NO puede leer tests/holdout/."
	@exit 1

mcp-conformance test-parity:
	@echo "FASE 7 y 9: no hay servidor MCP ni segundo motor todavía."
	@exit 1

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
gate-full:
	@echo "FASE 0: gate-full exige metas que todavía no son medibles."
	@echo ""
	@echo "  G-CATALOG-FRESH  necesita catalog/introspect.py"
	@echo "  G-COV-LINE       necesita código en los 8 paquetes testables"
	@echo "  G-COV-FUNC       ídem"
	@echo "  G-SECRETS        necesita .secrets.baseline"
	@echo ""
	@echo "Se implementa al final de la fase 0, cuando haya algo que medir."
	@echo "Un gate que aprueba porque no encuentra nada es una señal verde falsa."
	@exit 1

done:
	@echo "make done es la ÚNICA definición de 'hecho' y todavía no existe."
	@echo "Requiere gate-full, y gate-full requiere la fase 0 terminada."
	@exit 1

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
