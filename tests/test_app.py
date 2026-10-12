"""Testes para os ajudantes puros da GUI e para a construção da janela.

A GUI em si não é testada automaticamente (ver Step 4 do brief da Tarefa 8).
Aqui testamos só o que é puro: formatação de MB e parsing do alvo. A
construção de `App` é exercitada uma vez, sem mainloop, só para pegar erros
de layout/construção — sem simular arrasto ou clique.
"""

from __future__ import annotations

import gc
import sys
import threading
import time
import tkinter
from unittest import mock

import pytest

from dataclasses import dataclass, fields
from pathlib import Path

from gifc.app import (
    EXTENSOES_ACEITAS,
    MB,
    _alavancas_aplicaveis,
    _conversao_possivel,
    _descrever_params,
    _filtrar_caminhos,
    _formatos_dos_arquivos,
    _mb,
    _parse_alvo_mb,
    _texto_resultado,
)
from gifc.compress import CompressResult
from gifc.formatos import gif, jpeg, png, webp
from gifc.formatos.gif import Params
from gifc.ladder import ALAVANCAS, Toggles
from gifc.probe import Formato
from gifc.search import Attempt


# ---------- _mb ----------


def test_mb_zero():
    assert _mb(0) == "0.00 MB"


def test_mb_um_mb_exato():
    assert _mb(MB) == "1.00 MB"


def test_mb_fracionario():
    assert _mb(int(2.5 * MB)) == "2.50 MB"


def test_mb_arredonda():
    # 1_500_000 bytes / MB ~= 1.430511...
    assert _mb(1_500_000) == "1.43 MB"


# ---------- _parse_alvo_mb ----------


def test_parse_alvo_inteiro():
    assert _parse_alvo_mb("10") == 10 * MB


def test_parse_alvo_decimal_com_ponto():
    assert _parse_alvo_mb("2.5") == int(2.5 * MB)


def test_parse_alvo_decimal_com_virgula():
    assert _parse_alvo_mb("2,5") == int(2.5 * MB)


def test_parse_alvo_com_espacos():
    assert _parse_alvo_mb("  10  ") == 10 * MB


@pytest.mark.parametrize(
    "texto",
    ["", "   ", "0", "0,0", "-5", "-1.5", "abc", "dez", "1,2,3"],
)
def test_parse_alvo_invalido_levanta_value_error(texto):
    with pytest.raises(ValueError):
        _parse_alvo_mb(texto)


# ---------- construção da janela (sem mainloop) ----------


def _soltar_variaveis_tk(janela) -> None:
    """Descarta as `StringVar`/`BooleanVar` da janela ANTES de destruí-la.

    `tkinter.Variable.__del__` fala com o interpretador Tcl. Se o coletor de
    lixo as recolhe depois do `destroy()` (ou numa thread que não é a
    principal, o que acontece quando um teste posterior dispara o GC), o
    resultado é um `PytestUnraisableExceptionWarning` ("main thread is not in
    main loop"), atribuído a um teste que nada tem a ver com Tk. Soltar as
    referências aqui, na thread principal e com a raiz ainda viva, faz o
    `__del__` rodar no lugar certo.
    """
    for nome, valor in list(vars(janela).items()):
        if isinstance(valor, tkinter.Variable):
            delattr(janela, nome)
    gc.collect()


@pytest.fixture(scope="module")
def _janela():
    """Uma única janela para todo o módulo.

    Criar vários `Tk()` no mesmo processo é intermitentemente instável no
    Python da Windows Store — falha ao localizar `init.tcl`, sem padrão
    reproduzível. Compartilhar uma raiz só remove a fragilidade e ainda
    deixa a suíte mais rápida.
    """
    import gifc.app as app_mod

    # Nenhum modal pode bloquear a suíte, seja qual for o estado de bin/
    # ou do PATH nesta máquina.
    with (
        mock.patch.object(app_mod.messagebox, "showerror", lambda *a, **k: None),
        mock.patch.object(app_mod.messagebox, "showwarning", lambda *a, **k: None),
        mock.patch.object(app_mod.messagebox, "askyesno", lambda *a, **k: False),
    ):
        try:
            janela = app_mod.App()
        except tkinter.TclError as exc:
            pytest.skip(f"sem Tk utilizável: {exc}")
        janela.update()

    try:
        yield janela
    finally:
        _soltar_variaveis_tk(janela)
        janela.destroy()


@pytest.fixture
def app(_janela):
    """Estado zerado a cada teste, sobre a janela compartilhada."""
    _janela.log.delete(*_janela.log.get_children())
    while not _janela.fila.empty():
        _janela.fila.get_nowait()
    _janela.cancelar.clear()
    _janela.trabalhando = False
    _janela.ultima_pasta = None
    _janela._iniciar_lote(0)
    return _janela


# ---------- construção ----------


def test_app_constroi_e_realiza_widgets_sem_erro(app):
    assert str(app.title()) == "Comprime Aí"


def test_app_comeca_ociosa(app):
    assert app.trabalhando is False
    assert app._after_id is not None


# ---------- resiliência do laço de drenagem ----------


def test_drenagem_reagenda_mesmo_quando_um_handler_levanta(app, monkeypatch):
    """Um handler que explode não pode matar o laço de drenagem.

    Sem o `finally` no reagendamento, a janela congelaria para sempre no
    meio de um lote: sem log, sem barra e sem forma de recuperar.
    """
    def handler_explosivo(tipo, carga):
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "_tratar_mensagem", handler_explosivo)
    app.fila.put(("log", "qualquer coisa"))

    antes = app._after_id
    app._drenar_fila()

    assert app._after_id is not None
    assert app._after_id != antes, "o laço não se reagendou"


