from pathlib import Path

import pytest

from gifc.compress import CompressResult, compress_file, unique_output_path
from gifc.encode import EncodeError
from gifc.ladder import Toggles
from gifc.probe import GifInfo
from gifc.tools import Tools

TODOS = Toggles(True, True, True, True)


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

    monkeypatch.setattr(mod, "encode", encode_falha_uma_vez)

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

    monkeypatch.setattr(mod, "encode", encode_sempre_falha_no_indice_9)

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

    monkeypatch.setattr(mod, "encode", sempre_falha)

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

    monkeypatch.setattr(mod, "encode", encode_maior)

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

    monkeypatch.setattr(mod, "encode", encode_maior)

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

    monkeypatch.setattr(mod, "encode", encode_maior)

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

    monkeypatch.setattr(mod, "encode", encode_igual)

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
