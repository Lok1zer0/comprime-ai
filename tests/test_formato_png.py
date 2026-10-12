import dataclasses
import hashlib
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageCms, ImageDraw, ImageOps

from gifc import formatos
from gifc.compress import compress_file
from gifc.formatos.base import EncodeError
from gifc.formatos.png import (
    NEUTROS,
    SENTIDOS,
    TABELA,
    Params,
    build_oxipng_cmd,
    codificar,
)
from gifc.ladder import Toggles, aplicar_toggles, target_width
from gifc.probe import Formato, probe
from gifc.tools import BIN_DIR, MissingToolError, Tools, find_tools

TODOS = Toggles(True, True, True, True)


@pytest.fixture(scope="module")
def ferramentas() -> Tools:
    try:
        t = find_tools(BIN_DIR)
    except MissingToolError as exc:
        pytest.skip(f"ferramenta ausente: {exc.tool}")
    if t.oxipng is None:
        pytest.skip("oxipng não disponível em bin/")
    return t


# --- fixtures de imagem ---------------------------------------------------


def _png_de_interface(destino: Path) -> Path:
    """1280x800: áreas planas, um degradê de cabeçalho e muito texto — o
    caso de captura de tela que a spec mediu."""
    img = Image.new("RGB", (1280, 800), (245, 246, 248))
    d = ImageDraw.Draw(img)
    for x in range(1280):                       # barra superior em degradê
        t = x / 1279
        d.line([(x, 0), (x, 60)], fill=(int(30 + 90 * t), int(60 + 100 * t), 200))
    d.rectangle((0, 60, 220, 800), fill=(36, 41, 47))
    for i in range(14):                         # menu lateral
        d.rectangle((16, 90 + i * 40, 200, 114 + i * 40), fill=(52, 58, 66))
        d.text((24, 96 + i * 40), f"Item de menu {i}", fill=(230, 230, 230))
    for linha in range(30):                     # corpo: linhas de texto
        d.text((250, 90 + linha * 22),
               f"Linha {linha}: texto de exemplo da interface do aplicativo",
               fill=(20, 20, 20))
    d.rectangle((900, 100, 1240, 300), outline=(0, 120, 215), width=3)
    d.rectangle((905, 105, 1235, 295), fill=(255, 255, 255))
    # Um painel de degradê radial: milhares de cores, para que reduzir a
    # paleta destrua pixels de verdade (um teste de "sem perda" só significa
    # algo se quantizar for perda).
    radial = Image.radial_gradient("L").resize((330, 190))
    painel = Image.merge("RGB", (radial, radial.point(lambda v: 255 - v), radial))
    img.paste(painel, (905, 105))
    img.save(destino, optimize=True)
    return destino


def _png_gradiente_suave(destino: Path) -> Path:
    """Gradiente suave e previsível de 64x64: o PNG original é minúsculo
    (resíduos quase nulos nos filtros), então a tabela de 256 cores da paleta
    custa mais do que o ganho e o degrau 2 sai MAIOR que a entrada (+27%,
    medido, determinístico). Vale para imagens muito suaves e previsíveis;
    não para qualquer gradiente nem para fotografias reais.
    """
    w, h = 64, 64
    base = Image.linear_gradient("L").resize((w, h))
    radial = Image.radial_gradient("L").resize((w, h))
    img = Image.merge("RGB", (base, radial, base.point(lambda v: 255 - v)))
    img.save(destino, optimize=True)
    return destino


def _logo_transparente(destino: Path) -> Path:
    """RGBA 200x200: fundo totalmente transparente, disco vermelho opaco no
    centro e um disco verde semitransparente."""
    img = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((30, 30, 170, 170), fill=(200, 30, 30, 255))
    d.ellipse((80, 80, 120, 120), fill=(30, 200, 30, 128))
    img.save(destino, optimize=True)
    return destino


def _diferem(a: Image.Image, b: Image.Image):
    """Caixa dos pixels que diferem (None se idênticos), em RGBA.

    `alpha_only=False` é essencial: em RGBA o `getbbox()` padrão olha só o
    canal alfa e enxergaria "nenhuma diferença" entre duas imagens de cores
    totalmente diferentes e a mesma opacidade.
    """
    return ImageChops.difference(
        a.convert("RGBA"), b.convert("RGBA")
    ).getbbox(alpha_only=False)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- a tabela --------------------------------------------------------------


