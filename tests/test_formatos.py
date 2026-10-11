"""O registro `gifc.formatos`: extensões e ganchos comuns a todos os formatos."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from gifc import formatos
from gifc.compress import _try_path
from gifc.ladder import Toggles
from gifc.probe import Formato
from gifc.tools import Tools


@pytest.mark.parametrize("formato", list(Formato))
def test_todo_formato_declara_a_sua_extensao(formato):
    """Um `Formato` novo sem extensão declarada falha aqui, em vez de
    estourar KeyError no meio de uma compressão."""
    ext = formatos.extensao(formato)
    assert ext.startswith(".") and ext == ext.lower() and len(ext) > 1


@pytest.mark.parametrize(
    "formato,esperada",
    [
        (Formato.GIF, ".gif"),
        (Formato.PNG, ".png"),
        (Formato.JPEG, ".jpg"),   # não ".jpeg": é o que as pessoas esperam
        (Formato.WEBP, ".webp"),
    ],
)
def test_extensao_de_cada_formato(formato, esperada):
    assert formatos.extensao(formato) == esperada


def test_extensoes_sao_distintas():
    todas = [formatos.extensao(f) for f in Formato]
    assert len(todas) == len(set(todas))


def test_jpeg_nao_deriva_a_extensao_do_valor_do_enum():
    assert Formato.JPEG.value == "jpeg"
    assert formatos.extensao(Formato.JPEG) != "." + Formato.JPEG.value


@pytest.mark.parametrize("formato", list(Formato))
def test_try_path_usa_a_extensao_do_formato(formato):
    p = _try_path(Path("tmp"), 7, formato)
    assert p == Path("tmp") / f"try_7{formatos.extensao(formato)}"


def test_try_path_do_gif_mantem_a_convencao_anterior():
    assert _try_path(Path("tmp"), 3, Formato.GIF).name == "try_3.gif"


def test_toggles_efetivos_do_gif_usa_o_gancho_do_modulo():
    sem_gifsicle = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle=None)
    todos = Toggles(True, True, True, True)
    assert formatos.toggles_efetivos(Formato.GIF, todos, sem_gifsicle) == Toggles(
        True, False, True, True
    )


def test_toggles_efetivos_sem_gancho_devolve_inalterado(monkeypatch):
    """Módulo sem `toggles_efetivos`: os toggles passam sem alteração, mesmo
    sem gifsicle (a regra é do GIF, não do compress)."""
    sem_gancho = SimpleNamespace()  # nenhum atributo `toggles_efetivos`
    monkeypatch.setattr(formatos, "para", lambda formato: sem_gancho)
    sem_gifsicle = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle=None)
    todos = Toggles(True, True, True, True)
    assert formatos.toggles_efetivos(Formato.GIF, todos, sem_gifsicle) is todos
