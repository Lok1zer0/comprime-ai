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

from gifc.app import MB, _mb, _parse_alvo_mb
from gifc.formatos.gif import Params
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
    assert str(app.title()) == "Compressor de GIF"


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
