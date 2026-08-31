"""Configuración compartida de la suite.

Los tres perfiles de Hypothesis que `CLAUDE.md` declara se registran AQUÍ. Sin
esto, `--hypothesis-profile=dev` no falla con un mensaje claro: revienta la
ejecución entera con un INTERNALERROR, que es la peor forma de descubrir una
configuración ausente porque no se parece a un fallo de test.
"""

from __future__ import annotations

from hypothesis import HealthCheck, Verbosity, settings

# 25 ejemplos. Lo que corre en cada turno: tiene que caber en segundos.
settings.register_profile(
    "dev",
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# 100 ejemplos. Lo que exige el gate para cerrar un turno de trabajo.
settings.register_profile(
    "gate",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# 1000 ejemplos. Búsqueda nocturna: aquí es donde aparecen los contraejemplos
# que 25 no encuentran, y por eso el perfil existe aunque nadie lo mire a diario.
settings.register_profile(
    "nightly",
    max_examples=1000,
    deadline=None,
    verbosity=Verbosity.verbose,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.load_profile("dev")
