from pathlib import Path

import pytest

from gifc.probe import GifInfo, ProbeError, probe


@pytest.mark.integration
def test_probe_le_dimensoes_do_gif(tools, gif_pequeno):
    info = probe(gif_pequeno, tools)
    assert info.width == 64
    assert info.height == 64


@pytest.mark.integration
def test_probe_le_fps_e_frames(tools, gif_pequeno):
    info = probe(gif_pequeno, tools)
    assert info.fps == pytest.approx(10.0, abs=0.5)
    assert info.frame_count >= 9


@pytest.mark.integration
def test_probe_le_tamanho_do_arquivo(tools, gif_pequeno):
    info = probe(gif_pequeno, tools)
    assert info.size_bytes == gif_pequeno.stat().st_size
    assert info.size_bytes > 0


@pytest.mark.integration
def test_probe_arquivo_inexistente(tools, tmp_path):
    with pytest.raises(ProbeError):
        probe(tmp_path / "nao-existe.gif", tools)


@pytest.mark.integration
def test_probe_arquivo_invalido(tools, tmp_path):
    ruim = tmp_path / "ruim.gif"
    ruim.write_bytes(b"isto nao e um gif")
    with pytest.raises(ProbeError):
        probe(ruim, tools)


def test_gif_info_e_imutavel():
    info = GifInfo(
        path=Path("x.gif"), width=1, height=1, fps=1.0,
        frame_count=1, duration=1.0, size_bytes=1,
    )
    with pytest.raises(Exception):
        info.width = 2  # type: ignore[misc]
