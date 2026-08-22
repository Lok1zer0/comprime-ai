from pathlib import Path

import pytest

from gifc.ladder import BASE_LADDER, Params, Toggles, build, target_width
from gifc.probe import GifInfo


def info(fps: float = 30.0, width: int = 800) -> GifInfo:
    return GifInfo(
        path=Path("x.gif"), width=width, height=600, fps=fps,
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


def test_agressividade_nao_diminui_ao_longo_da_escada():
    """Nenhum degrau é melhor que o anterior em todas as dimensões."""
    for anterior, atual in zip(BASE_LADDER, BASE_LADDER[1:]):
        melhor_em_tudo = (
            atual.colors >= anterior.colors
            and atual.lossy <= anterior.lossy
            and atual.scale_pct >= anterior.scale_pct
            and (atual.fps is None or (anterior.fps is not None
                                       and atual.fps >= anterior.fps))
        )
        assert not melhor_em_tudo, f"{atual} não é mais agressivo que {anterior}"


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