def test_tabela_tem_dez_degraus():
    assert len(TABELA) == 10


def test_tabela_e_exatamente_a_da_spec():
    esperada = [
        (None, 2, 100), (None, 4, 100), (256, 4, 100), (128, 4, 100),
        (64, 4, 100), (32, 4, 100), (32, 4, 85), (16, 4, 85),
        (16, 4, 70), (16, 4, 50),
    ]
    assert [(p.colors, p.oxipng, p.scale_pct) for p in TABELA] == esperada


def test_degraus_zero_e_um_nao_quantizam():
    assert TABELA[0].colors is None
    assert TABELA[1].colors is None
    assert TABELA[0].scale_pct == TABELA[1].scale_pct == 100
    assert all(p.colors is not None for p in TABELA[2:])


def test_sentidos_declarados_cobrem_todos_os_campos():
    campos = {f.name for f in dataclasses.fields(Params)}
    assert set(SENTIDOS) == campos
    assert SENTIDOS["colors"] == "menor"
    assert SENTIDOS["scale_pct"] == "menor"
    assert SENTIDOS["oxipng"] == "maior"


def test_neutro_das_cores_e_nao_quantizar_e_nao_256():
    """O neutro da paleta é `None`: desligar a alavanca não pode quantizar,
    senão uma imagem suave e previsível cresce com o botão desligado."""
    assert NEUTROS["colors"] is None
    assert NEUTROS["scale_pct"] == 100


def test_paleta_desligada_nao_quantiza_nenhum_degrau():
    info = type("I", (), {"fps": 10.0})()
    escada = aplicar_toggles(
        TABELA, info, Toggles(palette=False), NEUTROS  # type: ignore[arg-type]
    )
    assert escada and all(p.colors is None for p in escada)


def test_o_png_esta_registrado_com_extensao_png():
    assert Formato.PNG in formatos.registrados()
    assert formatos.para(Formato.PNG).TABELA is TABELA
    assert formatos.extensao(Formato.PNG) == ".png"


# --- linha de comando do oxipng ------------------------------------------


def test_comando_do_oxipng_usa_o_nivel_e_o_arquivo_de_saida():
    tools = Tools("ffmpeg", "ffprobe", None, oxipng="oxipng")
    cmd = build_oxipng_cmd(tools, Path("saida.png"), Params(None, 4, 100))
    assert cmd[0] == "oxipng"
    assert cmd[cmd.index("-o") + 1] == "4"
    assert cmd[-1] == "saida.png"


def test_comando_do_oxipng_sem_binario_levanta_encodeerror():
    tools = Tools("ffmpeg", "ffprobe", None, oxipng=None)
    with pytest.raises(EncodeError, match="oxipng"):
        build_oxipng_cmd(tools, Path("saida.png"), Params(None, 4, 100))


# --- codificação sem oxipng (modo degradado) ------------------------------


def test_sem_oxipng_ainda_produz_um_png_valido(tmp_path):
    """Sem o binário o estágio sem perda é pulado, mas o Pillow já salvou
    com `optimize`: a escada continua utilizável."""
    src = _png_de_interface(tmp_path / "ui.png")
    dst = tmp_path / "out" / "ui.png"
    tools = Tools("ffmpeg", "ffprobe", None, oxipng=None)
    tamanho = codificar(src, dst, TABELA[2], probe(src), tools)
    assert tamanho == dst.stat().st_size
    with Image.open(dst) as img:
        assert img.format == "PNG"
        assert img.mode == "P"


def test_oxipng_inexistente_vira_encodeerror_em_portugues(tmp_path):
    src = _png_de_interface(tmp_path / "ui.png")
    tools = Tools("ffmpeg", "ffprobe", None, oxipng=str(tmp_path / "nao-existe.exe"))
    with pytest.raises(EncodeError, match="oxipng"):
        codificar(src, tmp_path / "out.png", TABELA[1], probe(src), tools)


# --- escala, perfil de cor e alfa opaco (sem oxipng: testes de unidade) ---

SEM_OXIPNG = Tools("ffmpeg", "ffprobe", None, oxipng=None)


def _cores(img: Image.Image) -> int:
    return len(img.convert("RGB").getcolors(maxcolors=1_000_000) or [])


