import itertools
from pathlib import Path
from typing import Sequence

import pytest

from gifc.ladder import BASE_LADDER, Params, Toggles, build, target_width
from gifc.probe import Formato, ImagemInfo


def info(fps: float = 30.0, width: int = 800) -> ImagemInfo:
    return ImagemInfo(
        path=Path("x.gif"), formato=Formato.GIF, width=width, height=600, fps=fps,
        frame_count=100, duration=100 / fps, size_bytes=1_000_000,
    )


TODOS = Toggles(palette=True, lossy=True, scale=True, fps=True)


def test_escada_base_tem_20_degraus():
    assert len(BASE_LADDER) == 20


def test_escada_base_comeca_no_degrau_mais_conservador():
    assert BASE_LADDER[0] == Params(colors=256, lossy=0, scale_pct=100, fps=None)


def test_escada_base_termina_no_mais_agressivo():
    ultimo = BASE_LADDER[-1]
    assert ultimo.colors == 32
    assert ultimo.lossy == 200
    assert ultimo.scale_pct == 50
    assert ultimo.fps == 10.0


def _assert_agressividade_nao_diminui(escada: Sequence[Params], rotulo: str) -> None:
    """Verifica que cada degrau é fracamente mais agressivo que o anterior
    em TODAS as dimensões — o mesmo invariante que `test_agressividade_nao_
    diminui_ao_longo_da_escada` checava só para `BASE_LADDER`, fatorado
    para ser reutilizável também sobre a saída de `build()`.

    Isto é necessário para que o tamanho codificado seja monotonicamente
    não-crescente, garantindo que a busca binária de lower bound é válida.
    """
    for i, (anterior, atual) in enumerate(zip(escada, escada[1:])):
        assert atual.colors <= anterior.colors, (
            f"[{rotulo}] Rung {i} -> {i+1}: colors aumentou "
            f"({anterior.colors} -> {atual.colors})"
        )
        assert atual.lossy >= anterior.lossy, (
            f"[{rotulo}] Rung {i} -> {i+1}: lossy diminui "
            f"({anterior.lossy} -> {atual.lossy})"
        )
        assert atual.scale_pct <= anterior.scale_pct, (
            f"[{rotulo}] Rung {i} -> {i+1}: scale_pct aumentou "
            f"({anterior.scale_pct} -> {atual.scale_pct})"
        )
        # fps: None é o maior (original), números são menores
        if anterior.fps is None:
            # Anterior tem fps original, atual pode ter qualquer coisa
            pass
        else:
            # Anterior tem fps específico, atual deve ter fps <= anterior.fps ou None não é permitido
            if atual.fps is not None:
                assert atual.fps <= anterior.fps, (
                    f"[{rotulo}] Rung {i} -> {i+1}: fps aumentou "
                    f"({anterior.fps} -> {atual.fps})"
                )
            else:
                # atual.fps is None, que significa source fps (None > qualquer número)
                assert False, (
                    f"[{rotulo}] Rung {i} -> {i+1}: fps aumentou "
                    f"({anterior.fps} -> None)"
                )


def test_agressividade_nao_diminui_ao_longo_da_escada():
    _assert_agressividade_nao_diminui(BASE_LADDER, "BASE_LADDER")


def test_params_e_hashavel():
    assert len({Params(64, 80, 100, None), Params(64, 80, 100, None)}) == 1


def test_build_com_todos_ligados_mantem_os_20():
    assert len(build(info(), TODOS)) == 20


def test_toggle_paleta_desligado_fixa_256_cores():
    escada = build(info(), Toggles(False, True, True, True))
    assert all(p.colors == 256 for p in escada)
    assert len(escada) < 20


def test_toggle_lossy_desligado_zera_lossy():
    escada = build(info(), Toggles(True, False, True, True))
    assert all(p.lossy == 0 for p in escada)


def test_toggle_escala_desligado_fixa_100():
    escada = build(info(), Toggles(True, True, False, True))
    assert all(p.scale_pct == 100 for p in escada)


def test_toggle_fps_desligado_zera_fps():
    escada = build(info(), Toggles(True, True, True, False))
    assert all(p.fps is None for p in escada)


def test_tudo_desligado_deixa_so_o_degrau_neutro():
    escada = build(info(), Toggles(False, False, False, False))
    assert escada == [Params(colors=256, lossy=0, scale_pct=100, fps=None)]


def test_fps_alvo_acima_do_original_vira_none():
    """GIF de 10fps não deve gerar degraus pedindo 12, 15 ou 20 fps."""
    escada = build(info(fps=10.0), TODOS)
    assert all(p.fps is None for p in escada)


def test_escada_nao_tem_duplicatas():
    for toggles in [
        TODOS,
        Toggles(False, True, True, True),
        Toggles(True, False, True, True),
        Toggles(True, True, False, True),
        Toggles(True, True, True, False),
    ]:
        escada = build(info(), toggles)
        assert len(escada) == len(set(escada))


@pytest.mark.parametrize(
    "toggles",
    [
        Toggles(p, l, s, f)
        for p, l, s, f in itertools.product([True, False], repeat=4)
    ],
    ids=lambda t: f"palette={t.palette},lossy={t.lossy},scale={t.scale},fps={t.fps}",
)
@pytest.mark.parametrize("fps_origem", [10, 12, 15, 20, 30])
def test_build_preserva_monotonicidade_para_qualquer_combinacao_de_toggles(
    toggles, fps_origem
):
    """A busca binária em `find_best` depende de `build()` devolver uma
    escada monotonicamente não-crescente — `test_agressividade_nao_diminui_
    ao_longo_da_escada` só verificava isso para `BASE_LADDER` sem toggles
    aplicados. Como o precondicionamento da busca é sobre a SAÍDA de
    `build()`, e não sobre `BASE_LADDER` diretamente, cada uma das 16
    combinações de toggles (2**4) precisa preservar o invariante, para
    alguns fps de origem representativos (incluindo valores baixos que
    colapsam mais degraus de fps em None via `test_fps_alvo_acima_do_
    original_vira_none`).
    """
    escada = build(info(fps=float(fps_origem)), toggles)
    _assert_agressividade_nao_diminui(escada, f"toggles={toggles}, fps={fps_origem}")


def test_dedup_preserva_a_ordem_original():
    escada = build(info(), Toggles(True, True, False, False))
    indices = [BASE_LADDER.index(
        Params(p.colors, p.lossy, 100, None)
    ) for p in escada if Params(p.colors, p.lossy, 100, None) in BASE_LADDER]
    assert indices == sorted(indices)


@pytest.mark.parametrize(
    "largura,pct,esperado",
    [
        (800, 100, 800),
        (800, 85, 680),
        (800, 70, 560),
        (800, 50, 400),
        (801, 85, 680),   # arredonda para par
        (10, 50, 16),     # respeita o mínimo
        (33, 100, 34),    # ímpar vira par
    ],
)
def test_target_width(largura, pct, esperado):
    assert target_width(largura, pct) == esperado
