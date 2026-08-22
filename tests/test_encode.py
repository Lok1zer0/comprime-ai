from pathlib import Path

import pytest

from gifc.encode import (
    EncodeError,
    build_ffmpeg_cmd,
    build_filter,
    build_gifsicle_cmd,
    encode,
)
from gifc.ladder import Params
from gifc.probe import GifInfo
from gifc.tools import Tools

FERRAMENTAS = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle="gifsicle")


def info(fps: float = 30.0, width: int = 800) -> GifInfo:
    return GifInfo(
        path=Path("x.gif"), width=width, height=600, fps=fps,
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
    assert "scale=" not in f


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


@pytest.mark.integration
def test_encode_produz_um_gif_valido(tools, gif_pequeno, tmp_path):
    from gifc.probe import probe

    origem = probe(gif_pequeno, tools)
    destino = tmp_path / "saida.gif"
    tamanho = encode(
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
    origem = probe(gif_pequeno, tools)
    encode(
        gif_pequeno, tmp_path / "saida.gif",
        Params(32, 100, 70, None), origem, tools,
    )
    assert gif_pequeno.read_bytes() == antes


@pytest.mark.integration
def test_encode_com_origem_invalida_levanta_erro(tools, tmp_path):
    ruim = tmp_path / "ruim.gif"
    ruim.write_bytes(b"nao e um gif")
    with pytest.raises(EncodeError):
        encode(
            ruim, tmp_path / "saida.gif", Params(64, 0, 100, None),
            info(), tools,
        )
