"""`G-OTEL-ATTRS` · los atributos emitidos cumplen el contrato OTel GenAI pineado.

Nivel 3, contrato. Lo que se comprueba sale de `docs/CONTRACTS/otel-genai.md`, que es una
copia literal de `_comun/` y un acuerdo con los proyectos 01, 02 y 04: si aquí se inventa
un nombre, lo que se rompe es un panel del 02, y se rompe en silencio.

Cuatro propiedades, y las cuatro se comprueban **sobre lo que se emite**, no sobre el
código que lo emite:

1. **Están todos los obligatorios.** §4 da la lista literal.
2. **Cero atributos propios dentro de `gen_ai.*`.** Es una allowlist: lo que no está en
   la lista del contrato es un invento, aunque suene bien.
3. **El modelo interno no conoce el estándar.** Se lee el fuente: si un campo de
   `LlmCall` se llamara `gen_ai_algo`, la traducción sería decorativa y la siguiente
   ruptura de la spec entraría hasta el dominio.
4. **Un fallo de observabilidad no tumba nada.** §6, y se prueba rompiendo el sumidero,
   no razonándolo.
"""

from __future__ import annotations

import pathlib

import pytest

from datawarden.telemetry import LlmCall, to_attributes
from datawarden.telemetry.model import LlmCall as _ModeloInterno
from datawarden.telemetry.otel import (
    ALLOWED_GEN_AI,
    MANDATORY,
    SEMCONV_VERSION,
    Dropped,
    atributos_ausentes,
    emit,
    gen_ai_inventados,
)

_RAIZ = pathlib.Path(__file__).resolve().parents[2]
_LOCK = _RAIZ / "otel-semconv.lock"

#: **La envuelta REAL de Ollama**, capturada el 2026-09-10 contra el `/api/generate` del
#: host con `qwen3.5:4b-mlx`. Se congela aquí en vez de inventarla porque el valor del
#: test está justo en los nombres: `prompt_eval_count` y no `input_tokens`,
#: `done_reason` y no `finish_reason`, y las duraciones en NANOsegundos. Una envuelta
#: escrita a mano habría usado los nombres que uno espera y el test habría pasado
#: mientras la traducción real fallaba.
_OLLAMA_REAL = {
    "model": "qwen3.5:4b-mlx",
    "created_at": "2026-09-10T12:46:11.563748Z",
    "done": True,
    "done_reason": "stop",
    "total_duration": 1_226_296_458,
    "load_duration": 1_152_127_333,
    "prompt_eval_count": 14,
    "prompt_eval_cached_count": 0,
    "prompt_eval_duration": 47_338_250,
    "eval_count": 1,
    "eval_duration": 18_353_459,
    "response": "SELECT 1",
}

#: Una llamada COMPLETA: todo lo obligatorio y todo lo opcional con valor.
_COMPLETA = LlmCall(
    operation="chat",
    provider="ollama",
    requested_model="qwen3.5:9b-mlx",
    responded_model="qwen3.5:9b-mlx",
    input_tokens=1_024,
    output_tokens=96,
    finish_reasons=("stop",),
    server_address="localhost:11434",
    temperature=0.0,
    max_tokens=2_048,
    cost_eur=0.0,
    pricing_version="2026-09-01",
    prompt_id="nl2sql",
    prompt_version=2,
    prompt_sha256="sha256:" + "0" * 64,
    ttft_ms=180.5,
    client_disconnected=False,
)

#: La mínima: solo lo obligatorio. Los «si aplica» no aplican y no deben aparecer.
_MINIMA = LlmCall(
    operation="embeddings",
    provider="ollama",
    requested_model="bge-m3:latest",
    responded_model="bge-m3:latest",
    input_tokens=12,
    output_tokens=0,
    finish_reasons=("stop",),
    server_address="localhost:11434",
)


@pytest.fixture(scope="module", autouse=True)
def _mide_la_meta() -> object:
    """Deja la medida de `G-OTEL-ATTRS` en su artefacto al terminar el módulo.

    **El número sale de las MISMAS funciones que asertan los tests, no de cuántos
    pasaron.** Contar tests verdes mediría la suite; lo que la meta quiere medir son los
    atributos, así que se vuelven a calcular aquí y se publica el recuento de
    violaciones. Si alguien borrara un test, este número no cambiaría — que es lo
    correcto: la meta no mejora porque se mire menos.
    """
    yield
    import sys

    sys.path.insert(0, str(_RAIZ / "scripts"))
    from gatelib import record

    violaciones: list[str] = []
    presentes = 0
    esperados = 0
    from datawarden.nl2sql.providers import LocalProvider

    del_camino_real = LocalProvider(model="qwen3.5:9b-mlx").llm_call(_OLLAMA_REAL)
    for llamada in (_COMPLETA, _MINIMA, del_camino_real):
        atributos = to_attributes(llamada)
        esperados += len(MANDATORY)
        presentes += len(MANDATORY) - len(atributos_ausentes(atributos))
        violaciones += [f"falta {n}" for n in atributos_ausentes(atributos)]
        violaciones += [f"inventado {n}" for n in gen_ai_inventados(atributos)]

    record(
        "arch-checks.json",
        "G-OTEL-ATTRS",
        value=float(len(violaciones)),
        adicionales={
            "atributos obligatorios presentes (%)": round(100.0 * presentes / esperados, 2)
        },
        detail={
            "semconv": SEMCONV_VERSION,
            "gen_ai permitidos": sorted(ALLOWED_GEN_AI),
            "violaciones": violaciones,
        },
        command="pytest tests/contract/test_otel_attrs.py -q",
    )