def _listras_em_paleta(destino: Path) -> Path:
    """400x40 em modo P, listras verticais de 1px em três cores, período 3.
    Um PNG já paletizado: o caso de capturas otimizadas por outra ferramenta
    e da própria saída desta."""
    img = Image.new("P", (400, 40))
    img.putpalette([255, 0, 0, 0, 255, 0, 0, 0, 255] + [0] * 759)
    for x in range(400):
        img.paste(x % 3, (x, 0, x + 1, 40))
    img.save(destino)
    return destino


def test_png_em_paleta_e_reduzido_com_antialiasing_e_nao_vizinho_mais_proximo(
    tmp_path,
):
    """O Pillow IGNORA o filtro em modos P e 1 e usa vizinho mais próximo:
    reduzir uma listrada em paleta daria só as 3 cores de origem, serrilhadas.
    Com a conversão para RGB antes da escala, o LANCZOS mistura as listras e
    sobram muito mais cores. Reverter a conversão faz este teste falhar."""
    src = _listras_em_paleta(tmp_path / "listras.png")
    with Image.open(src) as a:
        assert a.mode == "P" and _cores(a) == 3

    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[9], probe(src), SEM_OXIPNG)  # 16 cores, 50%

    with Image.open(dst) as b:
        assert b.width == target_width(400, 50)
        assert _cores(b) > 3, "reduziu em vizinho mais próximo (sem antialiasing)"


def _perfil_icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


@pytest.mark.parametrize("indice", [2, 5, 9])
def test_perfil_icc_sobrevive_aos_degraus_com_perda(tmp_path, indice):
    """Sem o perfil, uma imagem de gamut largo muda de cor em silêncio."""
    perfil = _perfil_icc()
    src = tmp_path / "icc.png"
    _png_de_interface(src)
    with Image.open(src) as img:
        img.load()
        img.save(src, icc_profile=perfil)
    with Image.open(src) as a:
        assert a.info.get("icc_profile") == perfil  # pré-condição

    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[indice], probe(src), SEM_OXIPNG)
    with Image.open(dst) as b:
        assert b.info.get("icc_profile") == perfil


def test_rgba_totalmente_opaco_usa_o_corte_mediano(tmp_path):
    """RGBA sem transparência real não precisa do FASTOCTREE (paleta pior):
    o resultado tem de ser o mesmo do RGB quantizado por corte mediano."""
    base = Image.open(_png_de_interface(tmp_path / "ui.png")).convert("RGBA")
    src = tmp_path / "opaco.png"
    base.save(src)
    assert base.getchannel("A").getextrema() == (255, 255)

    mediano = base.convert("RGB").quantize(
        64, method=Image.Quantize.MEDIANCUT).convert("RGB")
    octree = base.quantize(
        64, method=Image.Quantize.FASTOCTREE).convert("RGB")
    assert ImageChops.difference(mediano, octree).getbbox() is not None  # pré-cond.

    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[4], probe(src), SEM_OXIPNG)  # 64 cores, 100%
    with Image.open(dst) as b:
        assert "transparency" not in b.info
        assert ImageChops.difference(b.convert("RGB"), mediano).getbbox() is None


# --- integração com o oxipng de verdade ------------------------------------


@pytest.mark.integration
def test_png_de_interface_comprime_acima_de_cinquenta_por_cento(
    ferramentas, tmp_path
):
    src = _png_de_interface(tmp_path / "ui.png")
    original = src.stat().st_size
    dst = tmp_path / "saida" / "ui.png"

    final = codificar(src, dst, TABELA[2], probe(src), ferramentas)

    assert final == dst.stat().st_size
    assert final < original * 0.5, f"{original} -> {final}"
    # Prova que quantizou de verdade, em vez de só recomprimir.
    with Image.open(dst) as img:
        assert img.mode == "P"
        assert len(img.getcolors(maxcolors=256) or []) <= 256


