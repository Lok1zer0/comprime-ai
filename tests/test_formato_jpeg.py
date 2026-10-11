import dataclasses
import hashlib
import random
from pathlib import Path

import pytest
from PIL import (
    Image,
    ImageChops,
    ImageCms,
    ImageDraw,
    ImageOps,
    ImageStat,
    JpegImagePlugin,
)

from gifc import formatos
from gifc.compress import compress_file
from gifc.formatos.base import EncodeError
from gifc.formatos.jpeg import NEUTROS, SENTIDOS, TABELA, Params, codificar
from gifc.ladder import Toggles, aplicar_toggles, target_width
from gifc.probe import Formato, probe
from gifc.tools import Tools

TODOS = Toggles(True, True, True, True)
# JPEG não usa nenhuma ferramenta externa: tudo vai pelo Pillow.
SEM_FERRAMENTAS = Tools("ffmpeg", "ffprobe", None, oxipng=None)

ORIENTACAO = 0x0112


# --- fixtures de imagem ---------------------------------------------------


def _foto(destino: Path, tamanho=(640, 480), qualidade=95, **save) -> Path:
    """Foto sintética: ruído de baixa frequência (textura) sobre um degradê,
    com bordas nítidas — compressível, mas não trivial, como uma foto."""
    rng = random.Random(7)
    pequeno = Image.frombytes(
        "RGB", (80, 60), bytes(rng.randrange(256) for _ in range(80 * 60 * 3))
    )
    img = pequeno.resize(tamanho, Image.Resampling.BICUBIC)
    grad = Image.linear_gradient("L").resize(tamanho)
    img = Image.blend(img, Image.merge("RGB", (grad, grad, grad)), 0.5)
    d = ImageDraw.Draw(img)
    d.rectangle((40, 40, 200, 160), outline=(255, 255, 255), width=4)
    d.ellipse((300, 200, 460, 360), fill=(220, 40, 40))
    img.save(destino, "JPEG", quality=qualidade, **save)
    return destino


