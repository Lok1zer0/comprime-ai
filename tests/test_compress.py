import random
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
    if formato is Formato.GIF:
        alvo, ferramentas = 10_000_000, request.getfixturevalue("tools")
    else:
        alvo, ferramentas = original // 2, SEM_FERRAMENTAS

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
    if formato is not Formato.GIF:
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

    r = compress_file(copia, 10_000_000, TODOS, tools, converter_webp=True)

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

    r = mod.compress_file(
        src, 1_000_000, TODOS, Tools("ffmpeg", "ffprobe", "gifsicle"),
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
    mesmo = mod.compress_file(src, 1_000_000, TODOS, SEM_FERRAMENTAS)
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