def test_drenagem_registra_o_erro_no_log_em_vez_de_silenciar(app, monkeypatch):
    def handler_explosivo(tipo, carga):
        raise RuntimeError("boom")

    monkeypatch.setattr(app, "_tratar_mensagem", handler_explosivo)
    app.fila.put(("log", "qualquer coisa"))
    app._drenar_fila()

    textos = [app.log.item(i, "text") for i in app.log.get_children()]
    assert any("boom" in t for t in textos)


def test_drenagem_esvazia_a_fila_inteira_de_uma_vez(app):
    for i in range(5):
        app.fila.put(("log", f"linha {i}"))
    app._drenar_fila()

    textos = [app.log.item(i, "text") for i in app.log.get_children()]
    assert sum(1 for t in textos if t.startswith("linha ")) == 5
    assert app.fila.empty()


# ---------- log de tentativas ----------


def test_log_tentativa_bem_sucedida_mostra_o_tamanho(app):
    a = Attempt(
        index=3, params=Params(colors=128, lossy=30, scale_pct=100, fps=None),
        size_bytes=int(1.5 * MB),
    )
    app._log_tentativa(a, total=10)

    textos = [app.log.item(i, "text") for i in app.log.get_children()]
    assert any("1.50 MB" in t for t in textos)
    assert not any("falhou" in t for t in textos)


def test_log_tentativa_falha_mostra_a_mensagem_nao_um_tamanho(app):
    """Regressão: um degrau que falhou não pode ser renderizado como se
    tivesse produzido um arquivo de 8796093022208.00 MB — o sentinela
    FALHOU (sys.maxsize) nunca deve chegar como tamanho na UI. Em vez
    disso, a mensagem de erro carregada em `Attempt.erro` é mostrada."""
    a = Attempt(
        index=7, params=Params(colors=64, lossy=100, scale_pct=85, fps=None),
        size_bytes=sys.maxsize, erro="ffmpeg falhou: codec não suportado",
    )
    app._log_tentativa(a, total=10)

    textos = [app.log.item(i, "text") for i in app.log.get_children()]
    assert any("falhou" in t and "ffmpeg falhou: codec não suportado" in t for t in textos)
    assert not any("MB" in t for t in textos)


def test_log_tentativa_falha_trunca_mensagem_longa(app):
    a = Attempt(
        index=1, params=Params(colors=256, lossy=0, scale_pct=100, fps=None),
        size_bytes=sys.maxsize, erro="x" * 5000,
    )
    app._log_tentativa(a, total=10)

    textos = [app.log.item(i, "text") for i in app.log.get_children()]
    alvo = next(t for t in textos if "falhou" in t)
    assert len(alvo) < 500


# ---------- progresso do lote ----------


def test_progresso_comeca_em_zero(app):
    app._iniciar_lote(3)
    assert app.barra["value"] == 0


def test_progresso_avanca_com_as_tentativas(app):
    from gifc.app import TENTATIVAS_ESPERADAS_POR_ARQUIVO as E

    app._iniciar_lote(1)
    app.tentativas_arquivo_atual = 1
    app._atualizar_progresso()
    assert app.barra["value"] == pytest.approx(100.0 / E, abs=0.01)


def test_progresso_conta_o_lote_inteiro_nao_um_arquivo_so(app):
    """Com 2 arquivos, terminar o primeiro deve dar ~50%, não 100%."""
    app._iniciar_lote(2)
    app.arquivos_concluidos = 1
    app.tentativas_arquivo_atual = 0
    app._atualizar_progresso()
    assert app.barra["value"] == pytest.approx(50.0, abs=0.01)


def test_progresso_nunca_passa_de_100(app):
    app._iniciar_lote(1)
    app.tentativas_arquivo_atual = 999
    app._atualizar_progresso()
    assert app.barra["value"] == 100.0


def test_progresso_ignora_lote_vazio(app):
    app._iniciar_lote(0)
    app._atualizar_progresso()  # não pode dividir por zero
    assert app.barra["value"] == 0


def test_novo_lote_zera_o_progresso_do_anterior(app):
    app._iniciar_lote(1)
    app.tentativas_arquivo_atual = 5
    app._atualizar_progresso()
    assert app.barra["value"] == 100.0

    app._iniciar_lote(2)
    assert app.barra["value"] == 0
    assert app.arquivos_concluidos == 0
    assert app.tentativas_arquivo_atual == 0


# ---------- fechamento ----------


def test_fechar_cancela_o_after_pendente_e_sinaliza_o_worker(app, monkeypatch):
    """`destroy` é neutralizado: a janela é compartilhada pelo módulo.

    O que importa aqui é o que `_ao_fechar` faz ANTES de destruir — sinalizar
    o worker e cancelar o callback pendente, para que a drenagem não tente
    mexer em widget já morto.
    """
    monkeypatch.setattr(app, "destroy", lambda: None)
    assert app._after_id is not None

    app._ao_fechar()

    assert app.cancelar.is_set()
    assert app._after_id is None

    # devolve a janela ao estado utilizável para os próximos testes
    app.cancelar.clear()
    app._after_id = app.after(100, app._drenar_fila)


