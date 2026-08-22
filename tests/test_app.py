"""Testes para os ajudantes puros da GUI e para a construção da janela.

A GUI em si não é testada automaticamente (ver Step 4 do brief da Tarefa 8).
Aqui testamos só o que é puro: formatação de MB e parsing do alvo. A
construção de `App` é exercitada uma vez, sem mainloop, só para pegar erros
de layout/construção — sem simular arrasto ou clique.
"""

from __future__ import annotations

import tkinter
import pytest

from gifc.app import MB, _mb, _parse_alvo_mb


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


def _tk_disponivel() -> bool:
    try:
        r = tkinter.Tk()
        r.destroy()
        return True
    except tkinter.TclError:
        return False


@pytest.mark.skipif(not _tk_disponivel(), reason="sem display/Tk disponível")
def test_app_constroi_e_realiza_widgets_sem_erro(monkeypatch):
    # Nunca deixar um messagebox modal bloquear o teste, independente do
    # estado de bin/ ou do PATH nesta máquina.
    import gifc.app as app_mod

    monkeypatch.setattr(app_mod.messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(app_mod.messagebox, "showwarning", lambda *a, **k: None)
    monkeypatch.setattr(app_mod.messagebox, "askyesno", lambda *a, **k: False)

    app = app_mod.App()
    try:
        app.update()
        assert str(app.title()) == "Compressor de GIF"
    finally:
        app.destroy()