@pytest.mark.integration
def test_png_nunca_sai_maior_que_a_entrada(ferramentas, tmp_path):
    src = _png_gradiente_suave(tmp_path / "suave.png")
    original = src.stat().st_size
    info = probe(src)

    # Pré-condição: o caso é mesmo o da spec — 256 cores deixa o arquivo
    # MAIOR que a entrada, e um degrau mais fundo volta a encolher (a escada
    # não é monotônica em tamanho aí). Sem isto o teste passaria sem
    # exercitar nenhum dos caminhos de proteção da engine.
    q256 = codificar(src, tmp_path / "q256.png", TABELA[2], info, ferramentas)
    q64 = codificar(src, tmp_path / "q64.png", TABELA[4], info, ferramentas)
    assert q256 > original, f"fixture não reproduz o caso: {original} -> {q256}"
    assert q64 < original

    # Alvo folgado: o melhor resultado nunca pode ser maior que a entrada.
    folgado = compress_file(src, 50_000_000, TODOS, ferramentas)
    assert folgado.final_bytes <= original
    assert folgado.dst.is_file()
    assert folgado.dst.stat().st_size == folgado.final_bytes

    # Alvo apertado: a busca passa pelo degrau que cresce e ainda assim
    # entrega algo que cabe e que não é maior que a entrada.
    apertado = compress_file(src, original // 2, TODOS, ferramentas)
    assert apertado.final_bytes <= original // 2
    assert apertado.hit_target is True


@pytest.mark.integration
def test_transparencia_e_preservada_ao_quantizar(ferramentas, tmp_path):
    src = _logo_transparente(tmp_path / "logo.png")
    info = probe(src)

    # 2 = 256 cores; 5 = 32 cores; 9 = 16 cores a 50% (reduz a imagem).
    for indice in (2, 5, 9):
        dst = tmp_path / f"saida{indice}.png"
        codificar(src, dst, TABELA[indice], info, ferramentas)
        with Image.open(dst) as img:
            rgba = img.convert("RGBA")
        w, h = rgba.size
        centro = rgba.getpixel((w // 2, h // 2))
        cinza = rgba.getpixel((int(w * 0.30), int(h * 0.50)))
        assert rgba.getpixel((0, 0))[3] == 0, f"degrau {indice}: canto opaco"
        assert rgba.getpixel((w - 1, h - 1))[3] == 0
        assert cinza[3] >= 245, f"degrau {indice}: disco vermelho perdeu o alfa"
        assert cinza[0] > 150 and cinza[1] < 80, f"degrau {indice}: cor errada"
        assert 90 <= centro[3] <= 160, (
            f"degrau {indice}: semitransparência perdida ({centro})"
        )


@pytest.mark.integration
def test_oxipng_nao_descarta_o_perfil_icc(ferramentas, tmp_path):
    perfil = _perfil_icc()
    src = tmp_path / "icc.png"
    _png_de_interface(src)
    with Image.open(src) as img:
        img.load()
        img.save(src, icc_profile=perfil)
    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[2], probe(src), ferramentas)
    with Image.open(dst) as b:
        assert b.info.get("icc_profile") == perfil


@pytest.mark.integration
def test_imagem_sem_alfa_nao_ganha_transparencia(ferramentas, tmp_path):
    src = _png_de_interface(tmp_path / "ui.png")
    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[4], probe(src), ferramentas)
    with Image.open(dst) as img:
        assert "transparency" not in img.info
        assert img.convert("RGBA").getchannel("A").getextrema() == (255, 255)


@pytest.mark.integration
@pytest.mark.parametrize("indice", [0, 1])
def test_degrau_sem_perda_nao_altera_os_pixels(ferramentas, tmp_path, indice):
    src = _png_de_interface(tmp_path / "ui.png")
    dst = tmp_path / "saida.png"
    with Image.open(src) as a:
        # Pré-condição: há mais cores do que uma paleta de 256 comporta.
        assert len(a.getcolors(maxcolors=1_000_000) or []) > 256
    codificar(src, dst, TABELA[indice], probe(src), ferramentas)
    # Comparação em RGBA: o oxipng pode trocar a representação (p. ex. RGB
    # por paleta) sem mudar nenhum pixel, e é isso que "sem perda" promete.
    with Image.open(src) as a, Image.open(dst) as b:
        assert a.size == b.size
        assert _diferem(a, b) is None


@pytest.mark.integration
def test_degrau_sem_perda_preserva_alfa_de_entrada_rgba(ferramentas, tmp_path):
    src = _logo_transparente(tmp_path / "logo.png")
    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[0], probe(src), ferramentas)
    with Image.open(src) as a, Image.open(dst) as b:
        assert a.mode == "RGBA"
        assert _diferem(a, b) is None


