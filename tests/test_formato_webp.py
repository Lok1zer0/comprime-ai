import dataclasses
import hashlib
import random
import struct
from pathlib import Path

import pytest
from PIL import (
    Image,
    ImageChops,
    ImageCms,
    ImageDraw,
    ImageOps,
    ImageStat,
)

from gifc import formatos
from gifc.formatos.base import EncodeError
from gifc.formatos.webp import NEUTROS, SENTIDOS, TABELA, Params, codificar
from gifc.ladder import Toggles, aplicar_toggles, target_width
from gifc.probe import Formato, probe
from gifc.tools import Tools

# WebP não usa nenhuma ferramenta externa: tudo vai pelo Pillow.
SEM_FERRAMENTAS = Tools("ffmpeg", "ffprobe", None, oxipng=None)

ORIENTACAO = 0x0112


# --- fixtures de imagem ---------------------------------------------------


def _textura(tamanho=(640, 480)) -> Image.Image:
    """Ruído de baixa frequência sobre um degradê, com bordas nítidas:
    compressível, mas não trivial, como uma foto."""
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
    return img


def _foto_jpeg(destino: Path, tamanho=(640, 480), **save) -> Path:
    _textura(tamanho).save(destino, "JPEG", quality=95, **save)
    return destino


def _foto_png(destino: Path, tamanho=(640, 480)) -> Path:
    _textura(tamanho).save(destino, "PNG")
    return destino


