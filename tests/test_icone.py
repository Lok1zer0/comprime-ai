"""Testes do ícone derivado: geração, enquadramento e onde o app o procura.

O `.ico` não é commitado — `assets/cai-logo.png` é a única fonte da verdade e
o arquivo é derivado dela, na janela (modo de desenvolvimento) ou no
empacotamento. Estes testes travam as três coisas que isso exige: que todos os
tamanhos de DPI saiam no arquivo, que o recorte ignore a sombra de alfa baixo,
e que a janela gere o arquivo quando ele não existir.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest
from PIL import Image

from gifc import tema, tools
from tools.gerar_icone import ORIGEM, em_quadrado, gerar, recorta_conteudo

# O Windows escolhe o quadro mais próximo da escala de DPI em vigor. Faltando
# 20, 40 e 96, ele estica o vizinho e o ícone sai borrado em 125%, 150% e 175%.
TAMANHOS_ESPERADOS = {16, 20, 24, 32, 40, 48, 64, 96, 128, 256}


# ---------- geração ----------


def test_o_ico_tem_todos_os_tamanhos_de_dpi(tmp_path: Path) -> None:
    """O arquivo gerado traz os dez quadros, não só os sete usuais."""
    destino = gerar(origem=ORIGEM, destino=tmp_path / "icone.ico")
    with Image.open(destino) as ico:
        lados = {largura for largura, altura in ico.info["sizes"] if largura == altura}
    assert lados == TAMANHOS_ESPERADOS


def test_o_ico_gerado_e_quadrado_em_cada_tamanho(tmp_path: Path) -> None:
    """Quadro não-quadrado faria o Windows distorcer o desenho."""
    destino = gerar(origem=ORIGEM, destino=tmp_path / "icone.ico")
    with Image.open(destino) as ico:
        assert all(largura == altura for largura, altura in ico.info["sizes"])


def test_gerar_recusa_logo_ausente(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        gerar(origem=tmp_path / "nao-existe.png", destino=tmp_path / "icone.ico")


# ---------- recorte por limiar de alfa ----------


def _logo_com_sombra() -> Image.Image:
    """Quadrado opaco de 20x20 no centro de 100x100, cercado de sombra fraca.

    É a forma do logo real: o desenho ocupa um pedaço do quadro e uma sombra de
    alfa muito baixo se espalha até as bordas.
    """
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 3))
    img.paste((255, 0, 0, 255), (40, 40, 60, 60))
    return img


def test_recorte_ignora_a_sombra_de_alfa_baixo() -> None:
    """Enquadra os 20x20 do desenho, não os 100x100 que `getbbox` devolveria.

    `getbbox()` em RGBA olha todos os canais e a sombra tem alfa 3, diferente
    de zero: ele devolveria a imagem inteira e o desenho sairia a 20% do lado
    do ícone.
    """
    com_sombra = _logo_com_sombra()
    assert com_sombra.getbbox() == (0, 0, 100, 100), "fixture sem a sombra"
    assert recorta_conteudo(com_sombra).size == (20, 20)


def test_em_quadrado_centraliza_sem_distorcer() -> None:
    """Uma faixa larga vira quadrado por moldura, não por esticamento."""
    faixa = Image.new("RGBA", (80, 20), (255, 0, 0, 255))
    quadro = em_quadrado(faixa, margem=0.0)
    assert quadro.size == (80, 80)
    # A faixa ficou no meio vertical: linha 0 vazia, linha 40 pintada.
    assert quadro.getpixel((40, 0))[3] == 0
    assert quadro.getpixel((40, 40))[3] == 255


# ---------- o app gera o que falta ----------


def test_garantir_icone_gera_o_arquivo_quando_falta(tmp_path: Path) -> None:
    """Num clone novo o `.ico` não existe; a janela o deriva do logo."""
    destino = tmp_path / "icone.ico"
    assert not destino.exists()
    assert tema._garantir_icone(destino) is True
    assert destino.is_file()


def test_garantir_icone_nao_regera_o_que_ja_existe(tmp_path: Path) -> None:
    """Gerar custa quase um segundo; não é para rodar a cada janela."""
    destino = tmp_path / "icone.ico"
    destino.write_bytes(b"nao e um ico de verdade")
    with mock.patch("tools.gerar_icone.gerar", side_effect=AssertionError("regerou")):
        assert tema._garantir_icone(destino) is True
    assert destino.read_bytes() == b"nao e um ico de verdade"


def test_garantir_icone_falha_macio_sem_derrubar_nada(tmp_path: Path) -> None:
    """Sem ícone o programa funciona igual — a falha não pode propagar."""
    with mock.patch("tools.gerar_icone.gerar", side_effect=OSError("disco cheio")):
        assert tema._garantir_icone(tmp_path / "icone.ico") is False


@pytest.mark.skipif(sys.platform != "win32", reason="o ícone da janela é API do Windows")
def test_icone_da_janela_passa_pelo_garantir(tmp_path: Path) -> None:
    """A janela não pode olhar só `exists()`: ela pede o arquivo ao gerador.

    Sem isso, num clone novo a barra de tarefas mostra a pena do Python até
    alguém rodar o empacotamento à mão.
    """
    faltando = tmp_path / "icone.ico"
    with mock.patch.object(tema, "_garantir_icone", return_value=False) as garantir:
        assert tema.icone_da_janela(mock.Mock(), faltando) is False
    garantir.assert_called_once_with(faltando)


# ---------- onde o app procura o ícone ----------


def test_icone_path_acha_o_do_projeto_fora_do_pacote(tmp_path: Path) -> None:
    esperado = tools.PROJECT_ROOT / "assets" / "icone.ico"
    assert tools.icone_path() == esperado


def test_icone_path_prefere_o_bundle_quando_congelado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `onedir` os `datas` vão para `_internal/`, não para a pasta do exe.

    Procurar só em `APP_DIR` deixaria a janela sem ícone no pacote — a falha
    exata que o Step 1 da Tarefa 3 manda conferir.
    """
    bundle = tmp_path / "_internal"
    (bundle / "assets").mkdir(parents=True)
    (bundle / "assets" / "icone.ico").write_bytes(b"ico")
    monkeypatch.setattr(tools.sys, "frozen", True, raising=False)
    monkeypatch.setattr(tools.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(tools, "APP_DIR", tmp_path / "sem-assets")
    assert tools.icone_path() == bundle / "assets" / "icone.ico"


def test_icone_path_prefere_a_pasta_do_exe_ao_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quem põe um `.ico` ao lado do executável manda mais que o do pacote."""
    ao_lado = tmp_path / "exe" / "assets"
    ao_lado.mkdir(parents=True)
    (ao_lado / "icone.ico").write_bytes(b"ico")
    bundle = tmp_path / "_internal" / "assets"
    bundle.mkdir(parents=True)
    (bundle / "icone.ico").write_bytes(b"ico")
    monkeypatch.setattr(tools.sys, "frozen", True, raising=False)
    monkeypatch.setattr(tools.sys, "_MEIPASS", str(tmp_path / "_internal"), raising=False)
    monkeypatch.setattr(tools, "APP_DIR", tmp_path / "exe")
    assert tools.icone_path() == ao_lado / "icone.ico"