@pytest.mark.parametrize("llamada", [_COMPLETA, _MINIMA], ids=["completa", "minima"])
def test_estan_todos_los_atributos_obligatorios(llamada: LlmCall) -> None:
    """§4: «un span que no los lleve todos es un span roto»."""
    assert atributos_ausentes(to_attributes(llamada)) == ()


@pytest.mark.parametrize("llamada", [_COMPLETA, _MINIMA], ids=["completa", "minima"])
def test_cero_atributos_propios_dentro_de_gen_ai(llamada: LlmCall) -> None:
    """**Lo nuestro va en `app.*`.** Inventar en `gen_ai.*` te saca del estándar."""
    assert gen_ai_inventados(to_attributes(llamada)) == ()


def test_lo_propio_sale_bajo_app_y_no_se_cuela_en_el_estandar() -> None:
    atributos = to_attributes(_COMPLETA)

    propios = {k for k in atributos if k.startswith("app.")}
    assert propios == {
        "app.cost.eur",
        "app.cost.pricing_version",
        "app.prompt.id",
        "app.prompt.version",
        "app.prompt.sha256",
        "app.ttft_ms",
        "app.stream.client_disconnected",
    }
    assert not any(k.startswith("gen_ai.") for k in propios)


def test_los_si_aplica_no_aparecen_cuando_no_aplican() -> None:
    """«Si aplica» significa ausente, no `null`.

    Emitir `gen_ai.request.temperature: None` no es lo mismo que no emitirla: el panel
    del 02 la contaría como una temperatura declarada y la media saldría mal.
    """
    atributos = to_attributes(_MINIMA)

    assert "gen_ai.request.temperature" not in atributos
    assert "gen_ai.request.max_tokens" not in atributos
    assert "error.type" not in atributos


def test_error_type_aparece_solo_cuando_hubo_error() -> None:
    fallida = LlmCall(
        operation="chat",
        provider="ollama",
        requested_model="qwen3.5:9b-mlx",
        responded_model="qwen3.5:9b-mlx",
        input_tokens=10,
        output_tokens=0,
        finish_reasons=("error",),
        server_address="localhost:11434",
        error_type="TimeoutError",
    )

    assert to_attributes(fallida)["error.type"] == "TimeoutError"
    assert "error.type" not in to_attributes(_MINIMA)


def test_se_usan_los_nombres_nuevos_y_no_los_renombrados() -> None:
    """§1: tres rupturas que YA han ocurrido, y son las que más se ven copiadas mal."""
    atributos = to_attributes(_COMPLETA)

    assert "gen_ai.provider.name" in atributos and "gen_ai.system" not in atributos
    assert "gen_ai.usage.input_tokens" in atributos
    assert "gen_ai.usage.prompt_tokens" not in atributos
    assert "gen_ai.usage.output_tokens" in atributos
    assert "gen_ai.usage.completion_tokens" not in atributos


def test_el_modelo_interno_no_conoce_un_solo_nombre_del_estandar() -> None:
    """§2: si el modelo interno usara `gen_ai.*`, el traductor sería decorativo."""
    campos = set(_ModeloInterno.__dataclass_fields__)

    assert not any("gen_ai" in campo for campo in campos)
    fuente = (_RAIZ / "src" / "datawarden" / "telemetry" / "model.py").read_text(
        encoding="utf-8"
    )
    # Se mira el FUENTE y no solo los campos: una constante o un docstring con el
    # prefijo también sería conocimiento del estándar filtrado hacia dentro.
    assert "gen_ai." not in fuente.replace("`gen_ai.*`", "")


def test_el_traductor_es_el_unico_sitio_que_escribe_gen_ai() -> None:
    """Si aparecieran en dos sitios, la siguiente ruptura habría que arreglarla en dos."""
    raiz = _RAIZ / "src" / "datawarden"
    culpables = [
        ruta.relative_to(raiz).as_posix()
        for ruta in sorted(raiz.rglob("*.py"))
        if '"gen_ai.' in ruta.read_text(encoding="utf-8")
    ]

    assert culpables == ["telemetry/otel.py"], culpables


