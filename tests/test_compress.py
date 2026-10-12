import random
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from gifc import formatos
from gifc.compress import CompressResult, compress_file, unique_output_path
from gifc.formatos import gif as formato_gif
from gifc.formatos import webp as formato_webp
from gifc.formatos.base import EncodeError
from gifc.ladder import Toggles
from gifc.probe import Formato, ImagemInfo
from gifc.tools import Tools
from tests.imagens import captura_de_tela, degrade

TODOS = Toggles(True, True, True, True)


def _preparar(tmp_path, monkeypatch, gifsicle="gifsicle"):
    """Isola compress_file do disco: probe e codificar viram funções falsas."""
    from gifc import compress as mod

    src = tmp_path / "a.gif"
    src.write_bytes(b"GIF89a" + b"x" * 5_000)
    info = ImagemInfo(
        path=src, formato=Formato.GIF, width=100, height=100, fps=30.0,
        frame_count=30, duration=1.0, size_bytes=src.stat().st_size,
    )
    monkeypatch.setattr(mod, "probe", lambda p: info)
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

    monkeypatch.setattr(formato_gif, "codificar", encode_falho)

    r = mod.compress_file(src, 1_800, TODOS, tools)

    assert any(i % 2 == 0 for i in tentados), "nenhum degrau falho foi exercido"
    assert r.dst.is_file()
    assert r.final_bytes <= 1_800


def test_encode_fn_tenta_de_novo_apos_falha_transitoria_no_mesmo_degrau(
    tmp_path, monkeypatch
):
    """Uma falha isolada do ffmpeg/gifsicle não pode custar o degrau
    inteiro: `compress_file` tenta de novo uma vez antes de desistir dele.

    A escada padrão (TODOS os toggles, 20 degraus) faz a bisecção sondar o
    índice 9 primeiro. Ele falha só na primeira chamada e tem sucesso na
    segunda — o resultado final não pode carregar nenhum erro para esse
    índice, porque a falha foi recuperada pelo retry.
    """
    mod, src, info, tools = _preparar(tmp_path, monkeypatch)
    chamadas_por_indice: dict[int, int] = {}

    def encode_falha_uma_vez(s, d, params, i, t):
        idx = int(d.stem.split("_")[1])
        chamadas_por_indice[idx] = chamadas_por_indice.get(idx, 0) + 1
        if idx == 9 and chamadas_por_indice[idx] == 1:
            raise EncodeError("hiccup transitório")
        d.write_bytes(b"GIF89a" + b"y" * (2_000 - idx * 50))
        return d.stat().st_size

    monkeypatch.setattr(formato_gif, "codificar", encode_falha_uma_vez)

    r = mod.compress_file(src, 1_800, TODOS, tools)

    assert chamadas_por_indice[9] == 2, "o degrau 9 deveria ter sido tentado 2 vezes"
    assert not any(a.index == 9 and a.erro is not None for a in r.attempts), (
        "a falha transitória, recuperada no retry, não pode aparecer como erro"
    )


def test_encode_fn_desiste_apos_falhar_duas_vezes_seguidas_no_mesmo_degrau(
    tmp_path, monkeypatch
):
    """Só depois que o retry TAMBÉM falha é que o degrau vira RungFailed."""
    mod, src, info, tools = _preparar(tmp_path, monkeypatch)
    chamadas_por_indice: dict[int, int] = {}

    def encode_sempre_falha_no_indice_9(s, d, params, i, t):
        idx = int(d.stem.split("_")[1])
        chamadas_por_indice[idx] = chamadas_por_indice.get(idx, 0) + 1
        if idx == 9:
            raise EncodeError("degrau permanentemente ruim")
        d.write_bytes(b"GIF89a" + b"y" * (2_000 - idx * 50))
        return d.stat().st_size

    monkeypatch.setattr(formato_gif, "codificar", encode_sempre_falha_no_indice_9)

    r = mod.compress_file(src, 1_800, TODOS, tools)

    assert chamadas_por_indice[9] == 2, "deve tentar exatamente 2x antes de desistir"
    tentativa_9 = next(a for a in r.attempts if a.index == 9)
    assert tentativa_9.erro is not None
    assert "degrau permanentemente ruim" in tentativa_9.erro


def test_todos_os_degraus_falhando_levanta_erro(tmp_path, monkeypatch):
    mod, src, info, tools = _preparar(tmp_path, monkeypatch)

    caminhos_criados: list[Path] = []
    mkdtemp_original = mod.tempfile.mkdtemp

    def mkdtemp_espiao(*args, **kwargs):
        caminho = mkdtemp_original(*args, **kwargs)
        caminhos_criados.append(Path(caminho))
        return caminho

    monkeypatch.setattr(mod.tempfile, "mkdtemp", mkdtemp_espiao)

    def sempre_falha(s, d, params, i, t):
        raise EncodeError("quebrado")

    monkeypatch.setattr(formato_gif, "codificar", sempre_falha)

    with pytest.raises(mod.CompressError):
        mod.compress_file(src, 1_000, TODOS, tools)

    assert caminhos_criados, "mkdtemp nunca foi chamado"
    assert not caminhos_criados[0].exists(), (
        "o tmpdir deveria ter sido removido no finally após o CompressError"
    )


def test_ja_otimizado_copia_o_original(tmp_path, monkeypatch):
    """Um GIF que cresceria ao ser recodificado não é penalizado: o
    original é copiado como está, em vez do resultado maior."""
    mod, src, info, tools = _preparar(tmp_path, monkeypatch)

    def encode_maior(s, d, params, i, t):
        d.write_bytes(b"GIF89a" + b"z" * (info.size_bytes + 500))
        return d.stat().st_size

    monkeypatch.setattr(formato_gif, "codificar", encode_maior)

    r = mod.compress_file(src, 10_000, TODOS, tools)

    assert r.already_optimized is True
    assert r.params is None
    assert r.dst.read_bytes() == src.read_bytes()
    assert r.final_bytes == r.original_bytes


@pytest.mark.parametrize(
    "target_bytes, esperado",
    [
        (10_000, True),  # o original cabe no alvo
        (100, False),  # o original não cabe no alvo
    ],
)
def test_ja_otimizado_hit_target_usa_o_tamanho_original(
    tmp_path, monkeypatch, target_bytes, esperado
):
    """O recálculo de hit_target no ramo já-otimizado usa o tamanho do
    ORIGINAL copiado, não o da tentativa maior que foi descartada."""
    mod, src, info, tools = _preparar(tmp_path, monkeypatch)

    def encode_maior(s, d, params, i, t):
        d.write_bytes(b"GIF89a" + b"z" * (info.size_bytes + 500))
        return d.stat().st_size

    monkeypatch.setattr(formato_gif, "codificar", encode_maior)

    r = mod.compress_file(src, target_bytes, TODOS, tools)

    assert r.already_optimized is True
    assert r.hit_target is esperado


