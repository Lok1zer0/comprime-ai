"""Empacota o `comprime-ai` com o PyInstaller, mede e publica em `dist/`.

    python tools/build.py                 # onedir (padrão), publica e mede
    python tools/build.py --modo onefile  # .exe único
    python tools/build.py --modo ambos    # constrói os dois e aplica a regra
    python tools/build.py --verifica      # confere o que já está em dist/
    python tools/build.py --sem-medir     # só constrói

Três comportamentos vieram do empacotamento do ViralCut, cada um por um
motivo que custou caro a quem descobriu:

**Recusar o build com o `.exe` do pacote aberto.** Ele trava os próprios
arquivos, o PyInstaller falha no meio da cópia e o erro não diz a causa — se
diz algo, é um `PermissionError` em um caminho dentro de `dist/`. Uma
checagem no começo troca isso por uma frase que diz o que fazer.

**Construir numa pasta de estágio e só então substituir `dist/`.** Um build
que morre no meio não pode deixar o pacote anterior quebrado. Aqui não há
dados de usuário morando ao lado do programa, mas a propriedade é barata e
vale por si.

**Um modo `--verifica`**, que confere o pacote sem reconstruir — útil quando
só se quer rodar os testes de integração do pacote.

Falha alto e claro quando falta binário: diz qual, onde pôr e o que roda para
baixá-lo. Falha macio no ícone: sem ele o programa funciona igual, e derrubar
o build por enfeite é desproporcional.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from gifc import tools  # noqa: E402 - depende do sys.path montado acima

NOME = "comprime-ai"
SPEC = RAIZ / f"{NOME}.spec"
MODOS = ("onedir", "onefile")

# A spec fixou o número: `onefile` abaixo de 10 segundos de abertura a frio e o
# asset da release é um `.exe` único; acima, é o `onedir` num zip, com a
# instrução de extrair antes de abrir. O limite existe porque o `onefile`
# descompacta ~160 MB em %TEMP% a CADA abertura, sempre, e um leigo que espera
# meio minuto olhando para nada acha que não funcionou.
LIMITE_SEGUNDOS = 10.0

# Quanto esperar pela janela antes de desistir de uma medição.
TETO_DA_MEDICAO = 120.0

FERRAMENTAS = ("ffmpeg", "gifsicle", "oxipng")


class BuildError(Exception):
    """Falha do empacotamento, com mensagem pronta para quem está no terminal."""


# ----------------------------------------------------------------- lugares --


def exe_do_pacote(dist: Path, modo: str) -> Path:
    """O executável produzido, que fica em lugar diferente em cada modo."""
    if modo == "onefile":
        return dist / f"{NOME}.exe"
    if modo == "onedir":
        return dist / NOME / f"{NOME}.exe"
    raise BuildError(f"Modo desconhecido: {modo!r}. Use {' ou '.join(MODOS)}.")


def tamanho(caminho: Path) -> int:
    """Bytes de um arquivo, ou da árvore inteira quando é pasta."""
    if caminho.is_file():
        return caminho.stat().st_size
    return sum(p.stat().st_size for p in caminho.rglob("*") if p.is_file())


def _mb(n: int) -> str:
    return f"{n / 1_048_576:.1f} MB".replace(".", ",")


# ------------------------------------------- recusar com o `.exe` aberto ----


def exe_travado(caminho: Path) -> bool:
    """True quando o arquivo existe e não se pode escrever nele.

    Um `.exe` em execução é mapeado pelo Windows sem compartilhar escrita:
    abri-lo em `r+b` levanta `PermissionError`, e é exatamente a escrita que o
    PyInstaller precisaria fazer para substituí-lo. Perguntar ao sistema se o
    arquivo está gravável responde à pergunta que importa — "este build vai
    conseguir trocar este arquivo?" — sem depender de listar processos.
    """
    if not caminho.exists():
        return False
    try:
        with caminho.open("r+b"):
            return False
    except OSError:
        return True


def recusa_se_aberto(dist: Path, modo: str) -> None:
    """Para o build antes de começar se o pacote anterior está em uso."""
    exe = exe_do_pacote(dist, modo)
    if exe_travado(exe):
        raise BuildError(
            f"O arquivo {exe} está travado — provavelmente o {NOME} está "
            "aberto. Feche o programa e rode de novo.\n"
            "Se ele não estiver aberto, confira se o arquivo não ficou "
            "somente-leitura ou se algum antivírus o está segurando."
        )


# -------------------------------------------------- binários obrigatórios --


def _baixar_de_verdade(nome: str, destino: Path) -> None:
    """Baixa a ferramenta `nome` para a pasta de `destino`.

    O ffmpeg sai do cache em `build/cache-de-pacotes` quando o hash bate —
    sem isso todo build limpo retransferiria 132 MB.
    """
    bin_dir = destino.parent
    if nome == "ffmpeg":
        tools.download_ffmpeg(bin_dir)
    elif nome == "gifsicle":
        tools.download_gifsicle(bin_dir)
    elif nome == "oxipng":
        tools.download_oxipng(bin_dir)
    else:  # pragma: no cover - lista fechada em FERRAMENTAS
        raise BuildError(f"Não sei baixar {nome!r}.")


def garantir_binarios(
    bin_dir: Path,
    baixar: Callable[[str, Path], None] = _baixar_de_verdade,
) -> None:
    """Garante os três binários em `bin_dir`, baixando o que faltar."""
    for nome in FERRAMENTAS:
        destino = bin_dir / f"{nome}.exe"
        if destino.is_file():
            continue
        print(f"[build] baixando {nome}...")
        try:
            baixar(nome, destino)
        except Exception as exc:  # noqa: BLE001 - vira mensagem para o terminal
            raise BuildError(
                f"Não consegui obter o {nome}: {exc}\n"
                f"Ponha o arquivo {destino.name} em {bin_dir} à mão e rode de "
                "novo. As URLs e os hashes de cada ferramenta estão em "
                "gifc/tools.py."
            ) from exc
        if not destino.is_file():
            raise BuildError(
                f"O download do {nome} terminou sem deixar {destino}."
            )


# ------------------------------------------------- estágio e publicação ----


def _apagar(caminho: Path) -> None:
    if caminho.is_dir():
        shutil.rmtree(caminho, ignore_errors=True)
    elif caminho.exists():
        caminho.unlink(missing_ok=True)


def publicar(estagio: Path, destino: Path) -> Path:
    """Põe `estagio` no lugar de `destino`, substituindo-o por inteiro.

    Por inteiro, e não por cópia em cima: sobra do build anterior misturada
    com o novo pacote é defeito que só aparece na máquina de quem baixou.
    """
    if not estagio.exists():
        raise BuildError(f"Nada para publicar: {estagio} não existe.")
    anterior = destino.with_name(destino.name + ".anterior")
    _apagar(anterior)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino.rename(anterior)
    try:
        estagio.rename(destino)
    except OSError:
        # Estágio e destino em volumes diferentes: `rename` não atravessa.
        shutil.move(str(estagio), str(destino))
    _apagar(anterior)
    return destino


def _rodar_pyinstaller(modo: str, distpath: Path, workpath: Path) -> None:
    """Chama o PyInstaller com a spec do projeto, no modo pedido."""
    ambiente = dict(os.environ, COMPRIME_AI_MODO=modo)
    comando = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC),
        "--noconfirm",
        "--distpath",
        str(distpath),
        "--workpath",
        str(workpath),
    ]
    print(f"[build] {modo}: {' '.join(comando[2:])}")
    subprocess.run(comando, check=True, cwd=str(RAIZ), env=ambiente)


def constroi(
    modo: str,
    raiz: Path = RAIZ,
    rodar: Callable[[str, Path, Path], None] = _rodar_pyinstaller,
) -> Path:
    """Constrói no estágio e, só se der certo, publica em `raiz/dist`."""
    if modo not in MODOS:
        raise BuildError(f"Modo desconhecido: {modo!r}. Use {' ou '.join(MODOS)}.")
    estagio = raiz / "build" / f"estagio-{modo}"
    trabalho = raiz / "build" / f"trabalho-{modo}"
    _apagar(estagio)
    rodar(modo, estagio, trabalho)
    if not exe_do_pacote(estagio, modo).is_file():
        raise BuildError(
            f"O PyInstaller terminou sem produzir {exe_do_pacote(estagio, modo)}. "
            f"O pacote em {raiz / 'dist'} ficou como estava."
        )
    return publicar(estagio, raiz / "dist")


# ------------------------------------------------------------- verificação --


def verifica(dist: Path, modo: str) -> tuple[list[str], list[str]]:
    """Confere o pacote em `dist`. Devolve (problemas, avisos).

    Problema é o que impede o programa de funcionar na máquina de quem baixou.
    Aviso é o que degrada sem impedir — o ícone, por exemplo.
    """
    problemas: list[str] = []
    avisos: list[str] = []

    exe = exe_do_pacote(dist, modo)
    if not exe.is_file():
        problemas.append(f"não encontrei o executável {exe}")
        return problemas, avisos

    if modo == "onefile":
        avisos.append(
            "no onefile o conteúdo só existe extraído em tempo de execução: "
            "esta conferência alcança só o executável. Quem olha dentro são "
            "os testes de integração do pacote."
        )
        return problemas, avisos

    interno = exe.parent / "_internal"
    if not interno.is_dir():
        problemas.append(
            f"não encontrei a pasta {interno.name} ao lado do executável — "
            "o pacote está incompleto ou foi aberto de dentro do zip"
        )
        return problemas, avisos

    for nome in FERRAMENTAS:
        if not (interno / f"{nome}.exe").is_file():
            problemas.append(f"falta {nome}.exe em {interno}")

    tkdnd = interno / "tkinterdnd2" / "tkdnd"
    if not tkdnd.is_dir():
        problemas.append(
            f"falta a pasta nativa do tkdnd em {tkdnd} — com ela ausente o app "
            "abre e arrastar arquivo não faz nada, sem erro nenhum"
        )

    if not (interno / "assets" / "icone.ico").is_file():
        avisos.append("sem ícone no pacote (o programa funciona igual)")

    return problemas, avisos


# ----------------------------------------------------------------- medição --


# O bootloader do `onefile` descompacta o pacote em %TEMP% e RELANÇA a si
# mesmo; a janela é do processo FILHO, não do que lançamos. Medido: em 5
# segundos já havia três processos e dois com janela visível, e nenhum deles
# era o PID lançado. Por isso tudo aqui raciocina em família de PIDs, e nunca
# em nome de imagem: se o dono do projeto tiver o app aberto à mão, aquela
# janela tem o mesmo `comprime-ai.exe` e aprovaria a medição errada.


def _processos() -> dict[int, int]:
    """Mapa `pid -> pid do pai` de todos os processos do sistema."""
    if sys.platform != "win32":  # pragma: no cover - o alvo é Windows
        return {}
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    INVALIDO = ctypes.c_void_p(-1).value

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALIDO:
        return {}
    try:
        entrada = PROCESSENTRY32()
        entrada.dwSize = ctypes.sizeof(PROCESSENTRY32)
        mapa: dict[int, int] = {}
        if not k32.Process32First(snap, ctypes.byref(entrada)):
            return {}
        while True:
            mapa[entrada.th32ProcessID] = entrada.th32ParentProcessID
            if not k32.Process32Next(snap, ctypes.byref(entrada)):
                break
        return mapa
    finally:
        k32.CloseHandle(snap)


def _familia(
    pid: int, processos: Callable[[], dict[int, int]] = _processos
) -> set[int]:
    """`pid` e todos os descendentes dele, num só instantâneo.

    Desce em largura e não só um nível: o relançamento do bootloader pode
    acontecer mais de uma vez, e `vistos` também protege do ciclo que o
    instantâneo do Windows traz (o PID 0 aparece como pai de si mesmo).
    """
    filhos: dict[int, list[int]] = {}
    for filho, pai in processos().items():
        filhos.setdefault(pai, []).append(filho)
    vistos = {pid}
    fila = [pid]
    while fila:
        atual = fila.pop()
        for filho in filhos.get(atual, ()):
            if filho not in vistos:
                vistos.add(filho)
                fila.append(filho)
    return vistos


def _tem_janela(pids: set[int]) -> bool:
    """True se algum dos `pids` já tem uma janela de topo visível.

    Medir "até a janela existir" e não "até o processo existir" é o ponto: no
    onefile o processo nasce em milissegundos e passa o resto do tempo
    descompactando, que é justamente o tempo que o usuário espera olhando
    para nada.
    """
    if sys.platform != "win32":  # pragma: no cover - o alvo é Windows
        return False
    import ctypes
    from ctypes import wintypes

    u32 = ctypes.windll.user32
    achou = ctypes.c_int(0)
    CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visita(hwnd: int, _lparam: int) -> bool:
        dono = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(dono))
        if dono.value in pids and u32.IsWindowVisible(hwnd):
            achou.value = 1
            return False
        return True

    u32.EnumWindows(CALLBACK(visita), 0)
    return bool(achou.value)


def _mata_arvore(pid: int) -> None:
    """Encerra o processo e tudo que ele lançou.

    `Popen.kill()` mata só o pai: o filho do onefile sobrevive, a repetição
    seguinte não mede mais abertura a frio, e o `recusa_se_aberto` do build
    seguinte barra o build por um processo que a medição esqueceu. Falha em
    silêncio porque o alvo pode já ter morrido, e aí não há nada a fazer.
    """
    if sys.platform != "win32":  # pragma: no cover - o alvo é Windows
        return
    subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(pid)],
        capture_output=True,
        check=False,
    )


def _lanca(exe: Path) -> subprocess.Popen:
    return subprocess.Popen([str(exe)], cwd=str(exe.parent))


def _abre_uma_vez(
    exe: Path,
    teto: float = TETO_DA_MEDICAO,
    lancar: Callable[[Path], Any] = _lanca,
    familia: Callable[[int], set[int]] = _familia,
    tem_janela: Callable[[set[int]], bool] = _tem_janela,
    matar: Callable[[int], None] = _mata_arvore,
    dormir: Callable[[float], None] = time.sleep,
) -> float:
    """Lança o `.exe`, cronometra até a janela aparecer e encerra a árvore."""
    comeco = time.perf_counter()
    processo = lancar(exe)
    parentes = {processo.pid}
    try:
        while True:
            parentes = familia(processo.pid)
            if tem_janela(parentes):
                return time.perf_counter() - comeco
            # Só é morte de verdade se o pai saiu E nada da árvore ficou:
            # no onefile o pai espera o filho, mas se ele saísse antes, o
            # filho com a janela continuaria valendo.
            if processo.poll() is not None and parentes == {processo.pid}:
                raise BuildError(
                    f"O {exe.name} encerrou com código {processo.returncode} "
                    "antes de abrir janela."
                )
            if time.perf_counter() - comeco > teto:
                raise BuildError(
                    f"O {exe.name} não abriu janela em {teto:.0f} s."
                )
            dormir(0.05)
    finally:
        # A família que matamos é a última vista, e não só o PID lançado: se o
        # pai tiver saído, os órfãos não aparecem mais como descendentes dele
        # e um `taskkill /T` no pai não os alcançaria.
        for alvo in sorted(parentes | {processo.pid}):
            matar(alvo)
        processo.wait(timeout=30)


def mede_abertura(
    exe: Path,
    repeticoes: int = 3,
    uma_vez: Callable[[Path], float] = _abre_uma_vez,
) -> float:
    """O PIOR dos `repeticoes` tempos de abertura, em segundos.

    Pior caso, não média: a média esconde justamente o que decide a regra. O
    usuário leigo vive a abertura mais lenta na primeira vez, que é a única
    chance de ele concluir que o programa não funciona.
    """
    return max(uma_vez(exe) for _ in range(repeticoes))


def pior_caso(tempos: Iterable[float]) -> float:
    return max(tempos)


def decide_modo(segundos_do_onefile: float, limite: float = LIMITE_SEGUNDOS) -> str:
    """Aplica a regra da spec: abaixo do limite, `.exe` único; acima, pasta."""
    return "onefile" if segundos_do_onefile < limite else "onedir"


# --------------------------------------------------------------------- CLI --


def _relatar(dist: Path, modo: str, segundos: float | None) -> None:
    exe = exe_do_pacote(dist, modo)
    alvo = dist / NOME if modo == "onedir" else exe
    print(f"[build] {modo}: {alvo} — {_mb(tamanho(alvo))}")
    if segundos is not None:
        print(f"[build] {modo}: abertura (pior de 3) {segundos:.1f} s".replace(".", ","))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Empacota o comprime-ai.")
    p.add_argument("--modo", choices=(*MODOS, "ambos"), default="onedir")
    p.add_argument(
        "--verifica",
        action="store_true",
        help="confere o pacote que já está em dist/, sem reconstruir",
    )
    p.add_argument("--sem-medir", action="store_true", help="não cronometra a abertura")
    args = p.parse_args(argv)

    dist = RAIZ / "dist"
    modos = list(MODOS) if args.modo == "ambos" else [args.modo]

    try:
        if args.verifica:
            ruim = False
            for modo in modos:
                problemas, avisos = verifica(dist, modo)
                for a in avisos:
                    print(f"[aviso:{modo}] {a}")
                for pr in problemas:
                    print(f"[erro:{modo}] {pr}")
                if problemas:
                    ruim = True
                else:
                    _relatar(dist, modo, None)
            return 1 if ruim else 0

        for modo in modos:
            recusa_se_aberto(dist, modo)
        garantir_binarios(RAIZ / "bin")

        medidos: dict[str, float] = {}
        for modo in modos:
            constroi(modo)
            problemas, avisos = verifica(dist, modo)
            for a in avisos:
                print(f"[aviso:{modo}] {a}")
            if problemas:
                for pr in problemas:
                    print(f"[erro:{modo}] {pr}")
                return 1
            segundos = None
            if not args.sem_medir:
                segundos = mede_abertura(exe_do_pacote(dist, modo))
                medidos[modo] = segundos
            _relatar(dist, modo, segundos)

        if "onefile" in medidos:
            escolhido = decide_modo(medidos["onefile"])
            print(
                f"[build] regra dos {LIMITE_SEGUNDOS:.0f} s: o asset da release "
                f"é o {escolhido}"
            )
    except BuildError as exc:
        print(f"[build] {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"[build] o PyInstaller falhou (código {exc.returncode}).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
