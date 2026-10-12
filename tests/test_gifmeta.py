"""Testes do parser de GIF em Python puro. Os bytes são montados à mão."""

from __future__ import annotations

import struct

import pytest

from gifc.gifmeta import GifMetaError, GifRaw, parse


def _tela(w: int = 64, h: int = 48, tabela_n: int | None = None) -> bytes:
    """Cabeçalho + Logical Screen Descriptor (+ tabela global opcional)."""
    packed = 0 if tabela_n is None else 0x80 | tabela_n
    out = b"GIF89a" + struct.pack("<HHBBB", w, h, packed, 0, 0)
    if tabela_n is not None:
        out += bytes(3 * 2 ** (tabela_n + 1))
    return out


def _gce(delay: int) -> bytes:
    return b"\x21\xf9\x04" + struct.pack("<BHB", 0, delay, 0) + b"\x00"


def _subblocos(dados: bytes) -> bytes:
    partes = []
    for i in range(0, len(dados), 255):
        pedaco = dados[i : i + 255]
        partes.append(bytes([len(pedaco)]) + pedaco)
    partes.append(bytes(1))
    return b"".join(partes)


def _quadro(tabela_local_n: int | None = None, dados: bytes = b"\x01\x02\x03") -> bytes:
    packed = 0 if tabela_local_n is None else 0x80 | tabela_local_n
    out = b"\x2c" + struct.pack("<HHHHB", 0, 0, 1, 1, packed)
    if tabela_local_n is not None:
        out += bytes(3 * 2 ** (tabela_local_n + 1))
    return out + b"\x02" + _subblocos(dados)


TRAILER = b"\x3b"


def test_le_dimensoes_do_logical_screen_descriptor():
    gif = parse(_tela(64, 48) + _quadro() + TRAILER)
    assert (gif.width, gif.height) == (64, 48)


def test_conta_quadros_pelos_image_descriptors():
    gif = parse(_tela() + _quadro() * 3 + TRAILER)
    assert gif.frame_count == 3


def test_soma_delays_das_extensoes_de_controle():
    dados = _tela() + _gce(10) + _quadro() + _gce(15) + _quadro() + TRAILER
    assert parse(dados).total_delay_cs == 25


def test_delay_pendente_vai_para_o_quadro_seguinte():
    dados = _tela() + _gce(7) + _quadro() + _gce(0) + _quadro() + TRAILER
    gif = parse(dados)
    assert gif.total_delay_cs == 7
    assert gif.frame_count == 2


def test_gce_sem_quadro_depois_nao_entra_na_soma():
    dados = _tela() + _gce(5) + _quadro() + _gce(99) + TRAILER
    assert parse(dados).total_delay_cs == 5


def test_extensao_intercalada_nao_perde_o_delay_pendente():
    comentario = b"\x21\xfe" + _subblocos(b"oi")
    dados = _tela() + _gce(9) + comentario + _quadro() + TRAILER
    assert parse(dados).total_delay_cs == 9


def test_gif87a_sem_gce_tem_delay_zero():
    dados = b"GIF87a" + struct.pack("<HHBBB", 8, 8, 0, 0, 0) + _quadro() + TRAILER
    assert parse(dados) == GifRaw(width=8, height=8, frame_count=1, total_delay_cs=0)


def test_pula_tabela_global_de_cores():
    # N=1 -> 3 * 2^2 = 12 bytes; N=0 -> 6 bytes
    gif = parse(_tela(tabela_n=1) + _quadro() + TRAILER)
    assert gif.frame_count == 1
    gif = parse(_tela(tabela_n=0) + _gce(3) + _quadro() + TRAILER)
    assert (gif.frame_count, gif.total_delay_cs) == (1, 3)


def test_pula_tabela_local_de_cores():
    dados = _tela() + _gce(4) + _quadro(tabela_local_n=2) + _quadro() + TRAILER
    gif = parse(dados)
    assert gif.frame_count == 2
    assert gif.total_delay_cs == 4