def test_ja_otimizado_hit_target_discrimina_original_de_tentativa(tmp_path, monkeypatch):
    """Caso que de fato distingue as duas fórmulas possíveis de hit_target.

    Os dois casos parametrizados acima (10_000 e 100) não discriminam nada:
    tanto o tamanho do original quanto o da tentativa descartada dão a
    mesma resposta para esses alvos, porque ambos estão muito longe dos
    dois valores. Um mutante que trocasse `info.size_bytes` por
    `resultado.size_bytes` no recálculo de hit_target passaria despercebido
    por eles.

    A única janela onde as fórmulas discordam é um alvo estritamente entre
    o tamanho do original e o da tentativa: `original < alvo < tentativa`.
    Nessa janela, "cabe no original" é True e "cabe na tentativa" é False.
    Os valores são derivados de `info.size_bytes`, não fixos, para que a
    relação continue válida se o fixture de `_preparar` mudar de tamanho.
    """
    mod, src, info, tools = _preparar(tmp_path, monkeypatch)

    delta_tentativa = 500
    tentativa_tamanho = info.size_bytes + delta_tentativa
    alvo = info.size_bytes + delta_tentativa // 2
    assert info.size_bytes < alvo < tentativa_tamanho

    def encode_maior(s, d, params, i, t):
        d.write_bytes(b"GIF89a" + b"z" * (tentativa_tamanho - 6))
        assert d.stat().st_size == tentativa_tamanho
        return d.stat().st_size

    monkeypatch.setattr(formato_gif, "codificar", encode_maior)

    r = mod.compress_file(src, alvo, TODOS, tools)

    assert r.already_optimized is True
    # Só é True se hit_target foi calculado contra o tamanho do ORIGINAL.
    assert r.hit_target is True


def test_ja_otimizado_dispara_em_empate_exato(tmp_path, monkeypatch):
    """`>=`, não `>`: uma tentativa do MESMO tamanho do original também
    conta como "já otimizado" — não há ganho real em substituí-lo por um
    arquivo idêntico em tamanho."""
    mod, src, info, tools = _preparar(tmp_path, monkeypatch)

    def encode_igual(s, d, params, i, t):
        d.write_bytes(src.read_bytes())
        assert d.stat().st_size == info.size_bytes
        return d.stat().st_size

    monkeypatch.setattr(formato_gif, "codificar", encode_igual)

    r = mod.compress_file(src, 10_000, TODOS, tools)

    assert r.already_optimized is True


def test_sem_gifsicle_descarta_a_coluna_lossy(tmp_path, monkeypatch):
    """Modo degradado: manter lossy só geraria degraus idênticos."""
    mod, src, info, tools = _preparar(tmp_path, monkeypatch, gifsicle=None)
    vistos = []

    def encode_ok(s, d, params, i, t):
        vistos.append(params)
        d.write_bytes(b"GIF89a" + b"y" * 100)
        return d.stat().st_size

    monkeypatch.setattr(formato_gif, "codificar", encode_ok)
    # alvo abaixo da entrada (5.006 B): acima dela nada seria codificado
    mod.compress_file(src, 4_000, TODOS, tools)

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
def test_alvo_folgado_so_aplica_o_degrau_zero_num_gif(tools, tmp_path):
    """Com a entrada dentro do alvo (aqui o padrão de 10 MB) o GIF recebe o
    degrau 0 (256 cores, sem lossy, sem escala, framerate original) e nada
    além dele: uma codificação, e nenhum sacrifício de cor, resolução ou fps."""
    src = tmp_path / "anim.gif"
    subprocess.run(
        [tools.ffmpeg, "-y", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=320x240:rate=15:duration=2", "-t", "2", str(src)],
        check=True, capture_output=True,
    )
    r = compress_file(src, target_bytes=10_000_000, toggles=TODOS, tools=tools)

    assert r.already_optimized is False
    assert r.params == formato_gif.TABELA[0]
    assert [a.index for a in r.attempts] == [0]
    assert r.final_bytes < src.stat().st_size and r.hit_target is True


@pytest.mark.integration
def test_alvo_folgado_so_aplica_o_degrau_zero_num_jpeg(tmp_path):
    from gifc.formatos import jpeg as formato_jpeg

    src = tmp_path / "foto.jpg"
    _textura((640, 480)).save(src, "JPEG", quality=95)

    r = compress_file(src, 10_000_000, TODOS, SEM_FERRAMENTAS)

    assert r.already_optimized is False
    assert r.params == formato_jpeg.TABELA[0]            # q92, subamostragem da fonte
    assert [a.index for a in r.attempts] == [0]
    assert r.final_bytes < src.stat().st_size and r.hit_target is True


@pytest.mark.integration
def test_alvo_logo_abaixo_da_entrada_nao_sacrifica_resolucao_nem_framerate(
    tools, gif_pequeno, tmp_path
):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())

    r = compress_file(
        copia, target_bytes=copia.stat().st_size - 1, toggles=TODOS, tools=tools
    )

    # Folgado o bastante para o degrau 0: não deve sacrificar o resto.
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


# --- despacho por formato ------------------------------------------------

# PNG, JPEG e WebP não usam ferramenta externa: tudo vai pelo Pillow.
SEM_FERRAMENTAS = Tools("ffmpeg", "ffprobe", None, oxipng=None)

_PILLOW = {
    Formato.GIF: "GIF", Formato.PNG: "PNG",
    Formato.JPEG: "JPEG", Formato.WEBP: "WEBP",
}


def _textura(tamanho=(320, 240)) -> Image.Image:
    """Ruído de baixa frequência sobre um degradê, com bordas nítidas:
    compressível, mas não trivial, como uma foto."""
    rng = random.Random(7)
    pequeno = Image.frombytes(
        "RGB", (40, 30), bytes(rng.randrange(256) for _ in range(40 * 30 * 3))
    )
    img = pequeno.resize(tamanho, Image.Resampling.BICUBIC)
    grad = Image.linear_gradient("L").resize(tamanho)
    img = Image.blend(img, Image.merge("RGB", (grad, grad, grad)), 0.5)
    d = ImageDraw.Draw(img)
    d.rectangle((20, 20, 100, 80), outline=(255, 255, 255), width=3)
    d.ellipse((150, 100, 230, 180), fill=(220, 40, 40))
    return img