def _logo_rgba(destino: Path, tamanho=(200, 120), **save) -> Path:
    """Logo: círculo azul opaco, um retângulo vermelho semitransparente e
    fundo totalmente transparente, cujo RGB escondido é PRETO: o pior caso
    para quem descarta o canal alfa e para quem escala sem pré-multiplicar."""
    img = Image.new("RGBA", tamanho, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    larg, alt = tamanho
    d.ellipse((10, 10, 110, 110), fill=(20, 40, 220, 255))
    d.rectangle((130, 20, larg - 10, alt - 20), fill=(220, 30, 30, 128))
    img.save(destino, "PNG", **save)
    return destino


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sobre(img: Image.Image, cor) -> Image.Image:
    """`img` como se vê sobre um fundo liso `cor`."""
    rgba = img.convert("RGBA")
    fundo = Image.new("RGBA", rgba.size, cor)
    return Image.alpha_composite(fundo, rgba).convert("RGB")


def _diferenca_media(a: Image.Image, b: Image.Image) -> float:
    return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3


def _exibida(path: Path) -> Image.Image:
    with Image.open(path) as img:
        img.load()
        return ImageOps.exif_transpose(img).convert("RGB")


def _degrade_horizontal() -> Image.Image:
    """200x100, preto à esquerda e branco à direita."""
    linha = bytes(x * 255 // 199 for x in range(200))
    return Image.frombytes("L", (200, 100), linha * 100)


def _perfil_de_cinza(gama: float) -> bytes:
    """Um perfil ICC de tons de cinza mínimo mas válido (curva de gama),
    montado à mão: o LittleCMS do Pillow o aceita."""

    def s15(v: float) -> bytes:
        return struct.pack(">i", round(v * 65536))

    desc = b"gifc gray\x00"
    tags = {
        b"wtpt": b"XYZ \x00\x00\x00\x00" + s15(0.9642) + s15(1.0) + s15(0.8249),
        b"kTRC": b"curv\x00\x00\x00\x00" + struct.pack(">IH", 1, round(gama * 256)) + b"\x00\x00",
        b"desc": b"desc\x00\x00\x00\x00" + struct.pack(">I", len(desc)) + desc + b"\x00" * 78,
        b"cprt": b"text\x00\x00\x00\x00public domain\x00",
    }
    base = 128 + 4 + 12 * len(tags)
    tabela, corpo = b"", b""
    for assinatura, dados in tags.items():
        tabela += assinatura + struct.pack(">II", base + len(corpo), len(dados))
        corpo += dados + b"\x00" * (-len(dados) % 4)
    cab = struct.pack(
        ">I4sI4s4s4s", base + len(corpo), b"lcms", 0x02100000, b"mntr", b"GRAY", b"XYZ "
    )
    cab += b"\x00" * 12 + b"acsp" + b"\x00" * 20
    cab += s15(0.9642) + s15(1.0) + s15(0.8249)
    return cab.ljust(128, b"\x00") + struct.pack(">I", len(tags)) + tabela + corpo


def _degrau(**campos) -> Params:
    """O ÚNICO degrau da tabela com estes valores (`quality=70`,
    `scale_pct=50`, `quality=None` para o sem perda). Os testes escolhem o
    degrau pela propriedade de que dependem, nunca pela posição: se a tabela
    mudar, o teste ou segue medindo o que diz medir ou falha aqui."""
    achados = [
        p for p in TABELA
        if all(getattr(p, k) == v for k, v in campos.items())
    ]
    assert len(achados) == 1, f"{campos} casa com {len(achados)} degraus"
    return achados[0]


def _mais_agressivo() -> Params:
    return TABELA[-1]


def _id_qualidade(q) -> str:
    return "sem-perda" if q is None else f"q{q}"


QUALIDADES_DE_ALFA = [None, 90, 65, 45]


def _alfa_em(img: Image.Image, x: int, y: int) -> int:
    return img.convert("RGBA").getchannel("A").getpixel((x, y))


# --- a tabela --------------------------------------------------------------


def test_tabela_e_exatamente_a_da_spec():
    esperada = [
        (None, 100), (90, 100), (85, 100), (80, 100), (75, 100), (70, 100),
        (65, 100), (60, 85), (55, 85), (50, 70), (45, 50),
    ]
    assert [(p.quality, p.scale_pct) for p in TABELA] == esperada


def test_so_o_degrau_zero_e_sem_perda_e_so_a_cem_por_cento():
    """Escalar já é perda: um degrau lossless com escala seria uma
    contradição."""
    sem_perda = [i for i, p in enumerate(TABELA) if p.quality is None]
    assert sem_perda == [0]
    assert TABELA[0].scale_pct == 100


def test_sentidos_declarados_cobrem_todos_os_campos():
    campos = {f.name for f in dataclasses.fields(Params)}
    assert set(SENTIDOS) == campos
    assert SENTIDOS == {"quality": "menor", "scale_pct": "menor"}


def test_neutros_sao_o_extremo_menos_agressivo():
    assert NEUTROS == {"scale_pct": 100}


def test_escala_desligada_deixa_toda_a_escada_em_cem():
    info = type("I", (), {"fps": 10.0})()
    escada = aplicar_toggles(
        TABELA, info, Toggles(scale=False), NEUTROS  # type: ignore[arg-type]
    )
    assert escada and all(p.scale_pct == 100 for p in escada)


def test_o_webp_esta_registrado_com_extensao_webp():
    assert Formato.WEBP in formatos.registrados()
    assert formatos.para(Formato.WEBP).TABELA is TABELA
    assert formatos.extensao(Formato.WEBP) == ".webp"


# --- o formato de saída ----------------------------------------------------


@pytest.mark.parametrize("formato", list(Formato))
def test_formato_de_saida_sem_conversao_mantem_a_entrada(formato):
    assert formatos.formato_de_saida(formato, False) is formato


@pytest.mark.parametrize("formato", [Formato.PNG, Formato.JPEG])
def test_formato_de_saida_converte_png_e_jpeg(formato):
    assert formatos.formato_de_saida(formato, True) is Formato.WEBP


def test_formato_de_saida_nunca_converte_gif():
    assert formatos.formato_de_saida(Formato.GIF, True) is Formato.GIF


def test_formato_de_saida_de_webp_e_webp():
    assert formatos.formato_de_saida(Formato.WEBP, True) is Formato.WEBP
    assert formatos.formato_de_saida(Formato.WEBP, False) is Formato.WEBP


def test_extensao_de_jpeg_e_jpg_e_nao_jpeg():
    """A armadilha: `Formato.JPEG.value == "jpeg"`, mas a extensão é ".jpg"."""
    assert Formato.JPEG.value == "jpeg"
    assert formatos.extensao(Formato.JPEG) == ".jpg"


def test_mapa_de_extensoes_cobre_todos_os_formatos():
    for formato in Formato:
        assert formatos.extensao(formato).startswith(".")


def test_a_saida_de_um_jpeg_convertido_leva_extensao_webp():
    saida = formatos.formato_de_saida(Formato.JPEG, True)
    assert formatos.extensao(saida) == ".webp"


# --- a codificação ---------------------------------------------------------


@pytest.mark.integration
def test_webp_comprime_de_verdade(tmp_path):
    """Foto em PNG e em JPEG q95: o degrau 4 (q70) reduz substancialmente."""
    png = _foto_png(tmp_path / "foto.png")
    jpg = _foto_jpeg(tmp_path / "foto.jpg")
    for src, limite in ((png, 0.4), (jpg, 0.7)):
        dst = tmp_path / f"{src.stem}.webp"
        tamanho = codificar(
            src, dst, _degrau(quality=70), probe(src), SEM_FERRAMENTAS
        )
        assert tamanho == dst.stat().st_size
        assert tamanho < src.stat().st_size * limite, src.name


@pytest.mark.integration
def test_qualidade_menor_gera_arquivo_menor(tmp_path):
    src = _foto_png(tmp_path / "foto.png")
    info = probe(src)
    tamanhos = [
        codificar(
            src, tmp_path / f"s{i}.webp", _degrau(quality=q), info,
            SEM_FERRAMENTAS,
        )
        for i, q in enumerate((None, 90, 70, 65))
    ]
    assert tamanhos == sorted(tamanhos, reverse=True)
    assert len(set(tamanhos)) == 4


@pytest.mark.integration
def test_qualidade_e_metodo_chegam_ao_pillow(tmp_path, monkeypatch):
    """`method=6` é fixo em todos os degraus; o degrau sem perda pede
    `lossless=True` (e nenhuma qualidade), os outros, a qualidade do degrau."""
    vistos = []
    original = Image.Image.save

    def espia(self, fp, format=None, **params):
        vistos.append((
            params.get("quality"), params.get("method"),
            bool(params.get("lossless")),
        ))
        return original(self, fp, format, **params)

    monkeypatch.setattr(Image.Image, "save", espia)
    src = _foto_png(tmp_path / "foto.png", (64, 48))
    info = probe(src)
    for i, p in enumerate((_degrau(quality=None), _degrau(quality=90),
                           _mais_agressivo())):
        vistos.clear()
        codificar(src, tmp_path / f"s{i}.webp", p, info, SEM_FERRAMENTAS)
        assert vistos == [(p.quality, 6, p.quality is None)]


@pytest.mark.integration
def test_resultado_abre_como_webp_valido(tmp_path):
    src = _foto_png(tmp_path / "foto.png")
    dst = tmp_path / "out" / "saida.webp"   # a pasta ainda não existe
    codificar(src, dst, _degrau(quality=80), probe(src), SEM_FERRAMENTAS)
    dados = dst.read_bytes()
    assert dados[:4] == b"RIFF" and dados[8:12] == b"WEBP"
    with Image.open(dst) as img:
        img.load()  # decodifica tudo: falha se o WebP estiver corrompido
        assert img.format == "WEBP"
        assert img.size == (640, 480)
    assert probe(dst).formato is Formato.WEBP


def _captura_de_tela(destino: Path) -> Path:
    """Arte chapada com texto: o caso em que o WebP com perda DOBRA o PNG."""
    img = Image.new("RGB", (640, 400), "white")
    d = ImageDraw.Draw(img)
    for y in range(0, 400, 24):
        d.rectangle((20, y + 4, 400, y + 18), fill=(40, 90, 200))
        d.text((30, y + 5), "texto de exemplo", fill="white")
    img.save(destino)
    return destino


def _identicas(a: Image.Image, b: Image.Image) -> bool:
    """Pixel a pixel, alfa incluído. O alfa é comparado como canal PRÓPRIO
    (`getextrema` de RGBA misturaria cor e alfa) e a cor, vista sobre magenta:
    o RGB escondido sob alfa 0 não aparece e o codificador pode descartá-lo."""
    a, b = a.convert("RGBA"), b.convert("RGBA")
    if a.size != b.size:
        return False
    alfa = ImageChops.difference(a.getchannel("A"), b.getchannel("A"))
    visivel = ImageChops.difference(
        _sobre(a, (255, 0, 255, 255)), _sobre(b, (255, 0, 255, 255))
    )
    return alfa.getextrema() == (0, 0) and all(
        lo == hi == 0 for lo, hi in visivel.getextrema()
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "origem", ["png-foto", "png-alfa", "png-paleta", "jpg"]
)
def test_degrau_sem_perda_reproduz_os_pixels_da_fonte(tmp_path, origem):
    """O único degrau do projeto em que a igualdade de pixels é afirmável:
    decodifica a saída e compara com a fonte, alfa incluído."""
    if origem == "png-foto":
        src = _foto_png(tmp_path / "f.png")
    elif origem == "png-alfa":
        src = _logo_rgba(tmp_path / "f.png")
    elif origem == "png-paleta":
        with Image.open(_logo_rgba(tmp_path / "tmp.png")) as logo:
            logo.convert("RGBA").quantize(
                colors=16, method=Image.Quantize.FASTOCTREE
            ).save(tmp_path / "f.png")
        src = tmp_path / "f.png"
    else:
        src = _foto_jpeg(tmp_path / "f.jpg")
    dst = tmp_path / "saida.webp"
    codificar(src, dst, _degrau(quality=None), probe(src), SEM_FERRAMENTAS)
    with Image.open(src) as o, Image.open(dst) as s:
        original, saida = o.convert("RGBA"), s.convert("RGBA")
    assert _identicas(original, saida)
    if origem == "png-alfa":
        assert saida.getchannel("A").getextrema() == (0, 255)   # há alfa de verdade


def test_o_comparador_de_pixels_reconhece_diferencas():
    """Trava o proprio `_identicas`: um teste de igualdade que sempre dissesse
    'igual' não provaria nada."""
    base = Image.new("RGBA", (8, 8), (10, 20, 30, 255))
    assert _identicas(base, base.copy())
    cor = base.copy()
    cor.putpixel((3, 3), (11, 20, 30, 255))
    assert not _identicas(base, cor)
    alfa = base.copy()
    alfa.putpixel((3, 3), (10, 20, 30, 254))
    assert not _identicas(base, alfa)


@pytest.mark.integration
@pytest.mark.parametrize("arte", ["logo", "captura"])
def test_sem_perda_encolhe_arte_chapada_que_o_lossy_incharia(tmp_path, arte):
    """A razão de existir do degrau 0: em arte chapada o WebP com perda sai
    MAIOR que o PNG, e o sem perda, bem menor."""
    if arte == "logo":
        src = _logo_rgba(tmp_path / "a.png")
    else:
        src = _captura_de_tela(tmp_path / "a.png")
    info = probe(src)
    sem_perda = codificar(
        src, tmp_path / "sp.webp", _degrau(quality=None), info, SEM_FERRAMENTAS
    )
    com_perda = codificar(
        src, tmp_path / "cp.webp", _degrau(quality=90), info, SEM_FERRAMENTAS
    )
    assert sem_perda < src.stat().st_size * 0.7
    assert sem_perda < com_perda / 2


@pytest.mark.integration
@pytest.mark.parametrize("nome", ["foto.png", "foto.jpg"])
def test_nunca_toca_na_entrada(tmp_path, nome):
    src = tmp_path / nome
    (_foto_png if nome.endswith("png") else _foto_jpeg)(src)
    antes = _sha(src)
    codificar(
        src, tmp_path / "s.webp", _mais_agressivo(), probe(src), SEM_FERRAMENTAS
    )
    assert _sha(src) == antes


@pytest.mark.integration
def test_falha_vira_encode_error_em_portugues_e_nao_deixa_saida(tmp_path):
    src = tmp_path / "quebrado.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"lixo" * 20)
    dst = tmp_path / "saida.webp"
    info = probe(_foto_png(tmp_path / "ok.png", (64, 48)))
    with pytest.raises(EncodeError, match="WebP"):
        codificar(src, dst, _degrau(quality=90), info, SEM_FERRAMENTAS)
    assert not dst.exists()


@pytest.mark.integration
def test_webp_de_entrada_continua_webp(tmp_path):
    src = tmp_path / "entrada.webp"
    _textura().save(src, "WEBP", lossless=True)
    dst = tmp_path / "saida.webp"
    tamanho = codificar(
        src, dst, _degrau(quality=70), probe(src), SEM_FERRAMENTAS
    )
    assert tamanho < src.stat().st_size
    with Image.open(dst) as img:
        assert img.format == "WEBP" and img.size == (640, 480)


# --- transparência ---------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("quality", QUALIDADES_DE_ALFA, ids=_id_qualidade)
def test_png_transparente_convertido_mantem_alfa(tmp_path, quality):
    """O defeito silencioso: um logo que volta com fundo preto ou branco passa
    por qualquer checagem de tamanho. Compara o alfa e as cores visíveis
    sobre dois fundos diferentes."""
    src = _logo_rgba(tmp_path / "logo.png")
    dst = tmp_path / "logo.webp"
    degrau = _degrau(quality=quality)
    codificar(src, dst, degrau, probe(src), SEM_FERRAMENTAS)
    with Image.open(src) as o, Image.open(dst) as s:
        original, saida = o.convert("RGBA"), s.convert("RGBA")
    if degrau.scale_pct != 100:
        original = original.resize(saida.size, Image.Resampling.LANCZOS)

    # O canal alfa existe e o fundo continua TRANSPARENTE. Lê-se o canal "A"
    # (o getextrema de RGBA inteiro não distingue alfa de cor).
    alfa_o, alfa_s = original.getchannel("A"), saida.getchannel("A")
    assert alfa_s.getextrema() == (0, 255)
    assert alfa_s.getpixel((saida.width - 3, 3)) == 0         # canto: vazio
    centro_do_circulo = (60 * saida.width // 200, 60 * saida.height // 120)
    assert alfa_s.getpixel(centro_do_circulo) == 255
    assert ImageStat.Stat(ImageChops.difference(alfa_o, alfa_s)).mean[0] < 6

    # Vistas sobre magenta e sobre branco, as duas imagens têm de coincidir:
    # um fundo que virou preto/branco sólido apareceria numa das duas.
    for fundo in ((255, 0, 255, 255), (255, 255, 255, 255)):
        assert _diferenca_media(_sobre(original, fundo), _sobre(saida, fundo)) < 8


@pytest.mark.integration
def test_alfa_com_escala_nao_ganha_halo_escuro(tmp_path):
    """Escalar RGBA sem pré-multiplicar mistura o PRETO escondido sob o fundo
    transparente nas bordas: um halo escuro em volta de um logo branco."""
    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((50, 50, 350, 250), fill=(255, 255, 255, 255))
    src = tmp_path / "branco.png"
    img.save(src)
    dst = tmp_path / "branco.webp"
    codificar(src, dst, _degrau(scale_pct=50), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as s:
        saida = s.convert("RGBA")
    alfa = saida.getchannel("A")
    sobre_preto = _sobre(saida, (0, 0, 0, 255))
    # Sobre preto, um pixel branco com alfa a deve valer ~a. Com o preto
    # escondido vazando para a borda ele sairia bem abaixo disso.
    bordas = [
        (x, y)
        for x in range(saida.width)
        for y in range(saida.height)
        if 40 < alfa.getpixel((x, y)) < 215
    ]
    assert bordas, "esperava pixels de borda semitransparentes"
    for x, y in bordas:
        assert sobre_preto.getpixel((x, y))[0] >= alfa.getpixel((x, y)) - 40


@pytest.mark.integration
def test_png_em_paleta_com_transparencia_mantem_alfa(tmp_path):
    """PNG paletizado com tRNS (o que esta própria ferramenta grava): sem
    converter para RGBA o fundo transparente vira uma cor de paleta."""
    with Image.open(_logo_rgba(tmp_path / "logo.png")) as logo:
        pal = logo.convert("RGBA").quantize(
            colors=16, method=Image.Quantize.FASTOCTREE
        )
    src = tmp_path / "paleta.png"
    pal.save(src)
    with Image.open(src) as p:
        assert p.mode == "P" and "transparency" in p.info
    # sem perda, com perda sem escala, e com escala
    for i, degrau in enumerate((_degrau(quality=None), _degrau(quality=90),
                                _degrau(scale_pct=50))):
        dst = tmp_path / f"paleta{i}.webp"
        codificar(src, dst, degrau, probe(src), SEM_FERRAMENTAS)
        with Image.open(dst) as s:
            saida = s.convert("RGBA")
        assert _alfa_em(saida, saida.width - 3, 3) == 0
        assert _alfa_em(saida, 60 * saida.width // 200, 60 * saida.height // 120) == 255


@pytest.mark.integration
def test_png_com_cor_chave_transparente_mantem_alfa(tmp_path):
    """tRNS de cor-chave em RGB: o 'alfa' não é um canal, é um atributo."""
    img = Image.new("RGB", (80, 60), (0, 255, 0))
    ImageDraw.Draw(img).rectangle((20, 15, 60, 45), fill=(200, 30, 30))
    src = tmp_path / "chave.png"
    img.save(src, transparency=(0, 255, 0))
    dst = tmp_path / "chave.webp"
    codificar(src, dst, _degrau(quality=90), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as s:
        saida = s.convert("RGBA")
    assert _alfa_em(saida, 2, 2) == 0
    assert _alfa_em(saida, 40, 30) == 255


@pytest.mark.integration
def test_cinza_com_alfa_mantem_alfa(tmp_path):
    img = Image.new("LA", (80, 60), (0, 0))
    ImageDraw.Draw(img).rectangle((20, 15, 60, 45), fill=(200, 255))
    src = tmp_path / "la.png"
    img.save(src)
    dst = tmp_path / "la.webp"
    codificar(src, dst, _degrau(quality=90), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as s:
        saida = s.convert("RGBA")
    assert _alfa_em(saida, 2, 2) == 0
    assert _alfa_em(saida, 40, 30) == 255


@pytest.mark.integration
def test_cinza_com_cor_chave_e_perfil_de_cinza_mantem_alfa(tmp_path):
    """A conversão do perfil de cinza devolve uma imagem nova sem o `info`:
    a cor-chave transparente tem de virar canal antes disso."""
    img = Image.new("L", (80, 60), 0)
    ImageDraw.Draw(img).rectangle((20, 15, 60, 45), fill=200)
    src = tmp_path / "chave_cinza.png"
    img.save(src, transparency=0, icc_profile=_perfil_de_cinza(1.0))
    dst = tmp_path / "saida.webp"
    codificar(src, dst, _degrau(quality=90), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as s:
        saida = s.convert("RGBA")
    assert _alfa_em(saida, 2, 2) == 0
    assert _alfa_em(saida, 40, 30) == 255


@pytest.mark.integration
def test_perfil_de_cinza_e_aplicado_na_conversao_para_rgb(tmp_path):
    """Gama 1.0 em vez de ~2.2: o cinza 128 só PARECE meio-tom se for
    reinterpretado; pelo perfil ele é bem mais claro em sRGB. Se o perfil
    fosse ignorado (convert direto) sairia 128."""
    src = tmp_path / "gama.png"
    Image.new("L", (64, 64), 128).save(src, icc_profile=_perfil_de_cinza(1.0))
    dst = tmp_path / "saida.webp"
    codificar(src, dst, _degrau(quality=90), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as s:
        assert s.mode == "RGB"
        assert (s.info.get("icc_profile") or b"")[16:20] != b"GRAY"
        r, g, b = s.getpixel((32, 32))
    assert r > 170 and abs(r - g) < 4 and abs(r - b) < 4


@pytest.mark.integration
def test_rgba_totalmente_opaco_sai_sem_canal_alfa(tmp_path):
    """Muitas ferramentas gravam PNG como RGBA com alfa 100% opaco; carregar
    um canal alfa inútil só incha o arquivo. (Quem garante isso é o
    libwebp; o teste trava o resultado, seja qual for o mecanismo.)"""
    img = _textura((200, 150)).convert("RGBA")
    src = tmp_path / "opaco.png"
    img.save(src)
    dst = tmp_path / "opaco.webp"
    codificar(src, dst, _degrau(quality=85), probe(src), SEM_FERRAMENTAS)
    assert b"ALPH" not in dst.read_bytes()
    with Image.open(dst) as s:
        assert s.mode == "RGB"


# --- escala ----------------------------------------------------------------


@pytest.mark.integration
def test_dimensoes_preservadas_quando_escala_e_cem(tmp_path):
    src = _foto_png(tmp_path / "foto.png")
    dst = tmp_path / "saida.webp"
    codificar(src, dst, _degrau(quality=90), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as img:
        assert img.size == (640, 480)


@pytest.mark.integration
def test_escala_reduz_a_largura_pela_regra_do_projeto(tmp_path):
    src = _foto_png(tmp_path / "foto.png")
    info = probe(src)
    dst = tmp_path / "saida.webp"
    codificar(src, dst, _degrau(scale_pct=50), info, SEM_FERRAMENTAS)
    with Image.open(dst) as img:
        assert img.width == target_width(info.width, 50) == 320
        assert img.height == 240


def _listras(destino: Path, modo: str) -> Path:
    img = Image.new("RGB", (640, 480), "white")
    d = ImageDraw.Draw(img)
    for x in range(0, 640, 2):
        d.line([(x, 0), (x, 479)], fill="black")
    if modo == "P":
        img = img.quantize(colors=2)
    elif modo == "L":
        img = img.convert("L")
    img.save(destino, "PNG")
    return destino


@pytest.mark.integration
@pytest.mark.parametrize("modo", ["RGB", "P", "L"])
def test_escala_usa_filtro_de_qualidade_e_nao_vizinho_mais_proximo(tmp_path, modo):
    """Linhas finas reduzidas pela metade com vizinho mais próximo ficam
    serrilhadas (preto/branco puro); com LANCZOS viram cinza. O Pillow cai no
    vizinho mais próximo em modo P (e 1): o caminho tem de sair deles antes de
    escalar. O caso L NÃO tem essa armadilha (o Pillow respeita o filtro em
    L); fica como guarda de regressão do resultado, não do contorno."""
    src = _listras(tmp_path / "listras.png", modo)
    with Image.open(src) as o:
        assert o.mode == modo
    dst = tmp_path / "saida.webp"
    codificar(src, dst, _degrau(scale_pct=50), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as saida:
        recorte = saida.convert("L").crop((40, 40, 280, 200))
    assert 90 < ImageStat.Stat(recorte).mean[0] < 165   # cinza médio
    minimo, maximo = recorte.getextrema()
    assert minimo > 40 and maximo < 215                 # sem preto/branco puro


# --- perfil ICC, EXIF e outros modos --------------------------------------


def _perfil_distinguivel() -> bytes:
    """Um perfil RGB válido e distinguível do sRGB padrão (assinatura do
    criador no cabeçalho), para provar que é o DA FONTE que sai."""
    base = bytearray(
        ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    )
    base[80:84] = b"gifc"
    return bytes(base)


@pytest.mark.integration
@pytest.mark.parametrize("origem", ["png", "jpg", "png-alfa"])
@pytest.mark.parametrize("quality", [None, 90, 45], ids=_id_qualidade)
def test_perfil_icc_e_preservado(tmp_path, origem, quality):
    perfil = _perfil_distinguivel()
    if origem == "png":
        src = tmp_path / "f.png"
        _textura().save(src, icc_profile=perfil)
    elif origem == "jpg":
        src = tmp_path / "f.jpg"
        _textura().save(src, "JPEG", quality=95, icc_profile=perfil)
    else:
        src = tmp_path / "f.png"
        with Image.open(_logo_rgba(tmp_path / "tmp.png")) as logo:
            logo.save(src, icc_profile=perfil)
    dst = tmp_path / "saida.webp"
    codificar(src, dst, _degrau(quality=quality), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as s:
        assert s.info.get("icc_profile") == perfil


@pytest.mark.integration
def test_jpeg_cmyk_vira_rgb_e_nao_leva_o_perfil_cmyk(tmp_path):
    """WebP só grava RGB. Um perfil CMYK colado em dados RGB faria o
    visualizador interpretar as cores errado."""
    falso = bytearray(128)
    falso[16:20] = b"CMYK"
    src = tmp_path / "cmyk.jpg"
    _textura().convert("CMYK").save(src, "JPEG", quality=95, icc_profile=bytes(falso))
    dst = tmp_path / "saida.webp"
    codificar(src, dst, _degrau(quality=90), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as s:
        assert s.mode == "RGB"
        assert (s.info.get("icc_profile") or b"")[16:20] != b"CMYK"
        saida = s.convert("RGB")
    with Image.open(src) as o:
        esperado = o.convert("RGB")
    assert _diferenca_media(esperado, saida) < 10


@pytest.mark.integration
def test_jpeg_cinza_vira_webp_rgb(tmp_path):
    src = tmp_path / "cinza.jpg"
    _degrade_horizontal().save(src, "JPEG")
    dst = tmp_path / "saida.webp"
    codificar(src, dst, _degrau(quality=90), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as s:
        assert s.mode == "RGB"
        esquerda, direita = s.getpixel((5, 50)), s.getpixel((195, 50))
    assert esquerda[0] < 40 and direita[0] > 215


@pytest.mark.integration
def test_png_cinza_de_16_bits_converte(tmp_path):
    img = _degrade_horizontal().convert("I")
    img = img.point(lambda v: v * 256).convert("I;16")  # type: ignore[arg-type]
    src = tmp_path / "c16.png"
    img.save(src)
    dst = tmp_path / "saida.webp"
    codificar(src, dst, _degrau(quality=90), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as s:
        cinza = s.convert("L")
    assert cinza.getpixel((5, 50)) < 40 and cinza.getpixel((195, 50)) > 215


def _foto_rotulada(destino: Path, tamanho=(40, 20), orientacao=6) -> Path:
    larg, alt = tamanho
    img = Image.new("RGB", tamanho)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, larg // 2 - 1, alt - 1), fill=(220, 30, 30))
    d.rectangle((larg // 2, 0, larg - 1, alt - 1), fill=(30, 30, 220))
    exif = Image.Exif()
    exif[ORIENTACAO] = orientacao
    img.save(destino, "JPEG", quality=95, exif=exif)
    return destino


@pytest.mark.integration
@pytest.mark.parametrize("quality", [None, 90, 45], ids=_id_qualidade)
def test_foto_com_orientacao_exif_nao_sai_girada(tmp_path, quality):
    src = _foto_rotulada(tmp_path / "girada.jpg", (400, 200))
    assert _exibida(src).size == (200, 400)
    dst = tmp_path / "saida.webp"
    degrau = _degrau(quality=quality)
    codificar(src, dst, degrau, probe(src), SEM_FERRAMENTAS)
    saida = _exibida(dst)
    assert saida.height == 2 * saida.width           # continua retrato
    if degrau.scale_pct != 100:
        # a escala parte da largura EXIBIDA (200), não da armazenada (400)
        assert saida.width == target_width(200, degrau.scale_pct)
    topo = saida.getpixel((saida.width // 2, saida.height // 4))
    base = saida.getpixel((saida.width // 2, 3 * saida.height // 4))
    assert topo[0] > 150 > topo[2] and base[2] > 150 > base[0]
    with Image.open(dst) as s:
        assert s.getexif().get(ORIENTACAO) in (None, 1)   # sem giro duplo


@pytest.mark.integration
def test_exif_da_foto_e_mantido(tmp_path):
    exif = Image.Exif()
    exif[0x010F] = "Camera Teste"      # Make
    src = tmp_path / "f.jpg"
    _textura((200, 150)).save(src, "JPEG", quality=95, exif=exif)
    dst = tmp_path / "saida.webp"
    codificar(src, dst, _degrau(quality=90), probe(src), SEM_FERRAMENTAS)
    with Image.open(dst) as s:
        assert s.getexif().get(0x010F) == "Camera Teste"