def test_pula_extensao_de_aplicacao_netscape():
    netscape = b"\x21\xff\x0bNETSCAPE2.0" + b"\x03\x01\x00\x00\x00"
    dados = _tela() + netscape + _gce(6) + _quadro() + TRAILER
    gif = parse(dados)
    assert (gif.frame_count, gif.total_delay_cs) == (1, 6)


def test_pula_comentario():
    comentario = b"\x21\xfe" + _subblocos(b"x" * 300)
    gif = parse(_tela() + comentario + _quadro() + TRAILER)
    assert gif.frame_count == 1


def test_pula_extensao_de_texto():
    texto = b"\x21\x01\x0c" + bytes(12) + _subblocos(b"abc")
    gif = parse(_tela() + texto + _quadro() + TRAILER)
    assert gif.frame_count == 1


def test_nao_aplica_clamp_em_delay_pequeno():
    assert parse(_tela() + _gce(1) + _quadro() + TRAILER).total_delay_cs == 1


def test_delay_usa_uint16_little_endian():
    assert parse(_tela() + _gce(0x0102) + _quadro() + TRAILER).total_delay_cs == 258


def test_magia_invalida_levanta_erro():
    with pytest.raises(GifMetaError, match="assinatura"):
        parse(b"\x89PNG\r\n\x1a\n" + bytes(20))


def test_arquivo_vazio_levanta_erro():
    with pytest.raises(GifMetaError):
        parse(b"")


def test_truncado_no_cabecalho_levanta_erro():
    with pytest.raises(GifMetaError, match="cabeçalho"):
        parse(b"GIF8")


def test_truncado_no_descritor_de_tela_levanta_erro():
    with pytest.raises(GifMetaError, match="descritor de tela"):
        parse(b"GIF89a\x40\x00\x30")


def test_truncado_na_tabela_global_levanta_erro():
    completo = _tela(tabela_n=2)
    with pytest.raises(GifMetaError, match="tabela global"):
        parse(completo[:-3])


def test_truncado_no_meio_de_um_sub_bloco_levanta_erro():
    dados = _tela() + b"\x21\xfe\x0a" + b"abc"  # promete 10 bytes, entrega 3
    with pytest.raises(GifMetaError, match="sub-bloco"):
        parse(dados)


def test_truncado_no_meio_dos_dados_de_imagem_levanta_erro():
    dados = _tela() + _quadro(dados=b"z" * 50)[:-10]
    with pytest.raises(GifMetaError, match="nos dados de imagem, no meio de um sub-bloco"):
        parse(dados)


def test_truncado_no_descritor_de_imagem_levanta_erro():
    with pytest.raises(GifMetaError, match="descritor de imagem"):
        parse(_tela() + b"\x2c\x00\x00")


def test_truncado_na_tabela_local_levanta_erro():
    dados = _tela() + _quadro(tabela_local_n=2)[:12]
    with pytest.raises(GifMetaError, match="tabela local"):
        parse(dados)


def test_truncado_apos_marcador_de_extensao_levanta_erro():
    with pytest.raises(GifMetaError, match="extensão"):
        parse(_tela() + b"\x21")


def test_truncado_no_gce_levanta_erro():
    with pytest.raises(GifMetaError, match="controle gráfico"):
        parse(_tela() + b"\x21\xf9\x04\x00\x0a")


@pytest.mark.parametrize("tamanho", [0, 2, 3, 5])
def test_gce_com_tamanho_diferente_de_4_levanta_erro(tamanho: int):
    # um GCE malformado não pode deixar o delay pendente anterior vazar
    # para outro quadro: GCE(7) + GCE de tamanho errado + quadro
    ruim = b"\x21\xf9" + bytes([tamanho]) + bytes(tamanho) + bytes(1)
    offset = len(_tela()) + len(_gce(7))
    dados = _tela() + _gce(7) + ruim + _quadro() + TRAILER
    with pytest.raises(GifMetaError, match=rf"controle gráfico malformada no byte {offset}: .*{tamanho}"):
        parse(dados)


