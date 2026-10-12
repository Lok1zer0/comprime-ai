import re
import subprocess
from pathlib import Path

import pytest

from gifc.formatos.base import EncodeError
from gifc.formatos.gif import (
    NEUTROS,
    SENTIDOS,
    TABELA,
    Params,
    build_ffmpeg_cmd,
    build_filter,
    build_gifsicle_cmd,
    codificar,
    toggles_efetivos,
)
from gifc.ladder import Toggles, aplicar_toggles
from gifc.probe import Formato, ImagemInfo
from gifc.tools import Tools

FERRAMENTAS = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle="gifsicle")


def info(fps: float = 30.0, width: int = 800) -> ImagemInfo:
    return ImagemInfo(
        path=Path("x.gif"), formato=Formato.GIF, width=width, height=600, fps=fps,
        frame_count=100, duration=100 / fps, size_bytes=1_000_000,
    )


def test_filtro_usa_bayer_e_nao_floyd():
    """Bayer comprime muito melhor: o padrão é estático entre frames."""
    f = build_filter(info(), Params(256, 0, 100, None))
    assert "dither=bayer" in f
    assert "floyd" not in f


def test_filtro_usa_stats_mode_diff():
    f = build_filter(info(), Params(256, 0, 100, None))
    assert "stats_mode=diff" in f


def test_filtro_respeita_o_numero_de_cores():
    f = build_filter(info(), Params(64, 0, 100, None))
    assert "max_colors=64" in f


def test_filtro_omite_escala_quando_100():
    f = build_filter(info(), Params(256, 0, 100, None))
    # Ancorado a "início da string ou depois de vírgula" para checar que
    # nenhum filtro de vídeo `scale=` está presente, sem colidir com
    # `bayer_scale=` (que é sempre parte do dither, independente da escala,
    # e também termina em "scale="). Um `"scale=" not in f` ingênuo
    # combinaria com `bayer_scale=` e apagaria silenciosamente o
    # `bayer_scale=5` deliberado do filtro — não simplifique de volta para
    # substring.
    assert re.search(r"(^|,)scale=", f) is None
    assert "lanczos" not in f


def test_filtro_usa_bayer_scale_5():
    """bayer_scale=5 é uma escolha deliberada de compressão: alarga o
    padrão de dithering em relação ao padrão do ffmpeg (2), o que comprime
    melhor. Não é decoração incidental — não remova sem medir o impacto.
    """
    f = build_filter(info(), Params(256, 0, 100, None))
    assert "bayer_scale=5" in f


def test_filtro_inclui_escala_quando_reduzida():
    f = build_filter(info(width=800), Params(256, 0, 70, None))
    assert "scale=560:-1" in f
    assert "lanczos" in f


def test_filtro_omite_fps_quando_none():
    f = build_filter(info(), Params(256, 0, 100, None))
    assert "fps=" not in f


def test_filtro_inclui_fps_quando_reduzido():
    f = build_filter(info(fps=30.0), Params(256, 0, 100, 15.0))
    assert "fps=15" in f


def test_ffmpeg_cmd_tem_overwrite_e_saida():
    cmd = build_ffmpeg_cmd(
        FERRAMENTAS, Path("a.gif"), Path("b.gif"), info(),
        Params(256, 0, 100, None),
    )
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert cmd[-1] == "b.gif"


def test_gifsicle_cmd_usa_O3():
    cmd = build_gifsicle_cmd(
        FERRAMENTAS, Path("a.gif"), Path("b.gif"), Params(256, 0, 100, None)
    )
    assert "-O3" in cmd


def test_gifsicle_cmd_omite_lossy_quando_zero():
    cmd = build_gifsicle_cmd(
        FERRAMENTAS, Path("a.gif"), Path("b.gif"), Params(256, 0, 100, None)
    )
    assert not any(a.startswith("--lossy") for a in cmd)


def test_gifsicle_cmd_inclui_lossy_quando_positivo():
    cmd = build_gifsicle_cmd(
        FERRAMENTAS, Path("a.gif"), Path("b.gif"), Params(256, 80, 100, None)
    )
    assert "--lossy=80" in cmd