@pytest.mark.integration
def test_resultado_abre_como_png_valido(ferramentas, tmp_path):
    src = _png_de_interface(tmp_path / "ui.png")
    info = probe(src)
    for indice in (0, 3, 6, 9):
        dst = tmp_path / f"saida{indice}.png"
        codificar(src, dst, TABELA[indice], info, ferramentas)
        assert dst.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        with Image.open(dst) as img:
            img.load()  # decodifica tudo: falha se o PNG estiver corrompido
            assert img.format == "PNG"
        # Também o próprio probe do app o reconhece como PNG.
        assert probe(dst).formato is Formato.PNG


@pytest.mark.integration
def test_escala_reduz_a_largura_pela_regra_do_projeto(ferramentas, tmp_path):
    src = _png_de_interface(tmp_path / "ui.png")
    info = probe(src)
    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[6], info, ferramentas)  # 85%
    with Image.open(dst) as img:
        assert img.width == target_width(info.width, 85)
        assert img.height == round(info.height * img.width / info.width)


@pytest.mark.integration
def test_nunca_modifica_o_original(ferramentas, tmp_path):
    src = _png_de_interface(tmp_path / "ui.png")
    antes = _sha(src)
    info = probe(src)
    for indice in (0, 2, 9):
        codificar(src, tmp_path / f"s{indice}.png", TABELA[indice], info, ferramentas)
    compress_file(src, 20_000, TODOS, ferramentas)
    assert _sha(src) == antes


@pytest.mark.integration
def test_compress_file_encaixa_png_de_interface_num_alvo_apertado(
    ferramentas, tmp_path
):
    src = _png_de_interface(tmp_path / "ui.png")
    alvo = src.stat().st_size // 4
    r = compress_file(src, alvo, TODOS, ferramentas)
    assert r.hit_target is True
    assert r.final_bytes <= alvo
    assert r.dst.suffix == ".png"
    with Image.open(r.dst) as img:
        img.load()


@pytest.mark.integration
def test_oxipng_binario_roda(ferramentas):
    proc = subprocess.run(
        [ferramentas.oxipng, "--version"], capture_output=True, text=True
    )
    assert proc.returncode == 0 and "oxipng" in proc.stdout.lower()


# --- metadados: orientação EXIF, DPI e texto -------------------------------
#
# Os degraus 0 e 1 COPIAM o arquivo (o chunk eXIf segue com a tag de
# orientação, e os pixels ficam como estavam); os com perda salvam de novo.
# Se estes não tratassem a orientação como o JPEG e o WebP tratam, o degrau
# escolhido pela busca decidiria se a imagem aparece girada 90 graus.

ORIENTACAO = 0x0112