def test_fechar_espera_o_worker_terminar_antes_de_destruir(app, monkeypatch):
    """Fechar a janela com uma compressão em andamento não pode deixar o
    worker (e o tmpdir que ele limpa no `finally`) para trás: se o worker
    termina dentro da janela de espera, `_ao_fechar` aguarda por ele antes
    de destruir.
    """
    import gifc.app as app_mod

    monkeypatch.setattr(app_mod, "FECHAR_JOIN_TIMEOUT_SEGUNDOS", 1.0)
    monkeypatch.setattr(app, "destroy", lambda: None)

    terminou = threading.Event()
    t = threading.Thread(target=terminou.set, daemon=True)
    t.start()
    app.trabalhando = True
    app._thread = t

    app._ao_fechar()

    assert terminou.is_set()
    assert not t.is_alive()

    # devolve a janela ao estado utilizável para os próximos testes
    app.trabalhando = False
    app._thread = None
    app.cancelar.clear()
    app._after_id = app.after(100, app._drenar_fila)


def test_fechar_nao_bloqueia_para_sempre_se_o_worker_estiver_preso(app, monkeypatch):
    """Um worker preso dentro de uma chamada longa de subprocesso não pode
    fazer a janela parecer travada: o `join` é limitado, e `_ao_fechar`
    destrói a janela de qualquer forma quando o tempo se esgota — um
    best-effort, não uma garantia de limpeza."""
    import gifc.app as app_mod

    monkeypatch.setattr(app_mod, "FECHAR_JOIN_TIMEOUT_SEGUNDOS", 0.1)

    destruido = []
    monkeypatch.setattr(app, "destroy", lambda: destruido.append(True))

    preso = threading.Event()
    liberar = threading.Event()

    def trabalho_preso():
        preso.set()
        liberar.wait(timeout=5)  # simula um subprocesso que não retorna a tempo

    t = threading.Thread(target=trabalho_preso, daemon=True)
    t.start()
    assert preso.wait(timeout=2), "thread de teste não chegou a rodar"
    app.trabalhando = True
    app._thread = t

    inicio = time.monotonic()
    app._ao_fechar()
    duracao = time.monotonic() - inicio

    assert destruido == [True], "destroy deve rodar mesmo com o worker preso"
    assert duracao < 2.0, "_ao_fechar não pode bloquear além do timeout de join"

    liberar.set()
    t.join(timeout=2)

    # devolve a janela ao estado utilizável para os próximos testes
    app.trabalhando = False
    app._thread = None
    app.cancelar.clear()
    app._after_id = app.after(100, app._drenar_fila)


# ---------- filtragem de caminhos ----------


def _criar(tmp_path: Path, *nomes: str) -> list[Path]:
    caminhos = []
    for nome in nomes:
        p = tmp_path / nome
        p.write_bytes(b"x")
        caminhos.append(p)
    return caminhos


def test_extensoes_aceitas_sao_exatamente_as_cinco():
    assert EXTENSOES_ACEITAS == {".gif", ".png", ".jpg", ".jpeg", ".webp"}


def test_extensoes_aceitas_incluem_os_quatro_formatos(tmp_path):
    caminhos = _criar(tmp_path, "a.gif", "b.png", "c.jpg", "d.jpeg", "e.webp")
    aceitos, ignorados = _filtrar_caminhos(caminhos)
    assert aceitos == caminhos
    assert ignorados == 0


def test_extensao_e_aceita_sem_diferenciar_maiusculas(tmp_path):
    caminhos = _criar(tmp_path, "A.GIF", "B.PNG", "C.JPG", "D.JPEG", "E.WebP")
    aceitos, ignorados = _filtrar_caminhos(caminhos)
    assert aceitos == caminhos
    assert ignorados == 0


@pytest.mark.parametrize("nome", ["nota.txt", "foto.bmp", "scan.tiff", "sem_extensao"])
def test_extensao_nao_suportada_e_recusada(tmp_path, nome):
    aceitos, ignorados = _filtrar_caminhos(_criar(tmp_path, nome))
    assert aceitos == []
    assert ignorados == 1


def test_pasta_e_arquivo_inexistente_sao_recusados(tmp_path):
    pasta = tmp_path / "pasta.png"
    pasta.mkdir()
    fantasma = tmp_path / "fantasma.png"
    aceitos, ignorados = _filtrar_caminhos([pasta, fantasma])
    assert aceitos == []
    assert ignorados == 2


def test_filtragem_mistura_e_conta_so_os_recusados(tmp_path):
    caminhos = _criar(tmp_path, "a.png", "b.txt", "c.jpg", "d.bmp", "e.webp")
    aceitos, ignorados = _filtrar_caminhos(caminhos)
    assert [p.name for p in aceitos] == ["a.png", "c.jpg", "e.webp"]
    assert ignorados == 2


def test_formatos_por_extensao():
    assert _formatos_dos_arquivos(
        [Path("a.GIF"), Path("b.jpeg"), Path("c.jpg"), Path("d.webp"), Path("e.png")]
    ) == {Formato.GIF, Formato.JPEG, Formato.WEBP, Formato.PNG}


# ---------- quais alavancas valem para o que foi solto ----------


def test_so_png_nao_tem_framerate_nem_lossy():
    assert _alavancas_aplicaveis({Formato.PNG}, False) == {"palette", "scale"}


def test_gif_tem_todas_as_alavancas():
    assert _alavancas_aplicaveis({Formato.GIF}, False) == set(ALAVANCAS)


def test_jpeg_e_webp_so_tem_escala():
    for f in (Formato.JPEG, Formato.WEBP):
        assert _alavancas_aplicaveis({f}, False) == {"scale"}


