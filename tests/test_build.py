"""Testes da lógica do `tools/build.py`.

O que é só invocação do PyInstaller não dá para testar em unidade — a
verificação daquilo é o build real. O que dá, e está aqui, são as decisões que
o script toma por conta própria: recusar com o `.exe` aberto, publicar só no
fim, conferir um pacote existente, escolher o modo pelo tempo medido, e
cronometrar a abertura de um processo que se relança.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools.build import (
    LIMITE_SEGUNDOS,
    NOME,
    BuildError,
    _abre_uma_vez,
    _familia,
    _mata_arvore,
    _processos,
    _tem_janela,
    constroi,
    decide_modo,
    exe_do_pacote,
    exe_travado,
    garantir_binarios,
    mede_abertura,
    publicar,
    recusa_se_aberto,
    tamanho,
    verifica,
)


# ---------- onde fica o executável em cada modo ----------


def test_exe_do_pacote_no_onefile_fica_na_raiz_do_dist(tmp_path: Path) -> None:
    assert exe_do_pacote(tmp_path, "onefile") == tmp_path / f"{NOME}.exe"


def test_exe_do_pacote_no_onedir_fica_na_pasta_do_programa(tmp_path: Path) -> None:
    assert exe_do_pacote(tmp_path, "onedir") == tmp_path / NOME / f"{NOME}.exe"


def test_exe_do_pacote_recusa_modo_desconhecido(tmp_path: Path) -> None:
    with pytest.raises(BuildError):
        exe_do_pacote(tmp_path, "onetudo")


# ---------- recusar o build com o `.exe` aberto ----------


def test_exe_travado_e_falso_quando_o_arquivo_nao_existe(tmp_path: Path) -> None:
    """Pacote que ainda não existe não está aberto — o build tem de seguir."""
    assert exe_travado(tmp_path / f"{NOME}.exe") is False


def test_exe_travado_e_falso_num_arquivo_livre(tmp_path: Path) -> None:
    livre = tmp_path / f"{NOME}.exe"
    livre.write_bytes(b"MZ")
    assert exe_travado(livre) is False


def test_exe_travado_e_verdadeiro_quando_nao_se_pode_escrever(tmp_path: Path) -> None:
    """O mecanismo é abrir para escrita: é isso que o Windows nega.

    Um `.exe` em execução fica mapeado sem compartilhar escrita, então abrir
    `r+b` nele levanta `PermissionError` — a mesma falha que um arquivo
    somente-leitura produz, e é com ele que o teste exercita o caminho real,
    sem remendar função nenhuma.
    """
    preso = tmp_path / f"{NOME}.exe"
    preso.write_bytes(b"MZ")
    preso.chmod(stat.S_IREAD)
    try:
        assert exe_travado(preso) is True
    finally:
        preso.chmod(stat.S_IWRITE | stat.S_IREAD)


def test_recusa_se_aberto_levanta_com_mensagem_em_portugues(tmp_path: Path) -> None:
    preso = tmp_path / NOME / f"{NOME}.exe"
    preso.parent.mkdir()
    preso.write_bytes(b"MZ")
    preso.chmod(stat.S_IREAD)
    try:
        with pytest.raises(BuildError) as erro:
            recusa_se_aberto(tmp_path, "onedir")
        assert "feche" in str(erro.value).lower()
        assert NOME in str(erro.value)
    finally:
        preso.chmod(stat.S_IWRITE | stat.S_IREAD)


def test_recusa_se_aberto_deixa_passar_o_pacote_livre(tmp_path: Path) -> None:
    exe = tmp_path / NOME / f"{NOME}.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    recusa_se_aberto(tmp_path, "onedir")  # não levanta


# ---------- publicar só no fim ----------


def _pacote_falso(pasta: Path, marca: bytes = b"MZ") -> Path:
    """Uma árvore `onedir` plausível dentro de `pasta`."""
    programa = pasta / NOME
    interno = programa / "_internal"
    (interno / "tkinterdnd2" / "tkdnd").mkdir(parents=True)
    (interno / "assets").mkdir(parents=True)
    (programa / f"{NOME}.exe").write_bytes(marca)
    for nome in ("ffmpeg.exe", "gifsicle.exe", "oxipng.exe"):
        (interno / nome).write_bytes(b"bin")
    (interno / "assets" / "icone.ico").write_bytes(b"ico")
    return pasta


def test_publicar_move_o_estagio_para_o_destino(tmp_path: Path) -> None:
    estagio = _pacote_falso(tmp_path / "estagio")
    destino = tmp_path / "dist"
    publicar(estagio, destino)
    assert (destino / NOME / f"{NOME}.exe").read_bytes() == b"MZ"
    assert not estagio.exists()


def test_publicar_substitui_o_pacote_anterior_por_inteiro(tmp_path: Path) -> None:
    """Nada do build antigo pode sobrar misturado com o novo."""
    antigo = _pacote_falso(tmp_path / "dist", marca=b"VELHO")
    (antigo / NOME / "sobra-do-build-antigo.txt").write_text("lixo")
    estagio = _pacote_falso(tmp_path / "estagio", marca=b"NOVO")
    publicar(estagio, tmp_path / "dist")
    assert (tmp_path / "dist" / NOME / f"{NOME}.exe").read_bytes() == b"NOVO"
    assert not (tmp_path / "dist" / NOME / "sobra-do-build-antigo.txt").exists()


def test_publicar_recusa_estagio_inexistente(tmp_path: Path) -> None:
    with pytest.raises(BuildError):
        publicar(tmp_path / "nao-existe", tmp_path / "dist")


def test_constroi_nao_toca_o_dist_quando_o_pyinstaller_falha(tmp_path: Path) -> None:
    """A propriedade que paga o estágio: build que morre no meio não quebra
    o pacote anterior."""
    _pacote_falso(tmp_path / "dist", marca=b"ANTERIOR")

    def explode(modo: str, distpath: Path, workpath: Path) -> None:
        distpath.mkdir(parents=True, exist_ok=True)
        (distpath / "meio-do-caminho.bin").write_bytes(b"lixo")
        raise RuntimeError("o PyInstaller morreu no meio")

    with pytest.raises(RuntimeError):
        constroi("onedir", raiz=tmp_path, rodar=explode)
    assert (tmp_path / "dist" / NOME / f"{NOME}.exe").read_bytes() == b"ANTERIOR"
    assert not (tmp_path / "dist" / "meio-do-caminho.bin").exists()


def test_constroi_nao_toca_o_dist_quando_nao_sai_executavel(tmp_path: Path) -> None:
    """PyInstaller que termina com código 0 e sem `.exe` também é falha."""
    _pacote_falso(tmp_path / "dist", marca=b"ANTERIOR")

    def nao_produz_nada(modo: str, distpath: Path, workpath: Path) -> None:
        distpath.mkdir(parents=True, exist_ok=True)

    with pytest.raises(BuildError):
        constroi("onedir", raiz=tmp_path, rodar=nao_produz_nada)
    assert (tmp_path / "dist" / NOME / f"{NOME}.exe").read_bytes() == b"ANTERIOR"


def test_constroi_publica_o_que_o_pyinstaller_produziu(tmp_path: Path) -> None:
    def produz(modo: str, distpath: Path, workpath: Path) -> None:
        distpath.mkdir(parents=True, exist_ok=True)
        _pacote_falso(distpath, marca=b"NOVO")

    dist = constroi("onedir", raiz=tmp_path, rodar=produz)
    assert dist == tmp_path / "dist"
    assert (dist / NOME / f"{NOME}.exe").read_bytes() == b"NOVO"


def test_constroi_passa_o_modo_pedido_ao_pyinstaller(tmp_path: Path) -> None:
    vistos: list[str] = []

    def produz(modo: str, distpath: Path, workpath: Path) -> None:
        vistos.append(modo)
        distpath.mkdir(parents=True, exist_ok=True)
        (distpath / f"{NOME}.exe").write_bytes(b"MZ")

    constroi("onefile", raiz=tmp_path, rodar=produz)
    assert vistos == ["onefile"]


# ---------- conferir um pacote existente (`--verifica`) ----------


def test_verifica_aprova_um_onedir_completo(tmp_path: Path) -> None:
    _pacote_falso(tmp_path)
    problemas, avisos = verifica(tmp_path, "onedir")
    assert problemas == []
    assert avisos == []


def test_verifica_acusa_binario_que_falta(tmp_path: Path) -> None:
    _pacote_falso(tmp_path)
    (tmp_path / NOME / "_internal" / "oxipng.exe").unlink()
    problemas, _ = verifica(tmp_path, "onedir")
    assert any("oxipng.exe" in p for p in problemas)


def test_verifica_acusa_o_tkdnd_que_falta(tmp_path: Path) -> None:
    """A falha silenciosa mais provável de todo o empacotamento: o app abre,
    arrastar arquivo não faz nada, e nenhum erro aparece."""
    _pacote_falso(tmp_path)
    (tmp_path / NOME / "_internal" / "tkinterdnd2" / "tkdnd").rmdir()
    problemas, _ = verifica(tmp_path, "onedir")
    assert any("tkdnd" in p for p in problemas)


def test_verifica_acusa_executavel_que_falta(tmp_path: Path) -> None:
    problemas, _ = verifica(tmp_path, "onedir")
    assert any(f"{NOME}.exe" in p for p in problemas)


def test_verifica_trata_icone_ausente_como_aviso(tmp_path: Path) -> None:
    """Sem ícone o programa funciona igual — não é motivo para reprovar."""
    _pacote_falso(tmp_path)
    (tmp_path / NOME / "_internal" / "assets" / "icone.ico").unlink()
    problemas, avisos = verifica(tmp_path, "onedir")
    assert problemas == []
    assert any("ícone" in a for a in avisos)


def test_verifica_no_onefile_so_alcanca_o_executavel(tmp_path: Path) -> None:
    """Num `.exe` único o conteúdo só existe extraído em tempo de execução;
    dizer isso é mais honesto que aprovar sem ter olhado."""
    (tmp_path / f"{NOME}.exe").write_bytes(b"MZ" * 100)
    problemas, avisos = verifica(tmp_path, "onefile")
    assert problemas == []
    assert avisos, "o onefile tem de avisar que a conferência não alcança o conteúdo"


# ---------- tamanho ----------


def test_tamanho_de_arquivo(tmp_path: Path) -> None:
    arq = tmp_path / "a.bin"
    arq.write_bytes(b"x" * 1234)
    assert tamanho(arq) == 1234


def test_tamanho_soma_a_arvore_inteira(tmp_path: Path) -> None:
    (tmp_path / "sub" / "mais-fundo").mkdir(parents=True)
    (tmp_path / "a.bin").write_bytes(b"x" * 10)
    (tmp_path / "sub" / "b.bin").write_bytes(b"x" * 20)
    (tmp_path / "sub" / "mais-fundo" / "c.bin").write_bytes(b"x" * 30)
    assert tamanho(tmp_path) == 60


# ---------- decisão de modo ----------


def test_o_limite_da_spec_e_dez_segundos() -> None:
    assert LIMITE_SEGUNDOS == 10.0


def test_abertura_rapida_escolhe_o_exe_unico() -> None:
    assert decide_modo(4.2) == "onefile"


def test_abertura_lenta_escolhe_a_pasta_em_zip() -> None:
    assert decide_modo(23.7) == "onedir"


def test_exatamente_no_limite_nao_e_abaixo_do_limite() -> None:
    """A regra é "abaixo de 10 s"; 10,0 não está abaixo de 10,0."""
    assert decide_modo(LIMITE_SEGUNDOS) == "onedir"
    assert decide_modo(LIMITE_SEGUNDOS - 0.01) == "onefile"


# ---------- medição: pior caso de três ----------


def test_mede_abertura_usa_o_pior_caso(tmp_path: Path) -> None:
    """Média esconderia justamente o que interessa: a abertura mais lenta é a
    que o usuário leigo vive na primeira vez."""
    tempos = iter([1.0, 9.5, 3.0])
    pior = mede_abertura(tmp_path / "x.exe", repeticoes=3, uma_vez=lambda _e: next(tempos))
    assert pior == 9.5


def test_mede_abertura_repete_o_numero_pedido(tmp_path: Path) -> None:
    chamadas: list[int] = []

    def conta(_exe: Path) -> float:
        chamadas.append(1)
        return 1.0

    mede_abertura(tmp_path / "x.exe", repeticoes=3, uma_vez=conta)
    assert len(chamadas) == 3


# ---------- binários obrigatórios ----------


def test_garantir_binarios_baixa_so_o_que_falta(tmp_path: Path) -> None:
    (tmp_path / "ffmpeg.exe").write_bytes(b"MZ")
    baixados: list[str] = []

    def baixar(nome: str, destino: Path) -> None:
        baixados.append(nome)
        destino.write_bytes(b"MZ")

    garantir_binarios(tmp_path, baixar=baixar)
    assert baixados == ["gifsicle", "oxipng"]


def test_garantir_binarios_nao_baixa_nada_quando_esta_tudo_la(tmp_path: Path) -> None:
    for nome in ("ffmpeg.exe", "gifsicle.exe", "oxipng.exe"):
        (tmp_path / nome).write_bytes(b"MZ")

    def proibido(nome: str, destino: Path) -> None:
        raise AssertionError(f"baixou {nome} sem precisar")

    garantir_binarios(tmp_path, baixar=proibido)


def test_garantir_binarios_diz_o_que_fazer_quando_o_download_falha(
    tmp_path: Path,
) -> None:
    """Falhar alto e claro: o que baixar e onde pôr."""

    def falha(nome: str, destino: Path) -> None:
        raise OSError("sem rede")

    with pytest.raises(BuildError) as erro:
        garantir_binarios(tmp_path, baixar=falha)
    texto = str(erro.value)
    assert "ffmpeg" in texto
    assert str(tmp_path) in texto


# ---------- a árvore de processos do onefile ----------
#
# O bootloader do `onefile` descompacta o pacote em %TEMP% e RELANÇA a si
# mesmo: o processo que lançamos é o pai, e a janela é do filho. Duas coisas
# dependem disso e erraram antes de existir este bloco: cronometrar "até a
# janela existir" (medido: o PID lançado nunca ganha janela, e a medição
# reportava "não abriu em 120 s" num app que abre em um segundo) e encerrar
# depois (medido: `Popen.kill()` mata o pai e deixa o app rodando, o que
# estraga a repetição seguinte e faz o `recusa_se_aberto` barrar o build
# seguinte por um processo que a medição esqueceu).


def test_processos_enxerga_este_processo() -> None:
    mapa = _processos()
    assert os.getpid() in mapa
    assert mapa[os.getpid()] > 0


def test_familia_pega_filho_e_neto() -> None:
    """Descer um nível não basta: o onefile pode relançar mais de uma vez."""
    arvore = {100: 1, 200: 100, 300: 200, 400: 1, 500: 400}
    assert _familia(100, processos=lambda: arvore) == {100, 200, 300}


def test_familia_ignora_arvore_alheia() -> None:
    """Se o dono do projeto tiver o app aberto, aquela janela não é a nossa.

    É por isso que a busca é por parentesco de PID e não por nome de imagem:
    uma instância aberta à mão tem o mesmo `comprime-ai.exe` e aprovaria a
    medição errada.
    """
    arvore = {100: 1, 200: 100, 400: 1, 500: 400}
    familia = _familia(100, processos=lambda: arvore)
    assert familia == {100, 200}
    assert 400 not in familia and 500 not in familia


def test_familia_termina_com_ciclo_no_instantaneo() -> None:
    """O instantâneo do Windows traz o PID 0 como pai de si mesmo.

    Partindo dele, uma busca sem conjunto de vistos volta nele e não termina
    nunca: a medição ficaria pendurada em vez de medir. Este teste reprova por
    não terminar, que é a forma que o defeito tem.
    """
    arvore = {0: 0, 4: 0, 100: 4}
    assert _familia(0, processos=lambda: arvore) == {0, 4, 100}


@pytest.mark.skipif(sys.platform != "win32", reason="a busca de janela é do Windows")
def test_tem_janela_so_conta_janela_visivel_dos_pids_pedidos() -> None:
    """Com uma janela Tk de verdade, as duas metades da pergunta.

    Metade um: janela de outro PID não vale (o PID 0 nunca tem janela).
    Metade dois: janela escondida não vale — e é por isso que a checagem passa
    por `IsWindowVisible`. Um Tk retirado da tela continua tendo HWND, então
    sem essa checagem a medição pararia o cronômetro antes de haver algo na
    tela para o usuário ver.
    """
    import tkinter

    raiz = tkinter.Tk()
    try:
        raiz.geometry("1x1+0+0")
        raiz.update()
        assert _tem_janela({os.getpid()}) is True
        assert _tem_janela({0}) is False
        raiz.withdraw()
        raiz.update()
        assert _tem_janela({os.getpid()}) is False
    finally:
        raiz.destroy()


class _ProcessoFalso:
    """Imita o bootloader do onefile: nasce, vive, e não morre por conta."""

    def __init__(self, pid: int = 100) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode


def test_abre_uma_vez_aceita_a_janela_de_um_descendente(tmp_path: Path) -> None:
    """A janela do filho conta. Sem isso, o onefile nunca termina de medir."""
    proc = _ProcessoFalso(pid=100)
    arvores = iter([{100}, {100}, {100, 200}])
    perguntas: list[set[int]] = []

    def tem_janela(pids: set[int]) -> bool:
        perguntas.append(set(pids))
        return 200 in pids

    segundos = _abre_uma_vez(
        tmp_path / "x.exe",
        teto=10.0,
        lancar=lambda _e: proc,
        familia=lambda _pid: next(arvores, {100, 200}),
        tem_janela=tem_janela,
        matar=lambda _pid: None,
        dormir=lambda _s: None,
    )
    assert segundos >= 0.0
    assert {100, 200} in perguntas, "nunca perguntou pela árvore inteira"


def test_abre_uma_vez_mata_a_arvore_e_nao_so_o_que_lancou(tmp_path: Path) -> None:
    proc = _ProcessoFalso(pid=100)
    mortos: list[int] = []
    _abre_uma_vez(
        tmp_path / "x.exe",
        teto=10.0,
        lancar=lambda _e: proc,
        familia=lambda _pid: {100, 200},
        tem_janela=lambda _pids: True,
        matar=mortos.append,
        dormir=lambda _s: None,
    )
    assert set(mortos) >= {100, 200}, f"deixou processo vivo: matou só {mortos}"


def test_abre_uma_vez_encerra_a_arvore_mesmo_estourando_o_teto(
    tmp_path: Path,
) -> None:
    """Medição que desiste não pode deixar o app rodando atrás."""
    proc = _ProcessoFalso(pid=100)
    mortos: list[int] = []
    with pytest.raises(BuildError):
        _abre_uma_vez(
            tmp_path / "x.exe",
            teto=0.05,
            lancar=lambda _e: proc,
            familia=lambda _pid: {100, 200},
            tem_janela=lambda _pids: False,
            matar=mortos.append,
            dormir=lambda _s: time.sleep(0.02),
        )
    assert set(mortos) >= {100, 200}


def test_abre_uma_vez_acusa_processo_que_morre_sem_abrir_janela(
    tmp_path: Path,
) -> None:
    proc = _ProcessoFalso(pid=100)
    proc.returncode = 3
    with pytest.raises(BuildError) as erro:
        _abre_uma_vez(
            tmp_path / "x.exe",
            teto=10.0,
            lancar=lambda _e: proc,
            familia=lambda _pid: {100},
            tem_janela=lambda _pids: False,
            matar=lambda _pid: None,
            dormir=lambda _s: None,
        )
    assert "3" in str(erro.value)


@pytest.mark.skipif(sys.platform != "win32", reason="o encerramento é do Windows")
def test_mata_arvore_mata_o_filho_tambem() -> None:
    """A forma exata do onefile, com processos de verdade: pai lança filho.

    `Popen.kill()` no pai deixa o filho rodando -- foi assim que sobraram
    janelas do app depois da medição. Um nível já distingue: matar sem alcançar
    a árvore deixa o filho vivo.
    """
    codigo = (
        "import subprocess, sys, time\n"
        "f = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
        "print(f.pid, flush=True)\n"
        "time.sleep(300)\n"
    )
    pai = subprocess.Popen(
        [sys.executable, "-c", codigo], stdout=subprocess.PIPE, text=True
    )
    try:
        assert pai.stdout is not None
        filho = int(pai.stdout.readline().strip())
        assert filho in _processos(), "fixture: o filho nem nasceu"

        _mata_arvore(pai.pid)

        fim = time.time() + 20
        while time.time() < fim and filho in _processos():
            time.sleep(0.1)
        assert filho not in _processos(), "o filho sobreviveu ao encerramento"
        assert pai.pid not in _processos()
    finally:
        _mata_arvore(pai.pid)
        pai.kill()
        pai.wait(timeout=30)
