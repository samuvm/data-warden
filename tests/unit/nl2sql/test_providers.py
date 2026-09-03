"""Los proveedores. **Lo que se prueba es el CÓDIGO, nunca la salida del modelo.**

Aquí hay tres cosas deterministas y las tres deciden si un número es reproducible:
la clave de la caché grabada, el fallo de caché, y qué se considera «el SQL» dentro
de lo que un modelo contesta. Ninguna de las tres depende de lo que el modelo diga,
así que las tres se prueban.
"""

from __future__ import annotations

import pathlib

import pytest

from datawarden.domain.types import Position, RejectionReason, Severity
from datawarden.nl2sql.providers import (
    RecordedProvider,
    Request,
    ScriptedProvider,
    extract_sql,
)


def _rechazo(code: str = "column_policy") -> RejectionReason:
    return RejectionReason(
        rule_id="R008",
        code=code,
        message="column dim_customer.birth_date is masked and appears in a WHERE predicate",
        suggestion="use dim_customer.age_band instead",
        severity=Severity.POLICY,
        position=Position.WHERE,
        subject="dim_customer.birth_date",
    )


# ------------------------------------------------------ la clave de la caché ---


def test_dos_peticiones_iguales_tienen_la_misma_clave() -> None:
    assert (
        Request(question="cuantos clientes").cache_key()
        == Request(question="cuantos clientes").cache_key()
    )


def test_cambiar_la_version_del_prompt_cambia_la_clave() -> None:
    """**Es lo que impide que una medida vieja pase por una medida del prompt nuevo.**

    Un prompt cambia el número de `G-RECOVERY` sin cambiar una línea de lógica. Si la
    caché no dependiera de él, se podría reescribir el prompt entero y seguir
    publicando el número de antes.
    """
    uno = Request(question="cuantos", prompt_version="1").cache_key()
    dos = Request(question="cuantos", prompt_version="2").cache_key()

    assert uno != dos


def test_cambiar_la_consulta_anterior_cambia_la_clave() -> None:
    """Dos correcciones de consultas distintas son peticiones DISTINTAS.

    El modelo ve un texto distinto y contesta otra cosa. Si compartieran clave, la
    casete de la primera pasaría por respuesta de la segunda y el número saldría de
    una petición que nunca se hizo.
    """
    uno = Request(question="edad", rejection=_rechazo(), previous_sql="SELECT a")
    dos = Request(question="edad", rejection=_rechazo(), previous_sql="SELECT b")

    assert uno.cache_key() != dos.cache_key()


def test_cambiar_el_rechazo_cambia_la_clave() -> None:
    uno = Request(question="edad", rejection=_rechazo("column_policy")).cache_key()
    dos = Request(question="edad", rejection=_rechazo("row_limit")).cache_key()

    assert uno != dos


# ------------------------------------------------------- el fallo de caché ---


def test_un_fallo_de_cache_es_un_error_y_no_una_llamada_al_modelo(
    tmp_path: pathlib.Path,
) -> None:
    """`make eval-recovery` es determinista y gratis A PROPÓSITO.

    Si al no encontrar la entrada se cayera al modelo local, dejaría de serlo sin
    avisar, y el número saldría de una mezcla de grabado y generado que nadie podría
    reproducir en otra máquina.
    """
    provider = RecordedProvider(directory=tmp_path)

    with pytest.raises(KeyError, match="eval-refresh"):
        provider.generate(Request(question="nada grabado"))


def test_lo_grabado_se_lee_por_su_clave(tmp_path: pathlib.Path) -> None:
    provider = RecordedProvider(directory=tmp_path)
    peticion = Request(question="cuantos clientes hay")

    provider.record(peticion, "SELECT count(*) AS n FROM dim_customer", model="qwen3.5:9b-mlx")

    assert provider.generate(peticion) == "SELECT count(*) AS n FROM dim_customer"


def test_la_grabacion_dice_de_que_modelo_salio(tmp_path: pathlib.Path) -> None:
    """Una casete sin decir de qué modelo salió no se puede auditar.

    Y mezclar casetes de dos modelos produciría un número que no es de ninguno.
    """
    provider = RecordedProvider(directory=tmp_path)
    peticion = Request(question="cuantos")

    ruta = provider.record(peticion, "SELECT 1 AS n", model="qwen3.5:9b-mlx")

    assert '"model": "qwen3.5:9b-mlx"' in ruta.read_text(encoding="utf-8")


# ------------------------------------------------ qué se considera «el SQL» ---


@pytest.mark.parametrize(
    ("crudo", "esperado"),
    [
        ("SELECT 1 AS n", "SELECT 1 AS n"),
        ("  SELECT 1 AS n  ", "SELECT 1 AS n"),
        ("```sql\nSELECT 1 AS n\n```", "SELECT 1 AS n"),
        ("```\nSELECT 1 AS n\n```", "SELECT 1 AS n"),
        ("Aquí tienes:\n```sql\nSELECT 1 AS n\n```\nEso es todo.", "SELECT 1 AS n"),
        ("SELECT 1 AS n;", "SELECT 1 AS n"),
        ("<think>pienso mucho</think>\nSELECT 1 AS n", "SELECT 1 AS n"),
    ],
)
def test_el_envoltorio_del_modelo_no_es_lo_que_se_mide(crudo: str, esperado: str) -> None:
    """Un bloque de código delante no es un fallo de recuperación: es formato.

    Si el envoltorio llegara al guard, R001 lo rechazaría por no parsear y
    `G-RECOVERY` mediría cuántas veces el modelo obedece el formato en vez de
    cuántas veces se corrige. Son dos cosas y solo una es la tesis del proyecto.
    """
    assert extract_sql(crudo) == esperado


def test_el_limpiador_no_arregla_el_sql_solo_lo_desenvuelve() -> None:
    """**Un limpiador que «ayudara» al modelo estaría regalando puntos a la métrica.**

    Quien decide si una consulta se ejecuta es el guard. Si esto le añadiera un
    `LIMIT` o le quitara una columna, la recuperación medida sería la del limpiador.
    """
    crudo = "```sql\nSELECT birth_date FROM dim_customer\n```"

    assert extract_sql(crudo) == "SELECT birth_date FROM dim_customer"


# --------------------------------------------------------- el de los tests ---


def test_el_provider_de_guion_guarda_lo_que_recibio() -> None:
    """La mitad de lo que hay que comprobar del bucle es qué se le pasó al siguiente."""
    provider = ScriptedProvider(["a", "b"])

    provider.generate(Request(question="uno"))
    provider.generate(Request(question="dos"))

    assert [r.question for r in provider.recibido] == ["uno", "dos"]


def test_el_provider_de_guion_sin_respuestas_devuelve_vacio() -> None:
    assert ScriptedProvider([]).generate(Request(question="x")) == ""