def test_png_convertido_para_webp_perde_a_paleta():
    """A decisão segue o formato de SAÍDA: com a conversão, o PNG percorre a
    escada de WebP, que não tem coluna de cores."""
    assert _alavancas_aplicaveis({Formato.PNG}, True) == {"scale"}


@pytest.mark.parametrize("ordem", [
    [Formato.PNG, Formato.GIF], [Formato.GIF, Formato.PNG],
], ids=["png-primeiro", "gif-primeiro"])
@pytest.mark.parametrize("converter", [False, True])
def test_alavancas_de_um_lote_misto_sao_a_uniao_em_qualquer_ordem(ordem, converter):
    """Recebe LISTAS, de propósito: com um `set` de enum a ordem de iteração
    muda com o PYTHONHASHSEED, e um `=` no lugar de `|=` só falharia em parte
    das execuções. Com a ordem explícita, os dois sentidos são exercitados
    sempre: o resultado é a união, não o da última entrada."""
    assert _alavancas_aplicaveis(ordem, converter) == set(ALAVANCAS)


@pytest.mark.parametrize("ordem", [
    [Formato.PNG, Formato.JPEG], [Formato.JPEG, Formato.PNG],
], ids=["png-primeiro", "jpeg-primeiro"])
def test_uniao_de_png_e_jpeg_sem_conversao_inclui_a_paleta(ordem):
    """PNG tem paleta, JPEG não: a união tem, em qualquer ordem."""
    assert _alavancas_aplicaveis(ordem, False) == {"palette", "scale"}


def test_nada_solto_nao_restringe_nada():
    assert _alavancas_aplicaveis(set(), False) == set(ALAVANCAS)
    assert _conversao_possivel(set()) is True


@pytest.mark.parametrize(
    "entradas, esperado",
    [
        ({Formato.GIF}, False),
        ({Formato.WEBP}, False),
        ({Formato.PNG}, True),
        ({Formato.JPEG}, True),
        ({Formato.GIF, Formato.PNG}, True),
        ({Formato.GIF, Formato.WEBP}, False),
    ],
)
def test_conversao_so_e_possivel_com_png_ou_jpeg(entradas, esperado):
    assert _conversao_possivel(entradas) is esperado


# ---------- alavancas na janela ----------


def _desabilitado(widget) -> bool:
    return widget.instate(["disabled"])


def test_alavancas_de_gif_desabilitadas_quando_nao_ha_gif(app):
    app._ajustar_alavancas({Formato.PNG})
    assert _desabilitado(app.cb_fps)
    assert _desabilitado(app.cb_lossy)
    assert not _desabilitado(app.cb_palette)
    assert not _desabilitado(app.cb_scale)


def test_alavancas_de_gif_habilitadas_quando_ha_gif(app):
    app._ajustar_alavancas({Formato.PNG})
    app._ajustar_alavancas({Formato.PNG, Formato.GIF})
    assert not _desabilitado(app.cb_fps)
    assert not _desabilitado(app.cb_lossy)


def test_caixa_webp_desabilitada_quando_so_ha_gif(app):
    app._ajustar_alavancas({Formato.GIF})
    assert _desabilitado(app.cb_webp)


def test_caixa_webp_habilitada_quando_ha_png_ou_jpeg(app):
    for f in (Formato.PNG, Formato.JPEG):
        app._ajustar_alavancas({Formato.GIF})
        app._ajustar_alavancas({f})
        assert not _desabilitado(app.cb_webp), f


def test_caixa_webp_desligada_por_padrao(app):
    assert app.tg_webp.get() is False


def test_ligar_a_conversao_desabilita_a_paleta_de_um_png(app):
    """Os dois ficam amarrados: a caixa muda qual escada o PNG percorre."""
    app._ajustar_alavancas({Formato.PNG})
    assert not _desabilitado(app.cb_palette)
    app.tg_webp.set(True)
    try:
        app._ajustar_alavancas({Formato.PNG})
        assert _desabilitado(app.cb_palette)
    finally:
        app.tg_webp.set(False)
        app._ajustar_alavancas(set())


def test_clicar_na_caixa_reavalia_as_alavancas_sem_nova_soltura(app):
    """O clique real dispara o `command` da caixa: se ele não estiver ligado,
    a paleta ficaria habilitada mas ignorada em silêncio."""
    app._ajustar_alavancas({Formato.PNG})
    app.cb_webp.invoke()
    try:
        assert app.tg_webp.get() is True
        assert _desabilitado(app.cb_palette)
    finally:
        app.cb_webp.invoke()
        app._ajustar_alavancas(set())
    assert not _desabilitado(app.cb_palette)


def test_estado_inicial_tem_tudo_habilitado(app):
    app._ajustar_alavancas(set())
    for cb in (app.cb_palette, app.cb_lossy, app.cb_scale, app.cb_fps, app.cb_webp):
        assert not _desabilitado(cb)


# ---------- da soltura ao worker ----------


@pytest.fixture
def _lote_falso(app, monkeypatch, tmp_path):
    """Roda `_processar` de verdade, mas troca o worker por um gravador."""
    import gifc.app as app_mod

    chamadas: list[tuple] = []
    feito = threading.Event()

    def worker_falso(arquivos, alvo, toggles, converter_webp):
        chamadas.append((arquivos, alvo, toggles, converter_webp))
        feito.set()

    monkeypatch.setattr(app, "_trabalhar", worker_falso)
    monkeypatch.setattr(app, "tools", object())
    monkeypatch.setattr(app_mod.messagebox, "showinfo", lambda *a, **k: None)
    avisos: list[str] = []
    monkeypatch.setattr(
        app_mod.messagebox, "showwarning", lambda *a, **k: avisos.append(a[0])
    )

    def soltar(*nomes: str):
        feito.clear()
        app._processar(_criar(tmp_path, *nomes))

    def esperar():
        assert feito.wait(timeout=5), "o worker não foi disparado"
        app._thread.join(timeout=5)
        app.trabalhando = False

    yield soltar, esperar, chamadas, avisos
    app.tg_webp.set(False)
    app._ajustar_alavancas(set())