def _arquivo_real(formato: Formato, tmp_path: Path, request) -> Path:
    """Um arquivo de verdade de `formato`, com o nome que a extensão manda."""
    if formato is Formato.GIF:
        origem = request.getfixturevalue("gif_pequeno")
        destino = tmp_path / "entrada.gif"
        destino.write_bytes(origem.read_bytes())
        return destino
    destino = tmp_path / f"entrada{formatos.extensao(formato)}"
    opcoes = {} if formato is Formato.PNG else {"quality": 95}
    _textura().save(destino, _PILLOW[formato], **opcoes)
    return destino


def _preparar_formato(tmp_path, monkeypatch, formato, nome=None):
    """Como `_preparar`, mas para um arquivo de outro formato. O conteúdo é
    arbitrário: o codificador do teste é falso e o `probe` é substituído."""
    from gifc import compress as mod

    src = tmp_path / (nome or f"a{formatos.extensao(formato)}")
    src.write_bytes(b"x" * 5_000)
    info = ImagemInfo(
        path=src, formato=formato, width=100, height=100, fps=10.0,
        frame_count=1, duration=0.0, size_bytes=src.stat().st_size,
    )
    monkeypatch.setattr(mod, "probe", lambda p: info)
    return mod, src, info


def _codificador_falso(tamanho: int):
    """Um codificador que grava `tamanho` bytes. Devolve ele e a lista dos
    `Params` com que foi chamado."""
    vistos: list = []

    def falso(s, d, params, i, t):
        vistos.append(params)
        d.write_bytes(b"w" * tamanho)
        return tamanho

    return falso, vistos


def _webp_falso(monkeypatch, tamanho: int) -> list:
    falso, vistos = _codificador_falso(tamanho)
    monkeypatch.setattr(formato_webp, "codificar", falso)
    return vistos


def test_nome_de_saida_segue_o_formato_pedido(tmp_path):
    src = tmp_path / "gato.png"
    src.write_bytes(b"x")
    assert unique_output_path(src, Formato.WEBP).name == "gato_comprimido.webp"
    (tmp_path / "gato_comprimido.webp").write_bytes(b"y")
    assert unique_output_path(src, Formato.WEBP).name == "gato_comprimido_2.webp"
    # O nome livre é procurado na extensão da SAÍDA, não na da entrada.
    assert unique_output_path(src, Formato.PNG).name == "gato_comprimido.png"


def test_nome_de_saida_usa_a_extensao_do_mapa_e_nao_a_do_valor(tmp_path):
    """`Formato.JPEG.value` é "jpeg"; a extensão do arquivo é ".jpg"."""
    src = tmp_path / "foto.jpeg"
    src.write_bytes(b"x")
    assert unique_output_path(src, Formato.JPEG).name == "foto_comprimido.jpg"


@pytest.mark.parametrize("formato", list(Formato))
@pytest.mark.integration
def test_comprime_cada_formato(formato, tmp_path, request):
    src = _arquivo_real(formato, tmp_path, request)
    original = src.stat().st_size
    antes = src.read_bytes()
    alvo = original // 2
    ferramentas = (
        request.getfixturevalue("tools") if formato is Formato.GIF
        else SEM_FERRAMENTAS
    )

    r = compress_file(src, alvo, TODOS, ferramentas)

    assert r.formato_entrada is formato
    assert r.formato_saida is formato
    assert r.dst.is_file() and r.dst != src
    assert r.dst.suffix == formatos.extensao(formato)
    assert r.final_bytes == r.dst.stat().st_size
    assert r.final_bytes <= original
    assert src.read_bytes() == antes
    with Image.open(r.dst) as img:
        img.load()
        assert img.format == _PILLOW[formato]
    # A escada e o codificador são os do formato: os Params do resultado são
    # da classe do módulo certo, não os de GIF.
    assert not r.already_optimized
    assert isinstance(r.params, formatos.para(formato).Params)
    assert r.params in formatos.para(formato).TABELA
    assert r.hit_target is True and r.final_bytes <= alvo


@pytest.mark.parametrize("entrada", [Formato.PNG, Formato.JPEG])
@pytest.mark.integration
def test_extensao_da_saida_segue_o_formato_de_saida(entrada, tmp_path, request):
    src = _arquivo_real(entrada, tmp_path, request)
    antes = src.read_bytes()

    r = compress_file(
        src, src.stat().st_size // 2, TODOS, SEM_FERRAMENTAS, converter_webp=True,
    )

    assert r.formato_entrada is entrada
    assert r.formato_saida is Formato.WEBP
    assert r.dst.name == "entrada_comprimido.webp"
    assert not r.already_optimized
    assert isinstance(r.params, formato_webp.Params)
    with Image.open(r.dst) as img:
        img.load()
        assert img.format == "WEBP"
    assert src.read_bytes() == antes
    # Só o original e o resultado; nenhum intermediário com a extensão errada.
    assert {p.name for p in tmp_path.iterdir()} == {src.name, r.dst.name}


@pytest.mark.integration
def test_converter_webp_nao_afeta_gif(tools, gif_pequeno, tmp_path):
    copia = tmp_path / "entrada.gif"
    copia.write_bytes(gif_pequeno.read_bytes())

    r = compress_file(
        copia, copia.stat().st_size // 2, TODOS, tools, converter_webp=True
    )

    assert r.formato_entrada is Formato.GIF
    assert r.formato_saida is Formato.GIF
    assert r.dst.suffix == ".gif"
    assert isinstance(r.params, formato_gif.Params)
    with Image.open(r.dst) as img:
        assert img.format == "GIF"


