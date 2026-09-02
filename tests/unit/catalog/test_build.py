"""`catalog/build.py`, el módulo que ESCRIBE el catálogo generado.

Tenía 35 mutantes y CERO tests: 0,00 % de mutación, el peor módulo del proyecto y
el único con «sin tests» en `evals/reports/mutation.json`. La cobertura de línea de
`catalog/` era del 99,23 % al mismo tiempo, que es exactamente la diferencia que
`G-MUTATION` existe para encontrar: el código se ejecutaba de paso y nadie asertaba
sobre lo que hacía.

**Ni una línea de estos tests toca DuckDB.** I-13 y el criterio de salida de la
fase 0 lo exigen —«unit de `catalog/` contra esquema fixture en memoria»— y aquí se
cumple sustituyendo `introspect_duckdb` por un espía que registra con qué se le
llamó. Lo que `generate()` tiene que acertar no es leer un motor: es traducir dos
contratos compilados a los dos argumentos correctos, y eso no necesita una fila.

Lo que se fija aquí, y por qué cada cosa:

* **Las tres claves de contrato son parte del comportamiento.**
  `excluded_from_catalog`, `deprecated` y `reason` son el acuerdo con
  `scripts/compile_contracts.py`. Un mutante que renombre cualquiera de las tres
  produce un catálogo que publica columnas que la política excluye, y eso es una
  fuga que ningún test de línea ve.
* **`write()` promete que lo devuelto es lo que quedó en disco.** El docstring del
  módulo dice que `warden catalog build` y `check_catalog_fresh.py` tienen que
  producir EXACTAMENTE el mismo texto porque el segundo compara el sha del primero.
  Si el valor de retorno y el fichero divergen, `G-CATALOG-FRESH` compara dos cosas
  distintas y pasa igual.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from datawarden.catalog import SCHEMA_PATH, ColumnRow, build_schema, to_json
from datawarden.catalog import build as build_mod
from datawarden.catalog.types import CatalogSchema

# Un esquema mínimo pero real: dos tablas, tipos de tres familias distintas y una
# columna que la política excluye. Suficiente para que `to_json` produzca texto no
# trivial y para que un mutante que rompa el orden o la exclusión se note.
_FILAS = (
    ColumnRow("dim_customer", "view", "customer_sk", "INTEGER", False, 1),
    ColumnRow("dim_customer", "view", "email", "VARCHAR", True, 2),
    ColumnRow("dim_merchant", "view", "merchant_sk", "INTEGER", False, 1),
    ColumnRow("dim_merchant", "view", "traffic_weight", "DOUBLE", True, 2),
)


def _esquema() -> CatalogSchema:
    return build_schema(_FILAS, dialect="duckdb")


# --------------------------------------------------------------- _load_json ---


def test_load_json_devuelve_el_objeto_del_fichero(tmp_path: pathlib.Path) -> None:
    ruta = tmp_path / "x.json"
    ruta.write_text('{"a": 1, "b": [2, 3]}', encoding="utf-8")

    assert build_mod._load_json(ruta) == {"a": 1, "b": [2, 3]}


def test_load_json_lee_utf8_y_no_la_codificacion_del_sistema(
    tmp_path: pathlib.Path,
) -> None:
    """Los contratos llevan prosa en español: `reason` está lleno de acentos.

    Un mutante que cambie `encoding="utf-8"` sobrevive con un fixture ASCII y
    rompe en producción sobre el `overlay.json` real, que empieza por «Columna
    heredada de la primera ingesta».
    """
    ruta = tmp_path / "acentos.json"
    ruta.write_text('{"reason": "discrepa en el 0,4 % · «así»"}', encoding="utf-8")

    assert build_mod._load_json(ruta) == {"reason": "discrepa en el 0,4 % · «así»"}


def test_load_json_propaga_el_fallo_de_un_contrato_ilegible(
    tmp_path: pathlib.Path,
) -> None:
    """Fail-loud, no fail-silent: un contrato roto NO puede degradar a `{}`.

    Devolver un diccionario vacío aquí generaría un catálogo sin ninguna columna
    excluida —o sea, publicándolas todas— y el `make done` siguiente pasaría.
    """
    ruta = tmp_path / "roto.json"
    ruta.write_text("{no soy json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        build_mod._load_json(ruta)


# ----------------------------------------------------------------- generate ---


class _Espia:
    """Sustituye a `introspect_duckdb` y guarda con qué se le llamó."""

    def __init__(self, devuelve: CatalogSchema) -> None:
        self.devuelve = devuelve
        self.llamadas: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> CatalogSchema:
        self.llamadas.append((args, kwargs))
        return self.devuelve


@pytest.fixture
def espia(monkeypatch: pytest.MonkeyPatch) -> _Espia:
    doble = _Espia(_esquema())
    monkeypatch.setattr(build_mod, "introspect_duckdb", doble)
    return doble


def _contratos(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    *,
    policy: dict[str, Any],
    overlay: dict[str, Any],
) -> None:
    p = tmp_path / "policy.json"
    o = tmp_path / "overlay.json"
    p.write_text(json.dumps(policy), encoding="utf-8")
    o.write_text(json.dumps(overlay), encoding="utf-8")
    monkeypatch.setattr(build_mod, "POLICY_PATH", p)
    monkeypatch.setattr(build_mod, "OVERLAY_PATH", o)


def test_generate_pasa_la_base_de_datos_tal_cual(
    espia: _Espia, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    _contratos(
        monkeypatch,
        tmp_path,
        policy={"excluded_from_catalog": []},
        overlay={"deprecated": {}},
    )
    db = tmp_path / "cierzo.duckdb"

    build_mod.generate(db)

    ((args, _),) = espia.llamadas
    assert args == (db,)


def test_generate_traduce_excluded_from_catalog_a_excluded_columns(
    espia: _Espia, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """La clave del contrato es comportamiento, no detalle.

    Si un mutante renombra `excluded_from_catalog`, `generate` levanta `KeyError`
    (ver el test siguiente) o publica una columna que la política excluye.
    """
    _contratos(
        monkeypatch,
        tmp_path,
        policy={"excluded_from_catalog": ["dim_merchant.traffic_weight"]},
        overlay={"deprecated": {}},
    )

    build_mod.generate(tmp_path / "cierzo.duckdb")

    ((_, kwargs),) = espia.llamadas
    assert kwargs["excluded_columns"] == ["dim_merchant.traffic_weight"]


def test_generate_aplana_deprecated_a_columna_y_motivo(
    espia: _Espia, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """`{col: {"reason": ...}}` se aplana a `{col: motivo}`, no se pasa entero.

    Mata a la vez el mutante de la clave `deprecated`, el de la clave `reason` y
    cualquiera que devuelva el diccionario anidado sin aplanar.
    """
    _contratos(
        monkeypatch,
        tmp_path,
        policy={"excluded_from_catalog": []},
        overlay={
            "deprecated": {
                "fact_payment_attempt.amount_cents": {
                    "reason": "discrepa en el 0,4 %",
                    "since": "2026-01-01",
                },
                "dim_customer.legacy_id": {"reason": "sustituida por customer_sk"},
            }
        },
    )

    build_mod.generate(tmp_path / "cierzo.duckdb")

    ((_, kwargs),) = espia.llamadas
    assert kwargs["deprecated_columns"] == {
        "fact_payment_attempt.amount_cents": "discrepa en el 0,4 %",
        "dim_customer.legacy_id": "sustituida por customer_sk",
    }


@pytest.mark.parametrize(
    ("policy", "overlay"),
    [
        ({}, {"deprecated": {}}),
        ({"excluded_from_catalog": []}, {}),
        ({"excluded_from_catalog": []}, {"deprecated": {"a.b": {}}}),
    ],
    ids=["falta_excluded", "falta_deprecated", "falta_reason"],
)
def test_generate_falla_ruidosamente_si_el_contrato_no_trae_su_clave(
    espia: _Espia,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    policy: dict[str, Any],
    overlay: dict[str, Any],
) -> None:
    """Un contrato incompleto es un fallo, nunca un valor por defecto vacío.

    `.get(clave, [])` aquí sería el mutante más peligroso del módulo: produciría
    un catálogo que publica todas las columnas excluidas, en silencio y en verde.
    """
    _contratos(monkeypatch, tmp_path, policy=policy, overlay=overlay)

    with pytest.raises(KeyError):
        build_mod.generate(tmp_path / "cierzo.duckdb")


def test_generate_devuelve_lo_que_devuelve_la_introspeccion(
    espia: _Espia, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    _contratos(
        monkeypatch,
        tmp_path,
        policy={"excluded_from_catalog": []},
        overlay={"deprecated": {}},
    )

    assert build_mod.generate(tmp_path / "cierzo.duckdb") is espia.devuelve


# -------------------------------------------------------------------- write ---


def test_write_devuelve_exactamente_el_texto_que_quedo_en_disco(
    tmp_path: pathlib.Path,
) -> None:
    """La promesa del módulo, y la que sostiene `G-CATALOG-FRESH`.

    `check_catalog_fresh.py` compara el sha de lo que se generó contra el sha de
    lo que hay en disco. Si el retorno y el fichero divergen, compara dos cosas
    distintas y da verde de todos modos.
    """
    destino = tmp_path / "schema.json"

    devuelto = build_mod.write(_esquema(), destino)

    assert devuelto == destino.read_text(encoding="utf-8")


def test_write_serializa_con_to_json_y_no_con_otro_volcado(
    tmp_path: pathlib.Path,
) -> None:
    """Determinista por obligación: claves ordenadas y salto de línea final."""
    esquema = _esquema()
    destino = tmp_path / "schema.json"

    assert build_mod.write(esquema, destino) == to_json(esquema)


def test_write_crea_los_directorios_que_falten(tmp_path: pathlib.Path) -> None:
    """Mata `parents=True` -> `False`: `generated/` no existe en un clon limpio."""
    destino = tmp_path / "no" / "existe" / "todavia" / "schema.json"

    build_mod.write(_esquema(), destino)

    assert destino.is_file()


def test_write_no_protesta_si_el_directorio_ya_estaba(tmp_path: pathlib.Path) -> None:
    """Mata `exist_ok=True` -> `False`: regenerar el catálogo es lo normal."""
    destino = tmp_path / "generated" / "schema.json"
    destino.parent.mkdir()

    build_mod.write(_esquema(), destino)

    assert destino.is_file()


def test_write_sobrescribe_en_vez_de_anadir(tmp_path: pathlib.Path) -> None:
    """Dos generaciones seguidas dejan un fichero, no dos catálogos pegados."""
    destino = tmp_path / "schema.json"
    destino.write_text("basura previa que tiene que desaparecer\n", encoding="utf-8")

    devuelto = build_mod.write(_esquema(), destino)

    assert destino.read_text(encoding="utf-8") == devuelto
    assert "basura previa" not in devuelto


def test_write_escribe_utf8(tmp_path: pathlib.Path) -> None:
    """`to_json` usa `ensure_ascii=False`, así que el fichero lleva acentos de verdad.

    Un mutante en el `encoding` de `write_text` rompe sobre el catálogo real, que
    arrastra los motivos en prosa española de `overlay.json`.
    """
    esquema = build_schema(
        _FILAS,
        dialect="duckdb",
        deprecated_columns={"dim_customer.email": "obsoleta · usa «contacto»"},
    )
    destino = tmp_path / "schema.json"

    build_mod.write(esquema, destino)

    assert "obsoleta · usa «contacto»" in destino.read_text(encoding="utf-8")


def test_write_apunta_por_defecto_al_catalogo_generado_del_paquete() -> None:
    """Mata el mutante del argumento por defecto sin escribir en el repo.

    Un `write()` sin ruta tiene que ir a `catalog/generated/schema.json` y a
    ningún otro sitio: es lo que hace que `warden catalog build` y el check de
    frescura miren el mismo fichero.
    """
    import inspect

    assert inspect.signature(build_mod.write).parameters["path"].default == SCHEMA_PATH