def test_soltar_pngs_desabilita_as_alavancas_de_gif(app, _lote_falso):
    soltar, esperar, chamadas, _ = _lote_falso
    soltar("a.png", "b.png")
    esperar()
    assert _desabilitado(app.cb_fps)
    assert _desabilitado(app.cb_lossy)
    assert len(chamadas[0][0]) == 2


def test_soltar_gif_depois_de_png_reabilita_as_alavancas(app, _lote_falso):
    soltar, esperar, _, _ = _lote_falso
    soltar("a.png")
    esperar()
    soltar("b.gif")
    esperar()
    assert not _desabilitado(app.cb_fps)
    assert _desabilitado(app.cb_webp)


def test_a_caixa_webp_chega_ao_worker(app, _lote_falso):
    soltar, esperar, chamadas, _ = _lote_falso
    app.tg_webp.set(True)
    soltar("a.png")
    esperar()
    assert chamadas[-1][3] is True


def test_caixa_webp_desligada_chega_como_falso(app, _lote_falso):
    soltar, esperar, chamadas, _ = _lote_falso
    soltar("a.png")
    esperar()
    assert chamadas[-1][3] is False


def test_caixa_webp_marcada_nao_vale_para_um_lote_so_de_gif(app, _lote_falso):
    """A caixa fica desabilitada, mas continua marcada: o que vale é o que
    ela PODE fazer, não o que mostra."""
    soltar, esperar, chamadas, _ = _lote_falso
    app.tg_webp.set(True)
    soltar("a.gif")
    esperar()
    assert chamadas[-1][3] is False


def test_extensao_recusada_nao_dispara_o_worker(app, _lote_falso):
    soltar, _, chamadas, avisos = _lote_falso
    soltar("nota.txt", "foto.bmp")
    assert chamadas == []
    assert app.trabalhando is False
    assert avisos, "o usuário precisa ser avisado de que nada foi aceito"


# ---------- o worker não toca em widget ----------


class _TkVigiado:
    """Proxy de `tkapp` que anota toda chamada feita fora da thread principal.

    Vigiar `Variable.get` não basta: `BooleanVar` e `StringVar` definem o PRÓPRIO
    `get`. Por isso a vigília fica no nível mais baixo, o objeto Tcl que todo
    widget e toda variável usam. Anota ANTES de repassar, então o registro
    existe mesmo que o código chamador engula o `RuntimeError` (o Tcl só o
    levanta sem mainloop; com o mainloop a chamada passaria em silêncio).
    """

    def __init__(self, real, violacoes: list[str]) -> None:
        self._real = real
        self._violacoes = violacoes

    def __getattr__(self, nome):
        atributo = getattr(self._real, nome)
        if not callable(atributo):
            return atributo

        def vigiado(*args, **kw):
            if threading.current_thread() is not threading.main_thread():
                self._violacoes.append(f"{nome}{args[:2]!r}")
                # Não repassa: o Tcl real bloquearia ~1s esperando o mainloop
                # e depois levantaria este mesmo erro.
                raise RuntimeError("chamada ao Tk fora da thread principal")
            return atributo(*args, **kw)

        return vigiado


def _vigiar_tk(app, monkeypatch) -> list[str]:
    """Troca o `tk` da janela, de cada widget e de cada variável pelo proxy."""
    violacoes: list[str] = []
    proxy = _TkVigiado(app.tk, violacoes)
    pendentes = [app]
    while pendentes:
        w = pendentes.pop()
        monkeypatch.setattr(w, "tk", proxy)
        pendentes.extend(w.winfo_children())
    for v in vars(app).values():
        if isinstance(v, tkinter.Variable):
            monkeypatch.setattr(v, "_tk", proxy)
    return violacoes