@pytest.mark.parametrize("entrada", [Formato.GIF, Formato.WEBP])
def test_converter_webp_so_vale_para_png_e_jpeg(tmp_path, monkeypatch, entrada):
    """GIF nunca converte, e WebP já é WebP: com a caixa ligada a escada e o
    codificador continuam os do formato de entrada."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, entrada)
    modulo = formatos.para(entrada)
    falso, vistos = _codificador_falso(100)
    monkeypatch.setattr(modulo, "codificar", falso)

    # alvo abaixo da entrada (5.000 B): acima dela só o sem perda é medido
    r = mod.compress_file(
        src, 4_000, TODOS, Tools("ffmpeg", "ffprobe", "gifsicle"),
        converter_webp=True,
    )

    assert vistos and all(isinstance(p, modulo.Params) for p in vistos)
    assert r.formato_saida is entrada
    assert r.dst.suffix == formatos.extensao(entrada)


@pytest.mark.parametrize("entrada", [Formato.PNG, Formato.JPEG])
def test_resultado_carrega_os_dois_formatos(tmp_path, monkeypatch, entrada):
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, entrada)
    vistos_webp = _webp_falso(monkeypatch, 100)

    convertido = mod.compress_file(
        src, 1_000_000, TODOS, SEM_FERRAMENTAS, converter_webp=True,
    )
    assert convertido.formato_entrada is entrada
    assert convertido.formato_saida is Formato.WEBP
    assert convertido.dst.suffix == ".webp"
    assert vistos_webp
    assert all(isinstance(p, formato_webp.Params) for p in vistos_webp)

    modulo = formatos.para(entrada)
    falso, vistos = _codificador_falso(100)
    monkeypatch.setattr(modulo, "codificar", falso)
    mesmo = mod.compress_file(src, 4_000, TODOS, SEM_FERRAMENTAS)
    assert mesmo.formato_entrada is entrada
    assert mesmo.formato_saida is entrada
    assert mesmo.dst.suffix == formatos.extensao(entrada)
    assert vistos and all(isinstance(p, modulo.Params) for p in vistos)


def test_conversao_usa_os_neutros_do_webp_e_nao_os_da_entrada(
    tmp_path, monkeypatch
):
    """Com a alavanca de resolução desligada a escada de WebP fica a 100%: os
    neutros e a tabela são os do formato de SAÍDA."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    vistos = _webp_falso(monkeypatch, 100)

    mod.compress_file(
        src, 1_000_000, Toggles(True, True, False, True), SEM_FERRAMENTAS,
        converter_webp=True,
    )

    assert vistos
    assert {p.scale_pct for p in vistos} == {100}
    assert all(p.quality in {q.quality for q in formato_webp.TABELA} for p in vistos)


