"""O registro `gifc.formatos`: extensões e ganchos comuns a todos os formatos."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

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


# --- formato sem módulo: não é falha de degrau -----------------------------


def test_formato_sem_modulo_levanta_o_tipo_proprio(monkeypatch):
    """`EncodeError` dentro do `encode_fn` quer dizer "este degrau falhou,
    continue"; de `formatos.para` quer dizer "este arquivo é impossível".
    Tipos diferentes, para ninguém confundir um com o outro."""
    monkeypatch.delitem(formatos._REGISTRO, Formato.WEBP)

    with pytest.raises(formatos.FormatoNaoSuportado, match="WEBP"):
        formatos.para(Formato.WEBP)
    assert not issubclass(formatos.FormatoNaoSuportado, formatos.EncodeError)


def test_formato_sem_modulo_nao_vira_degrau_que_falhou(tmp_path, monkeypatch):
    """No `compress_file` o erro sobe de uma vez: nenhum degrau é tentado, e
    nada fica para trás."""
    from gifc.compress import compress_file

    monkeypatch.delitem(formatos._REGISTRO, Formato.WEBP)
    src = tmp_path / "a.png"
    Image.new("RGB", (8, 8), "red").save(src)

    with pytest.raises(formatos.FormatoNaoSuportado):
        compress_file(
            src, 1, Toggles(), Tools("ffmpeg", "ffprobe", None), converter_webp=True,
        )
    assert [p.name for p in tmp_path.iterdir()] == ["a.png"]


# --- degraus equivalentes ---------------------------------------------------


def test_sem_equivalentes_sem_gancho_devolve_a_escada_inalterada(monkeypatch):
    sem_gancho = SimpleNamespace()
    monkeypatch.setattr(formatos, "para", lambda formato: sem_gancho)
    escada = [object(), object()]
    sem_ferramentas = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle=None)
    assert formatos.sem_equivalentes(Formato.GIF, escada, sem_ferramentas) == escada


def test_sem_equivalentes_mantem_o_primeiro_de_cada_grupo(monkeypatch):
    gancho = SimpleNamespace(chave_de_equivalencia=lambda p, tools: p // 10)
    monkeypatch.setattr(formatos, "para", lambda formato: gancho)
    sem_ferramentas = Tools(ffmpeg="ffmpeg", ffprobe="ffprobe", gifsicle=None)
    assert formatos.sem_equivalentes(
        Formato.GIF, [11, 12, 25, 21, 33, 39], sem_ferramentas
    ) == [11, 25, 33]


def test_png_sem_oxipng_funde_os_degraus_sem_perda():
    from gifc.formatos import png

    sem = Tools("ffmpeg", "ffprobe", None, oxipng=None)
    com = Tools("ffmpeg", "ffprobe", None, oxipng="oxipng")
    antes = formatos.sem_equivalentes(Formato.PNG, list(png.TABELA), com)
    depois = formatos.sem_equivalentes(Formato.PNG, list(png.TABELA), sem)

    assert antes == list(png.TABELA)             # com oxipng, todos distintos
    assert depois == [png.TABELA[0], *png.TABELA[2:]]   # o 1 é o mesmo arquivo