def _rodar_na_thread(alvo, *args) -> BaseException | None:
    """Roda `alvo` numa thread de verdade; devolve a exceção que a matou."""
    erro: list[BaseException] = []

    def corpo():
        try:
            alvo(*args)
        except BaseException as exc:  # noqa: BLE001 - só para inspecionar
            erro.append(exc)

    t = threading.Thread(target=corpo, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()
    return erro[0] if erro else None


def _compress_falso(monkeypatch, recebidos: list):
    import gifc.app as app_mod

    def falso(src, alvo, toggles, tools, **kw):
        recebidos.append(kw)
        return _resultado(src=src)

    monkeypatch.setattr(app_mod, "compress_file", falso)


def test_worker_repassa_a_conversao_sem_tocar_em_nenhum_widget(
    app, monkeypatch, tmp_path
):
    """`_trabalhar` roda numa thread real. Qualquer chamada ao Tcl que parta
    dela (ler `tg_webp`, `alvo_mb`, mexer em `status`, `log`, `barra`...) é
    registrada pelo vigia, e o teste exige zero."""
    violacoes = _vigiar_tk(app, monkeypatch)
    recebidos: list[dict] = []
    _compress_falso(monkeypatch, recebidos)
    monkeypatch.setattr(app, "tools", object())

    arquivos = _criar(tmp_path, "a.png", "b.jpg")
    erro = _rodar_na_thread(app._trabalhar, arquivos, MB, Toggles(), True)

    assert erro is None
    assert violacoes == []
    assert [kw["converter_webp"] for kw in recebidos] == [True, True]
    tipos = []
    while not app.fila.empty():
        tipos.append(app.fila.get_nowait()[0])
    assert tipos.count("resultado") == 2
    assert tipos[-1] == "fim"


def test_o_vigia_enxerga_a_thread_principal_sem_falso_alarme(app, monkeypatch):
    """Sem este controle, um vigia quebrado que nunca anota nada faria todos
    os outros testes passarem. Aqui a thread principal mexe em tudo."""
    violacoes = _vigiar_tk(app, monkeypatch)
    app.tg_webp.get()
    app.status.config(text="ok")
    app._log("x")
    assert violacoes == []


def _worker_que_le(var):
    def worker(self, arquivos, alvo, toggles, converter_webp):
        getattr(self, var).get()
    return worker


def _worker_que_escreve_variavel(self, arquivos, alvo, toggles, converter_webp):
    self.tg_webp.set(True)


def _worker_que_configura_widget(self, arquivos, alvo, toggles, converter_webp):
    self.status.config(text="mexi")


def _worker_que_insere_no_log(self, arquivos, alvo, toggles, converter_webp):
    self.log.insert("", "end", text="mexi")


def _worker_que_engole_runtimeerror(self, arquivos, alvo, toggles, converter_webp):
    """O Tcl só levanta `RuntimeError` sem mainloop. Com mainloop a chamada
    passa em silêncio; engolir o erro simula esse caso, em que só o vigia vê."""
    try:
        self.tg_webp.get()
        self.status.config(text="mexi")
    except RuntimeError:
        pass


@pytest.mark.parametrize(
    "worker",
    [
        _worker_que_le("tg_webp"),
        _worker_que_le("alvo_mb"),
        _worker_que_escreve_variavel,
        _worker_que_configura_widget,
        _worker_que_insere_no_log,
        _worker_que_engole_runtimeerror,
    ],
    ids=[
        "le-BooleanVar", "le-StringVar", "escreve-variavel", "config-de-widget",
        "insere-no-Treeview", "engole-RuntimeError",
    ],
)
def test_o_vigia_pega_um_worker_que_toca_o_tk(app, monkeypatch, worker):
    """Prova do vigia: workers deliberadamente errados TÊM de ser pegos,
    inclusive os que leem `BooleanVar`/`StringVar` (que têm `get` próprio, e
    escapavam de um patch em `Variable.get`) e os que engolem o RuntimeError."""
    violacoes = _vigiar_tk(app, monkeypatch)
    _rodar_na_thread(worker, app, [], MB, Toggles(), False)
    assert violacoes, "o vigia não viu o acesso ao Tk fora da thread principal"


@pytest.mark.parametrize("mutacao", ["le_variavel", "config_de_widget"])
def test_o__trabalhar_real_quebrado_e_pego_pelo_vigia(app, monkeypatch, tmp_path, mutacao):
    """Mutação do código de produção: o `_trabalhar` real, com uma linha
    proibida injetada, tem de reprovar no mesmo teste que guarda o original."""
    original = type(app)._trabalhar

    def quebrado(self, arquivos, alvo, toggles, converter_webp):
        try:
            if mutacao == "le_variavel":
                converter_webp = self.tg_webp.get()
            else:
                self.status.config(text="mexi")
        except RuntimeError:
            pass
        original(self, arquivos, alvo, toggles, converter_webp)

    violacoes = _vigiar_tk(app, monkeypatch)
    _compress_falso(monkeypatch, [])
    monkeypatch.setattr(app, "tools", object())
    _rodar_na_thread(
        quebrado, app, _criar(tmp_path, "a.png"), MB, Toggles(), False
    )
    assert violacoes


# ---------- texto do resultado ----------


def _resultado(**kw) -> CompressResult:
    base = dict(
        src=Path("foto.png"), dst=Path("foto_comprimido.png"),
        original_bytes=4 * MB, final_bytes=2 * MB,
        params=png.TABELA[2], hit_target=True, already_optimized=False,
        formato_entrada=Formato.PNG, formato_saida=Formato.PNG,
    )
    base.update(kw)
    return CompressResult(**base)


def test_resultado_de_jpeg_nunca_promete_ausencia_de_perda():
    """A spec é explícita: reempacotar JPEG degrada mesmo no degrau mais
    conservador. Vale para TODOS os degraus, e para a saída JPEG e WebP."""
    for saida, modulo in ((Formato.JPEG, jpeg), (Formato.WEBP, webp)):
        for params in modulo.TABELA:
            r = _resultado(
                src=Path("foto.jpg"), dst=Path("foto_comprimido.x"),
                params=params, formato_entrada=Formato.JPEG, formato_saida=saida,
            )
            texto = _texto_resultado(r)
            assert "sem perda" not in texto.lower(), (saida, params, texto)
            assert "lossless" not in texto.lower(), (saida, params, texto)


def test_resultado_de_png_sem_perda_pode_dizer_sem_perda():
    for params in png.TABELA[:2]:
        assert "sem perda" in _texto_resultado(_resultado(params=params))


def test_resultado_de_png_com_perda_nao_diz_sem_perda():
    for params in png.TABELA[2:]:
        assert "sem perda" not in _texto_resultado(_resultado(params=params))


def test_resultado_de_webp_sem_perda_so_no_degrau_zero():
    textos = [
        _texto_resultado(_resultado(
            src=Path("a.webp"), dst=Path("a_comprimido.webp"), params=p,
            formato_entrada=Formato.WEBP, formato_saida=Formato.WEBP,
        ))
        for p in webp.TABELA
    ]
    assert "sem perda" in textos[0]
    assert all("sem perda" not in t for t in textos[1:])


def test_png_convertido_no_degrau_zero_do_webp_e_sem_perda():
    r = _resultado(
        dst=Path("foto_comprimido.webp"), params=webp.TABELA[0],
        formato_saida=Formato.WEBP,
    )
    assert "sem perda" in _texto_resultado(r)


def test_resultado_de_gif_nunca_diz_sem_perda():
    for params in gif.TABELA:
        r = _resultado(
            src=Path("a.gif"), dst=Path("a_comprimido.gif"), params=params,
            formato_entrada=Formato.GIF, formato_saida=Formato.GIF,
        )
        assert "sem perda" not in _texto_resultado(r)


def test_resultado_sem_conversao_nao_nomeia_formatos():
    texto = _texto_resultado(_resultado())
    assert "→ WebP" not in texto
    assert "PNG →" not in texto
    assert "foto_comprimido.png" in texto


@pytest.mark.parametrize(
    "entrada, rotulo", [(Formato.PNG, "PNG"), (Formato.JPEG, "JPEG")]
)
def test_resultado_convertido_nomeia_os_dois_formatos(entrada, rotulo):
    r = _resultado(
        dst=Path("foto_comprimido.webp"), params=webp.TABELA[3],
        formato_entrada=entrada, formato_saida=Formato.WEBP,
    )
    texto = _texto_resultado(r)
    assert f"{rotulo} → WebP" in texto
    assert "foto_comprimido.webp" in texto


def _mantido(entrada: Formato = Formato.PNG, **kw) -> CompressResult:
    ext = {Formato.PNG: "png", Formato.JPEG: "jpg", Formato.GIF: "gif",
           Formato.WEBP: "webp"}[entrada]
    base = dict(
        src=Path(f"a.{ext}"), dst=Path(f"a_comprimido.{ext}"), params=None,
        already_optimized=True, final_bytes=4 * MB,
        formato_entrada=entrada, formato_saida=entrada,
    )
    base.update(kw)
    return _resultado(**base)


def test_original_mantido_sem_conversao_diz_que_ja_estava_otimizado():
    texto = _texto_resultado(_mantido(), converteu_webp=False)
    assert "já estava otimizado" in texto
    assert "WebP" not in texto and "webp" not in texto
    assert "a_comprimido.png" in texto


@pytest.mark.parametrize("entrada", [Formato.PNG, Formato.JPEG])
def test_original_mantido_com_conversao_explica_que_o_webp_nao_ajudou(entrada):
    """Com a caixa ligada o arquivo nunca percorreu a escada do próprio
    formato: "já estava otimizado" seria mentira sobre ela. O texto explica
    por que o original ficou, sem prometer um `.webp` que não existe."""
    r = _mantido(entrada)
    texto = _texto_resultado(r, converteu_webp=True)
    rotulo = "PNG" if entrada is Formato.PNG else "JPEG"
    assert "nenhum degrau de WebP ficou menor" in texto
    assert f"a escada de {rotulo} não foi tentada" in texto
    assert "já estava otimizado" not in texto
    assert r.dst.name in texto and ".webp" not in texto
    assert "sem perda" not in texto


@pytest.mark.parametrize("entrada", [Formato.GIF, Formato.WEBP])
def test_original_mantido_de_gif_ou_webp_nunca_cita_a_conversao(entrada):
    """A caixa vale para o LOTE; só PNG/JPEG podem ter tentado converter. Um
    GIF (que nunca converte) ou um WebP num lote com a caixa ligada mantém o
    texto de sempre."""
    texto = _texto_resultado(_mantido(entrada), converteu_webp=True)
    assert "já estava otimizado" in texto
    assert "nenhum degrau de WebP" not in texto


def test_com_conversao_que_funcionou_o_texto_nao_explica_nada():
    r = _resultado(
        dst=Path("foto_comprimido.webp"), params=webp.TABELA[3],
        formato_saida=Formato.WEBP,
    )
    texto = _texto_resultado(r, converteu_webp=True)
    assert "nenhum degrau" not in texto and "não foi tentada" not in texto
    assert "PNG → WebP" in texto


def test_log_resultado_usa_a_flag_do_lote_gravada_em_processar(app):
    """`_log_resultado` não recebe a caixa: lê o que `_processar` gravou."""
    app._lote_converteu = True
    app._log_resultado(_mantido())
    app._lote_converteu = False
    app._log_resultado(_mantido())
    textos = [app.log.item(i, "text") for i in app.log.get_children()]
    assert "nenhum degrau de WebP ficou menor" in textos[0]
    assert "já estava otimizado" in textos[1]


def test_processar_grava_a_flag_do_lote(app, _lote_falso):
    soltar, esperar, _, _ = _lote_falso
    app.tg_webp.set(True)
    soltar("a.png")
    esperar()
    assert app._lote_converteu is True
    soltar("b.gif")
    esperar()
    assert app._lote_converteu is False, "caixa marcada mas inútil para só GIF"


def test_original_mantido_de_jpeg_nao_diz_sem_perda():
    for conv in (False, True):
        texto = _texto_resultado(_mantido(Formato.JPEG), converteu_webp=conv)
        assert "JPEG" in texto
        assert "sem perda" not in texto


def test_texto_traz_tamanhos_e_reducao():
    texto = _texto_resultado(_resultado())
    assert "4.00 MB" in texto and "2.00 MB" in texto and "50%" in texto


def test_log_resultado_escreve_o_texto_no_log(app):
    app._log_resultado(_resultado())
    textos = [app.log.item(i, "text") for i in app.log.get_children()]
    assert any("foto_comprimido.png" in t for t in textos)


def test_log_resultado_avisa_quando_nao_atingiu_o_alvo(app):
    app._log_resultado(_resultado(hit_target=False))
    textos = [app.log.item(i, "text") for i in app.log.get_children()]
    assert any("ATENÇÃO" in t for t in textos)


# ---------- log de tentativas de qualquer formato ----------


_FORMATOS = pytest.mark.parametrize(
    "modulo", [gif, png, jpeg, webp], ids=lambda m: m.__name__.rsplit(".", 1)[-1]
)


@_FORMATOS
def test_log_tentativa_renderiza_todos_os_degraus_de_cada_formato(app, modulo):
    """`Params` de cada formato tem colunas diferentes; o log não pode supor
    nenhuma. Sem isto, o `.colors` do GIF levantava AttributeError no
    primeiro degrau de JPEG/WebP."""
    for i, params in enumerate(modulo.TABELA):
        app._log_tentativa(
            Attempt(index=i, params=params, size_bytes=MB), total=len(modulo.TABELA)
        )
    textos = [app.log.item(i, "text") for i in app.log.get_children()]
    assert len(textos) == len(modulo.TABELA)
    assert all("1.00 MB" in t for t in textos)


@_FORMATOS
def test_descricao_de_params_distingue_todos_os_degraus(modulo):
    """Se uma coluna ficasse sem renderizar, dois degraus diferentes
    pareceriam iguais no log."""
    descricoes = [_descrever_params(p) for p in modulo.TABELA]
    assert len(set(descricoes)) == len(descricoes)


@_FORMATOS
def test_descricao_de_params_menciona_cada_coluna(modulo):
    """Uma parte por coluna, nenhuma alavanca escondida."""
    p = modulo.TABELA[-1]
    texto = _descrever_params(p)
    assert f"{p.scale_pct}%" in texto
    assert texto.count("·") == len(fields(p)) - 1


def test_descricao_de_params_nao_quebra_com_coluna_desconhecida():
    @dataclass(frozen=True)
    class Futuro:
        brilho: int
        scale_pct: int

    texto = _descrever_params(Futuro(3, 80))
    assert "brilho" in texto and "3" in texto and "80%" in texto


@pytest.mark.parametrize("modulo", [jpeg, webp], ids=["jpeg", "webp"])
def test_log_de_tentativas_nunca_afirma_ausencia_de_perda(app, modulo):
    """O log de uma tentativa não sabe de onde o arquivo veio. O degrau 0 do
    WebP roda tanto para PNG (sem perda de verdade) quanto para JPEG (nunca),
    então a frase só pode aparecer no texto do resultado, que conhece a
    entrada."""
    for i, params in enumerate(modulo.TABELA):
        app._log_tentativa(
            Attempt(index=i, params=params, size_bytes=MB), total=len(modulo.TABELA)
        )
    textos = [app.log.item(i, "text") for i in app.log.get_children()]
    assert not any("sem perda" in t.lower() for t in textos)
    assert not any("lossless" in t.lower() for t in textos)


def test_log_tentativa_do_gif_mantem_o_formato_antigo(app):
    app._log_tentativa(
        Attempt(index=3, params=Params(128, 30, 100, None), size_bytes=int(1.5 * MB)),
        total=20,
    )
    textos = [app.log.item(i, "text") for i in app.log.get_children()]
    assert textos == ["  degrau 3/19 · 128 cores · lossy 30 · 100% · orig → 1.50 MB"]


# ---------- do motor real ao texto ----------


def _png_real(tmp_path: Path) -> Path:
    from PIL import Image

    src = tmp_path / "tela.png"
    Image.effect_noise((64, 64), 60).convert("RGB").save(src, format="PNG")
    return src


@pytest.mark.parametrize("delta, converteu", [(+500, False), (-500, True)])
def test_texto_descreve_o_arquivo_escrito_e_nao_o_pedido(
    tmp_path, monkeypatch, delta, converteu
):
    """Com a conversão PEDIDA nos dois casos, só o resultado do motor decide
    o que a mensagem promete: um WebP maior que a entrada faz o original ser
    copiado, e a linha não pode citar `.webp`."""
    from gifc.compress import compress_file
    from gifc.formatos import webp as formato_webp
    from gifc.tools import Tools

    src = _png_real(tmp_path)
    tamanho = src.stat().st_size + delta

    def webp_falso(s, d, params, info, tools):
        d.write_bytes(b"w" * tamanho)
        return tamanho

    monkeypatch.setattr(formato_webp, "codificar", webp_falso)
    r = compress_file(
        src, 10 * MB, Toggles(), Tools("ffmpeg", "ffprobe", None),
        converter_webp=True,
    )

    texto = _texto_resultado(r, converteu_webp=True)
    assert r.dst.is_file()
    assert r.dst.name in texto
    if converteu:
        assert r.dst.suffix == ".webp"
        assert "PNG → WebP" in texto
    else:
        assert r.dst.suffix == ".png"
        assert ".webp" not in texto
        assert "nenhum degrau de WebP ficou menor" in texto
        assert "cópia mantida (PNG)" in texto