def test_la_version_del_traductor_es_la_pineada_en_el_lock() -> None:
    """§3: subir la versión es un cambio consciente CON revisión del traductor.

    Este test es lo que convierte esa frase en una puerta: cambiar el `.lock` sin tocar
    el traductor se pone rojo aquí, que es exactamente cuando hay que mirarlo.
    """
    lock = dict(
        linea.split(":", 1)
        for linea in _LOCK.read_text(encoding="utf-8").splitlines()
        if ":" in linea and not linea.lstrip().startswith("#")
    )

    assert lock["version"].strip() == SEMCONV_VERSION
    assert len(lock["sha"].strip()) == 40, "el sha del tag va COMPLETO o no es un pin"


def test_un_almacen_de_trazas_caido_no_tumba_la_aplicacion() -> None:
    """§6, la regla que no se negocia. **Se prueba rompiendo el sumidero.**

    No se comprueba «que hay un try»: se le da un sumidero que revienta y se exige que
    quien emite siga vivo y que el descarte quede CONTADO. Un descarte silencioso es
    peor que un descarte medido.
    """
    descartados = Dropped()

    def sumidero_caido(_: dict[str, object]) -> None:
        raise ConnectionError("el colector no responde")

    llegó = emit(_COMPLETA, sumidero_caido, dropped=descartados)

    assert llegó is False
    assert descartados.count == 1


def test_lo_que_llega_bien_no_cuenta_como_descartado() -> None:
    """Sin esto, el contador podría estar contando siempre y el test de arriba pasaría."""
    descartados = Dropped()
    recibidos: list[dict[str, object]] = []

    llegó = emit(_COMPLETA, recibidos.append, dropped=descartados)

    assert llegó is True
    assert descartados.count == 0
    assert atributos_ausentes(recibidos[0]) == ()


def test_el_camino_real_del_proveedor_produce_atributos_conformes() -> None:
    """**Esto es lo que impide que la meta mida un traductor que nadie llama.**

    Los tests de arriba construyen un `LlmCall` a mano; este parte de la respuesta que
    Ollama devuelve de verdad y la pasa por `LocalProvider.llm_call`, que es la función
    del camino que se ejecuta. Sin él, `G-OTEL-ATTRS` podría estar verde con la
    aplicación sin emitir un solo atributo — el mismo error de método que ya costó
    `G-PII-LEAK`, `G-SECRETS`, `G-MCP-CONFORM` y `check_mcp_live`.
    """
    from datawarden.nl2sql.providers import LocalProvider

    proveedor = LocalProvider(model="qwen3.5:9b-mlx")
    atributos = to_attributes(proveedor.llm_call(_OLLAMA_REAL))

    assert atributos_ausentes(atributos) == ()
    assert gen_ai_inventados(atributos) == ()
    # El modelo que RESPONDIÓ, no el que se pidió. El contrato los separa a propósito y
    # aquí difieren de verdad: es la única forma de comprobar que no se copia uno.
    assert atributos["gen_ai.request.model"] == "qwen3.5:9b-mlx"
    assert atributos["gen_ai.response.model"] == "qwen3.5:4b-mlx"
    # Nanosegundos de Ollama → milisegundos de `app.ttft_ms`.
    assert atributos["app.ttft_ms"] == pytest.approx(47.338)


def test_el_proveedor_emite_y_un_sumidero_roto_no_lo_tumba() -> None:
    """El proveedor entero, con el sumidero caído. Tiene que devolver el SQL igual."""
    from datawarden.nl2sql.providers import LocalProvider

    recibidos: list[dict[str, object]] = []
    proveedor = LocalProvider(model="qwen3.5:9b-mlx", telemetry=recibidos.append)
    assert emit(proveedor.llm_call(_OLLAMA_REAL), recibidos.append, dropped=Dropped())
    assert atributos_ausentes(recibidos[0]) == ()

    roto = LocalProvider(model="qwen3.5:9b-mlx")

    def sumidero_caido(_: dict[str, object]) -> None:
        raise TimeoutError("el colector no responde")

    assert emit(roto.llm_call(_OLLAMA_REAL), sumidero_caido, dropped=roto.dropped) is False
    assert roto.dropped.count == 1


def test_la_allowlist_no_esta_vacia() -> None:
    """Una allowlist vacía haría que `gen_ai_inventados` marcara todo, o nada.

    Es el control de la propia medida: sin él, un error que dejara `ALLOWED_GEN_AI`
    vacío pondría verde el test 2 por el motivo contrario al que se quiere.
    """
    assert len(ALLOWED_GEN_AI) >= 8
    assert all(nombre.startswith("gen_ai.") for nombre in ALLOWED_GEN_AI)
    assert set(MANDATORY) - ALLOWED_GEN_AI == {"server.address"}
