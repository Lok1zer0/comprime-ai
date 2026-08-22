from pathlib import Path

import pytest

from gifc.compress import CompressResult, compress_file, unique_output_path
from gifc.encode import EncodeError
from gifc.ladder import Toggles
from gifc.probe import GifInfo
from gifc.tools import Tools

TODOS = Toggles(True, True, True, True)
FAKE_TOOLS = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle="gifsicle")


def _preparar(tmp_path, monkeypatch, gifsicle="gifsicle"):
    """Isola compress_file do disco: probe e encode viram funções falsas."""
    from gifc import compress as mod

    src = tmp_path / "a.gif"
    src.write_bytes(b"GIF89a" + b"x" * 5_000)
    info = GifInfo(
        path=src, width=100, height=100, fps=30.0,
        frame_count=30, duration=1.0, size_bytes=src.stat().st_size,
    )
    monkeypatch.setattr(mod, "probe", lambda p, t: info)
    return mod, src, info, Tools("ffmpeg", "ffprobe", gifsicle)


def test_nome_de_saida_padrao(tmp_path):
    src = tmp_path / "gato.gif"
    src.write_bytes(b"x")
    assert unique_output_path(src).name == "gato_comprimido.gif"


def test_nome_de_saida_nao_sobrescreve(tmp_path):
    src = tmp_path / "gato.gif"
    src.write_bytes(b"x")
    (tmp_path / "gato_comprimido.gif").write_bytes(b"y")
    assert unique_output_path(src).name == "gato_comprimido_2.gif"


def test_nome_de_saida_incrementa_ate_achar_livre(tmp_path):
    src = tmp_path / "gato.gif"
    src.write_bytes(b"x")
    (tmp_path / "gato_comprimido.gif").write_bytes(b"y")
    (tmp_path / "gato_comprimido_2.gif").write_bytes(b"y")
    assert unique_output_path(src).name == "gato_comprimido_3.gif"


def test_degrau_que_falha_e_pulado(tmp_path, monkeypatch):
    """Um degrau que quebra não derruba o arquivo: a busca segue adiante."""
    mod, src, info, tools = _preparar(tmp_path, monkeypatch)
    tentados: list[int] = []

    def encode_falho(s, d, params, i, t):
        idx = int(d.stem.split("_")[1])
        tentados.append(idx)
        if idx % 2 == 0:
            raise EncodeError("degrau ruim")
        d.write_bytes(b"GIF89a" + b"y" * (2_000 - idx * 50))
        return d.stat().st_size

    monkeypatch.setattr(mod, "encode", encode_falho)

    r = mod.compress_file(src, 1_800, TODOS, tools)

    assert any(i % 2 == 0 for i in tentados), "nenhum degrau falho foi exercido"
    assert r.dst.is_file()
    assert r.final_bytes <= 1_800


def test_todos_os_degraus_falhando_levanta_erro(tmp_path, monkeypatch):
    mod, src, info, tools = _preparar(tmp_path, monkeypatch)

    def sempre_falha(s, d, params, i, t):
        raise EncodeError("quebrado")

    monkeypatch.setattr(mod, "encode", sempre_falha)

    with pytest.raises(mod.CompressError):
        mod.compress_file(src, 1_000, TODOS, tools)


def test_sem_gifsicle_descarta_a_coluna_lossy(tmp_path, monkeypatch):
    """Modo degradado: manter lossy só geraria degraus idênticos."""
    mod, src, info, tools = _preparar(tmp_path, monkeypatch, gifsicle=None)
    vistos = []

    def encode_ok(s, d, params, i, t):
        vistos.append(params)
        d.write_bytes(b"GIF89a" + b"y" * 100)
        return d.stat().st_size

    monkeypatch.setattr(mod, "encode", encode_ok)
    mod.compress_file(src, 1_000_000, TODOS, tools)

    assert vistos
    assert all(p.lossy == 0 for p in vistos)


@pytest.mark.integration
def test_comprime_e_atinge_alvo_generoso(tools, gif_pequeno, tmp_path):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())

    r = compress_file(copia, target_bytes=10_000_000, toggles=TODOS, tools=tools)

    assert isinstance(r, CompressResult)
    assert r.hit_target is True
    assert r.dst.is_file()
    assert r.final_bytes > 0


@pytest.mark.integration
def test_alvo_generoso_usa_o_degrau_de_maior_qualidade(tools, gif_pequeno, tmp_path):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())

    r = compress_file(copia, target_bytes=10_000_000, toggles=TODOS, tools=tools)

    # Alvo folgado: não deve sacrificar resolução nem framerate.
    assert r.params is not None
    assert r.params.scale_pct == 100
    assert r.params.fps is None


@pytest.mark.integration
def test_nunca_modifica_o_original(tools, gif_pequeno, tmp_path):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())
    antes = copia.read_bytes()

    compress_file(copia, target_bytes=1_000, toggles=TODOS, tools=tools)

    assert copia.read_bytes() == antes


@pytest.mark.integration
def test_alvo_impossivel_entrega_o_menor_e_avisa(tools, gif_pequeno, tmp_path):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())

    r = compress_file(copia, target_bytes=1, toggles=TODOS, tools=tools)

    assert r.hit_target is False
    assert r.dst.is_file()
    assert r.final_bytes > 1


@pytest.mark.integration
def test_nao_deixa_temporarios_para_tras(tools, gif_pequeno, tmp_path):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())

    r = compress_file(copia, target_bytes=10_000_000, toggles=TODOS, tools=tools)

    restantes = {p.name for p in tmp_path.iterdir()}
    assert restantes == {copia.name, r.dst.name}