def test_encode_modo_degradado_sem_gifsicle_usa_so_o_ffmpeg(tmp_path, monkeypatch):
    """Sem gifsicle (`Tools.gifsicle is None`), `codificar()` não roda o
    segundo estágio: o intermediário do ffmpeg vira o resultado final via
    `intermediario.replace(dst)`. Isto interage com o `finally` que faz
    `intermediario.unlink(missing_ok=True)` — sem o `missing_ok=True`, esse
    unlink levantaria FileNotFoundError depois que o `.replace()` já moveu
    o arquivo para fora do caminho original.

    `subprocess.run` é substituído por um fake que escreve no caminho de
    destino passado no comando (o último elemento de `build_ffmpeg_cmd`),
    para não depender de um ffmpeg real instalado.
    """
    chamadas: list[list[str]] = []

    def fake_run(cmd, capture_output, text, timeout):
        chamadas.append(cmd)
        Path(cmd[-1]).write_bytes(b"GIF89a-fake")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    ferramentas_degradadas = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle=None)
    src = tmp_path / "a.gif"
    src.write_bytes(b"GIF89a-original")
    dst = tmp_path / "saida.gif"

    tamanho = codificar(
        src, dst, Params(64, 0, 100, None), info(), ferramentas_degradadas
    )

    assert len(chamadas) == 1, "modo degradado não deveria chamar o gifsicle"
    assert dst.is_file()
    assert dst.read_bytes() == b"GIF89a-fake"
    assert tamanho == dst.stat().st_size
    # nada além de origem e destino deve sobrar no diretório: o
    # intermediário foi movido (replace), não copiado.
    assert {p.name for p in tmp_path.iterdir()} == {src.name, dst.name}


@pytest.mark.integration
def test_encode_produz_um_gif_valido(tools, gif_pequeno, tmp_path):
    from gifc.probe import probe

    origem = probe(gif_pequeno)
    destino = tmp_path / "saida.gif"
    tamanho = codificar(
        gif_pequeno, destino, Params(64, 80, 100, None), origem, tools
    )
    assert destino.is_file()
    assert tamanho == destino.stat().st_size
    assert tamanho > 0
    assert destino.read_bytes()[:3] == b"GIF"


@pytest.mark.integration
def test_encode_nao_altera_o_arquivo_de_origem(tools, gif_pequeno, tmp_path):
    from gifc.probe import probe

    antes = gif_pequeno.read_bytes()
    origem = probe(gif_pequeno)
    codificar(
        gif_pequeno, tmp_path / "saida.gif",
        Params(32, 100, 70, None), origem, tools,
    )
    assert gif_pequeno.read_bytes() == antes


@pytest.mark.integration
def test_encode_com_origem_invalida_levanta_erro(tools, tmp_path):
    ruim = tmp_path / "ruim.gif"
    ruim.write_bytes(b"nao e um gif")
    with pytest.raises(EncodeError):
        codificar(
            ruim, tmp_path / "saida.gif", Params(64, 0, 100, None),
            info(), tools,
        )



# ---------- a escada de GIF (antes em tests/test_ladder.py) ----------

TODOS = Toggles(palette=True, lossy=True, scale=True, fps=True)


def build(info_: ImagemInfo, toggles: Toggles) -> list[Params]:
    return aplicar_toggles(TABELA, info_, toggles, NEUTROS)


def test_escada_base_tem_20_degraus():
    assert len(TABELA) == 20


def test_escada_base_comeca_no_degrau_mais_conservador():
    assert TABELA[0] == Params(colors=256, lossy=0, scale_pct=100, fps=None)


def test_escada_base_termina_no_mais_agressivo():
    ultimo = TABELA[-1]
    assert ultimo.colors == 32
    assert ultimo.lossy == 200
    assert ultimo.scale_pct == 50
    assert ultimo.fps == 10.0


def test_params_e_hashavel():
    assert len({Params(64, 80, 100, None), Params(64, 80, 100, None)}) == 1


def test_sentidos_do_gif():
    """Lossy é o único campo em que um número MAIOR é mais agressivo."""
    assert SENTIDOS == {
        "colors": "menor", "lossy": "maior", "scale_pct": "menor", "fps": "menor",
    }


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
    indices = [TABELA.index(
        Params(p.colors, p.lossy, 100, None)
    ) for p in escada if Params(p.colors, p.lossy, 100, None) in TABELA]
    assert indices == sorted(indices)


def test_neutros_do_gif():
    """Os valores que as alavancas desligadas fixam: nada de paleta reduzida,
    lossy, escala ou fps reduzido."""
    assert NEUTROS == {"colors": 256, "lossy": 0, "scale_pct": 100, "fps": None}


def test_neutros_do_gif_sao_o_primeiro_degrau():
    """O degrau 0 é o menos agressivo em todas as colunas."""
    degrau_0 = TABELA[0]
    assert {c: getattr(degrau_0, c) for c in NEUTROS} == NEUTROS


# ---------- toggles_efetivos (modo degradado sem gifsicle) ----------


def test_sem_gifsicle_o_toggle_lossy_e_desligado():
    sem_gifsicle = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle=None)
    assert toggles_efetivos(TODOS, sem_gifsicle) == Toggles(True, False, True, True)


def test_com_gifsicle_os_toggles_passam_inalterados():
    assert toggles_efetivos(TODOS, FERRAMENTAS) == TODOS
    desligado = Toggles(False, False, True, False)
    assert toggles_efetivos(desligado, FERRAMENTAS) == desligado


def test_sem_gifsicle_preserva_os_outros_toggles():
    sem_gifsicle = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle=None)
    entrada = Toggles(False, True, False, True)
    assert toggles_efetivos(entrada, sem_gifsicle) == Toggles(False, False, False, True)
