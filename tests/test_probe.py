import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import gifc.probe as probe_module
from gifc.probe import FPS_PADRAO, GifInfo, ProbeError, _parse_fps, probe
from gifc.tools import Tools


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


@pytest.mark.integration
def test_probe_le_duracao_do_gif(tools, gif_pequeno):
    info = probe(gif_pequeno, tools)
    assert info.duration == pytest.approx(1.0, abs=0.3)


def test_parse_fps_taxa_normal():
    assert _parse_fps("25/1") == (25.0, True)


def test_parse_fps_taxa_zero():
    assert _parse_fps("0/0") == (FPS_PADRAO, False)


def test_parse_fps_valor_nao_parseavel():
    assert _parse_fps("lixo") == (FPS_PADRAO, False)


def test_parse_fps_valor_nao_positivo():
    assert _parse_fps("0/1") == (FPS_PADRAO, False)
    assert _parse_fps("-5/1") == (FPS_PADRAO, False)


def test_probe_duracao_desconhecida_quando_fps_e_fallback(tmp_path, monkeypatch):
    """Quando o fps é a convenção de reserva e o ffprobe não informa
    duração, o resultado deve ser 0.0 (desconhecido) em vez de uma
    duração calculada a partir de um fps que não foi medido."""
    arquivo = tmp_path / "amostra.gif"
    arquivo.write_bytes(b"GIF89a")  # conteúdo irrelevante: subprocess é mockado

    payload = {
        "streams": [
            {
                "width": 64,
                "height": 64,
                "avg_frame_rate": "0/0",
                "nb_read_frames": "10",
            }
        ],
        "format": {
            "format_name": "gif",
        },
    }

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(probe_module.subprocess, "run", fake_run)

    tools_falsos = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle=None)
    info = probe(arquivo, tools_falsos)
    assert info.fps == FPS_PADRAO
    assert info.duration == 0.0