def test_dimensoes_zero_sao_aceitas_por_enquanto():
    # Decisão registrada: o parser reporta o que o arquivo diz; quem consome
    # decide o que fazer com 0x0. Se isto passar a levantar erro, que seja
    # deliberado e este teste mude junto.
    gif = parse(_tela(0, 0) + _quadro() + TRAILER)
    assert (gif.width, gif.height) == (0, 0)
    assert gif.frame_count == 1


def test_bloco_desconhecido_levanta_erro():
    with pytest.raises(GifMetaError, match="0x99"):
        parse(_tela() + b"\x99" + TRAILER)


def test_sem_trailer_mas_com_quadros_e_aceito():
    gif = parse(_tela() + _gce(2) + _quadro() + _quadro())
    assert (gif.frame_count, gif.total_delay_cs) == (2, 2)


def test_sem_trailer_e_sem_quadros_levanta_erro():
    with pytest.raises(GifMetaError, match="fim do arquivo"):
        parse(_tela())


def test_trailer_sem_quadros_levanta_erro():
    with pytest.raises(GifMetaError, match="nenhum quadro"):
        parse(_tela() + TRAILER)


def test_bytes_apos_o_trailer_sao_ignorados():
    gif = parse(_tela() + _quadro() + TRAILER + b"lixo\x99\x99")
    assert gif.frame_count == 1


# Blocos do GIF de varredura: (bytes, quadros lidos até aqui, delay somado até aqui).
# Os totais esperados são escritos à mão, independentes do parser.
_BLOCOS_VARREDURA = [
    (_tela(64, 48, tabela_n=1), 0, 0),
    (b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00", 0, 0),
    (_gce(5), 0, 0),
    (_quadro(tabela_local_n=1, dados=b"q" * 40), 1, 5),
    (b"\x21\xfe" + _subblocos(b"comentario"), 1, 5),
    (_gce(3), 1, 5),
    (_quadro(), 2, 8),
    (_gce(9), 2, 8),  # GCE órfão: seu delay não pertence a nenhum quadro
    (TRAILER, 2, 8),
]
_GIF_VARREDURA = b"".join(b for b, _, _ in _BLOCOS_VARREDURA)

# Fronteiras limpas derivadas dos comprimentos dos blocos montados, nunca
# um literal: editar o fixture não pode silenciar a metade interessante.
_FRONTEIRAS: dict[int, tuple[int, int]] = {}
_acumulado = 0
for _bytes, _quadros, _delay in _BLOCOS_VARREDURA:
    _acumulado += len(_bytes)
    _FRONTEIRAS[_acumulado] = (_quadros, _delay)


@pytest.mark.parametrize("corte", range(len(_GIF_VARREDURA) + 1))
def test_truncamento_em_qualquer_ponto(corte: int):
    """Corte numa fronteira limpa com >=1 quadro é aceito com o resultado exato;
    qualquer outro corte (dentro de um bloco, ou sem quadros lidos) levanta
    GifMetaError. Nunca IndexError nem struct.error."""
    quadros, delay = _FRONTEIRAS.get(corte, (0, 0))
    if quadros >= 1:
        esperado = GifRaw(width=64, height=48, frame_count=quadros, total_delay_cs=delay)
        assert parse(_GIF_VARREDURA[:corte]) == esperado
    else:
        with pytest.raises(GifMetaError):
            parse(_GIF_VARREDURA[:corte])


def test_varredura_cobre_as_duas_metades():
    # Guarda contra o fixture degenerar: precisa haver cortes aceitos e rejeitados.
    aceitos = [c for c, (q, _) in _FRONTEIRAS.items() if q >= 1]
    assert len(aceitos) >= 4
    assert min(aceitos) > len(_BLOCOS_VARREDURA[0][0])


@pytest.mark.slow
def test_arquivo_grande_e_processado():
    dados_imagem = b"\xab" * (15 * 1024 * 1024)
    quadro = _quadro(dados=dados_imagem)
    gif_bytes = _tela() + _gce(2) + quadro * 4 + TRAILER
    assert len(gif_bytes) > 60 * 1024 * 1024
    gif = parse(gif_bytes)
    assert gif.frame_count == 4
    assert gif.total_delay_cs == 2