def _png_rotulado(
    destino: Path, tamanho=(400, 200), orientacao=6, **extra
) -> Path:
    """Metade esquerda vermelha, metade direita azul e um quadrado verde no
    canto, com a tag de orientação num chunk eXIf. Girada por `orientacao` 6 (90 graus horários), a imagem
    exibida é um retrato com o VERMELHO NO TOPO."""
    larg, alt = tamanho
    img = Image.new("RGB", tamanho)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, larg // 2 - 1, alt - 1), fill=(220, 30, 30))
    d.rectangle((larg // 2, 0, larg - 1, alt - 1), fill=(30, 30, 220))
    d.rectangle((0, 0, 29, 29), fill=(30, 200, 30))   # tira a simetria: as 8 orientações se distinguem
    exif = Image.Exif()
    exif[ORIENTACAO] = orientacao
    exif[0x010F] = "Camera Teste"      # Make
    img.save(destino, "PNG", exif=exif, **extra)
    return destino


def _exibida(caminho: Path) -> Image.Image:
    """Como um visualizador que respeita o eXIf mostraria o arquivo."""
    with Image.open(caminho) as img:
        return ImageOps.exif_transpose(img).convert("RGB")


def _topo_e_base(img: Image.Image):
    return (
        img.getpixel((img.width // 2, img.height // 4)),
        img.getpixel((img.width // 2, 3 * img.height // 4)),
    )


@pytest.mark.integration
@pytest.mark.parametrize("com_oxipng", [True, False], ids=["oxipng", "sem-oxipng"])
@pytest.mark.parametrize("indice", [0, 1, 2, 5, 9])
def test_a_orientacao_exibida_sobrevive_a_qualquer_degrau(
    ferramentas, tmp_path, indice, com_oxipng
):
    """O defeito: o degrau 0 devolvia `{274: 6}` e o 2 devolvia `{}` com os
    pixels não girados, então um visualizador mostrava os dois a 90 graus um
    do outro, a partir da MESMA entrada."""
    src = _png_rotulado(tmp_path / "girada.png")
    esperada = _exibida(src)
    assert esperada.size == (200, 400)      # pré-condição: a tag gira de fato
    topo, base = _topo_e_base(esperada)
    assert topo[0] > 150 > topo[2] and base[2] > 150 > base[0]

    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[indice], probe(src), ferramentas if com_oxipng else SEM_OXIPNG)

    saida = _exibida(dst)
    assert saida.height == 2 * saida.width                        # continua retrato
    if TABELA[indice].scale_pct != 100:
        # a escala parte da largura EXIBIDA (200), não da armazenada (400)
        assert saida.width == target_width(200, TABELA[indice].scale_pct)
    topo, base = _topo_e_base(saida)
    assert topo[0] > 150 > topo[2], "o vermelho deixou de estar no topo"
    assert base[2] > 150 > base[0]
    with Image.open(dst) as s:
        marcada = s.getexif().get(ORIENTACAO)
    if TABELA[indice].colors is None and TABELA[indice].scale_pct == 100:
        assert marcada == 6      # sem perda: arquivo e pixels como estavam
    else:
        assert marcada in (None, 1), "a tag ficou e os pixels já foram girados"


@pytest.mark.integration
@pytest.mark.parametrize("orientacao", [2, 3, 4, 5, 6, 7, 8])
def test_toda_orientacao_exif_e_resolvida_no_degrau_com_perda(tmp_path, orientacao):
    src = _png_rotulado(tmp_path / "o.png", orientacao=orientacao)
    esperada = _exibida(src)
    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[3], probe(src), SEM_OXIPNG)   # 128 cores, 100%
    saida = _exibida(dst)
    assert saida.size == esperada.size
    assert _diferem(esperada, saida) is None     # três cores: a quantização é exata
    with Image.open(dst) as s:
        assert s.getexif().get(ORIENTACAO) in (None, 1)


@pytest.mark.integration
@pytest.mark.parametrize("indice", [0, 2, 9])
def test_o_resto_do_exif_e_mantido(tmp_path, indice):
    src = _png_rotulado(tmp_path / "g.png")
    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[indice], probe(src), SEM_OXIPNG)
    with Image.open(dst) as s:
        assert s.getexif().get(0x010F) == "Camera Teste"


@pytest.mark.integration
@pytest.mark.parametrize("indice", [0, 2, 9])
def test_png_sem_exif_continua_sem_exif(tmp_path, indice):
    src = _png_de_interface(tmp_path / "ui.png")
    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[indice], probe(src), SEM_OXIPNG)
    with Image.open(dst) as s:
        assert len(s.getexif()) == 0


@pytest.mark.integration
@pytest.mark.parametrize("indice, fator", [(0, 1.0), (2, 1.0), (6, 0.85), (9, 0.5)])
def test_dpi_e_mantido_e_acompanha_a_escala(tmp_path, indice, fator):
    """O pHYs diz quantos pixels cabem numa polegada. Reduzir os pixels sem
    reduzir o valor encolheria a imagem no papel: ele acompanha a escala."""
    src = _png_de_interface(tmp_path / "ui.png")
    with Image.open(src) as img:
        img.load()
        img.save(src, dpi=(300, 300))
    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[indice], probe(src), SEM_OXIPNG)
    with Image.open(dst) as s:
        dpi_x, dpi_y = s.info["dpi"]
        largura = s.width
    assert largura == (1280 if indice < 6 else target_width(1280, TABELA[indice].scale_pct))
    esperado = 300 * largura / 1280
    assert dpi_x == pytest.approx(esperado, rel=0.01)
    assert dpi_y == pytest.approx(esperado, rel=0.02)


@pytest.mark.integration
def test_dpi_troca_de_eixo_quando_a_imagem_e_girada(tmp_path):
    src = _png_rotulado(tmp_path / "g.png", dpi=(300, 150))
    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[2], probe(src), SEM_OXIPNG)
    with Image.open(dst) as s:
        dpi_x, dpi_y = s.info["dpi"]
    # a coluna de pixels do arquivo virou a linha da imagem exibida
    assert dpi_x == pytest.approx(150, rel=0.01)
    assert dpi_y == pytest.approx(300, rel=0.01)