def _foto_rotulada(destino: Path, tamanho=(40, 20), orientacao=6) -> Path:
    """Armazenada como `tamanho` (esquerda vermelha, direita azul) e marcada
    com a orientação EXIF: o que o visualizador exibe é a imagem girada."""
    larg, alt = tamanho
    img = Image.new("RGB", tamanho)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, larg // 2 - 1, alt - 1), fill=(220, 30, 30))
    d.rectangle((larg // 2, 0, larg - 1, alt - 1), fill=(30, 30, 220))
    exif = Image.Exif()
    exif[ORIENTACAO] = orientacao
    img.save(destino, "JPEG", quality=95, exif=exif)
    return destino


def _exibida(path: Path) -> Image.Image:
    """A imagem como um visualizador a mostra: com o EXIF aplicado."""
    with Image.open(path) as img:
        img.load()
        return ImageOps.exif_transpose(img).convert("RGB")


def _diferenca_media(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- a tabela --------------------------------------------------------------


def test_tabela_tem_doze_degraus():
    assert len(TABELA) == 12


def test_comeca_em_qualidade_noventa_e_dois():
    assert TABELA[0].quality == 92


def test_tabela_e_exatamente_a_da_spec():
    esperada = [
        (92, 0, 100), (88, 0, 100), (85, 2, 100), (80, 2, 100),
        (75, 2, 100), (70, 2, 100), (65, 2, 100), (60, 2, 85),
        (55, 2, 85), (50, 2, 70), (45, 2, 70), (40, 2, 50),
    ]
    assert [(p.quality, p.subsampling, p.scale_pct) for p in TABELA] == esperada


def test_sentido_de_subsampling_e_maior_mais_agressivo():
    """A inversão: na convenção do Pillow 0 é 4:4:4 (menos agressivo) e 2 é
    4:2:0 (mais). Declarar 'menor' aqui faria a invariante aceitar uma escada
    que VOLTA de 4:2:0 para 4:4:4."""
    assert SENTIDOS["subsampling"] == "maior"
    assert SENTIDOS["quality"] == "menor"
    assert SENTIDOS["scale_pct"] == "menor"


def test_sentidos_declarados_cobrem_todos_os_campos():
    campos = {f.name for f in dataclasses.fields(Params)}
    assert set(SENTIDOS) == campos


def test_neutros_sao_o_extremo_menos_agressivo():
    assert NEUTROS == {"scale_pct": 100}


def test_escala_desligada_deixa_toda_a_escada_em_cem():
    info = type("I", (), {"fps": 10.0})()
    escada = aplicar_toggles(
        TABELA, info, Toggles(scale=False), NEUTROS  # type: ignore[arg-type]
    )
    assert escada and all(p.scale_pct == 100 for p in escada)


def test_o_jpeg_esta_registrado_com_extensao_jpg():
    assert Formato.JPEG in formatos.registrados()
    assert formatos.para(Formato.JPEG).TABELA is TABELA
    assert formatos.extensao(Formato.JPEG) == ".jpg"


# --- a codificação ---------------------------------------------------------


@pytest.mark.integration
def test_jpeg_comprime_de_verdade(tmp_path):
    """Foto salva em qualidade 95; o degrau 4 (q75) reduz substancialmente."""
    src = _foto(tmp_path / "foto.jpg", qualidade=95)
    dst = tmp_path / "saida.jpg"
    tamanho = codificar(src, dst, TABELA[4], probe(src), SEM_FERRAMENTAS)
    assert tamanho == dst.stat().st_size
    assert tamanho < src.stat().st_size * 0.6


@pytest.mark.integration
def test_qualidade_menor_gera_arquivo_menor(tmp_path):
    src = _foto(tmp_path / "foto.jpg")
    info = probe(src)
    tamanhos = [
        codificar(src, tmp_path / f"s{i}.jpg", TABELA[i], info, SEM_FERRAMENTAS)
        for i in (0, 4, 6)
    ]
    assert tamanhos == sorted(tamanhos, reverse=True)
    assert len(set(tamanhos)) == 3


@pytest.mark.integration
def test_subsampling_do_degrau_e_o_que_sai_no_arquivo(tmp_path):
    """O Pillow só honra `subsampling` quando passado explícito; sem isso os
    degraus 0 e 1 (4:4:4) sairiam 4:2:0 sem ninguém notar, pois o arquivo
    continuaria menor."""
    src = _foto(tmp_path / "foto.jpg")
    info = probe(src)
    for indice, esperado in ((0, 0), (1, 0), (2, 2), (11, 2)):
        dst = tmp_path / f"s{indice}.jpg"
        codificar(src, dst, TABELA[indice], info, SEM_FERRAMENTAS)
        with Image.open(dst) as img:
            assert JpegImagePlugin.get_sampling(img) == esperado, indice


@pytest.mark.integration
def test_a_qualidade_do_degrau_e_a_que_sai_no_arquivo(tmp_path):
    """Sem comparar o tamanho: relê as tabelas de quantização e confirma que
    o q92 quantiza mais fino que o q40 em todos os coeficientes somados."""
    src = _foto(tmp_path / "foto.jpg")
    info = probe(src)
    somas = {}
    for indice in (0, 11):
        dst = tmp_path / f"s{indice}.jpg"
        codificar(src, dst, TABELA[indice], info, SEM_FERRAMENTAS)
        with Image.open(dst) as img:
            somas[indice] = sum(sum(t) for t in img.quantization.values())
    assert somas[0] < somas[11]


@pytest.mark.integration
def test_progressive_esta_ligado_na_saida(tmp_path):
    src = _foto(tmp_path / "foto.jpg", progressive=False)
    info = probe(src)
    with Image.open(src) as original:
        assert not original.info.get("progressive")
    for indice in (0, 5, 11):
        dst = tmp_path / f"s{indice}.jpg"
        codificar(src, dst, TABELA[indice], info, SEM_FERRAMENTAS)
        with Image.open(dst) as img:
            assert img.info.get("progressive") or img.info.get("progression")


@pytest.mark.integration
def test_resultado_abre_como_jpeg_valido(tmp_path):
    src = _foto(tmp_path / "foto.jpg")
    dst = tmp_path / "out" / "saida.jpg"   # a pasta ainda não existe
    codificar(src, dst, TABELA[3], probe(src), SEM_FERRAMENTAS)
    assert dst.read_bytes()[:3] == b"\xff\xd8\xff"
    with Image.open(dst) as img:
        img.load()  # decodifica tudo: falha se o JPEG estiver corrompido
        assert img.format == "JPEG"
    assert probe(dst).formato is Formato.JPEG


@pytest.mark.integration
def test_dimensoes_preservadas_quando_escala_e_cem(tmp_path):
    src = _foto(tmp_path / "foto.jpg", tamanho=(640, 480))
    dst = tmp_path / "saida.jpg"
    codificar(src, dst, TABELA[0], probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as img:
        assert img.size == (640, 480)


@pytest.mark.integration
def test_escala_reduz_a_largura_pela_regra_do_projeto(tmp_path):
    src = _foto(tmp_path / "foto.jpg", tamanho=(640, 480))
    info = probe(src)
    dst = tmp_path / "saida.jpg"
    codificar(src, dst, TABELA[11], info, SEM_FERRAMENTAS)  # 50%
    with Image.open(dst) as img:
        assert img.width == target_width(info.width, 50) == 320
        assert img.height == 240


@pytest.mark.integration
def test_escala_usa_filtro_de_qualidade_e_nao_vizinho_mais_proximo(tmp_path):
    """Um padrão de linhas finas reduzido pela metade com vizinho mais
    próximo fica serrilhado (preto/branco puro); com LANCZOS vira cinza."""
    img = Image.new("RGB", (640, 480), "white")
    d = ImageDraw.Draw(img)
    for x in range(0, 640, 2):
        d.line([(x, 0), (x, 479)], fill="black")
    src = tmp_path / "listras.jpg"
    img.save(src, "JPEG", quality=95, subsampling=0)
    dst = tmp_path / "saida.jpg"
    codificar(src, dst, TABELA[11], probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as saida:
        recorte = saida.convert("L").crop((40, 40, 280, 200))
    assert 90 < ImageStat.Stat(recorte).mean[0] < 165   # cinza médio
    minimo, maximo = recorte.getextrema()
    assert minimo > 40 and maximo < 215                 # sem preto/branco puro


# --- orientação EXIF -------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("indice", [0, 4, 11])
def test_foto_com_orientacao_exif_nao_sai_girada(tmp_path, indice):
    """40x20 armazenada, tag 6, exibida 20x40. A saída precisa ser EXIBIDA
    igual à entrada — vale em degraus com e sem escala."""
    src = _foto_rotulada(tmp_path / "girada.jpg")
    assert _exibida(src).size == (20, 40)
    dst = tmp_path / "saida.jpg"
    codificar(src, dst, TABELA[indice], probe(src), SEM_FERRAMENTAS)
    entrada, saida = _exibida(src), _exibida(dst)
    if TABELA[indice].scale_pct == 100:
        assert saida.size == entrada.size == (20, 40)
        # Mesma orientação E mesmo conteúdo: comparar só o .size não pegaria
        # um giro de 180 graus.
        assert _diferenca_media(entrada, saida) < 12
    else:
        # Escalada: continua retrato, e o vermelho continua em cima, como
        # a entrada o exibia.
        assert saida.height > saida.width
        topo = saida.getpixel((saida.width // 2, saida.height // 4))
        base = saida.getpixel((saida.width // 2, 3 * saida.height // 4))
        assert topo[0] > 150 > topo[2] and base[2] > 150 > base[0]


@pytest.mark.integration
def test_tag_de_orientacao_nao_sobra_depois_de_girar_os_pixels(tmp_path):
    """Os pixels foram girados de verdade; a tag que sobrasse giraria a foto
    uma segunda vez em qualquer visualizador."""
    src = _foto_rotulada(tmp_path / "girada.jpg")
    dst = tmp_path / "saida.jpg"
    codificar(src, dst, TABELA[0], probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as img:
        assert img.size == (20, 40)
        assert img.getexif().get(ORIENTACAO) in (None, 1)


@pytest.mark.integration
@pytest.mark.parametrize("orientacao", [2, 3, 4, 5, 6, 7, 8])
def test_todas_as_orientacoes_saem_como_entraram(tmp_path, orientacao):
    src = _foto_rotulada(tmp_path / "e.jpg", orientacao=orientacao)
    dst = tmp_path / "s.jpg"
    codificar(src, dst, TABELA[0], probe(src), SEM_FERRAMENTAS)
    entrada, saida = _exibida(src), _exibida(dst)
    assert saida.size == entrada.size
    assert _diferenca_media(entrada, saida) < 12


@pytest.mark.integration
def test_escala_de_foto_rotacionada_usa_o_lado_certo(tmp_path):
    """400x200 armazenada, tag 6: exibida 200x400. A 50% o lado EXIBIDO cai
    à metade nos dois eixos — 100x200. Escalar pela largura ARMAZENADA (400)
    daria 200 de largura: nenhuma redução do que se vê."""
    src = _foto_rotulada(tmp_path / "girada.jpg", tamanho=(400, 200))
    assert _exibida(src).size == (200, 400)
    dst = tmp_path / "saida.jpg"
    codificar(src, dst, TABELA[11], probe(src), SEM_FERRAMENTAS)  # 50%
    assert _exibida(dst).size == (100, 200)


@pytest.mark.integration
def test_orientacao_nao_apaga_o_resto_do_exif(tmp_path):
    exif = Image.Exif()
    exif[ORIENTACAO] = 6
    exif[0x010F] = "Fabricante de Teste"     # Make
    exif[0x0110] = "Modelo de Teste"         # Model
    src = tmp_path / "e.jpg"
    Image.new("RGB", (40, 20), "gray").save(src, "JPEG", exif=exif)
    dst = tmp_path / "s.jpg"
    codificar(src, dst, TABELA[0], probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as img:
        saida = img.getexif()
        assert saida.get(0x010F) == "Fabricante de Teste"
        assert saida.get(0x0110) == "Modelo de Teste"


# --- cor -------------------------------------------------------------------


@pytest.mark.integration
def test_perfil_icc_sobrevive_a_escala_e_a_recompressao(tmp_path):
    perfil = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = tmp_path / "icc.jpg"
    Image.new("RGB", (200, 100), "teal").save(src, "JPEG", icc_profile=perfil)
    info = probe(src)
    for indice in (0, 11):   # sem escala e com escala (que descarta o info)
        dst = tmp_path / f"s{indice}.jpg"
        codificar(src, dst, TABELA[indice], info, SEM_FERRAMENTAS)
        with Image.open(dst) as img:
            assert img.info.get("icc_profile") == perfil, indice


@pytest.mark.integration
def test_jpeg_em_tons_de_cinza_continua_em_tons_de_cinza(tmp_path):
    src = tmp_path / "cinza.jpg"
    Image.linear_gradient("L").resize((200, 100)).save(src, "JPEG")
    dst = tmp_path / "s.jpg"
    codificar(src, dst, TABELA[4], probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as img:
        assert img.mode == "L"


@pytest.mark.integration
def test_entrada_cmyk_vira_rgb_sem_inverter_as_cores(tmp_path):
    src = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (64, 64), (0, 255, 255, 0)).save(src, "JPEG", quality=95)
    with Image.open(src) as original:
        esperado = original.convert("RGB").getpixel((32, 32))
    dst = tmp_path / "s.jpg"
    codificar(src, dst, TABELA[0], probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as img:
        assert img.mode == "RGB"
        r, g, b = img.getpixel((32, 32))
    assert abs(r - esperado[0]) < 25 and abs(g - esperado[1]) < 25
    assert abs(b - esperado[2]) < 25
    assert r > 200 and g < 60 and b < 60     # vermelho, não ciano


@pytest.mark.integration
def test_entrada_com_alfa_e_achatada_sobre_branco(tmp_path):
    """Um JPEG não tem alfa. Converter RGBA direto para RGB descartaria o
    canal e mostraria o que estiver por baixo (preto), não o branco."""
    src = tmp_path / "logo.png"
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((20, 20, 44, 44), fill=(200, 30, 30, 255))
    img.save(src)
    dst = tmp_path / "s.jpg"
    codificar(src, dst, TABELA[0], probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as saida:
        rgb = saida.convert("RGB")
        canto, centro = rgb.getpixel((2, 2)), rgb.getpixel((32, 32))
    assert min(canto) > 240
    assert centro[0] > 150 and centro[1] < 80


# --- garantias gerais ------------------------------------------------------


@pytest.mark.integration
def test_nunca_modifica_o_original(tmp_path):
    src = _foto(tmp_path / "foto.jpg")
    antes = _sha(src)
    info = probe(src)
    for indice in (0, 4, 11):
        codificar(src, tmp_path / f"s{indice}.jpg", TABELA[indice], info,
                  SEM_FERRAMENTAS)
    compress_file(src, src.stat().st_size // 3, TODOS, SEM_FERRAMENTAS)
    assert _sha(src) == antes


@pytest.mark.integration
def test_arquivo_corrompido_vira_encodeerror_em_portugues(tmp_path):
    ok = _foto(tmp_path / "ok.jpg")
    src = tmp_path / "ruim.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0" + b"isto nao e um jpeg" * 5)
    dst = tmp_path / "s.jpg"
    with pytest.raises(EncodeError, match="JPEG"):
        codificar(src, dst, TABELA[0], probe(ok), SEM_FERRAMENTAS)
    assert not dst.exists()


@pytest.mark.integration
def test_compress_file_encaixa_foto_num_alvo_apertado(tmp_path):
    src = _foto(tmp_path / "foto.jpg", qualidade=95)
    alvo = src.stat().st_size // 4
    r = compress_file(src, alvo, TODOS, SEM_FERRAMENTAS)
    assert r.hit_target is True
    assert r.final_bytes <= alvo
    assert r.dst.suffix == ".jpg"
    with Image.open(r.dst) as img:
        img.load()
