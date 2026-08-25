"""GUI Tkinter: arraste GIFs, defina um alvo, receba os comprimidos.

Disciplina de threading: a compressão roda numa thread separada
(`_trabalhar`). Essa thread NUNCA toca em um widget — ela só faz
`self.fila.put(...)` (queue.Queue é thread-safe) e lê `self.cancelar.is_set`
(threading.Event, também thread-safe). Toda leitura da fila e toda
atualização de widget acontece em `_drenar_fila`, agendada via `after()` na
thread principal.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import BooleanVar, StringVar, messagebox, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

from gifc.compress import CompressResult, compress_file
from gifc.ladder import Toggles
from gifc.search import Attempt, Cancelled
from gifc.tools import (
    BIN_DIR,
    GIFSICLE_URL,
    DownloadError,
    MissingToolError,
    Tools,
    download_gifsicle,
    find_tools,
)

MB = 1024 * 1024
PRESETS = [("Discord 10", 10.0), ("WhatsApp 16", 16.0), ("5", 5.0), ("2", 2.0)]

# A busca é binária sobre a escada de 20 degraus (gifc/ladder.py), então o
# pior caso é floor(log2(20)) + 1 = 5 tentativas por arquivo. Usado só para
# estimar o progresso do lote de forma honesta — não afeta a busca em si.
TENTATIVAS_ESPERADAS_POR_ARQUIVO = 5


def _mb(n: int) -> str:
    """Formata um tamanho em bytes como string de MB com duas casas."""
    return f"{n / MB:.2f} MB"


def _parse_alvo_mb(texto: str) -> int:
    """Converte o texto do campo de alvo (em MB) para um inteiro de bytes.

    Aceita vírgula ou ponto como separador decimal e ignora espaços nas
    pontas. Levanta `ValueError` para texto vazio, não numérico, zero ou
    negativo.
    """
    texto = texto.strip().replace(",", ".")
    if not texto:
        raise ValueError("Informe um número de MB maior que zero.")
    valor = float(texto)
    if valor <= 0:
        raise ValueError("O alvo deve ser maior que zero.")
    return int(valor * MB)


class App(TkinterDnD.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Compressor de GIF")
        self.geometry("580x660")
        self.minsize(480, 560)

        self.tools: Tools | None = None
        self.erro_ferramentas: str | None = None
        self.fila: queue.Queue = queue.Queue()
        self.cancelar = threading.Event()
        self.trabalhando = False
        self.ultima_pasta: Path | None = None
        self._after_id: str | None = None

        # Progresso do lote atual (ver _iniciar_lote / _atualizar_progresso).
        self.total_arquivos = 0
        self.arquivos_concluidos = 0
        self.tentativas_arquivo_atual = 0

        self.alvo_mb = StringVar(value="10")
        self.tg_palette = BooleanVar(value=True)
        self.tg_lossy = BooleanVar(value=True)
        self.tg_scale = BooleanVar(value=True)
        self.tg_fps = BooleanVar(value=True)

        self._montar()
        self.protocol("WM_DELETE_WINDOW", self._ao_fechar)
        self._checar_ferramentas()
        self._after_id = self.after(100, self._drenar_fila)

    # ---------- construção da interface ----------

    def _montar(self) -> None:
        raiz = ttk.Frame(self, padding=12)
        raiz.pack(fill="both", expand=True)

        self.drop = ttk.Label(
            raiz,
            text="Arraste GIFs aqui",
            anchor="center",
            relief="ridge",
            padding=30,
        )
        self.drop.pack(fill="x")
        self.drop.drop_target_register(DND_FILES)
        self.drop.dnd_bind("<<Drop>>", self._ao_soltar)

        ttk.Button(raiz, text="Escolher arquivos...", command=self._escolher).pack(
            fill="x", pady=(6, 12)
        )

        alvo = ttk.LabelFrame(raiz, text="Tamanho-alvo", padding=8)
        alvo.pack(fill="x")
        linha = ttk.Frame(alvo)
        linha.pack(fill="x")
        ttk.Entry(linha, textvariable=self.alvo_mb, width=8).pack(side="left")
        ttk.Label(linha, text="MB").pack(side="left", padx=(4, 12))
        for rotulo, valor in PRESETS:
            ttk.Button(
                linha, text=rotulo, width=11,
                command=lambda v=valor: self.alvo_mb.set(str(v)),
            ).pack(side="left", padx=2)

        alav = ttk.LabelFrame(raiz, text="Alavancas permitidas", padding=8)
        alav.pack(fill="x", pady=12)
        for texto, var in [
            ("Reduzir paleta de cores", self.tg_palette),
            ("Compressão lossy da paleta", self.tg_lossy),
            ("Reduzir resolução", self.tg_scale),
            ("Reduzir framerate", self.tg_fps),
        ]:
            ttk.Checkbutton(alav, text=texto, variable=var).pack(anchor="w")

        self.barra = ttk.Progressbar(raiz, mode="determinate")
        self.barra.pack(fill="x")

        self.status = ttk.Label(raiz, text="Pronto.", anchor="w")
        self.status.pack(fill="x", pady=(6, 4))

        self.log = ttk.Treeview(
            raiz, columns=("msg",), show="tree", height=12, selectmode="none"
        )
        self.log.pack(fill="both", expand=True)

        rodape = ttk.Frame(raiz)
        rodape.pack(fill="x", pady=(8, 0))
        self.btn_cancelar = ttk.Button(
            rodape, text="Cancelar", command=self._cancelar, state="disabled"
        )
        self.btn_cancelar.pack(side="left")
        self.btn_pasta = ttk.Button(
            rodape, text="Abrir pasta", command=self._abrir_pasta, state="disabled"
        )
        self.btn_pasta.pack(side="right")

    # ---------- ferramentas ----------

    def _checar_ferramentas(self) -> None:
        try:
            self.tools = find_tools(BIN_DIR)
        except MissingToolError as exc:
            self.erro_ferramentas = str(exc)
            messagebox.showerror("Ferramenta ausente", str(exc))
            self._log(f"ERRO: {exc}")
            return

        if self.tools.gifsicle is None:
            baixar = messagebox.askyesno(
                "Baixar gifsicle?",
                "O gifsicle faz a compressão lossy, a alavanca mais eficiente.\n\n"
                f"Baixar (~225 KB) de:\n{GIFSICLE_URL}\n\n"
                "Ele fica na pasta bin/ do app. Nada é instalado no sistema.\n\n"
                "Sem ele o app funciona, mas comprime bem menos.",
            )
            if baixar:
                self._baixar_gifsicle()
            else:
                self._log("Modo degradado: sem compressão lossy.")

    def _baixar_gifsicle(self) -> None:
        self.status.config(text="Baixando gifsicle...")
        self.update_idletasks()
        try:
            download_gifsicle(BIN_DIR)
            self.tools = find_tools(BIN_DIR)
            self._log("gifsicle instalado em bin/.")
            self.status.config(text="Pronto.")
        except DownloadError as exc:
            messagebox.showwarning("Download falhou", str(exc))
            self._log(f"Modo degradado: {exc}")
            self.status.config(text="Pronto (modo degradado).")

    # ---------- entrada de arquivos ----------

    def _ao_soltar(self, evento) -> None:
        caminhos = [Path(p) for p in self.splitlist(evento.data)]
        self._processar(caminhos)

    def _escolher(self) -> None:
        from tkinter import filedialog

        nomes = filedialog.askopenfilenames(
            title="Escolha os GIFs", filetypes=[("GIF", "*.gif")]
        )
        self._processar([Path(n) for n in nomes])

    def _processar(self, caminhos: list[Path]) -> None:
        if self.trabalhando:
            messagebox.showinfo("Aguarde", "Já existe uma compressão em andamento.")
            return
        if self.tools is None:
            messagebox.showerror(
                "Sem ferramentas",
                self.erro_ferramentas or "ffmpeg não foi encontrado.",
            )
            return

        gifs = [p for p in caminhos if p.suffix.lower() == ".gif" and p.is_file()]
        ignorados = len(caminhos) - len(gifs)
        if ignorados:
            self._log(f"{ignorados} arquivo(s) ignorado(s): não são GIF.")
        if not gifs:
            messagebox.showwarning("Nada a fazer", "Nenhum GIF válido.")
            return

        try:
            alvo = _parse_alvo_mb(self.alvo_mb.get())
        except ValueError:
            messagebox.showerror(
                "Alvo inválido", "Informe um número de MB maior que zero."
            )
            return

        toggles = Toggles(
            palette=self.tg_palette.get(),
            lossy=self.tg_lossy.get(),
            scale=self.tg_scale.get(),
            fps=self.tg_fps.get(),
        )

        self.trabalhando = True
        self.cancelar.clear()
        self.btn_cancelar.config(state="normal")
        self.log.delete(*self.log.get_children())
        self._iniciar_lote(len(gifs))

        threading.Thread(
            target=self._trabalhar, args=(gifs, alvo, toggles), daemon=True
        ).start()

    # ---------- thread de trabalho ----------
    #
    # Tudo neste método (e em tudo que ele chama) roda fora da thread
    # principal. Não é permitido tocar em nenhum widget aqui — só em
    # `self.fila` (queue.Queue, thread-safe) e `self.cancelar`
    # (threading.Event, thread-safe). `self.tools` é lido, nunca escrito,
    # aqui.

    def _trabalhar(self, gifs: list[Path], alvo: int, toggles: Toggles) -> None:
        assert self.tools is not None
        for i, gif in enumerate(gifs, 1):
            if self.cancelar.is_set():
                break
            self.fila.put(("status", f"[{i}/{len(gifs)}] {gif.name}"))
            try:
                r = compress_file(
                    gif, alvo, toggles, self.tools,
                    on_progress=lambda a, t: self.fila.put(("tentativa", (a, t))),
                    should_cancel=self.cancelar.is_set,
                )
                self.fila.put(("resultado", r))
            except Cancelled:
                self.fila.put(("log", "Cancelado."))
                break
            except Exception as exc:  # falha em um arquivo não derruba o lote
                self.fila.put(("erro_arquivo", f"{gif.name}: {exc}"))
        self.fila.put(("fim", None))

    # ---------- drenagem da fila na thread da UI ----------

    def _drenar_fila(self) -> None:
        """Drena a fila do worker e reagenda a si mesma.

        O reagendamento fica num `finally` de propósito: se um handler
        levantar, o Tk engole o traceback silenciosamente, e sem o `finally`
        o laço morreria para sempre — a janela ficaria congelada no meio de
        um lote, sem log, sem barra e sem forma de recuperar. Cada mensagem
        também é tratada isoladamente, para que uma carga inesperada não
        derrube o laço inteiro.
        """
        try:
            while True:
                try:
                    tipo, carga = self.fila.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._tratar_mensagem(tipo, carga)
                except Exception as exc:  # nunca derruba o laço de drenagem
                    self._log(f"Erro interno ao processar mensagem: {exc}")
        finally:
            self._after_id = self.after(100, self._drenar_fila)

    def _tratar_mensagem(self, tipo: str, carga: object) -> None:
        if tipo == "status":
            self.status.config(text=carga)
        elif tipo == "log":
            self._log(str(carga))
        elif tipo == "erro_arquivo":
            self.arquivos_concluidos += 1
            self.tentativas_arquivo_atual = 0
            self._atualizar_progresso()
            self._log(f"ERRO em {carga}")
        elif tipo == "tentativa":
            self._log_tentativa(*carga)  # type: ignore[misc]
        elif tipo == "resultado":
            self._log_resultado(carga)  # type: ignore[arg-type]
        elif tipo == "fim":
            self._terminar()

    def _log(self, texto: str) -> None:
        item = self.log.insert("", "end", text=texto)
        self.log.see(item)

    def _log_tentativa(self, a: Attempt, total: int) -> None:
        if a.erro is not None:
            mensagem = a.erro if len(a.erro) <= 200 else a.erro[:200] + "..."
            self._log(f"  degrau {a.index}/{total - 1} falhou: {mensagem}")
        else:
            p = a.params
            fps = "orig" if p.fps is None else f"{p.fps:g}fps"
            self._log(
                f"  degrau {a.index}/{total - 1} · {p.colors} cores · "
                f"lossy {p.lossy} · {p.scale_pct}% · {fps} → {_mb(a.size_bytes)}"
            )
        self.tentativas_arquivo_atual += 1
        self._atualizar_progresso()

    # ---------- progresso do lote ----------

    def _iniciar_lote(self, total_arquivos: int) -> None:
        """Zera o progresso no começo de cada lote."""
        self.total_arquivos = total_arquivos
        self.arquivos_concluidos = 0
        self.tentativas_arquivo_atual = 0
        self.barra.config(value=0)

    def _atualizar_progresso(self) -> None:
        """Progresso do lote inteiro, não de um arquivo isolado.

        Conta em unidades de tentativa: cada arquivo custa cerca de
        TENTATIVAS_ESPERADAS_POR_ARQUIVO codificações, então a barra anda de
        forma suave mesmo quando o usuário soltou um único GIF — em vez de
        ficar parada em zero por vinte segundos e pular direto para 100.
        """
        if self.total_arquivos <= 0:
            return
        esperado = self.total_arquivos * TENTATIVAS_ESPERADAS_POR_ARQUIVO
        feito = (
            self.arquivos_concluidos * TENTATIVAS_ESPERADAS_POR_ARQUIVO
            + self.tentativas_arquivo_atual
        )
        self.barra.config(value=min(100.0, 100.0 * feito / esperado))

    def _log_resultado(self, r: CompressResult) -> None:
        self.ultima_pasta = r.dst.parent
        self.arquivos_concluidos += 1
        self.tentativas_arquivo_atual = 0
        self._atualizar_progresso()
        if r.already_optimized:
            self._log(f"{r.src.name}: já estava otimizado, cópia mantida.")
        else:
            self._log(
                f"{r.src.name}: {_mb(r.original_bytes)} → {_mb(r.final_bytes)} "
                f"(−{r.reduction_pct:.0f}%) → {r.dst.name}"
            )
        if not r.hit_target:
            self._log(
                f"  ATENÇÃO: não foi possível chegar ao alvo. "
                f"Menor tamanho possível: {_mb(r.final_bytes)}"
            )

    def _terminar(self) -> None:
        self.trabalhando = False
        self.barra.config(value=0)
        self.btn_cancelar.config(state="disabled")
        self.status.config(text="Pronto.")
        if self.ultima_pasta is not None:
            self.btn_pasta.config(state="normal")

    def _ao_fechar(self) -> None:
        """Fecha a janela sem deixar callback pendente para trás.

        Sinaliza o cancelamento para que um worker em andamento pare no
        próximo checkpoint, cancela o `after` pendente para que a drenagem
        não tente mexer em widget já destruído, e só então destrói.
        """
        self.cancelar.set()
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass  # já disparou ou a janela já está indo embora
            self._after_id = None
        self.destroy()

    def _cancelar(self) -> None:
        self.cancelar.set()
        self.status.config(text="Cancelando...")

    def _abrir_pasta(self) -> None:
        if self.ultima_pasta is None:
            return
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(self.ultima_pasta)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(self.ultima_pasta)])
        else:
            subprocess.Popen(["xdg-open", str(self.ultima_pasta)])