@pytest.mark.integration
@pytest.mark.parametrize("indice", [0, 2, 9])
def test_chunks_de_texto_sao_mantidos(tmp_path, indice):
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Fulana de Tal")
    info.add_text("Descrição", PngImagePlugin.iTXt("ação é vida", "pt", "Descrição"))
    src = tmp_path / "t.png"
    _png_de_interface(src)
    with Image.open(src) as img:
        img.load()
        img.save(src, pnginfo=info)
    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[indice], probe(src), SEM_OXIPNG)
    with Image.open(dst) as s:
        s.load()
        assert s.text.get("Author") == "Fulana de Tal"
        assert s.text.get("Descrição") == "ação é vida"


@pytest.mark.integration
@pytest.mark.parametrize("girada", [True, False], ids=["girada", "sem-giro"])
def test_xmp_nao_volta_a_girar_a_imagem(tmp_path, girada):
    """O XMP pode repetir a orientação (tiff:Orientation). Copiá-lo depois de
    girar os pixels a faria ser aplicada duas vezes; sem giro ele é mantido."""
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "Fulana")
    info.add_text(
        "XML:com.adobe.xmp",
        PngImagePlugin.iTXt("<x:xmpmeta/>", "", "XML:com.adobe.xmp"),
    )
    src = _png_rotulado(tmp_path / "g.png", orientacao=6 if girada else 1, pnginfo=info)
    dst = tmp_path / "saida.png"
    codificar(src, dst, TABELA[2], probe(src), SEM_OXIPNG)
    with Image.open(dst) as s:
        s.load()
        assert s.text.get("Author") == "Fulana"
        assert ("XML:com.adobe.xmp" in s.text) is (not girada)


def test_sem_oxipng_os_degraus_zero_e_um_geram_o_mesmo_arquivo(tmp_path):
    """A premissa de `chave_de_equivalencia`: sem o binário os dois degraus só
    diferem no nível do oxipng, que não roda, e o arquivo sai byte a byte
    igual."""
    src = _png_de_interface(tmp_path / "ui.png")
    saidas = []
    for i in (0, 1):
        dst = tmp_path / f"d{i}.png"
        codificar(src, dst, TABELA[i], probe(src), SEM_OXIPNG)
        saidas.append(dst.read_bytes())
    assert saidas[0] == saidas[1] == src.read_bytes()


def _com_exif_ilegivel(origem: Path, destino: Path) -> Path:
    """`origem` com um chunk eXIf cujo conteúdo não é TIFF."""
    import struct
    import zlib

    dados = origem.read_bytes()
    corpo = b"isto-nao-e-um-tiff"
    chunk = (
        struct.pack(">I", len(corpo)) + b"eXIf" + corpo
        + struct.pack(">I", zlib.crc32(b"eXIf" + corpo) & 0xFFFFFFFF)
    )
    depois_do_ihdr = 8 + 4 + 4 + 13 + 4
    destino.write_bytes(dados[:depois_do_ihdr] + chunk + dados[depois_do_ihdr:])
    return destino


@pytest.mark.integration
@pytest.mark.parametrize("indice", [0, 2, 9])
def test_exif_ilegivel_nao_derruba_o_degrau(tmp_path, indice):
    """Um PNG com eXIf corrompido foi aberto e convertido sem problema antes de
    o EXIF ser lido; ler a orientação não pode transformar isso num erro cru
    (SyntaxError) que escapa da busca e mata o arquivo inteiro."""
    base = _png_de_interface(tmp_path / "ui.png")
    src = _com_exif_ilegivel(base, tmp_path / "ruim.png")
    dst = tmp_path / "saida.png"

    codificar(src, dst, TABELA[indice], probe(src), SEM_OXIPNG)

    with Image.open(dst) as s:
        s.load()
        assert s.format == "PNG"
        if indice != 0:      # o degrau 0 copia o arquivo, chunk ruim incluído
            assert len(s.getexif()) == 0