@pytest.mark.parametrize(
    "entrada, extensao_original",
    [(Formato.PNG, ".png"), (Formato.JPEG, ".jpg")],
)
def test_sem_ganho_a_conversao_devolve_o_original_e_diz_a_verdade(
    tmp_path, monkeypatch, entrada, extensao_original
):
    """Pediu WebP, mas nenhum degrau de WebP ficou menor que a entrada: o
    original é copiado, e `formato_saida` e a extensão dizem o que FOI
    escrito (a entrada), não o que foi pedido."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, entrada)
    _webp_falso(monkeypatch, info.size_bytes + 500)

    r = mod.compress_file(src, 10_000, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert r.already_optimized is True
    assert r.formato_entrada is entrada
    assert r.formato_saida is entrada
    assert r.dst.suffix == extensao_original
    assert r.dst.read_bytes() == src.read_bytes()
    assert not list(tmp_path.glob("*.webp"))


def test_com_ganho_a_conversao_reporta_webp(tmp_path, monkeypatch):
    """O outro ramo do mesmo teste: o WebP menor é o que se escreve."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    _webp_falso(monkeypatch, info.size_bytes - 500)

    r = mod.compress_file(src, 10_000, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert r.already_optimized is False
    assert r.formato_saida is Formato.WEBP
    assert r.dst.suffix == ".webp"
    assert r.dst.stat().st_size == info.size_bytes - 500


def test_empate_exato_na_conversao_tambem_devolve_o_original(
    tmp_path, monkeypatch
):
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    _webp_falso(monkeypatch, info.size_bytes)

    r = mod.compress_file(src, 10_000, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert r.already_optimized is True
    assert r.formato_saida is Formato.PNG
    assert r.dst.suffix == ".png"


def test_conversao_sem_ganho_nao_deixa_tmpdir_nem_webp(tmp_path, monkeypatch):
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.JPEG)
    _webp_falso(monkeypatch, info.size_bytes + 1)
    criados: list[Path] = []
    original = mod.tempfile.mkdtemp

    def espiao(*a, **k):
        criados.append(Path(original(*a, **k)))
        return str(criados[-1])

    monkeypatch.setattr(mod.tempfile, "mkdtemp", espiao)

    r = mod.compress_file(src, 10_000, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert criados and not criados[0].exists()
    assert {p.name for p in tmp_path.iterdir()} == {src.name, r.dst.name}


# --- entrada que já cabe no alvo: só o que é de graça ----------------------
#
# Um alvo que a entrada já cumpre pede "menor de graça", não "menor a
# qualquer custo". Nesse regime só o prefixo sem perda é considerado; se ele
# não encolhe o arquivo, a resposta é `already_optimized`. Com a entrada FORA
# do alvo a escada é percorrida o quanto for preciso.


def _tamanho_pelo_degrau(monkeypatch, base: int, passo: int) -> None:
    """WebP falso cujo tamanho DIMINUI com o índice do degrau na tabela:
    `base - passo * índice`. Sem toggles desligados a escada é a tabela."""

    def falso(s, d, params, i, t):
        tamanho = base - passo * formato_webp.TABELA.index(params)
        d.write_bytes(b"w" * tamanho)
        return tamanho

    monkeypatch.setattr(formato_webp, "codificar", falso)


# Uma curva que SOBE antes de cair, como a do WebP em foto: o sem perda é MAIOR
# que a entrada de 5.000 B e os degraus com perda são menores. É o tipo de
# curva que a de cima (monótona) nunca produziria.
WEBP_FOTO = [9_000, 4_800, 4_200, 3_900, 3_500, 3_100, 2_900, 2_000, 1_800, 1_500, 1_000]
# E a arte chapada: o sem perda é menor que a entrada, os com perda não.
WEBP_FLAT = [1_200, 9_000, 8_000, 7_000, 6_000, 5_500, 5_200, 4_000, 3_500, 3_000, 2_000]


@pytest.mark.parametrize("alvo", [10_000_000, 5_000])   # 5.000 = a entrada
def test_conversao_pedida_com_alvo_folgado_percorre_a_escada_inteira(
    tmp_path, monkeypatch, alvo
):
    """Marcar "Converter para WebP" é o consentimento para recodificar: com a
    entrada já dentro do alvo (o padrão de 10 MB) a conversão NÃO pode virar um
    "só se for de graça". O sem perda do WebP (9.000 B) é maior que a entrada
    de 5.000 B; o limite da busca é "menor que a entrada", então vence o degrau
    de maior qualidade que de fato encolhe: o 1 (4.800 B)."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.JPEG)
    assert alvo >= info.size_bytes
    codificados = _curva_falsa(monkeypatch, formato_webp, WEBP_FOTO)

    r = mod.compress_file(src, alvo, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert len(codificados) > 1                      # foi além do degrau 0
    assert r.already_optimized is False
    assert r.formato_saida is Formato.WEBP and r.dst.suffix == ".webp"
    assert r.params == formato_webp.TABELA[1] and r.final_bytes == 4_800
    assert r.hit_target is True                      # contra o alvo do usuário


@pytest.mark.parametrize("alvo", [10_000_000, 5_000])
def test_mesmo_formato_com_alvo_folgado_so_considera_o_degrau_zero(
    tmp_path, monkeypatch, alvo
):
    """O contraponto, sem conversão: um WebP com perda que já cabe. O sem
    perda (9.000 B) é maior que ele, não há nada de graça, e os degraus com
    perda, que dariam, NÃO são tentados. O original volta."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.WEBP)
    assert alvo >= info.size_bytes
    codificados = _curva_falsa(monkeypatch, formato_webp, WEBP_FOTO)

    r = mod.compress_file(src, alvo, TODOS, SEM_FERRAMENTAS)

    assert codificados == [0]
    assert r.already_optimized is True and r.params is None
    assert r.formato_saida is Formato.WEBP
    assert r.dst.read_bytes() == src.read_bytes()
    assert r.hit_target is True
    assert [a.index for a in r.attempts] == [0]


@pytest.mark.parametrize("alvo", [10_000_000, 5_000])
def test_entrada_que_ja_cabe_usa_o_sem_perda_quando_ele_encolhe(
    tmp_path, monkeypatch, alvo
):
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    codificados = _curva_falsa(monkeypatch, formato_webp, WEBP_FLAT)

    r = mod.compress_file(src, alvo, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert codificados == [0]      # nenhum com perda, mesmo havendo ganho maior
    assert r.already_optimized is False
    assert r.params == formato_webp.TABELA[0] and r.final_bytes == 1_200
    assert r.hit_target is True


def test_png_ja_otimizado_nao_e_quantizado_para_economizar_um_pouco(
    tmp_path, monkeypatch
):
    """O caso que motivou o piso: o PNG que a própria ferramenta gerou, solto
    de novo com o alvo padrão de 10 MB. Os degraus 3 em diante (128 cores...)
    renderiam alguns KB, num arquivo que já cabia no alvo mil vezes."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    # 0 e 1 (sem perda) mal encolhem; 2 em diante, quantizados, encolhem mais
    tamanhos = [4_900, 4_850, 5_068, 2_641, 3_323, 2_259, 2_000, 1_900, 1_800, 1_700]
    codificados = _curva_falsa(monkeypatch, formato_png, tamanhos)

    r = mod.compress_file(src, 10_000_000, TODOS, COM_OXIPNG)

    assert codificados == [0, 1]
    assert r.params == formato_png.TABELA[1]   # o MENOR do prefixo, não o primeiro
    assert r.params.colors is None and r.params.scale_pct == 100
    assert r.final_bytes == 4_850 and r.hit_target is True


def test_png_sem_oxipng_so_codifica_um_degrau_sem_perda(tmp_path, monkeypatch):
    """Sem o oxipng os degraus 0 e 1 geram o MESMO arquivo: um encode a
    menos, e nenhum degrau quantizado ou reduzido."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    codificados = _curva_falsa(monkeypatch, formato_png, [5_000] * 10)

    r = mod.compress_file(src, 10_000_000, TODOS, SEM_FERRAMENTAS)

    assert codificados == [0]
    assert r.already_optimized is True


@pytest.mark.parametrize("formato", [Formato.GIF, Formato.JPEG])
def test_formato_sem_prefixo_so_tenta_o_degrau_zero_se_ja_cabe(
    tmp_path, monkeypatch, formato
):
    """GIF e JPEG não têm degrau sem perda, mas o degrau 0 é o menos danoso da
    escada: com a entrada dentro do alvo é o ÚNICO elegível. Nenhum outro é
    codificado, e se ele encolhe o arquivo é o entregue."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, formato)
    modulo = formatos.para(formato)
    falso, vistos = _codificador_falso(4_000)
    monkeypatch.setattr(modulo, "codificar", falso)

    r = mod.compress_file(
        src, 10_000_000, TODOS, Tools("ffmpeg", "ffprobe", "gifsicle")
    )

    assert vistos == [modulo.TABELA[0]]
    assert r.already_optimized is False and r.params == modulo.TABELA[0]
    assert r.final_bytes == 4_000 and r.hit_target is True
    assert [a.index for a in r.attempts] == [0]


@pytest.mark.parametrize("formato", [Formato.GIF, Formato.JPEG])
def test_formato_sem_prefixo_devolve_o_original_se_o_degrau_zero_nao_encolhe(
    tmp_path, monkeypatch, formato
):
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, formato)
    modulo = formatos.para(formato)
    falso, vistos = _codificador_falso(info.size_bytes + 100)
    monkeypatch.setattr(modulo, "codificar", falso)

    r = mod.compress_file(
        src, 10_000_000, TODOS, Tools("ffmpeg", "ffprobe", "gifsicle")
    )

    assert vistos == [modulo.TABELA[0]]
    assert r.already_optimized is True and r.params is None
    assert r.formato_saida is formato and r.hit_target is True
    assert r.dst.read_bytes() == src.read_bytes() and r.dst != src


@pytest.mark.parametrize("formato", [Formato.GIF, Formato.JPEG])
def test_degrau_zero_nao_vira_sem_perda(formato):
    """Elegível sob um alvo folgado não é o mesmo que sem perda: `SEM_PERDA`
    segue vazio em GIF e JPEG (a interface não pode dizer "sem perda" deles)."""
    assert formatos.para(formato).SEM_PERDA == ()


def test_entrada_que_ja_cabe_com_todos_os_degraus_sem_perda_falhando_e_erro(
    tmp_path, monkeypatch
):
    """Se o que se tentou falhou em tudo, não é "já otimizado": é um erro."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)

    def quebrado(s, d, params, i, t):
        raise EncodeError("quebrado")

    monkeypatch.setattr(formato_webp, "codificar", quebrado)

    with pytest.raises(mod.CompressError):
        mod.compress_file(src, 10_000_000, TODOS, SEM_FERRAMENTAS, converter_webp=True)


def test_entrada_que_ja_cabe_um_degrau_sem_perda_falho_nao_derruba_o_outro(
    tmp_path, monkeypatch
):
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)

    def falso(s, d, params, i, t):
        if formato_png.TABELA.index(params) == 0:
            raise EncodeError("oxipng falhou")
        d.write_bytes(b"w" * 4_000)
        return 4_000

    monkeypatch.setattr(formato_png, "codificar", falso)

    r = mod.compress_file(src, 10_000_000, TODOS, COM_OXIPNG)

    assert r.params == formato_png.TABELA[1] and r.final_bytes == 4_000
    assert next(a for a in r.attempts if a.index == 0).erro == "oxipng falhou"


def test_entrada_que_ja_cabe_respeita_o_cancelamento(tmp_path, monkeypatch):
    from gifc.search import Cancelled

    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    codificados = _curva_falsa(monkeypatch, formato_webp, WEBP_FLAT)

    with pytest.raises(Cancelled):
        mod.compress_file(
            src, 10_000_000, TODOS, SEM_FERRAMENTAS, converter_webp=True,
            should_cancel=lambda: True,
        )
    assert codificados == []


def test_entrada_um_byte_acima_do_alvo_percorre_a_escada(tmp_path, monkeypatch):
    """A fronteira: com a entrada UM byte acima do alvo, ela não cabe, e a
    escada é percorrida até onde for preciso (aqui, os com perda)."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.JPEG)
    codificados = _curva_falsa(monkeypatch, formato_webp, WEBP_FOTO)
    alvo = info.size_bytes - 1

    r = mod.compress_file(src, alvo, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert codificados[0] == 0 and len(codificados) > 1
    assert r.already_optimized is False
    assert r.params == formato_webp.TABELA[1] and r.final_bytes == 4_800
    assert r.hit_target is True


def test_alvo_abaixo_da_entrada_acha_o_de_maior_qualidade_numa_curva_irregular(
    tmp_path, monkeypatch
):
    """Curva com uma inversão na cauda (degrau 4 maior que o 3): a que os
    testes monótonos antigos nunca exerciam. O degrau 3 cabe e é o de maior
    qualidade; a bisecção, por sorte da curva, também o acha."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.JPEG)
    tamanhos = [9_000, 8_000, 7_000, 3_800, 4_300, 3_000, 2_900, 2_000, 1_800, 1_500, 1_000]
    _curva_falsa(monkeypatch, formato_webp, tamanhos)

    r = mod.compress_file(src, 3_900, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert r.params == formato_webp.TABELA[3] and r.hit_target is True


def test_alvo_abaixo_da_entrada_segue_inalterado(tmp_path, monkeypatch):
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.JPEG)
    _tamanho_pelo_degrau(monkeypatch, base=6_000, passo=500)
    alvo = 3_500
    assert alvo < info.size_bytes - 1
    esperado = next(
        i for i in range(len(formato_webp.TABELA)) if 6_000 - 500 * i <= alvo
    )

    r = mod.compress_file(src, alvo, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert r.params == formato_webp.TABELA[esperado]
    assert r.final_bytes <= alvo and r.hit_target is True


def test_alvo_abaixo_da_entrada_e_inatingivel_continua_sem_atingir(
    tmp_path, monkeypatch
):
    """O regime não pode transformar "não deu" em "deu"."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.JPEG)
    _tamanho_pelo_degrau(monkeypatch, base=4_900, passo=10)

    r = mod.compress_file(src, 100, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert r.already_optimized is False
    assert r.hit_target is False
    assert r.final_bytes > 100


def test_entrada_minuscula_cai_em_ja_otimizado_sem_quebrar(tmp_path, monkeypatch):
    """Com 1 byte nada o encolhe: o resultado é o original copiado, não um
    erro nem um alvo negativo."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    src.write_bytes(b"x")
    monkeypatch.setattr(mod, "probe", lambda p: ImagemInfo(
        path=src, formato=Formato.PNG, width=1, height=1, fps=10.0,
        frame_count=1, duration=0.0, size_bytes=1,
    ))
    _webp_falso(monkeypatch, 30)

    r = mod.compress_file(src, 10_000_000, TODOS, SEM_FERRAMENTAS,
                          converter_webp=True)

    assert r.already_optimized is True
    assert r.formato_saida is Formato.PNG
    assert r.dst.read_bytes() == b"x"
    assert r.hit_target is True   # o original (1 byte) cabe nos 10 MB pedidos


@pytest.mark.integration
def test_png_ja_otimizado_pela_ferramenta_nao_e_quantizado_de_novo(tools, tmp_path):
    """De ponta a ponta, com o oxipng de verdade: um degradê que a ferramenta
    já otimizou, solto de novo com o alvo padrão de 10 MB. Antes ia a 128
    cores para economizar 1,9 KB."""
    if tools.oxipng is None:
        pytest.skip("oxipng não disponível em bin/")
    src = tmp_path / "degrade.png"
    degrade().save(src, "PNG", optimize=True)

    r = compress_file(src, 10_000_000, TODOS, tools)

    assert r.hit_target is True
    assert r.already_optimized or (
        r.params.colors is None and r.params.scale_pct == 100
    )
    with Image.open(src) as a, Image.open(r.dst) as b:
        assert a.size == b.size
        assert list(a.convert("RGB").getdata()) == list(b.convert("RGB").getdata())


@pytest.mark.integration
def test_foto_com_conversao_e_alvo_folgado_converte_de_verdade(tmp_path):
    """JPEG, alvo padrão de 10 MB, conversão ligada: a foto já cabe, mas a caixa
    foi marcada. Entrega-se o WebP de maior qualidade que fica menor que ela,
    não o JPEG de volta."""
    src = tmp_path / "foto.jpg"
    _textura((640, 480)).save(src, "JPEG", quality=95)
    original = src.stat().st_size

    r = compress_file(src, 10_000_000, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert r.already_optimized is False and r.hit_target is True
    assert r.formato_saida is Formato.WEBP and r.dst.suffix == ".webp"
    assert r.final_bytes < original * 0.8, (r.final_bytes, original)
    assert r.params != formato_webp.TABELA[0]       # o sem perda é maior que a foto


@pytest.mark.integration
def test_foto_com_conversao_e_alvo_apertado_reduz_de_verdade(tmp_path):
    """Já a mesma foto com um alvo que ela NÃO cumpre: a escada é percorrida
    e a conversão para WebP com perda entrega."""
    src = tmp_path / "foto.jpg"
    _textura((640, 480)).save(src, "JPEG", quality=95)
    original = src.stat().st_size

    r = compress_file(src, original * 8 // 10, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert r.already_optimized is False
    assert r.formato_saida is Formato.WEBP and r.dst.suffix == ".webp"
    assert r.hit_target is True and r.final_bytes <= original * 8 // 10
    assert r.params != formato_webp.TABELA[0]


@pytest.mark.integration
def test_webp_com_perda_e_alvo_folgado_nao_e_reencodado(tmp_path):
    src = tmp_path / "foto.webp"
    _textura((640, 480)).save(src, "WEBP", quality=95)
    original = src.read_bytes()

    r = compress_file(src, 10_000_000, TODOS, SEM_FERRAMENTAS)

    assert r.already_optimized is True and r.hit_target is True
    assert r.dst.read_bytes() == original


@pytest.mark.integration
def test_webp_com_alvo_apertado_e_sem_conversao_reduz_de_verdade(tmp_path):
    src = tmp_path / "foto.webp"
    _textura((640, 480)).save(src, "WEBP", quality=95)
    original = src.stat().st_size

    r = compress_file(src, original * 8 // 10, TODOS, SEM_FERRAMENTAS)

    assert r.already_optimized is False
    assert r.formato_saida is Formato.WEBP
    assert r.hit_target is True and r.final_bytes <= original * 8 // 10
    assert r.params != formato_webp.TABELA[0]


# --- a escada não é monótona onde há degraus sem perda ---------------------
#
# A bisecção de `find_best` só é correta numa curva de tamanho monótona, e
# ela NÃO é monótona onde a escada tem degraus sem perda: em arte chapada o
# degrau sem perda é muito MENOR que os com perda logo abaixo dele. O
# `compress_file` por isso codifica o prefixo `SEM_PERDA` fora da bisecção e
# só bisecta o resto.

from gifc.formatos import png as formato_png  # noqa: E402

COM_OXIPNG = Tools("ffmpeg", "ffprobe", None, oxipng="oxipng")


def _curva_falsa(monkeypatch, modulo, tamanhos: list[int]) -> list[int]:
    """Troca o `codificar` de `modulo` por um que grava `tamanhos[degrau]`
    bytes, onde `degrau` é o índice do `Params` na TABELA. Devolve a lista
    (preenchida na ordem) dos degraus codificados."""
    assert len(tamanhos) == len(modulo.TABELA)
    codificados: list[int] = []

    def falso(s, d, params, i, t):
        degrau = modulo.TABELA.index(params)
        codificados.append(degrau)
        d.write_bytes(b"w" * tamanhos[degrau])
        return tamanhos[degrau]

    monkeypatch.setattr(modulo, "codificar", falso)
    return codificados


# Arte chapada em WebP: o degrau 0 (sem perda) é o MENOR, e os com perda logo
# abaixo dele são muito maiores, só voltando a baixar mais adiante. É o
# formato da curva medida numa captura de tela; os valores são sintéticos.
WEBP_CHAPADO = [100, 9_000, 8_000, 7_000, 6_000, 5_500, 5_200, 4_000, 3_500, 3_000, 2_000]


def test_webp_degrau_sem_perda_menor_que_os_com_perda_e_escolhido(
    tmp_path, monkeypatch
):
    """O defeito: a primeira sonda (degrau 5, 5.500 B) estoura o alvo, a
    bisecção descartava tudo à esquerda e o degrau 0 (100 B, sem perda e
    resolução cheia) nunca era medido."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    codificados = _curva_falsa(monkeypatch, formato_webp, WEBP_CHAPADO)
    alvo = 4_000
    assert alvo < info.size_bytes - 1   # fora do regime de alvo folgado

    r = mod.compress_file(src, alvo, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert r.params == formato_webp.TABELA[0]
    assert r.final_bytes == 100 and r.hit_target is True
    assert r.already_optimized is False
    assert codificados == [0]   # coube: nada mais precisa ser medido


def test_prefixo_nao_cabendo_a_cauda_ainda_e_bisectada(tmp_path, monkeypatch):
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    # o sem perda (4.500) não cabe nos 4.000; da cauda cabem os degraus 7 em diante
    tamanhos = [4_500, 9_000, 8_000, 7_000, 6_000, 5_500, 5_200, 4_000, 3_500, 3_000, 2_000]
    codificados = _curva_falsa(monkeypatch, formato_webp, tamanhos)

    r = mod.compress_file(src, 4_000, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert codificados[0] == 0   # o prefixo vem antes da cauda
    assert r.params == formato_webp.TABELA[7]
    assert r.hit_target is True
    assert len(codificados) == len(set(codificados))


def test_o_menor_indice_entre_prefixo_e_cauda_vence(tmp_path, monkeypatch):
    """PNG, dois degraus sem perda: o 0 não cabe, o 1 cabe. O 1 vence, e a
    cauda (cujos degraus caberiam, e são menores) nem chega a ser medida."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    tamanhos = [4_900, 3_900, 3_800, 3_000, 2_500, 2_000, 1_500, 1_200, 1_000, 900]
    codificados = _curva_falsa(monkeypatch, formato_png, tamanhos)

    r = mod.compress_file(src, 4_000, TODOS, COM_OXIPNG)

    assert r.params == formato_png.TABELA[1]
    assert r.final_bytes == 3_900
    assert codificados == [0, 1]


def test_primeiro_degrau_do_prefixo_que_cabe_dispensa_o_resto(tmp_path, monkeypatch):
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    tamanhos = [3_900, 3_000, 3_800, 3_000, 2_500, 2_000, 1_500, 1_200, 1_000, 900]
    codificados = _curva_falsa(monkeypatch, formato_png, tamanhos)

    r = mod.compress_file(src, 4_000, TODOS, COM_OXIPNG)

    assert r.params == formato_png.TABELA[0]
    assert codificados == [0]


def test_png_tem_inversoes_na_curva_e_o_prefixo_as_cobre(tmp_path, monkeypatch):
    """A forma da curva medida num degradê com oxipng: 4.550, 5.068, 2.641,
    3.323, 2.259. Com o alvo em 3.000 B o degrau 3 (128 cores) cabe e é o de
    maior qualidade; a bisecção sobre a escada inteira devolvia o 5."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    src.write_bytes(b"x" * 20_000)
    monkeypatch.setattr(mod, "probe", lambda p: ImagemInfo(
        path=src, formato=Formato.PNG, width=1280, height=800, fps=10.0,
        frame_count=1, duration=0.0, size_bytes=20_000,
    ))
    # os degraus 0 e 1 não cabem; o 2 (5.068) é uma inversão DENTRO da cauda
    tamanhos = [4_550, 4_550, 5_068, 2_641, 3_323, 2_259, 2_000, 1_900, 1_800, 1_700]
    _curva_falsa(monkeypatch, formato_png, tamanhos)

    r = mod.compress_file(src, 3_000, TODOS, COM_OXIPNG)

    assert r.params == formato_png.TABELA[3]


def test_degrau_do_prefixo_que_falha_nao_derruba_a_busca(tmp_path, monkeypatch):
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    chamadas: dict[int, int] = {}

    def falso(s, d, params, i, t):
        degrau = formato_webp.TABELA.index(params)
        chamadas[degrau] = chamadas.get(degrau, 0) + 1
        if degrau == 0:
            raise EncodeError("o sem perda quebrou")
        d.write_bytes(b"w" * WEBP_CHAPADO[degrau])
        return WEBP_CHAPADO[degrau]

    monkeypatch.setattr(formato_webp, "codificar", falso)

    r = mod.compress_file(src, 4_000, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert chamadas[0] == 2          # a falha transitória ainda ganha a segunda chance
    assert r.hit_target is True and r.final_bytes <= 4_000
    zero = next(a for a in r.attempts if a.index == 0)
    assert zero.erro == "o sem perda quebrou"


def test_progresso_traz_indices_da_escada_inteira(tmp_path, monkeypatch):
    """O log mostra `degrau i/total`: com o prefixo à parte os índices e o
    total continuam sendo os da escada completa."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    tamanhos = [4_500, 9_000, 8_000, 7_000, 6_000, 5_500, 5_200, 4_000, 3_500, 3_000, 2_000]
    _curva_falsa(monkeypatch, formato_webp, tamanhos)
    vistos: list[tuple[int, int]] = []

    r = mod.compress_file(
        src, 4_000, TODOS, SEM_FERRAMENTAS, converter_webp=True,
        on_progress=lambda a, total: vistos.append((a.index, total)),
    )

    assert vistos and {t for _, t in vistos} == {len(formato_webp.TABELA)}
    assert [i for i, _ in vistos] == [a.index for a in r.attempts]
    assert vistos[0][0] == 0
    # cada índice reportado é o do Params daquela tentativa na tabela
    assert all(formato_webp.TABELA[a.index] == a.params for a in r.attempts)


def test_cancelamento_vale_no_prefixo(tmp_path, monkeypatch):
    from gifc.search import Cancelled

    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    codificados = _curva_falsa(monkeypatch, formato_webp, WEBP_CHAPADO)

    with pytest.raises(Cancelled):
        mod.compress_file(
            src, 4_000, TODOS, SEM_FERRAMENTAS, converter_webp=True,
            should_cancel=lambda: True,
        )
    assert codificados == []


def test_escada_so_de_prefixo_nao_quebra(tmp_path, monkeypatch):
    """PNG sem paleta e sem escala: sobram só os dois degraus sem perda, e a
    cauda fica vazia. `find_best` recusa uma escada vazia."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    _curva_falsa(monkeypatch, formato_png, [4_900, 4_800] + [1_000] * 8)

    r = mod.compress_file(
        src, 4_000, Toggles(False, True, False, True), COM_OXIPNG,
    )

    assert r.hit_target is False
    assert r.params == formato_png.TABELA[1]    # o menor que se mediu
    assert r.already_optimized is False


@pytest.mark.parametrize(
    "entrada, modulo, ferramentas, conversao, esperado",
    [
        (Formato.PNG, formato_png, COM_OXIPNG, False, 2 + 4),         # cauda de 8
        (Formato.JPEG, formato_webp, SEM_FERRAMENTAS, True, 1 + 4),   # cauda de 10
    ],
    ids=["png", "webp"],
)
def test_orcamento_de_encodes_no_pior_caso(
    tmp_path, monkeypatch, entrada, modulo, ferramentas, conversao, esperado
):
    """Pior caso (nada cabe): o prefixo inteiro mais a bisecção da cauda."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, entrada)
    codificados = _curva_falsa(monkeypatch, modulo, [4_900] * len(modulo.TABELA))

    mod.compress_file(src, 100, TODOS, ferramentas, converter_webp=conversao)

    assert len(codificados) == esperado, codificados


_captura_de_tela = captura_de_tela


@pytest.mark.integration
def test_captura_de_tela_png_para_webp_entrega_o_sem_perda(tmp_path):
    """O defeito de ponta a ponta, com codificador de verdade: numa captura
    de tela o degrau 5 do WebP (q70) é maior que o alvo, e a bisecção sobre a
    escada inteira entregava um degrau com perda e escala reduzida. O sem
    perda — pixel idêntico, resolução cheia, uma fração do tamanho — nunca
    era medido."""
    from PIL import ImageChops

    src = tmp_path / "captura.png"
    _captura_de_tela().save(src, "PNG", optimize=True)
    original = src.stat().st_size
    alvo = original * 3 // 4

    # A premissa do teste: a sonda do meio da bisecção antiga estoura o alvo.
    sonda = tmp_path / "sonda.webp"
    assert formato_webp.codificar(
        src, sonda, formato_webp.TABELA[5], None, SEM_FERRAMENTAS
    ) > alvo
    sonda.unlink()

    r = compress_file(src, alvo, TODOS, SEM_FERRAMENTAS, converter_webp=True)

    assert r.params == formato_webp.TABELA[0]
    assert r.formato_saida is Formato.WEBP and r.hit_target is True
    assert r.final_bytes < original / 4
    assert r.attempts[0].index == 0
    with Image.open(src) as a, Image.open(r.dst) as b:
        assert a.size == b.size
        assert ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is None


def test_png_sem_oxipng_poupa_um_encode_em_qualquer_alvo(tmp_path, monkeypatch):
    """Os degraus 0 e 1 geram o mesmo arquivo sem oxipng, e a busca não mede
    os dois: com alvo apertado, no pior caso, são 1 + 4 codificações e não
    2 + 4."""
    mod, src, info = _preparar_formato(tmp_path, monkeypatch, Formato.PNG)
    codificados = _curva_falsa(monkeypatch, formato_png, [4_900] * 10)

    mod.compress_file(src, 100, TODOS, SEM_FERRAMENTAS)

    assert codificados.count(0) == 1 and 1 not in codificados
    assert len(codificados) <= 1 + 4
