"""Data Warden · acceso conversacional a un lakehouse con cinco anillos de control.

El mapa de zonas está en `CLAUDE.md` y no es decorativo: cada paquete tiene una
regla de test distinta porque se prueba de forma distinta.

  domain/                       tipos congelados y puros. TDD obligatorio, 90 %
  guard/ (+ rules/)             el corazón. TDD PURO, 95 %, mutación 85 %
  mask/ · audit/ · principal/   deterministas. TDD obligatorio, 95 %
  cost/ · catalog/ · evalsupport/   deterministas, 90 %
  engines/ · mcp/ · http/       adaptadores: contrato y snapshot, NO cobertura
  nl2sql/ · agent/ · prompts/   se MIDEN, no se testean. TDD PROHIBIDO
"""

__version__ = "0.1.0"
