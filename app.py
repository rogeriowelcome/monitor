import json
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import yfinance as yf
import pandas as pd
import corretora
import corretora_mock
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import os
import sys
import shutil
import socket
import threading
import time
import logging
import traceback
import winreg
import requests
from logging.handlers import RotatingFileHandler
import numpy as np
from datetime import datetime, date, timedelta

from nucleo import *



COR_ALTA = "#2e9e44"
COR_BAIXA = "#e53935"
COR_LINHA = "#2196f3"

TEMAS = {
    "claro": {"bg": "#f0f0f0", "fg": "#000000", "campo": "#ffffff", "botao": "#e1e1e1", "sel": "#0078d7", "grafico": "#ffffff", "grade": "#b0b0b0"},
    "escuro": {"bg": "#1e1e1e", "fg": "#e6e6e6", "campo": "#2d2d2d", "botao": "#3a3a3a", "sel": "#264f78", "grafico": "#252526", "grade": "#555555"},
}


def cor_texto():
    return TEMAS[tema_atual[0]]["fg"]

def na_tela(funcao):
    try:
        janela.after(0, funcao)
    except RuntimeError:
        pass

tarefas_rodando = set()

def em_segundo_plano(tarefa, ao_terminar, botao=None, chave=None):
    if chave:
        if chave in tarefas_rodando:
            return
        tarefas_rodando.add(chave)
    if botao:
        botao.config(state="disabled")

    def rodar():
        try:
            resultado = tarefa()
        except Exception as e:
            log.exception("Erro em tarefa de segundo plano")
            resultado = {"erro": "Erro inesperado: " + str(e)}

        def finalizar():
            tarefas_rodando.discard(chave)
            if botao:
                botao.config(state="normal")
            ao_terminar(resultado)
        na_tela(finalizar)

    threading.Thread(target=rodar, daemon=True).start()

def desenhar_linha(eixo, tela, historico, titulo, operacoes=None):
    eixo.clear()
    if historico is not None:
        eixo.plot(historico.index, historico["Close"], color=COR_LINHA)
        if operacoes:
            idx = historico.index
            for op in operacoes:
                try:
                    d = pd.Timestamp(op["data"])
                    if idx.tz is not None:
                        d = d.tz_localize(idx.tz)
                except Exception:
                    continue
                if d < idx[0] or d > idx[-1]:
                    continue
                cor = COR_ALTA if op["operacao"] == "compra" else COR_BAIXA
                marcador = "^" if op["operacao"] == "compra" else "v"
                eixo.scatter([d], [op["preco"]], marker=marcador, color=cor, s=70, zorder=5)
        eixo.set_title(titulo)
        eixo.set_ylabel("Preco")
        eixo.grid(True)
        eixo.figure.autofmt_xdate()
    else:
        eixo.text(0.5, 0.5, "Sem dados para este periodo", ha="center", va="center", transform=eixo.transAxes)
    tela.draw()

def ler_ticker(campo):
    return campo.get().strip().upper()

def criar_seletor_periodo(pai, variavel, ao_mudar):
    tk.Label(pai, text="Periodo:").pack(side=tk.LEFT, padx=(10, 3))
    seletor = ttk.Combobox(pai, textvariable=variavel, values=list(PERIODOS.keys()), state="readonly", width=10)
    seletor.pack(side=tk.LEFT)
    seletor.bind("<<ComboboxSelected>>", lambda e: ao_mudar())
    return seletor

def notificar(titulo, mensagem):
    def enviar():
        try:
            from plyer import notification
            notification.notify(title=titulo, message=mensagem, app_name="Monitor de Investimentos", timeout=10)
        except Exception:
            log.exception("Falha ao enviar notificacao do Windows")
            na_tela(lambda: messagebox.showinfo(titulo, mensagem))
    threading.Thread(target=enviar, daemon=True).start()

# ---------------- Tema ----------------

tema_atual = ["claro"]

def estilo_graficos(c):
    plt.rcParams.update({
        "figure.facecolor": c["grafico"], "axes.facecolor": c["grafico"], "axes.edgecolor": c["fg"],
        "axes.labelcolor": c["fg"], "text.color": c["fg"], "xtick.color": c["fg"], "ytick.color": c["fg"],
        "grid.color": c["grade"], "legend.facecolor": c["grafico"], "legend.edgecolor": c["grade"],
    })

def pintar_figura(figura, c):
    figura.set_facecolor(c["grafico"])
    for eixo in figura.axes:
        eixo.set_facecolor(c["grafico"])
        eixo.tick_params(colors=c["fg"])
        for borda in eixo.spines.values():
            borda.set_color(c["fg"])
        for texto in [eixo.title, eixo.xaxis.label, eixo.yaxis.label] + list(eixo.texts):
            texto.set_color(c["fg"])
        legenda = eixo.get_legend()
        if legenda:
            legenda.get_frame().set_facecolor(c["grafico"])
            for texto in legenda.get_texts():
                texto.set_color(c["fg"])
    figura.canvas.draw_idle()

def pintar(widget, c):
    tipo = widget.winfo_class()
    try:
        if tipo in ("Frame", "Tk", "Toplevel"):
            widget.configure(bg=c["bg"])
        elif tipo == "Label":
            if str(widget.cget("fg")).lower() not in (COR_ALTA, COR_BAIXA):
                widget.configure(fg=c["fg"])
            widget.configure(bg=c["bg"])
        elif tipo == "Button":
            widget.configure(bg=c["botao"], fg=c["fg"], activebackground=c["sel"], activeforeground=c["fg"])
        elif tipo == "Entry":
            widget.configure(bg=c["campo"], fg=c["fg"], insertbackground=c["fg"])
        elif tipo in ("Radiobutton", "Checkbutton"):
            widget.configure(bg=c["bg"], fg=c["fg"], selectcolor=c["campo"], activebackground=c["bg"], activeforeground=c["fg"])
        elif tipo == "Listbox":
            widget.configure(bg=c["campo"], fg=c["fg"], selectbackground=c["sel"])
        elif tipo == "Canvas":
            widget.configure(bg=c["grafico"])
    except tk.TclError:
        pass
    for filho in widget.winfo_children():
        pintar(filho, c)

def aplicar_tema():
    c = TEMAS[tema_atual[0]]
    estilo = ttk.Style()
    estilo.theme_use("clam")
    estilo.configure(".", background=c["bg"], foreground=c["fg"], fieldbackground=c["campo"])
    estilo.configure("TFrame", background=c["bg"])
    estilo.configure("TNotebook", background=c["bg"], borderwidth=0)
    estilo.configure("TNotebook.Tab", background=c["botao"], foreground=c["fg"], padding=(10, 4))
    estilo.map("TNotebook.Tab", background=[("selected", c["bg"])])
    estilo.configure("Treeview", background=c["campo"], fieldbackground=c["campo"], foreground=c["fg"], rowheight=24)
    estilo.configure("Treeview.Heading", background=c["botao"], foreground=c["fg"])
    estilo.map("Treeview", background=[("selected", c["sel"])])
    estilo.configure("TCombobox", fieldbackground=c["campo"], background=c["botao"], foreground=c["fg"], arrowcolor=c["fg"])
    estilo.map("TCombobox", fieldbackground=[("readonly", c["campo"])], foreground=[("readonly", c["fg"])])
    estilo.configure("Vertical.TScrollbar", background=c["botao"], troughcolor=c["bg"], arrowcolor=c["fg"])
    janela.option_add("*TCombobox*Listbox.background", c["campo"])
    janela.option_add("*TCombobox*Listbox.foreground", c["fg"])
    pintar(janela, c)
    estilo_graficos(c)
    for figura in figuras:
        pintar_figura(figura, c)
    botao_tema.config(text="Tema claro" if tema_atual[0] == "escuro" else "Tema escuro")
    desenhar_resumo()

def alternar_tema():
    tema_atual[0] = "claro" if tema_atual[0] == "escuro" else "escuro"
    salvar_config("tema", tema_atual[0])
    aplicar_tema()

# ---------------- Mercado ----------------

ultimo_mercado_ok = [None]
grafico_mercado_atual = ["^BVSP"]

def carregar_mercado():
    label_mercado_status.config(text="Carregando mercado...", fg=cor_texto())

    def tarefa():
        return [(t, n, cotacao(t)) for t, n in listar_mercado()]

    em_segundo_plano(tarefa, _mostrar_mercado, botao_mercado, chave="mercado")

def _mostrar_mercado(resultados):
    if isinstance(resultados, list) and not resultados:
        tabela_mercado.delete(*tabela_mercado.get_children())
        label_mercado_status.config(text="Sua lista esta vazia. Adicione ativos abaixo.", fg=cor_texto())
        return
    if isinstance(resultados, dict) or all(r[2] is None for r in resultados):
        quando = ultimo_mercado_ok[0]
        texto = "Sem conexao com a internet."
        texto += " Mostrando dados das " + quando + "." if quando else " Tentando de novo em 5 minutos."
        label_mercado_status.config(text=texto, fg=COR_BAIXA)
        log.warning("Mercado: sem conexao")
        return
    tabela_mercado.delete(*tabela_mercado.get_children())
    for ticker, nome, cot in resultados:
        if cot is None:
            tabela_mercado.insert("", tk.END, iid=ticker, values=(nome, ticker, "indisponivel", "-"))
            continue
        tag = "alta" if (cot["variacao"] or 0) >= 0 else "baixa"
        simbolo = simbolo_do_ativo(ticker, cot["moeda"])
        preco_txt = (simbolo + " " if simbolo else "") + formatar_br(cot["preco"])
        tabela_mercado.insert("", tk.END, iid=ticker, values=(nome, ticker, preco_txt, formatar_var(cot["variacao"])), tags=(tag,))
    if tabela_mercado.exists(grafico_mercado_atual[0]):
        tabela_mercado.selection_set(grafico_mercado_atual[0])
    ok = sum(1 for r in resultados if r[2] is not None)
    log.info("Mercado: %d de %d ativos atualizados", ok, len(resultados))
    ultimo_mercado_ok[0] = datetime.now().strftime("%H:%M")
    label_mercado_status.config(text="Atualizado as " + ultimo_mercado_ok[0] + " - atualiza sozinho a cada 5 minutos", fg=cor_texto())

def mostrar_grafico_mercado(ticker):
    grafico_mercado_atual[0] = ticker
    nomes = dict(listar_mercado())
    nome = nomes.get(ticker, ticker)
    periodo = periodo_mercado.get()
    em_segundo_plano(lambda: buscar_historico(ticker, PERIODOS[periodo]),
                     lambda hist: desenhar_linha(ax_mercado, canvas_mercado, hist, nome + " - " + periodo))

def ao_selecionar_mercado(evento):
    selecao = tabela_mercado.selection()
    if selecao and selecao[0] != grafico_mercado_atual[0]:
        mostrar_grafico_mercado(selecao[0])

def adicionar_ao_mercado():
    ticker = ler_ticker(campo_mercado_ticker)
    nome = campo_mercado_nome.get().strip() or ticker
    if not ticker:
        messagebox.showwarning("Aviso", "Digite o codigo do ativo")
        return
    if ticker in dict(listar_mercado()):
        messagebox.showinfo("Aviso", ticker + " ja esta na lista")
        return

    def tarefa():
        if cotacao(ticker) is None:
            return {"erro": motivo_falha(ticker)}
        executar("INSERT INTO mercado_lista (ticker, nome) VALUES (?, ?)", (ticker, nome))
        return {}

    def mostrar(r):
        if "erro" in r:
            messagebox.showerror("Erro", r["erro"])
            return
        campo_mercado_ticker.delete(0, tk.END)
        campo_mercado_nome.delete(0, tk.END)
        carregar_mercado()

    em_segundo_plano(tarefa, mostrar, botao_adicionar_mercado)

def remover_do_mercado():
    selecao = tabela_mercado.selection()
    if not selecao:
        messagebox.showwarning("Aviso", "Selecione um ativo na tabela")
        return
    executar("DELETE FROM mercado_lista WHERE ticker = ?", (selecao[0],))
    tabela_mercado.delete(selecao[0])

def restaurar_mercado_padrao():
    if not messagebox.askyesno("Restaurar lista", "Voltar para a lista original de 12 ativos?"):
        return
    executar("DELETE FROM mercado_lista")
    conexao = conectar()
    try:
        conexao.executemany("INSERT INTO mercado_lista (ticker, nome) VALUES (?, ?)", MERCADO_PADRAO)
        conexao.commit()
    finally:
        conexao.close()
    carregar_mercado()

def agendar_atualizacoes():
    carregar_mercado()
    atualizar_carteira()
    atualizar_renda_fixa()
    carregar_benchmark()
    checar_alertas_automatico()
    janela.after(5 * 60 * 1000, agendar_atualizacoes)

# ---------------- Cotacao ----------------

ultimo_ticker_cotacao = [None]

def atualizar_cotacao():
    ticker = ler_ticker(campo_ticker)
    if not ticker:
        messagebox.showwarning("Aviso", "Digite um ticker")
        return
    label_preco.config(text="Buscando " + ticker + "...")
    periodo = periodo_cotacao.get()

    def tarefa():
        cot = cotacao(ticker)
        if cot is None:
            return {"erro": motivo_falha(ticker)}
        return {"cot": cot, "hist": buscar_historico(ticker, PERIODOS[periodo]),
                "ops": [o for o in listar_operacoes() if o["ticker"] == ticker]}

    def mostrar(r):
        if "erro" in r:
            label_preco.config(text="Preco: -")
            messagebox.showerror("Erro", r["erro"])
            return
        ultimo_ticker_cotacao[0] = ticker
        cot = r["cot"]
        label_preco.config(text=ticker + ": " + formatar_preco(cot["preco"], simbolo_moeda(ticker, cot["moeda"])) + "  (" + formatar_var(cot["variacao"]) + ")")
        desenhar_linha(ax, canvas, r["hist"], ticker + " - " + periodo, r["ops"])
        verificar_alertas(ticker, cot["preco"])

    em_segundo_plano(tarefa, mostrar, botao_cotacao)

def simbolo_moeda(ticker, moeda):
    return "" if ticker.startswith("^") else moeda

def mudar_periodo_cotacao():
    ticker = ultimo_ticker_cotacao[0]
    if not ticker:
        return
    periodo = periodo_cotacao.get()
    em_segundo_plano(lambda: {"hist": buscar_historico(ticker, PERIODOS[periodo]),
                              "ops": [o for o in listar_operacoes() if o["ticker"] == ticker]},
                     lambda r: desenhar_linha(ax, canvas, r["hist"], ticker + " - " + periodo, r["ops"]))

# ---------------- Carteira ----------------

ultimo_resumo = [None]

def registrar_operacao():
    ticker = ler_ticker(campo_ticker_carteira)
    operacao = operacao_var.get()
    tipo = CODIGO_TIPO.get(tipo_var.get(), "acao")
    dia = ler_data(campo_data_operacao.get())
    if not ticker:
        messagebox.showwarning("Aviso", "Digite o codigo do ativo")
        return
    if dia is None:
        messagebox.showwarning("Aviso", "Data invalida. Use o formato DD/MM/AAAA (ex: 15/03/2025)")
        return
    if dia > date.today():
        messagebox.showwarning("Aviso", "A data nao pode ser no futuro")
        return
    try:
        q = float(campo_quantidade.get().strip().replace(",", "."))
        preco = float(campo_preco_operacao.get().strip().replace(",", "."))
    except ValueError:
        messagebox.showwarning("Aviso", "Quantidade e preco devem ser numeros")
        return
    if q <= 0 or preco <= 0:
        messagebox.showwarning("Aviso", "Quantidade e preco devem ser maiores que zero")
        return
    label_carteira_status.config(text="Registrando " + operacao + " de " + ticker + "...", fg=cor_texto())

    def tarefa():
        cot = cotacao(ticker)
        if cot is None:
            return {"erro": motivo_falha(ticker)}
        cambio = cambio_na_data(cot["moeda"], dia.isoformat())
        if cambio is None:
            return {"erro": "Nao consegui o cambio de " + cot["moeda"] + " em " + dia.strftime("%d/%m/%Y") + ". Tente de novo."}
        nova = {"id": None, "ticker": ticker, "tipo_ativo": tipo, "operacao": operacao, "data": dia.isoformat(),
                "quantidade": q, "preco": preco, "moeda": cot["moeda"], "cambio": cambio}
        try:
            calcular_posicoes(ordenar_operacoes(listar_operacoes() + [nova]))
        except ValueError as e:
            return {"erro": str(e)}
        executar("INSERT INTO operacoes (ticker, tipo_ativo, operacao, data, quantidade, preco, moeda, cambio) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (ticker, tipo, operacao, dia.isoformat(), q, preco, cot["moeda"], cambio))
        return {}

    def mostrar(r):
        if "erro" in r:
            label_carteira_status.config(text="")
            messagebox.showerror("Erro", r["erro"])
            return
        for campo in (campo_ticker_carteira, campo_quantidade, campo_preco_operacao):
            campo.delete(0, tk.END)
        atualizar_carteira()

    em_segundo_plano(tarefa, mostrar, botao_registrar)

def atualizar_carteira():
    label_carteira_status.config(text="Atualizando precos...", fg=cor_texto())

    def tarefa():
        operacoes = listar_operacoes()
        for op in operacoes:
            if not op["moeda"]:
                cot = cotacao(op["ticker"])
                if cot:
                    op["moeda"] = cot["moeda"]
                    executar("UPDATE operacoes SET moeda = ? WHERE id = ?", (op["moeda"], op["id"]))
            if op["cambio"] is None and op["moeda"]:
                op["cambio"] = cambio_na_data(op["moeda"], op["data"])
                if op["cambio"] is not None:
                    executar("UPDATE operacoes SET cambio = ? WHERE id = ?", (op["cambio"], op["id"]))
        taxas = {m: taxa_para_real(m) for m in {op["moeda"] or "BRL" for op in operacoes}}
        posicoes = calcular_posicoes(operacoes, {m: t for m, t in taxas.items() if t})
        linhas = []
        for ticker, p in posicoes.items():
            aberta = p["q"] > 0
            cot = cotacao(ticker) if aberta else None
            div = calcular_dividendos(serie_dividendos(ticker), operacoes, ticker) if aberta and p["tipo"] not in SEM_DIVIDENDOS else None
            linhas.append({"ticker": ticker, "p": p, "aberta": aberta, "preco": cot["preco"] if cot else None,
                           "taxa": taxas.get(p["moeda"] or "BRL"), "div": div})
        abertas = [l for l in linhas if l["aberta"]]
        sem_net = bool(abertas) and all(l["preco"] is None for l in abertas) and not tem_internet()
        return {"linhas": linhas, "operacoes": operacoes, "sem_internet": sem_net,
                "sem_cambio": any(op["cambio"] is None for op in operacoes)}

    em_segundo_plano(tarefa, _mostrar_carteira, botao_atualizar_carteira, chave="carteira")

def _mostrar_carteira(r):
    if "erro" in r:
        label_carteira_status.config(text=r["erro"], fg=COR_BAIXA)
        return
    tabela_carteira.delete(*tabela_carteira.get_children())
    total_investido = 0
    total_atual = 0
    total_realizado = 0
    avisos = []
    ativos = {}
    for l in r["linhas"]:
        p = l["p"]
        total_realizado += p["realizado_brl"]
        if not l["aberta"]:
            continue
        taxa = l["taxa"]
        if taxa is None:
            avisos.append("sem cambio para " + p["moeda"])
            taxa = 0
        preco = l["preco"]
        if preco is None:
            avisos.append(l["ticker"] + " sem cotacao")
            preco = p["custo"] / p["q"]
        valor_brl = p["q"] * preco * taxa
        lucro_brl = valor_brl - p["custo_brl"]
        lucro_pct = lucro_brl / p["custo_brl"] * 100 if p["custo_brl"] else 0
        total_investido += p["custo_brl"]
        total_atual += valor_brl
        tabela_carteira.insert("", tk.END, iid=l["ticker"], tags=("lucro" if lucro_brl >= 0 else "prejuizo",), values=(
            l["ticker"], NOME_TIPO.get(p["tipo"], p["tipo"]), formatar_qtd(p["q"]),
            formatar_preco(p["custo"] / p["q"], p["moeda"]),
            formatar_preco(l["preco"], p["moeda"]) if l["preco"] is not None else "sem cotacao",
            formatar_preco(p["custo_brl"], "BRL"),
            formatar_preco(valor_brl, "BRL"),
            formatar_preco(lucro_brl, "BRL"),
            formatar_var(round(lucro_pct, 2)),
        ))
        ativos[l["ticker"]] = {"q": p["q"], "valor_brl": valor_brl, "preco": preco, "moeda": p["moeda"],
                               "taxa": taxa, "div": l["div"], "tipo": p["tipo"]}
    lucro_aberto = total_atual - total_investido
    label_patrimonio.config(text="Patrimonio: " + formatar_preco(total_atual, "BRL"))
    label_lucro.config(text="Lucro em aberto: " + formatar_preco(lucro_aberto, "BRL"), fg=COR_ALTA if lucro_aberto >= 0 else COR_BAIXA)
    label_realizado.config(text="Lucro ja realizado (vendas): " + formatar_preco(total_realizado, "BRL"),
                           fg=COR_ALTA if total_realizado > 0 else COR_BAIXA if total_realizado < 0 else cor_texto())
    if r["sem_cambio"]:
        avisos.append("cambio historico indisponivel para algumas operacoes (usando o de hoje)")
    importadas = sum(1 for op in r["operacoes"] if op["importada"])
    if importadas:
        avisos.append("{} operacao(oes) vieram da carteira antiga com a data de hoje - clique duas vezes nelas no historico para corrigir a data".format(importadas))
    estado_patrimonio["bolsa"] = None
    if r["sem_internet"]:
        label_carteira_status.config(text="Sem conexao com a internet - valores calculados pelo preco medio.", fg=COR_BAIXA)
    elif avisos:
        label_carteira_status.config(text="Atencao: " + ", ".join(avisos), fg=COR_BAIXA)
    else:
        label_carteira_status.config(text="Atualizado as " + datetime.now().strftime("%H:%M") + " - tudo convertido para reais", fg=cor_texto())
        estado_patrimonio["bolsa"] = (total_atual, total_investido)
    mostrar_operacoes(r["operacoes"])
    ultimo_resumo[0] = ativos
    salvar_historico()
    desenhar_resumo()

estado_patrimonio = {"bolsa": None, "rf": None}

def salvar_historico():
    bolsa, rf = estado_patrimonio["bolsa"], estado_patrimonio["rf"]
    if bolsa is None or rf is None:
        return
    valor, investido = bolsa[0] + rf[0], bolsa[1] + rf[1]
    if valor > 0:
        executar("INSERT OR REPLACE INTO historico_patrimonio (data, valor, investido) VALUES (?, ?, ?)",
                 (date.today().isoformat(), valor, investido))

def mostrar_operacoes(operacoes):
    tabela_operacoes.delete(*tabela_operacoes.get_children())
    for op in reversed(operacoes):
        cambio = op["cambio"]
        total = op["quantidade"] * op["preco"] * cambio if cambio is not None else None
        tabela_operacoes.insert("", tk.END, iid=str(op["id"]), tags=("importada" if op["importada"] else op["operacao"],), values=(
            formatar_data(op["data"]) + (" ?" if op["importada"] else ""),
            op["operacao"].capitalize() + (" (importada)" if op["importada"] else ""), op["ticker"], formatar_qtd(op["quantidade"]),
            formatar_preco(op["preco"], op["moeda"] or "BRL"),
            "-" if (op["moeda"] or "BRL") == "BRL" else (formatar_br(cambio) if cambio is not None else "?"),
            formatar_preco(total, "BRL") if total is not None else "?"))

def excluir_operacao():
    selecao = tabela_operacoes.selection()
    if not selecao:
        messagebox.showwarning("Aviso", "Selecione uma operacao no historico")
        return
    id_op = int(selecao[0])
    restantes = [op for op in listar_operacoes() if op["id"] != id_op]
    try:
        calcular_posicoes(restantes)
    except ValueError as e:
        messagebox.showerror("Nao da para excluir", "Sem essa compra, uma venda ficaria maior do que o que voce tinha:\n\n" + str(e) + "\n\nExclua a venda primeiro.")
        return
    valores = tabela_operacoes.item(selecao[0], "values")
    if not messagebox.askyesno("Excluir operacao", "Excluir {} de {} {} em {}?".format(valores[1].lower(), valores[3], valores[2], valores[0])):
        return
    executar("DELETE FROM operacoes WHERE id = ?", (id_op,))
    atualizar_carteira()

def corrigir_data(evento=None):
    selecao = tabela_operacoes.selection()
    if not selecao:
        messagebox.showwarning("Aviso", "Selecione uma operacao no historico")
        return
    id_op = int(selecao[0])
    operacoes = listar_operacoes()
    alvo = next(op for op in operacoes if op["id"] == id_op)
    texto = simpledialog.askstring("Corrigir data", "Data correta da {} de {} {} (DD/MM/AAAA):".format(
        alvo["operacao"], formatar_qtd(alvo["quantidade"]), alvo["ticker"]), initialvalue=formatar_data(alvo["data"]), parent=janela)
    if texto is None:
        return
    dia = ler_data(texto)
    if dia is None or dia > date.today():
        messagebox.showwarning("Aviso", "Data invalida. Use DD/MM/AAAA e uma data que ja passou.")
        return

    def tarefa():
        cambio = cambio_na_data(alvo["moeda"] or "BRL", dia.isoformat())
        if cambio is None:
            return {"erro": "Nao consegui o cambio de " + dia.strftime("%d/%m/%Y") + ". Tente de novo."}
        corrigida = dict(alvo, data=dia.isoformat(), cambio=cambio)
        try:
            calcular_posicoes(ordenar_operacoes([corrigida if op["id"] == id_op else op for op in operacoes]))
        except ValueError as e:
            return {"erro": str(e)}
        executar("UPDATE operacoes SET data = ?, cambio = ?, importada = 0 WHERE id = ?", (dia.isoformat(), cambio, id_op))
        return {}

    def mostrar(r):
        if "erro" in r:
            messagebox.showerror("Erro", r["erro"])
            return
        atualizar_carteira()

    em_segundo_plano(tarefa, mostrar)

def ao_selecionar_posicao(evento):
    selecao = tabela_carteira.selection()
    if selecao:
        campo_ticker_carteira.delete(0, tk.END)
        campo_ticker_carteira.insert(0, selecao[0])
        ativo = (ultimo_resumo[0] or {}).get(selecao[0])
        if ativo:
            tipo_var.set(NOME_TIPO.get(ativo["tipo"], "Acao"))

def sugerir_tipo_no_formulario(evento=None):
    ticker = ler_ticker(campo_ticker_carteira)
    if ticker:
        tipo_var.set(NOME_TIPO[sugerir_tipo(ticker)])

# ---------------- Renda fixa ----------------

ultimo_rf = [None]

def formatar_taxa(valor):
    return formatar_br(valor).rstrip("0").rstrip(",")

def formatar_rentabilidade(indexador, taxa):
    if indexador == "pre":
        return formatar_taxa(taxa) + "% ao ano"
    if indexador == "cdi":
        return formatar_taxa(taxa) + "% do CDI"
    if indexador == "selic":
        return formatar_taxa(taxa) + "% da Selic"
    if indexador == "ipca":
        return "IPCA + " + formatar_taxa(taxa) + "%"
    return "Poupanca"

def ao_mudar_produto(evento=None):
    opcoes = [INDEXADORES[i] for i in PRODUTOS_RF[produto_var.get()]["indexadores"]]
    combo_indexador.config(values=opcoes)
    if indexador_var.get() not in opcoes:
        indexador_var.set(opcoes[0])
    ao_mudar_indexador()

def ao_mudar_indexador(evento=None):
    indexador = CODIGO_INDEXADOR[indexador_var.get()]
    campo_taxa_rf.config(state="normal")
    if indexador == "poupanca":
        campo_taxa_rf.delete(0, tk.END)
        campo_taxa_rf.config(state="disabled")
        label_taxa_rf.config(text="Taxa: automatica")
        return
    label_taxa_rf.config(text={"pre": "Taxa (% ao ano):", "cdi": "Quantos % do CDI:", "selic": "Quantos % da Selic:",
                               "ipca": "IPCA + quantos % ao ano:"}[indexador])
    if indexador == "selic" and not campo_taxa_rf.get():
        campo_taxa_rf.insert(0, "100")

rf_editando = [None]

def limpar_formulario_rf():
    for campo in (campo_nome_rf, campo_valor_rf, campo_venc_rf, campo_taxa_rf):
        campo.config(state="normal")
        campo.delete(0, tk.END)
    campo_data_rf.delete(0, tk.END)
    campo_data_rf.insert(0, date.today().strftime("%d/%m/%Y"))

def cancelar_edicao_rf():
    rf_editando[0] = None
    botao_salvar_rf.config(text="Adicionar aplicacao")
    botao_cancelar_rf.pack_forget()
    limpar_formulario_rf()
    label_rf_status.config(text="", fg=cor_texto())

def carregar_rf_para_editar(evento=None):
    selecao = tabela_rf.selection()
    if not selecao:
        return
    alvo = next((a for a in (ultimo_rf[0] or []) if a["id"] == int(selecao[0])), None)
    if not alvo:
        return
    limpar_formulario_rf()
    rf_editando[0] = alvo["id"]
    produto_var.set(alvo["produto"])
    ao_mudar_produto()
    indexador_var.set(INDEXADORES[alvo["indexador"]])
    ao_mudar_indexador()
    campo_nome_rf.insert(0, alvo["nome"])
    if alvo["indexador"] != "poupanca":
        campo_taxa_rf.insert(0, formatar_taxa(alvo["taxa"]))
    campo_valor_rf.insert(0, formatar_br(alvo["valor"]))
    campo_data_rf.delete(0, tk.END)
    campo_data_rf.insert(0, formatar_data(alvo["data"]))
    if alvo["vencimento"]:
        campo_venc_rf.insert(0, formatar_data(alvo["vencimento"]))
    botao_salvar_rf.config(text="Salvar alteracao")
    botao_cancelar_rf.pack(side=tk.LEFT, padx=5)
    label_rf_status.config(text="Editando '{}' - ajuste os campos e clique em Salvar alteracao.".format(alvo["nome"]), fg=cor_texto())

def salvar_renda_fixa():
    produto = produto_var.get()
    indexador = CODIGO_INDEXADOR[indexador_var.get()]
    nome = campo_nome_rf.get().strip() or produto
    dia = ler_data(campo_data_rf.get())
    texto_vencimento = campo_venc_rf.get().strip()
    vencimento = ler_data(texto_vencimento) if texto_vencimento else None
    try:
        valor = ler_valor_em_reais(campo_valor_rf.get())
        taxa = float(campo_taxa_rf.get().strip().replace(",", ".")) if indexador != "poupanca" else 0.0
    except ValueError:
        messagebox.showwarning("Aviso", "Valor e taxa devem ser numeros (ex: valor 10.000,00 e taxa 110)")
        return
    if valor <= 0 or (indexador != "poupanca" and taxa <= 0):
        messagebox.showwarning("Aviso", "Valor e taxa devem ser maiores que zero")
        return
    if dia is None or dia > date.today():
        messagebox.showwarning("Aviso", "Data da aplicacao invalida. Use DD/MM/AAAA e uma data que ja passou.")
        return
    if texto_vencimento and (vencimento is None or vencimento <= dia):
        messagebox.showwarning("Aviso", "Vencimento invalido. Use DD/MM/AAAA, depois da data da aplicacao (ou deixe em branco).")
        return
    if rf_editando[0] is not None:
        executar("UPDATE renda_fixa SET nome = ?, produto = ?, indexador = ?, taxa = ?, valor = ?, data = ?, vencimento = ? WHERE id = ?",
                 (nome, produto, indexador, taxa, valor, dia.isoformat(), vencimento.isoformat() if vencimento else None, rf_editando[0]))
        cancelar_edicao_rf()
    else:
        executar("INSERT INTO renda_fixa (nome, produto, indexador, taxa, valor, data, vencimento) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (nome, produto, indexador, taxa, valor, dia.isoformat(), vencimento.isoformat() if vencimento else None))
        limpar_formulario_rf()
    atualizar_renda_fixa()

def atualizar_renda_fixa():
    label_rf_status.config(text="Calculando rendimentos com os indices do Banco Central...", fg=cor_texto())

    def tarefa():
        aplicacoes = listar_renda_fixa()
        if not aplicacoes:
            return {"aplicacoes": [], "ok": True, "ultimo_indice": None}
        inicio = date.fromisoformat(min(a["data"] for a in aplicacoes))
        indices = {}
        ok = True
        for nome in {"cdi"} | {a["indexador"] for a in aplicacoes if a["indexador"] != "pre"}:
            indices[nome], sucesso = obter_indices(nome, inicio)
            ok = ok and sucesso
        hoje = date.today().isoformat()
        for a in aplicacoes:
            a["resultado"] = calcular_renda_fixa(a, indices, hoje)
        return {"aplicacoes": aplicacoes, "ok": ok, "ultimo_indice": indices["cdi"][-1][0] if indices.get("cdi") else None}

    em_segundo_plano(tarefa, _mostrar_renda_fixa, botao_atualizar_rf, chave="renda_fixa")

def _mostrar_renda_fixa(r):
    if "erro" in r:
        label_rf_status.config(text=r["erro"], fg=COR_BAIXA)
        return
    tabela_rf.delete(*tabela_rf.get_children())
    aplicado = bruto = liquido = 0
    for a in r["aplicacoes"]:
        res = a["resultado"]
        aplicado += a["valor"]
        bruto += res["bruto"]
        liquido += res["liquido"]
        impostos = []
        if res["iof"]:
            impostos.append("IOF " + formatar_preco(res["iof"], "BRL"))
        if res["imposto"]:
            impostos.append("IR " + formatar_preco(res["imposto"], "BRL") + " (" + formatar_taxa(res["aliquota"]) + "%)")
        imposto = " + ".join(impostos) if impostos else ("isento" if PRODUTOS_RF[a["produto"]]["isento"] else "-")
        vencimento = (formatar_data(a["vencimento"]) + (" vencido" if res["vencido"] else "")) if a["vencimento"] else "-"
        tabela_rf.insert("", tk.END, iid=str(a["id"]), tags=("vencido",) if res["vencido"] else (), values=(
            a["nome"], a["produto"], formatar_rentabilidade(a["indexador"], a["taxa"]), formatar_data(a["data"]),
            formatar_preco(a["valor"], "BRL"), formatar_preco(res["bruto"], "BRL"), formatar_preco(res["rendimento"], "BRL"),
            imposto, formatar_preco(res["liquido"], "BRL"), vencimento))
    label_rf_totais.config(text="Aplicado: {}     Valor bruto: {}     Valor liquido (apos IR): {}".format(
        formatar_preco(aplicado, "BRL"), formatar_preco(bruto, "BRL"), formatar_preco(liquido, "BRL")))
    if r["ok"]:
        estado_patrimonio["rf"] = (bruto, aplicado)
        texto = "Atualizado as " + datetime.now().strftime("%H:%M")
        if r["ultimo_indice"]:
            texto += " - indices do Banco Central ate " + formatar_data(r["ultimo_indice"])
        label_rf_status.config(text=texto, fg=cor_texto())
    else:
        estado_patrimonio["rf"] = None
        label_rf_status.config(text="Nao consegui falar com o Banco Central. Valores calculados com os indices ja salvos (podem estar desatualizados).", fg=COR_BAIXA)
    ultimo_rf[0] = r["aplicacoes"]
    checar_vencimentos(r["aplicacoes"])
    salvar_historico()
    desenhar_resumo()

def checar_vencimentos(aplicacoes):
    """Notifica uma vez por aplicacao quando o vencimento esta a 7 dias ou ja passou."""
    try:
        avisados = json.loads(ler_config("lembretes_rf", "{}") or "{}")
    except (TypeError, ValueError):
        avisados = {}
    hoje = date.today()
    mudou = False
    for a in aplicacoes:
        if not a["vencimento"]:
            continue
        dias = (date.fromisoformat(a["vencimento"]) - hoje).days
        if dias > 7:
            continue
        chave = "{}:{}".format(a["id"], a["vencimento"])
        if chave in avisados:
            continue
        res = a["resultado"]
        liquido = formatar_preco(res["liquido"], "BRL")
        if dias <= 0:
            mensagem = "{} venceu em {}. Valor liquido estimado no vencimento: {}".format(
                a["nome"], formatar_data(a["vencimento"]), liquido)
        else:
            mensagem = "{} vence em {} dia(s) ({}). Liquido estimado: {}".format(
                a["nome"], dias, formatar_data(a["vencimento"]), liquido)
        notificar("Renda fixa vencendo", mensagem)
        avisados[chave] = hoje.isoformat()
        mudou = True
    if mudou:
        salvar_config("lembretes_rf", json.dumps(avisados))

def excluir_renda_fixa():
    selecao = tabela_rf.selection()
    if not selecao:
        messagebox.showwarning("Aviso", "Selecione uma aplicacao na tabela")
        return
    valores = tabela_rf.item(selecao[0], "values")
    if not messagebox.askyesno("Excluir aplicacao", "Excluir '{}' ({} aplicados em {})?\nUse isto quando voce resgatar a aplicacao.".format(valores[0], valores[4], valores[3])):
        return
    executar("DELETE FROM renda_fixa WHERE id = ?", (int(selecao[0]),))
    atualizar_renda_fixa()

benchmark_cache = {"cdi": None, "ibov": None, "dia_ibov": None}

def carregar_benchmark():
    def tarefa():
        linha = consultar("SELECT MIN(data) FROM historico_patrimonio")
        inicio = date.fromisoformat(linha[0][0]) - timedelta(days=10) if linha and linha[0][0] else date.today() - timedelta(days=30)
        cdi, ok = obter_indices("cdi", inicio)
        ibov = benchmark_cache["ibov"]
        if benchmark_cache["dia_ibov"] != date.today():
            try:
                hist = yf.Ticker("^BVSP").history(start=inicio.isoformat())
                if len(hist):
                    ibov = [(d.date().isoformat(), float(v)) for d, v in hist["Close"].items()]
            except Exception:
                log.warning("Nao consegui baixar o historico do Ibovespa")
        return {"cdi": cdi if ok and cdi else benchmark_cache["cdi"], "ibov": ibov}

    def mostrar(r):
        if "erro" in r:
            return
        benchmark_cache["cdi"] = r["cdi"]
        benchmark_cache["ibov"] = r["ibov"]
        benchmark_cache["dia_ibov"] = date.today()
        desenhar_resumo()

    em_segundo_plano(tarefa, mostrar, chave="benchmark")


# ---------------- Resumo (pizza, historico e dividendos) ----------------

def desenhar_resumo():
    ativos = ultimo_resumo[0] or {}
    aplicacoes = ultimo_rf[0] or []
    por_categoria = {}
    for a in ativos.values():
        nome = NOME_TIPO.get(a["tipo"], a["tipo"])
        por_categoria[nome] = por_categoria.get(nome, 0) + a["valor_brl"]
    for ap in aplicacoes:
        grupo = PRODUTOS_RF[ap["produto"]]["grupo"]
        por_categoria[grupo] = por_categoria.get(grupo, 0) + ap["resultado"]["bruto"]
    total = sum(por_categoria.values())
    total_bolsa = sum(a["valor_brl"] for a in ativos.values())
    total_rf = sum(ap["resultado"]["bruto"] for ap in aplicacoes)
    label_patrimonio_total.config(text="Patrimonio total: {}   (bolsa e cripto: {}  |  renda fixa: {})".format(
        formatar_preco(total, "BRL"), formatar_preco(total_bolsa, "BRL"), formatar_preco(total_rf, "BRL")))

    ax_pizza.clear()
    fatias = sorted([(n, v) for n, v in por_categoria.items() if v > 0], key=lambda x: -x[1])
    if fatias:
        fatias_desenhadas, _, _ = ax_pizza.pie([v for n, v in fatias], autopct=lambda pct: "%1.0f%%" % pct if pct >= 5 else "",
                                              startangle=90, counterclock=False, pctdistance=0.72)
        legendas = ["{} {}%".format(n, formatar_taxa(round(v / total * 100, 1))) for n, v in fatias]
        ax_pizza.legend(fatias_desenhadas, legendas, loc="center left", bbox_to_anchor=(0.95, 0.5), fontsize=8, frameon=False)
        ax_pizza.set_title("Distribuicao por tipo (em R$)")
    else:
        ax_pizza.text(0.5, 0.5, "Carteira vazia", ha="center", va="center", transform=ax_pizza.transAxes)
        ax_pizza.axis("off")

    ax_hist.clear()
    registros = consultar("SELECT data, valor, investido FROM historico_patrimonio ORDER BY data")
    if registros:
        datas = [datetime.strptime(d, "%Y-%m-%d") for d, v, i in registros]
        marcador = "o" if len(registros) < 30 else None
        base_i = registros[0][2]
        ax_hist.plot(datas, [(v / i - 1) * 100 if i else 0 for d, v, i in registros],
                     color=COR_LINHA, marker=marcador, label="Carteira (valor / investido)")
        ax_hist.plot(datas, [(i / base_i - 1) * 100 if base_i else 0 for d, v, i in registros],
                     color="gray", linestyle="--", marker=marcador, label="Aportes")
        cdi_pct, ibov_pct = rentabilidades_benchmark(registros, benchmark_cache["cdi"], benchmark_cache["ibov"])
        if cdi_pct:
            ax_hist.plot(datas, cdi_pct, color="#e08e00", linestyle="-.", marker=marcador, label="CDI")
        if ibov_pct:
            ax_hist.plot(datas, ibov_pct, color="#7b1fa2", linestyle=":", marker=marcador, label="Ibovespa")
        ax_hist.axhline(0, color="gray", linewidth=0.5)
        ax_hist.legend(fontsize=8)
        ax_hist.grid(True)
        ax_hist.set_title("Rentabilidade desde o primeiro registro (%)")
        if len(datas) == 1:
            ax_hist.set_xlim(datas[0] - timedelta(days=3), datas[0] + timedelta(days=3))
        for rotulo in ax_hist.get_xticklabels():
            rotulo.set_rotation(30)
            rotulo.set_ha("right")
    else:
        ax_hist.text(0.5, 0.5, "O historico comeca a ser salvo hoje", ha="center", va="center", transform=ax_hist.transAxes)
    fig_resumo.tight_layout()
    canvas_resumo.draw()

    tabela_dividendos.delete(*tabela_dividendos.get_children())
    total_div = 0
    for ticker, a in ativos.items():
        if a["tipo"] in SEM_DIVIDENDOS:
            continue
        div = a["div"]
        if div is None:
            tabela_dividendos.insert("", tk.END, values=(ticker, formatar_qtd(a["q"]), "indisponivel", "-", "-"))
            continue
        recebido = div["recebido"] * a["taxa"]
        total_div += recebido
        dy = div["por_acao"] / a["preco"] * 100 if a["preco"] else 0
        tabela_dividendos.insert("", tk.END, values=(
            ticker, formatar_qtd(a["q"]), formatar_preco(div["por_acao"], a["moeda"]),
            formatar_preco(recebido, "BRL"), formatar_br(dy) + "%"))
    label_total_dividendos.config(text="Voce recebeu nos ultimos 12 meses: " + formatar_preco(total_div, "BRL"))

    meta = float(ler_config("meta_patrimonio", "0") or 0)
    if meta > 0:
        pct = min(total / meta * 100, 100)
        barra_meta["value"] = pct
        label_meta.config(text="Meta: {} - {}% atingido (faltam {})".format(
            formatar_preco(meta, "BRL"), formatar_br(round(pct, 1)),
            formatar_preco(max(meta - total, 0), "BRL")))
    else:
        barra_meta["value"] = 0
        label_meta.config(text="Sem meta definida")

    metas = metas_distribuicao()
    combo_reb.config(values=sorted(set(list(por_categoria) + list(metas))))
    tabela_reb.delete(*tabela_reb.get_children())
    for nome in sorted(set(list(por_categoria) + list(metas))):
        atual_pct = por_categoria.get(nome, 0) / total * 100 if total else 0
        meta_pct = metas.get(nome)
        if meta_pct is None:
            dif = "-"
        else:
            dif_brl = (meta_pct - atual_pct) / 100 * total
            if abs(dif_brl) < 1:
                dif = "equilibrado"
            else:
                dif = ("comprar ~" if dif_brl > 0 else "vender ~") + formatar_preco(abs(dif_brl), "BRL")
        tabela_reb.insert("", tk.END, values=(nome, formatar_br(round(atual_pct, 1)) + "%",
                                            "-" if meta_pct is None else formatar_br(meta_pct) + "%", dif))
    atualizar_irpf()


def definir_meta_grupo():
    grupo = combo_reb.get()
    if not grupo:
        messagebox.showwarning("Aviso", "Escolha um tipo de ativo")
        return
    try:
        pct = float(campo_reb_pct.get().strip().replace(",", "."))
    except ValueError:
        messagebox.showwarning("Aviso", "Percentual invalido (ex: 30)")
        return
    metas = metas_distribuicao()
    if pct <= 0:
        metas.pop(grupo, None)
    else:
        metas[grupo] = pct
    salvar_config("meta_distribuicao", json.dumps(metas))
    campo_reb_pct.delete(0, tk.END)
    desenhar_resumo()

def definir_meta():
    texto = meta_var.get().strip()
    if not texto:
        salvar_config("meta_patrimonio", "0")
        desenhar_resumo()
        return
    try:
        meta = ler_valor_em_reais(texto)
    except ValueError:
        messagebox.showwarning("Aviso", "Meta invalida (ex: 100.000,00)")
        return
    if meta <= 0:
        messagebox.showwarning("Aviso", "A meta deve ser maior que zero")
        return
    salvar_config("meta_patrimonio", str(meta))
    desenhar_resumo()

def atualizar_irpf():
    tabela_irpf.delete(*tabela_irpf.get_children())
    total = 0.0
    for m in apuracao_mensal(listar_operacoes()):
        total += m["ir_estimado"]
        for d in m["detalhe"]:
            tabela_irpf.insert("", tk.END, values=(
                m["mes"], NOME_TIPO.get(d["tipo"], d["tipo"]), formatar_preco(d["vendido"], "BRL"),
                formatar_preco(d["lucro"], "BRL"), d["regra"], formatar_preco(d["imposto"], "BRL")))
    label_irpf_total.config(text="IR estimado acumulado: " + formatar_preco(total, "BRL"))

def exportar_excel():
    caminho = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Planilha Excel", "*.xlsx")],
                                         initialfile="minha_carteira.xlsx", title="Salvar planilha")
    if not caminho:
        return
    try:
        posicoes = calcular_posicoes(listar_operacoes())
        ativos = ultimo_resumo[0] or {}
        carteira = [{"Ativo": t, "Tipo": NOME_TIPO.get(p["tipo"], p["tipo"]), "Quantidade": p["q"],
                     "Preco medio": round(p["custo"] / p["q"], 4), "Moeda": p["moeda"] or "BRL",
                     "Preco atual": ativos.get(t, {}).get("preco"),
                     "Investido (R$)": round(p["custo_brl"], 2),
                     "Valor hoje (R$)": round(ativos.get(t, {}).get("valor_brl") or 0, 2),
                     "Lucro realizado (R$)": round(p["realizado_brl"], 2)}
                    for t, p in sorted(posicoes.items()) if p["q"] > 0]
        operacoes = [{"Data": formatar_data(o["data"]), "Operacao": o["operacao"], "Ativo": o["ticker"],
                      "Tipo": NOME_TIPO.get(o["tipo_ativo"], o["tipo_ativo"]), "Quantidade": o["quantidade"],
                      "Preco": o["preco"], "Moeda": o["moeda"] or "BRL",
                      "Cambio do dia": o["cambio"], "Importada": "sim" if o["importada"] else "nao"}
                     for o in listar_operacoes()]
        fixa = [{"Nome": a["nome"], "Produto": a["produto"],
                 "Rentabilidade": formatar_rentabilidade(a["indexador"], a["taxa"]),
                 "Aplicado em": formatar_data(a["data"]), "Valor aplicado": a["valor"],
                 "Valor bruto": round(a["resultado"]["bruto"], 2),
                 "Rendimento": round(a["resultado"]["rendimento"], 2),
                 "IOF": round(a["resultado"]["iof"], 2),
                 "IR": round(a["resultado"]["imposto"], 2),
                 "Valor liquido": round(a["resultado"]["liquido"], 2),
                 "Vencimento": formatar_data(a["vencimento"]) if a["vencimento"] else ""}
                for a in ultimo_rf[0] or []]
        with pd.ExcelWriter(caminho) as planilha:
            pd.DataFrame(carteira).to_excel(planilha, sheet_name="Carteira", index=False)
            pd.DataFrame(operacoes).to_excel(planilha, sheet_name="Operacoes", index=False)
            pd.DataFrame(fixa).to_excel(planilha, sheet_name="Renda fixa", index=False)
        messagebox.showinfo("Exportado", "Planilha salva em:\n" + caminho)
    except Exception as e:
        log.exception("Erro ao exportar planilha")
        messagebox.showerror("Erro ao exportar", "Nao consegui salvar a planilha:\n" + str(e))

def exportar_pdf():
    caminho = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
                                         initialfile="relatorio_investimentos.pdf", title="Salvar relatorio")
    if not caminho:
        return
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        estilos = getSampleStyleSheet()
        partes = [Paragraph("Monitor de Investimentos - Relatorio", estilos["Title"]),
                  Paragraph("Gerado em " + datetime.now().strftime("%d/%m/%Y %H:%M"), estilos["Normal"]),
                  Spacer(1, 12)]

        def tabela(titulo, cabecalho, linhas):
            partes.append(Paragraph(titulo, estilos["Heading2"]))
            if not linhas:
                partes.append(Paragraph("(vazio)", estilos["Normal"]))
            else:
                t = Table([cabecalho] + linhas, repeatRows=1)
                t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dddddd")),
                                       ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                       ("FONTSIZE", (0, 0), (-1, -1), 8)]))
                partes.append(t)
            partes.append(Spacer(1, 12))

        ativos = ultimo_resumo[0] or {}
        posicoes = calcular_posicoes(listar_operacoes())
        aplicacoes = ultimo_rf[0] or []
        total = sum(a["valor_brl"] for a in ativos.values()) + sum(a["resultado"]["bruto"] for a in aplicacoes)
        partes.append(Paragraph("Patrimonio total: " + formatar_preco(total, "BRL"), estilos["Heading3"]))

        tabela("Carteira", ["Ativo", "Tipo", "Qtd", "Preco medio", "Preco atual", "Valor (R$)", "Lucro (R$)"],
               [[t, NOME_TIPO.get(p["tipo"], p["tipo"]), formatar_qtd(p["q"]),
                 formatar_preco(p["custo"] / p["q"], p["moeda"] or "BRL") if p["q"] else "-",
                 formatar_preco(ativos.get(t, {}).get("preco"), p["moeda"] or "BRL") if ativos.get(t, {}).get("preco") else "-",
                 formatar_preco(ativos.get(t, {}).get("valor_brl") or 0, "BRL"),
                 formatar_preco((ativos.get(t, {}).get("valor_brl") or 0) - p["custo_brl"], "BRL")]
                for t, p in sorted(posicoes.items()) if p["q"] > 0])

        tabela("Renda fixa", ["Nome", "Produto", "Rentabilidade", "Aplicado", "Bruto", "Impostos", "Liquido"],
               [[a["nome"], a["produto"], formatar_rentabilidade(a["indexador"], a["taxa"]),
                 formatar_preco(a["valor"], "BRL"), formatar_preco(a["resultado"]["bruto"], "BRL"),
                 formatar_preco(a["resultado"]["iof"] + a["resultado"]["imposto"], "BRL"),
                 formatar_preco(a["resultado"]["liquido"], "BRL")]
                for a in aplicacoes])

        tabela("Dividendos (12 meses)", ["Ativo", "Recebido (R$)"],
               [[t, formatar_preco(a["div"]["recebido"] * a["taxa"], "BRL")]
                for t, a in ativos.items() if a.get("div") and a["div"]["recebido"]])

        linhas_ir = [[m["mes"], NOME_TIPO.get(d["tipo"], d["tipo"]), formatar_preco(d["vendido"], "BRL"),
                      formatar_preco(d["lucro"], "BRL"), d["regra"], formatar_preco(d["imposto"], "BRL")]
                     for m in apuracao_mensal(listar_operacoes()) for d in m["detalhe"]]
        tabela("IR estimado por mes (vendas)", ["Mes", "Tipo", "Vendido", "Lucro", "Regra", "IR"], linhas_ir)

        partes.append(Paragraph(
            "Estimativas: renda fixa pelos indices oficiais do Banco Central; IR por regras "
            "simplificadas (nao considera day trade, prejuizo acumulado para compensar nem o "
            "carne-leao real). Confirme com sua contabilidade.", estilos["Italic"]))
        SimpleDocTemplate(caminho, pagesize=A4, title="Relatorio de investimentos").build(partes)
        messagebox.showinfo("Exportado", "Relatorio salvo em:\n" + caminho)
    except Exception as e:
        log.exception("Erro ao exportar PDF")
        messagebox.showerror("Erro ao exportar", "Nao consegui salvar o PDF:\n" + str(e))

def backup_banco():
    destino = filedialog.asksaveasfilename(defaultextension=".db", filetypes=[("Banco SQLite", "*.db")],
                                         initialfile="backup_investimentos.db", title="Salvar backup")
    if not destino:
        return
    try:
        origem = conectar()
        try:
            copia = sqlite3.connect(destino)
            try:
                origem.backup(copia)
            finally:
                copia.close()
        finally:
            origem.close()
        messagebox.showinfo("Backup", "Copia do banco salva em:\n" + destino)
    except Exception as e:
        log.exception("Erro no backup")
        messagebox.showerror("Erro", "Falha no backup:\n" + str(e))

# ---------------- Corretora (testnet Binance - dinheiro de mentira) ----------------

def credenciais_binance():
    chave = ler_config("binance_key", "")
    segredo = ler_config("binance_secret", "")
    return chave, segredo

def broker_backend():
    return corretora_mock if ler_config("broker_modo", "local") == "local" else corretora

def mudar_modo_broker():
    salvar_config("broker_modo", broker_modo.get())
    atualizar_broker()

def salvar_credenciais_binance():
    chave = campo_binance_key.get().strip()
    segredo = campo_binance_secret.get().strip()
    if not chave or not segredo:
        messagebox.showwarning("Aviso", "Cole a API key e o secret da testnet (testnet.binance.vision)")
        return
    salvar_config("binance_key", chave)
    salvar_config("binance_secret", segredo)
    campo_binance_key.delete(0, tk.END)
    campo_binance_secret.delete(0, tk.END)
    label_broker_status.config(text="Credenciais salvas. Clique em Atualizar.", fg=cor_texto())
    atualizar_broker()

def esquecer_credenciais_binance():
    salvar_config("binance_key", "")
    salvar_config("binance_secret", "")
    tabela_saldos.delete(*tabela_saldos.get_children())
    tabela_ordens.delete(*tabela_ordens.get_children())
    label_broker_status.config(text="Credenciais removidas.", fg=cor_texto())

def atualizar_broker():
    backend = broker_backend()
    chave, segredo = credenciais_binance()
    if backend is corretora and (not chave or not segredo):
        label_broker_status.config(text="Sem credenciais: crie conta gratis em testnet.binancefuture.com e cole a API key acima - ou use o modo Simulado.", fg=cor_texto())
        return
    label_broker_status.config(text="Consultando a corretora...", fg=cor_texto())
    symbol_hist = campo_ordem_symbol.get().strip().upper() or None

    def tarefa():
        try:
            backend.sincronizar_relogio()
            historico = []
            if backend is corretora_mock or symbol_hist:
                historico = backend.ultimas_ordens(chave, segredo, symbol_hist)
            return {"saldos": backend.saldos(chave, segredo),
                    "ordens": backend.ordens_abertas(chave, segredo),
                    "historico": historico}
        except corretora.BinanceErro as e:
            return {"erro": str(e)}

    def mostrar(r):
        if "erro" in r:
            label_broker_status.config(text=r["erro"], fg=COR_BAIXA)
            return
        tabela_saldos.delete(*tabela_saldos.get_children())
        for b in r["saldos"]:
            tabela_saldos.insert("", tk.END, values=(b["asset"], b["free"], b["locked"]))
        tabela_ordens.delete(*tabela_ordens.get_children())
        for o in r["ordens"]:
            tabela_ordens.insert("", tk.END, iid=str(o["orderId"]), values=(
                o["symbol"], o["side"], o["type"], o["origQty"], o["price"], o["status"]))
        tabela_hist_ordens.delete(*tabela_hist_ordens.get_children())
        for o in r["historico"]:
            tabela_hist_ordens.insert("", tk.END, values=(
                o["symbol"], o["side"], o["type"], o["origQty"], o["price"],
                o.get("avgPrice") or "-", o["status"]))
        nome = "Simulado" if backend is corretora_mock else "Testnet"
        label_broker_status.config(
            text="{}: {} saldo(s), {} ordem(ns) aberta(s) - atualizado as {}".format(
                nome, len(r["saldos"]), len(r["ordens"]), datetime.now().strftime("%H:%M")), fg=cor_texto())

    em_segundo_plano(tarefa, mostrar, botao_atualizar_broker, chave="broker")

def enviar_ordem_broker():
    backend = broker_backend()
    chave, segredo = credenciais_binance()
    if backend is corretora and not chave:
        messagebox.showwarning("Aviso", "Salve as credenciais da testnet primeiro - ou use o modo Simulado")
        return
    symbol = campo_ordem_symbol.get().strip().upper()
    lado = ordem_lado.get()
    tipo = ordem_tipo.get()
    qtd = campo_ordem_qtd.get().strip()
    preco = campo_ordem_preco.get().strip()
    so_validar = ordem_validar.get() == 1
    if not symbol or not qtd or (tipo == "LIMIT" and not preco):
        messagebox.showwarning("Aviso", "Preencha simbolo, quantidade e preco (para LIMIT)")
        return
    if not messagebox.askyesno("Confirmar ordem",
            "{} {} {} de {}{}?".format(
                "MONTAR" if so_validar else "EXECUTAR", lado, qtd, symbol,
                " a " + preco if tipo == "LIMIT" else " a mercado")):
        return

    def tarefa():
        try:
            backend.sincronizar_relogio()
            return backend.enviar_ordem(chave, segredo, symbol, lado, tipo, qtd,
                                        preco or None, so_validar=so_validar)
        except corretora.BinanceErro as e:
            return {"erro": str(e)}

    def mostrar(r):
        if "erro" in r:
            label_ordem_status.config(text=r["erro"], fg=COR_BAIXA)
            return
        if r.get("preview"):
            label_ordem_status.config(
                text="Ordem montada (nao enviada): " + corretora.montar_query(r["params"])[:110],
                fg=cor_texto())
        else:
            label_ordem_status.config(
                text="Ordem {}: {} {} - executado {}".format(
                    r.get("orderId", "?"), r.get("status", "?"), r.get("side", ""),
                    r.get("executedQty", "?")), fg=COR_ALTA)
        atualizar_broker()

    em_segundo_plano(tarefa, mostrar, botao_enviar_ordem, chave="broker_ordem")

def cancelar_ordem_broker():
    backend = broker_backend()
    chave, segredo = credenciais_binance()
    selecao = tabela_ordens.selection()
    if not selecao:
        messagebox.showwarning("Aviso", "Selecione uma ordem aberta na tabela")
        return
    valores = tabela_ordens.item(selecao[0], "values")
    if not messagebox.askyesno("Cancelar ordem", "Cancelar a ordem {} de {}?".format(selecao[0], valores[0])):
        return

    def tarefa():
        try:
            backend.sincronizar_relogio()
            return backend.cancelar_ordem(chave, segredo, valores[0], int(selecao[0]))
        except corretora.BinanceErro as e:
            return {"erro": str(e)}

    def mostrar(r):
        if "erro" in r:
            label_ordem_status.config(text=r["erro"], fg=COR_BAIXA)
            return
        label_ordem_status.config(text="Ordem {} cancelada".format(selecao[0]), fg=cor_texto())
        atualizar_broker()

    em_segundo_plano(tarefa, mostrar, chave="broker_ordem")

# ---------------- Alertas ----------------

def adicionar_alerta():
    ticker = ler_ticker(campo_ticker_alerta)
    pa = campo_alerta.get().strip().replace(",", ".")
    tipo = tipo_alerta.get()
    if not ticker or not pa:
        messagebox.showwarning("Aviso", "Preencha o codigo e o preco alvo")
        return
    try:
        paf = float(pa)
    except ValueError:
        messagebox.showwarning("Aviso", "Preco alvo invalido")
        return

    def tarefa():
        cot = cotacao(ticker)
        if cot is None:
            return {"erro": motivo_falha(ticker)}
        executar("INSERT INTO alertas (ticker, preco_alvo, tipo, ativo) VALUES (?, ?, ?, 1)", (ticker, paf, tipo))
        return {"cot": cot}

    def mostrar(r):
        if "erro" in r:
            messagebox.showerror("Erro", r["erro"])
            return
        campo_ticker_alerta.delete(0, tk.END)
        campo_alerta.delete(0, tk.END)
        atualizar_alertas()
        verificar_alertas(ticker, r["cot"]["preco"])

    em_segundo_plano(tarefa, mostrar, botao_criar_alerta)

def atualizar_alertas():
    lista_alertas.delete(0, tk.END)
    alertas_na_lista.clear()
    for a in listar_alertas():
        alertas_na_lista.append(a[0])
        status = "Ativo" if a[4] == 1 else "Disparado"
        lista_alertas.insert(tk.END, "{} | {} de {} | {}".format(a[1], a[3], formatar_br(a[2]), status))

def verificar_alertas(ticker, preco_atual):
    disparados = []
    alertas = consultar("SELECT id, ticker, preco_alvo, tipo, ativo FROM alertas WHERE ticker = ? AND ativo = 1", (ticker,))
    for a in alertas:
        if (a[3] == "acima" and preco_atual >= a[2]) or (a[3] == "abaixo" and preco_atual <= a[2]):
            disparados.append(a)
            executar("UPDATE alertas SET ativo = 0 WHERE id = ?", (a[0],))
    if not disparados:
        return
    atualizar_alertas()
    for a in disparados:
        log.info("Alerta disparado: %s %s %s (preco %s)", ticker, a[3], a[2], preco_atual)
        notificar("Alerta: " + ticker, "{} esta em {} ({} de {})".format(ticker, formatar_br(preco_atual), a[3], formatar_br(a[2])))

def checar_alertas_automatico():
    def tarefa():
        tickers = [t for (t,) in consultar("SELECT DISTINCT ticker FROM alertas WHERE ativo = 1")]
        return {"precos": {t: cotacao(t) for t in tickers}}

    def mostrar(r):
        for ticker, cot in r.get("precos", {}).items():
            if cot:
                verificar_alertas(ticker, cot["preco"])

    em_segundo_plano(tarefa, mostrar, chave="alertas")

def remover_alerta():
    selecao = lista_alertas.curselection()
    if not selecao:
        messagebox.showwarning("Aviso", "Selecione um alerta")
        return
    executar("DELETE FROM alertas WHERE id = ?", (alertas_na_lista[selecao[0]],))
    atualizar_alertas()

# ---------------- Comparacao ----------------

def comparar_ativos():
    t1 = ler_ticker(comp_ticker1)
    t2 = ler_ticker(comp_ticker2)
    if not t1 or not t2:
        messagebox.showwarning("Aviso", "Digite dois tickers")
        return
    periodo = periodo_comp.get()

    def tarefa():
        h1 = buscar_historico(t1, PERIODOS[periodo])
        h2 = buscar_historico(t2, PERIODOS[periodo])
        faltando = [t for t, h in ((t1, h1), (t2, h2)) if h is None]
        if faltando:
            return {"erro": motivo_falha(faltando[0])}
        return {"h1": h1, "h2": h2}

    def mostrar(r):
        if "erro" in r:
            messagebox.showerror("Erro", r["erro"])
            return
        ax_comp.clear()
        for ticker, hist, cor in ((t1, r["h1"], COR_LINHA), (t2, r["h2"], "orange")):
            base = hist["Close"].iloc[0]
            ax_comp.plot(hist.index, (hist["Close"] / base - 1) * 100, label=ticker, color=cor)
        ax_comp.axhline(0, color="gray", linewidth=0.8)
        ax_comp.legend()
        ax_comp.set_ylabel("Variacao no periodo (%)")
        ax_comp.set_title("Comparacao: " + t1 + " x " + t2 + " (" + periodo + ")")
        ax_comp.grid(True)
        fig_comp.autofmt_xdate()
        canvas_comp.draw()

    em_segundo_plano(tarefa, mostrar, botao_comparar)

# ---------------- Ranking ----------------

def ranking_altas_baixas():
    tickers = [t.strip() for t in ranking_tickers.get().upper().split(",") if t.strip()]
    if not tickers:
        messagebox.showwarning("Aviso", "Digite tickers separados por virgula")
        return
    lista_ranking.delete(0, tk.END)
    lista_ranking.insert(tk.END, "Buscando " + str(len(tickers)) + " ativos...")

    def tarefa():
        resultados = []
        falhas = []
        for t in tickers:
            cot = cotacao(t)
            if cot is None or cot["variacao"] is None:
                falhas.append(t)
            else:
                resultados.append((t, cot))
        if not resultados and not tem_internet():
            return {"erro": "Sem conexao com a internet."}
        return {"resultados": resultados, "falhas": falhas}

    def mostrar(r):
        lista_ranking.delete(0, tk.END)
        if "erro" in r:
            lista_ranking.insert(tk.END, r["erro"])
            return
        ordenados = sorted(r["resultados"], key=lambda x: x[1]["variacao"], reverse=True)
        for posicao, (t, cot) in enumerate(ordenados, start=1):
            lista_ranking.insert(tk.END, "{}. {} | {} | {}".format(posicao, t, formatar_preco(cot["preco"], simbolo_moeda(t, cot["moeda"])), formatar_var(cot["variacao"])))
            lista_ranking.itemconfig(tk.END, fg=COR_ALTA if cot["variacao"] >= 0 else COR_BAIXA)
        if r["falhas"]:
            lista_ranking.insert(tk.END, "")
            lista_ranking.insert(tk.END, "Nao encontrados: " + ", ".join(r["falhas"]))

    em_segundo_plano(tarefa, mostrar, botao_ranking)

# ---------------- Simulador ----------------

def simular_aportes():
    try:
        mensal_f = float(sim_mensal.get().strip().replace(",", "."))
        meses_i = int(sim_meses.get().strip())
        taxa = sim_taxa.get().strip().replace(",", ".")
        taxa_anual = float(taxa) / 100
    except ValueError:
        messagebox.showwarning("Aviso", "Preencha aporte, meses e taxa com numeros")
        return
    taxa_mensal = (1 + taxa_anual) ** (1 / 12) - 1
    valor_futuro = 0
    for i in range(meses_i):
        valor_futuro = (valor_futuro + mensal_f) * (1 + taxa_mensal)
    total_investido = mensal_f * meses_i
    label_sim_resultado.config(text="Aporte de {} por {} meses a {}% ao ano:\nTotal investido: {}\nValor final (hipotetico): {}\nRendimento: {}".format(
        formatar_preco(mensal_f, "BRL"), meses_i, taxa.replace(".", ","), formatar_preco(total_investido, "BRL"),
        formatar_preco(valor_futuro, "BRL"), formatar_preco(valor_futuro - total_investido, "BRL")))

def simular_retro():
    """'E se eu tivesse investido X em Y na data Z' - quanto valeria hoje, vs o mesmo valor no CDI."""
    ticker = ler_ticker(retro_ticker)
    dia = ler_data(retro_data.get())
    try:
        valor = ler_valor_em_reais(retro_valor.get())
    except ValueError:
        messagebox.showwarning("Aviso", "Valor invalido (ex: 10.000,00)")
        return
    if not ticker or dia is None or dia >= date.today() or valor <= 0:
        messagebox.showwarning("Aviso", "Digite o codigo, um valor positivo e uma data passada (DD/MM/AAAA)")
        return

    def tarefa():
        hist = buscar_historico_desde(ticker, dia.isoformat())
        if hist is None:
            return {"erro": motivo_falha(ticker)}
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return {"erro": "Dados insuficientes depois de " + formatar_data(dia.isoformat())}
        cot = cotacao(ticker)
        indices_cdi, _ = obter_indices("cdi", dia)
        fator_cdi = 1.0
        for d, v, f in indices_cdi:
            if dia.isoformat() <= d:
                fator_cdi *= 1 + v / 100
        return {"primeiro": float(closes.iloc[0]), "ultimo": float(closes.iloc[-1]),
                "data_ini": closes.index[0].date().isoformat(), "moeda": cot["moeda"] if cot else "BRL",
                "fator_cdi": fator_cdi}

    def mostrar(r):
        if "erro" in r:
            messagebox.showerror("Erro", r["erro"])
            return
        moeda = r["moeda"]
        hoje_ativo = valor / r["primeiro"] * r["ultimo"]
        hoje_cdi = valor * r["fator_cdi"]
        pct_ativo = (hoje_ativo / valor - 1) * 100
        pct_cdi = (hoje_cdi / valor - 1) * 100
        label_retro_resultado.config(text=(
            "Se voce tivesse aplicado {} em {} no primeiro pregao apos {}:\n"
            "{} hoje ({:+.1f}%)   |   no CDI: {} ({:+.1f}%)\n"
            "(sem dividendos nem cambio para ativos em outra moeda)").format(
                formatar_preco(valor, "BRL"), ticker, formatar_data(r["data_ini"]),
                formatar_preco(hoje_ativo, moeda), pct_ativo,
                formatar_preco(hoje_cdi, "BRL"), pct_cdi))

    em_segundo_plano(tarefa, mostrar, botao_retro)

# ---------------- Previsao ----------------

def prever_preco():
    ticker = ler_ticker(prev_ticker)
    try:
        dias_i = int(prev_dias.get().strip())
    except ValueError:
        messagebox.showwarning("Aviso", "Dias invalido")
        return
    if not ticker:
        messagebox.showwarning("Aviso", "Digite um ticker")
        return

    def tarefa():
        historico = buscar_historico(ticker, str(dias_i) + "d")
        if historico is None:
            return {"erro": motivo_falha(ticker)}
        if len(historico) < 10:
            return {"erro": "Dados insuficientes. Use pelo menos 15 dias de historico."}
        return {"hist": historico}

    def mostrar(r):
        if "erro" in r:
            messagebox.showerror("Erro", r["erro"])
            return
        precos = r["hist"]["Close"].values
        x = np.arange(len(precos))
        coeficientes = np.polyfit(x, precos, 1)
        tendencia = np.polyval(coeficientes, x)
        previsao = np.polyval(coeficientes, len(precos))
        ax_prev.clear()
        ax_prev.plot(x, precos, label="Historico", color=COR_LINHA)
        ax_prev.plot(x, tendencia, "--", color="gray", label="Tendencia")
        ax_prev.plot(len(precos), previsao, "o", color=COR_BAIXA, label="Proximo ponto")
        ax_prev.legend()
        ax_prev.set_title(ticker + " - tendencia linear")
        ax_prev.set_ylabel("Preco")
        canvas_prev.draw()
        label_prev_resultado.config(text="Tendencia linear para o proximo pregao: " + formatar_br(previsao) + "\n(apenas exercicio de estudo - NAO use para decidir investimentos)")

    em_segundo_plano(tarefa, mostrar, botao_prever)

# ---------------- Iniciar com o Windows ----------------

CHAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOME_RUN = "MonitorInvestimentos"

def comando_inicializacao():
    if getattr(sys, "frozen", False):
        return '"' + sys.executable + '" --minimizado'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return '"' + pythonw + '" "' + os.path.abspath(__file__) + '" --minimizado'

def inicia_com_windows():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN) as chave:
            winreg.QueryValueEx(chave, NOME_RUN)
            return True
    except OSError:
        return False

def definir_inicializacao(ligar):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN, 0, winreg.KEY_SET_VALUE) as chave:
        if ligar:
            winreg.SetValueEx(chave, NOME_RUN, 0, winreg.REG_SZ, comando_inicializacao())
        else:
            try:
                winreg.DeleteValue(chave, NOME_RUN)
            except FileNotFoundError:
                pass

def alternar_inicializacao():
    try:
        definir_inicializacao(iniciar_var.get())
    except OSError as e:
        iniciar_var.set(inicia_com_windows())
        messagebox.showerror("Erro", "Nao consegui alterar a inicializacao: " + str(e))
        return
    log.info("Iniciar com o Windows: %s", iniciar_var.get())
    if iniciar_var.get():
        messagebox.showinfo("Pronto", "O Monitor vai abrir minimizado sempre que voce ligar o computador.\nAssim os alertas funcionam o tempo todo.")

# ================= Interface =================

trava_instancia = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    trava_instancia.bind(("127.0.0.1", 47653))
except OSError:
    if "--minimizado" not in sys.argv:
        aviso = tk.Tk()
        aviso.withdraw()
        messagebox.showinfo("Monitor de Investimentos", "O Monitor ja esta aberto.\nProcure o icone dele na barra de tarefas.")
        aviso.destroy()
    sys.exit(0)

criar_tabelas()
tema_atual[0] = ler_config("tema", "claro")
estilo_graficos(TEMAS[tema_atual[0]])

janela = tk.Tk()
janela.title("Monitor de Investimentos")
janela.geometry("1100x800")

def erro_na_interface(tipo, valor, tb):
    log.error("Erro na interface", exc_info=(tipo, valor, tb))
    traceback.print_exception(tipo, valor, tb)

janela.report_callback_exception = erro_na_interface

barra = tk.Frame(janela)
barra.pack(fill="x")
tk.Label(barra, text="Monitor de Investimentos", font=("Arial", 13, "bold")).pack(side=tk.LEFT, padx=10, pady=5)
botao_tema = tk.Button(barra, text="Tema escuro", command=alternar_tema)
botao_tema.pack(side=tk.RIGHT, padx=10, pady=5)
iniciar_var = tk.BooleanVar(value=inicia_com_windows())
tk.Checkbutton(barra, text="Iniciar com o Windows", variable=iniciar_var, command=alternar_inicializacao).pack(side=tk.RIGHT, padx=10)

notebook = ttk.Notebook(janela)
notebook.pack(fill="both", expand=True)

# Aba Mercado
aba_mercado = ttk.Frame(notebook)
notebook.add(aba_mercado, text="Mercado")

topo_mercado = tk.Frame(aba_mercado)
topo_mercado.pack(fill="x", pady=5)
label_mercado_status = tk.Label(topo_mercado, text="Carregando mercado...")
label_mercado_status.pack(side=tk.LEFT, padx=10)
botao_mercado = tk.Button(topo_mercado, text="Atualizar agora", command=carregar_mercado)
botao_mercado.pack(side=tk.RIGHT, padx=10)

quadro_mercado = tk.Frame(aba_mercado)
quadro_mercado.pack(fill="x", padx=10)
tabela_mercado = ttk.Treeview(quadro_mercado, columns=("nome", "ticker", "preco", "variacao"), show="headings", height=10)
for coluna, titulo, largura in [("nome", "Ativo", 200), ("ticker", "Codigo", 120), ("preco", "Preco", 160), ("variacao", "Variacao no dia", 140)]:
    tabela_mercado.heading(coluna, text=titulo)
    tabela_mercado.column(coluna, width=largura, anchor="center")
tabela_mercado.tag_configure("alta", foreground=COR_ALTA)
tabela_mercado.tag_configure("baixa", foreground=COR_BAIXA)
rolagem_mercado = ttk.Scrollbar(quadro_mercado, orient="vertical", command=tabela_mercado.yview)
tabela_mercado.configure(yscrollcommand=rolagem_mercado.set)
rolagem_mercado.pack(side=tk.RIGHT, fill="y")
tabela_mercado.pack(side=tk.LEFT, fill="x", expand=True)
tabela_mercado.bind("<<TreeviewSelect>>", ao_selecionar_mercado)

editar_mercado = tk.Frame(aba_mercado)
editar_mercado.pack(fill="x", padx=10, pady=5)
tk.Label(editar_mercado, text="Codigo:").pack(side=tk.LEFT)
campo_mercado_ticker = tk.Entry(editar_mercado, width=12)
campo_mercado_ticker.pack(side=tk.LEFT, padx=3)
tk.Label(editar_mercado, text="Nome (opcional):").pack(side=tk.LEFT, padx=(8, 0))
campo_mercado_nome = tk.Entry(editar_mercado, width=18)
campo_mercado_nome.pack(side=tk.LEFT, padx=3)
botao_adicionar_mercado = tk.Button(editar_mercado, text="Adicionar a lista", command=adicionar_ao_mercado)
botao_adicionar_mercado.pack(side=tk.LEFT, padx=5)
tk.Button(editar_mercado, text="Remover selecionado", command=remover_do_mercado).pack(side=tk.LEFT, padx=5)
tk.Button(editar_mercado, text="Restaurar lista padrao", command=restaurar_mercado_padrao).pack(side=tk.LEFT, padx=5)

periodo_mercado = tk.StringVar(value="1 mes")
linha_grafico_mercado = tk.Frame(aba_mercado)
linha_grafico_mercado.pack(fill="x", padx=10)
tk.Label(linha_grafico_mercado, text="Clique em um ativo para ver o grafico dele").pack(side=tk.LEFT)
criar_seletor_periodo(linha_grafico_mercado, periodo_mercado, lambda: mostrar_grafico_mercado(grafico_mercado_atual[0]))

fig_mercado, ax_mercado = plt.subplots(figsize=(9, 3))
canvas_mercado = FigureCanvasTkAgg(fig_mercado, master=aba_mercado)
canvas_mercado.get_tk_widget().pack(padx=10, pady=5, fill="both", expand=True)

# Aba Cotacao
aba_cotacao = ttk.Frame(notebook)
notebook.add(aba_cotacao, text="Cotacao")

tk.Label(aba_cotacao, text="Codigo (ex: PETR4.SA, AAPL, BTC-USD):").pack(pady=5)
campo_ticker = tk.Entry(aba_cotacao, width=30)
campo_ticker.pack()
campo_ticker.bind("<Return>", lambda e: atualizar_cotacao())

linha_cotacao = tk.Frame(aba_cotacao)
linha_cotacao.pack(pady=5)
botao_cotacao = tk.Button(linha_cotacao, text="Buscar cotacao", command=atualizar_cotacao)
botao_cotacao.pack(side=tk.LEFT)
periodo_cotacao = tk.StringVar(value="1 mes")
criar_seletor_periodo(linha_cotacao, periodo_cotacao, mudar_periodo_cotacao)

label_preco = tk.Label(aba_cotacao, text="Preco: -", font=("Arial", 16, "bold"))
label_preco.pack(pady=10)

fig, ax = plt.subplots(figsize=(8, 4))
canvas = FigureCanvasTkAgg(fig, master=aba_cotacao)
canvas.get_tk_widget().pack(padx=10, pady=10)

tk.Label(aba_cotacao, text="Acoes brasileiras terminam em .SA (PETR4.SA). Cripto: BTC-USD, ETH-USD. FIIs: MXRF11.SA").pack()

# Aba Carteira
aba_carteira = ttk.Frame(notebook)
notebook.add(aba_carteira, text="Carteira")

def criar_tabela(pai, colunas, altura):
    quadro = tk.Frame(pai)
    quadro.pack(fill="x", padx=10, pady=3)
    tabela = ttk.Treeview(quadro, columns=[c[0] for c in colunas], show="headings", height=altura)
    for coluna, titulo, largura in colunas:
        tabela.heading(coluna, text=titulo)
        tabela.column(coluna, width=largura, anchor="center")
    rolagem = ttk.Scrollbar(quadro, orient="vertical", command=tabela.yview)
    tabela.configure(yscrollcommand=rolagem.set)
    rolagem.pack(side=tk.RIGHT, fill="y")
    tabela.pack(side=tk.LEFT, fill="x", expand=True)
    return tabela

form_carteira = tk.Frame(aba_carteira)
form_carteira.pack(pady=5)

tk.Label(form_carteira, text="Codigo:").grid(row=0, column=0, padx=5, pady=3, sticky="e")
campo_ticker_carteira = tk.Entry(form_carteira, width=14)
campo_ticker_carteira.grid(row=0, column=1, sticky="w")

tk.Label(form_carteira, text="Data (DD/MM/AAAA):").grid(row=0, column=2, padx=(15, 5), sticky="e")
campo_data_operacao = tk.Entry(form_carteira, width=14)
campo_data_operacao.grid(row=0, column=3, sticky="w")
campo_data_operacao.insert(0, date.today().strftime("%d/%m/%Y"))

tk.Label(form_carteira, text="Operacao:").grid(row=0, column=4, padx=(15, 5), sticky="e")
operacao_var = tk.StringVar(value="compra")
opcoes_operacao = tk.Frame(form_carteira)
opcoes_operacao.grid(row=0, column=5, sticky="w")
tk.Radiobutton(opcoes_operacao, text="Compra", variable=operacao_var, value="compra").pack(side=tk.LEFT)
tk.Radiobutton(opcoes_operacao, text="Venda", variable=operacao_var, value="venda").pack(side=tk.LEFT)

tk.Label(form_carteira, text="Quantidade:").grid(row=1, column=0, padx=5, pady=3, sticky="e")
campo_quantidade = tk.Entry(form_carteira, width=14)
campo_quantidade.grid(row=1, column=1, sticky="w")

tk.Label(form_carteira, text="Preco por unidade:").grid(row=1, column=2, padx=(15, 5), sticky="e")
campo_preco_operacao = tk.Entry(form_carteira, width=14)
campo_preco_operacao.grid(row=1, column=3, sticky="w")

tk.Label(form_carteira, text="Tipo:").grid(row=1, column=4, padx=(15, 5), sticky="e")
tipo_var = tk.StringVar(value="Acao")
ttk.Combobox(form_carteira, textvariable=tipo_var, values=[nome for codigo, nome in TIPOS], state="readonly", width=12).grid(row=1, column=5, sticky="w")
campo_ticker_carteira.bind("<FocusOut>", sugerir_tipo_no_formulario)

tk.Label(aba_carteira, text="O preco e na moeda do ativo (acao americana em dolar). O dolar do dia da operacao e buscado sozinho.").pack()

botoes_carteira = tk.Frame(aba_carteira)
botoes_carteira.pack(pady=5)
botao_registrar = tk.Button(botoes_carteira, text="Registrar operacao", command=registrar_operacao)
botao_registrar.pack(side=tk.LEFT, padx=5)
botao_atualizar_carteira = tk.Button(botoes_carteira, text="Atualizar precos", command=atualizar_carteira)
botao_atualizar_carteira.pack(side=tk.LEFT, padx=5)

tk.Label(aba_carteira, text="Posicao atual", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
tabela_carteira = criar_tabela(aba_carteira, [
    ("ticker", "Ativo", 95), ("tipo", "Tipo", 55), ("q", "Qtd", 75), ("pm", "Preco medio", 110), ("atual", "Preco atual", 110),
    ("investido", "Investido R$", 115), ("valor", "Valor hoje R$", 115), ("lucro", "Lucro R$", 110), ("pct", "Lucro %", 80)], 7)
tabela_carteira.tag_configure("lucro", foreground=COR_ALTA)
tabela_carteira.tag_configure("prejuizo", foreground=COR_BAIXA)
tabela_carteira.bind("<<TreeviewSelect>>", ao_selecionar_posicao)

totais = tk.Frame(aba_carteira)
totais.pack(pady=3)
label_patrimonio = tk.Label(totais, text="Patrimonio: R$ 0,00", font=("Arial", 12, "bold"))
label_patrimonio.pack(side=tk.LEFT, padx=15)
label_lucro = tk.Label(totais, text="Lucro em aberto: R$ 0,00", font=("Arial", 12))
label_lucro.pack(side=tk.LEFT, padx=15)
label_realizado = tk.Label(totais, text="Lucro ja realizado (vendas): R$ 0,00", font=("Arial", 12))
label_realizado.pack(side=tk.LEFT, padx=15)

label_carteira_status = tk.Label(aba_carteira, text="", wraplength=1000)
label_carteira_status.pack()

topo_operacoes = tk.Frame(aba_carteira)
topo_operacoes.pack(fill="x", padx=10, pady=(8, 0))
tk.Label(topo_operacoes, text="Historico de operacoes", font=("Arial", 11, "bold")).pack(side=tk.LEFT)
tk.Button(topo_operacoes, text="Excluir operacao selecionada", command=excluir_operacao).pack(side=tk.RIGHT)
tk.Button(topo_operacoes, text="Corrigir data", command=corrigir_data).pack(side=tk.RIGHT, padx=5)
tabela_operacoes = criar_tabela(aba_carteira, [
    ("data", "Data", 100), ("operacao", "Operacao", 90), ("ticker", "Ativo", 100), ("q", "Qtd", 90),
    ("preco", "Preco", 120), ("cambio", "Dolar do dia", 100), ("total", "Total em R$", 130)], 6)
tabela_operacoes.tag_configure("venda", foreground=COR_BAIXA)
tabela_operacoes.tag_configure("importada", foreground="#e08e00")
tabela_operacoes.bind("<Double-1>", corrigir_data)

# Aba Renda Fixa
aba_rf = ttk.Frame(notebook)
notebook.add(aba_rf, text="Renda Fixa")

form_rf = tk.Frame(aba_rf)
form_rf.pack(pady=8)

tk.Label(form_rf, text="Produto:").grid(row=0, column=0, padx=5, pady=4, sticky="e")
produto_var = tk.StringVar(value="CDB")
combo_produto = ttk.Combobox(form_rf, textvariable=produto_var, values=list(PRODUTOS_RF.keys()), state="readonly", width=18)
combo_produto.grid(row=0, column=1, sticky="w")
combo_produto.bind("<<ComboboxSelected>>", ao_mudar_produto)

tk.Label(form_rf, text="Nome (opcional):").grid(row=0, column=2, padx=(15, 5), sticky="e")
campo_nome_rf = tk.Entry(form_rf, width=24)
campo_nome_rf.grid(row=0, column=3, sticky="w")

tk.Label(form_rf, text="Rentabilidade:").grid(row=1, column=0, padx=5, pady=4, sticky="e")
indexador_var = tk.StringVar(value=INDEXADORES["cdi"])
combo_indexador = ttk.Combobox(form_rf, textvariable=indexador_var, state="readonly", width=18)
combo_indexador.grid(row=1, column=1, sticky="w")
combo_indexador.bind("<<ComboboxSelected>>", ao_mudar_indexador)

label_taxa_rf = tk.Label(form_rf, text="Quantos % do CDI:")
label_taxa_rf.grid(row=1, column=2, padx=(15, 5), sticky="e")
campo_taxa_rf = tk.Entry(form_rf, width=12)
campo_taxa_rf.grid(row=1, column=3, sticky="w")

tk.Label(form_rf, text="Valor aplicado (R$):").grid(row=2, column=0, padx=5, pady=4, sticky="e")
campo_valor_rf = tk.Entry(form_rf, width=14)
campo_valor_rf.grid(row=2, column=1, sticky="w")

tk.Label(form_rf, text="Data da aplicacao:").grid(row=2, column=2, padx=(15, 5), sticky="e")
campo_data_rf = tk.Entry(form_rf, width=12)
campo_data_rf.grid(row=2, column=3, sticky="w")
campo_data_rf.insert(0, date.today().strftime("%d/%m/%Y"))

tk.Label(form_rf, text="Vencimento (opcional):").grid(row=2, column=4, padx=(15, 5), sticky="e")
campo_venc_rf = tk.Entry(form_rf, width=12)
campo_venc_rf.grid(row=2, column=5, sticky="w")

botoes_rf = tk.Frame(aba_rf)
botoes_rf.pack(pady=4)
botao_salvar_rf = tk.Button(botoes_rf, text="Adicionar aplicacao", command=salvar_renda_fixa)
botao_salvar_rf.pack(side=tk.LEFT, padx=5)
botao_cancelar_rf = tk.Button(botoes_rf, text="Cancelar edicao", command=cancelar_edicao_rf)
botao_atualizar_rf = tk.Button(botoes_rf, text="Atualizar", command=atualizar_renda_fixa)
botao_atualizar_rf.pack(side=tk.LEFT, padx=5)
tk.Button(botoes_rf, text="Excluir / resgatei", command=excluir_renda_fixa).pack(side=tk.LEFT, padx=5)

label_rf_status = tk.Label(aba_rf, text="")
label_rf_status.pack()

tabela_rf = criar_tabela(aba_rf, [
    ("nome", "Nome", 130), ("produto", "Produto", 110), ("rent", "Rentabilidade", 115), ("data", "Aplicado em", 85),
    ("aplicado", "Aplicado", 95), ("bruto", "Valor bruto", 100), ("rend", "Rendimento", 95), ("ir", "Impostos", 150),
    ("liquido", "Valor liquido", 100), ("venc", "Vencimento", 100)], 11)
tabela_rf.tag_configure("vencido", foreground="#e08e00")
tabela_rf.bind("<Double-1>", carregar_rf_para_editar)

label_rf_totais = tk.Label(aba_rf, text="Aplicado: R$ 0,00     Valor bruto: R$ 0,00     Valor liquido (apos IR): R$ 0,00", font=("Arial", 12, "bold"))
label_rf_totais.pack(pady=6)
tk.Label(aba_rf, wraplength=1000, justify="center", text=(
    "Estimativa pela taxa contratada, com CDI, Selic, IPCA e poupanca oficiais do Banco Central. "
    "IR regressivo sobre o rendimento (22,5% ate 180 dias, 20% ate 360, 17,5% ate 720 e 15% depois); LCI, LCA e poupanca sao isentas. "
    "Resgates com menos de 30 dias descontam IOF sobre o rendimento. "
    "Nao considera taxa de custodia do Tesouro nem o preco de mercado: "
    "vender um titulo do Tesouro antes do vencimento pode dar um valor diferente. Duplo clique na linha para editar.")).pack(padx=10)

# Aba Resumo
aba_resumo = ttk.Frame(notebook)
notebook.add(aba_resumo, text="Resumo")

label_patrimonio_total = tk.Label(aba_resumo, text="Patrimonio total: R$ 0,00", font=("Arial", 13, "bold"))
label_patrimonio_total.pack(pady=(8, 0))

linha_resumo = tk.Frame(aba_resumo)
linha_resumo.pack(pady=(4, 0))
tk.Button(linha_resumo, text="Exportar para Excel", command=exportar_excel).pack(side=tk.LEFT, padx=4)
tk.Button(linha_resumo, text="Exportar PDF", command=exportar_pdf).pack(side=tk.LEFT, padx=4)
tk.Button(linha_resumo, text="Backup do banco", command=backup_banco).pack(side=tk.LEFT, padx=4)
tk.Label(linha_resumo, text="   Meta (R$):").pack(side=tk.LEFT)
meta_var = tk.StringVar()
_meta_salva = float(ler_config("meta_patrimonio", "0") or 0)
if _meta_salva > 0:
    meta_var.set(formatar_br(_meta_salva))
campo_meta = tk.Entry(linha_resumo, textvariable=meta_var, width=14)
campo_meta.pack(side=tk.LEFT)
tk.Button(linha_resumo, text="Definir meta", command=definir_meta).pack(side=tk.LEFT, padx=4)
barra_meta = ttk.Progressbar(aba_resumo, length=560, maximum=100)
barra_meta.pack(pady=(4, 0))
label_meta = tk.Label(aba_resumo, text="Sem meta definida")
label_meta.pack()
tk.Label(aba_resumo, text="O grafico de rentabilidade compara sua carteira com CDI e Ibovespa; "
         "a linha cinza de aportes sobe quando voce aplica dinheiro novo (nao e rendimento).").pack()

fig_resumo, (ax_pizza, ax_hist) = plt.subplots(1, 2, figsize=(10, 3.6))
canvas_resumo = FigureCanvasTkAgg(fig_resumo, master=aba_resumo)
canvas_resumo.get_tk_widget().pack(padx=10, pady=5, fill="x")

tk.Label(aba_resumo, text="Dividendos pagos nos ultimos 12 meses", font=("Arial", 12, "bold")).pack(pady=(10, 3))
colunas_div = [("ticker", "Ativo", 110), ("q", "Qtd atual", 100), ("por_acao", "Pago por acao (12m)", 150), ("total", "Voce recebeu (R$)", 160), ("dy", "Dividend yield", 120)]
tabela_dividendos = ttk.Treeview(aba_resumo, columns=[c[0] for c in colunas_div], show="headings", height=7)
for coluna, titulo, largura in colunas_div:
    tabela_dividendos.heading(coluna, text=titulo)
    tabela_dividendos.column(coluna, width=largura, anchor="center")
tabela_dividendos.pack(padx=10)

label_total_dividendos = tk.Label(aba_resumo, text="Voce recebeu nos ultimos 12 meses: R$ 0,00", font=("Arial", 12, "bold"))
label_total_dividendos.pack(pady=5)
tk.Label(aba_resumo, text="Calculado pelas datas das suas operacoes: so conta quem tinha a acao antes da data-com. Valores antes de impostos.").pack()

frame_reb = tk.LabelFrame(aba_resumo, text="Meta de distribuicao por tipo (%)")
frame_reb.pack(padx=10, pady=6, fill="x")
linha_reb = tk.Frame(frame_reb)
linha_reb.pack(pady=3)
tk.Label(linha_reb, text="Tipo:").pack(side=tk.LEFT, padx=4)
combo_reb = ttk.Combobox(linha_reb, state="readonly", width=14)
combo_reb.pack(side=tk.LEFT)
tk.Label(linha_reb, text="Meta %:").pack(side=tk.LEFT, padx=4)
campo_reb_pct = tk.Entry(linha_reb, width=6)
campo_reb_pct.pack(side=tk.LEFT)
tk.Button(linha_reb, text="Definir (0 remove)", command=definir_meta_grupo).pack(side=tk.LEFT, padx=6)
tabela_reb = criar_tabela(frame_reb, [
    ("grupo", "Tipo", 140), ("atual", "Atual", 90), ("meta", "Meta", 90), ("dif", "Diferenca", 200)], 4)

# Aba Imposto de Renda
aba_irpf = ttk.Frame(notebook)
notebook.add(aba_irpf, text="Imposto de Renda")

tk.Label(aba_irpf, text="Lucro realizado em vendas, por mes (estimativa)", font=("Arial", 12, "bold")).pack(pady=(10, 4))
tabela_irpf = criar_tabela(aba_irpf, [
    ("mes", "Mes", 90), ("tipo", "Tipo", 90), ("vendido", "Vendido (R$)", 120),
    ("lucro", "Lucro (R$)", 110), ("regra", "Regra aplicada", 280), ("ir", "IR estimado", 110)], 12)
label_irpf_total = tk.Label(aba_irpf, text="IR estimado acumulado: R$ 0,00", font=("Arial", 12, "bold"))
label_irpf_total.pack(pady=6)
tk.Label(aba_irpf, wraplength=950, justify="center", text=(
    "Estimativa simplificada para ajudar na declaracao: venda de acoes no mes ate R$ 20 mil e isenta de IR "
    "(mercado a vista); cripto ate R$ 35 mil; FII sempre 20%; exterior via carne-leao. "
    "Nao considera day trade (20%), prejuizo acumulado para compensar nem operacoes fora do app. "
    "Confirme sempre com sua contabilidade.")).pack(padx=10, pady=4)

# Aba Corretora (testnet Binance)
aba_broker = ttk.Frame(notebook)
notebook.add(aba_broker, text="Corretora (teste)")

tk.Label(aba_broker, text="Modo Simulado: corretora de mentira embutida, sem rede. "
         "Modo Testnet: futuros da Binance com dinheiro falso (conta gratis em testnet.binancefuture.com, login com GitHub).",
         wraplength=900).pack(pady=(8, 2))

form_broker = tk.Frame(aba_broker)
form_broker.pack(pady=2)
tk.Label(form_broker, text="Modo:").grid(row=0, column=0, padx=4, sticky="e")
broker_modo = tk.StringVar(value=ler_config("broker_modo", "local"))
tk.Radiobutton(form_broker, text="Simulado local", variable=broker_modo, value="local",
               command=mudar_modo_broker).grid(row=0, column=1, sticky="w")
tk.Radiobutton(form_broker, text="Testnet Binance (futuros)", variable=broker_modo, value="testnet",
               command=mudar_modo_broker).grid(row=0, column=2, columnspan=2, sticky="w")
tk.Label(form_broker, text="API key:").grid(row=1, column=0, padx=4, sticky="e")
campo_binance_key = tk.Entry(form_broker, width=42)
campo_binance_key.grid(row=1, column=1, padx=4)
tk.Label(form_broker, text="Secret:").grid(row=1, column=2, padx=4, sticky="e")
campo_binance_secret = tk.Entry(form_broker, width=42, show="*")
campo_binance_secret.grid(row=1, column=3, padx=4)
tk.Button(form_broker, text="Salvar credenciais", command=salvar_credenciais_binance).grid(row=1, column=4, padx=4)
tk.Button(form_broker, text="Esquecer", command=esquecer_credenciais_binance).grid(row=1, column=5, padx=4)

linha_broker = tk.Frame(aba_broker)
linha_broker.pack(pady=2)
botao_atualizar_broker = tk.Button(linha_broker, text="Atualizar saldos e ordens", command=atualizar_broker)
botao_atualizar_broker.pack(side=tk.LEFT, padx=4)
label_broker_status = tk.Label(linha_broker, text="")
label_broker_status.pack(side=tk.LEFT, padx=8)

tk.Label(aba_broker, text="Saldos (so o que nao esta zerado)", font=("Arial", 10, "bold")).pack(pady=(6, 0))
tabela_saldos = criar_tabela(aba_broker, [
    ("ativo", "Ativo", 120), ("livre", "Livre", 200), ("travado", "Bloqueado", 200)], 4)

ordem_frame = tk.LabelFrame(aba_broker, text="Nova ordem")
ordem_frame.pack(padx=10, pady=6, fill="x")
tk.Label(ordem_frame, text="Simbolo:").grid(row=0, column=0, padx=4, pady=4, sticky="e")
campo_ordem_symbol = tk.Entry(ordem_frame, width=12)
campo_ordem_symbol.grid(row=0, column=1, sticky="w")
campo_ordem_symbol.insert(0, "BTCUSDT")
ordem_lado = tk.StringVar(value="BUY")
tk.Radiobutton(ordem_frame, text="Comprar", variable=ordem_lado, value="BUY").grid(row=0, column=2)
tk.Radiobutton(ordem_frame, text="Vender", variable=ordem_lado, value="SELL").grid(row=0, column=3)
ordem_tipo = tk.StringVar(value="MARKET")
tk.Radiobutton(ordem_frame, text="A mercado", variable=ordem_tipo, value="MARKET").grid(row=0, column=4)
tk.Radiobutton(ordem_frame, text="Limite", variable=ordem_tipo, value="LIMIT").grid(row=0, column=5)
tk.Label(ordem_frame, text="Qtd:").grid(row=1, column=0, padx=4, sticky="e")
campo_ordem_qtd = tk.Entry(ordem_frame, width=14)
campo_ordem_qtd.grid(row=1, column=1, sticky="w")
tk.Label(ordem_frame, text="Preco (limite):").grid(row=1, column=2, padx=4, sticky="e")
campo_ordem_preco = tk.Entry(ordem_frame, width=14)
campo_ordem_preco.grid(row=1, column=3, sticky="w")
ordem_validar = tk.IntVar(value=1)
tk.Checkbutton(ordem_frame, text="So montar (nao envia)", variable=ordem_validar).grid(row=1, column=4, columnspan=2, sticky="w")
botao_enviar_ordem = tk.Button(ordem_frame, text="Enviar ordem", command=enviar_ordem_broker)
botao_enviar_ordem.grid(row=1, column=6, padx=6)
label_ordem_status = tk.Label(ordem_frame, text="")
label_ordem_status.grid(row=2, column=0, columnspan=7, pady=2)

tk.Label(aba_broker, text="Ordens abertas", font=("Arial", 10, "bold")).pack(pady=(4, 0))
tabela_ordens = criar_tabela(aba_broker, [
    ("sym", "Simbolo", 90), ("lado", "Lado", 70), ("tipo", "Tipo", 80),
    ("qtd", "Qtd", 110), ("preco", "Preco", 110), ("status", "Status", 100)], 5)
tk.Button(aba_broker, text="Cancelar ordem selecionada", command=cancelar_ordem_broker).pack(pady=4)

tk.Label(aba_broker, text="Historico de ordens (do simbolo do campo)", font=("Arial", 10, "bold")).pack(pady=(4, 0))
tabela_hist_ordens = criar_tabela(aba_broker, [
    ("sym", "Simbolo", 90), ("lado", "Lado", 70), ("tipo", "Tipo", 80),
    ("qtd", "Qtd", 110), ("preco", "Preco limite", 110), ("exec", "Preco exec.", 110),
    ("status", "Status", 100)], 5)
tk.Label(aba_broker, wraplength=900, justify="center", text=(
    "As credenciais ficam salvas no banco local - OK para a testnet, NUNCA faca isso com a corretora real. "
    "No simulado, ordens a mercado executam na hora; ordens limite executam quando o preco simulado cruzar o limite. "
    "O estado do simulado reseta a cada vez que o app abre.")).pack(padx=10, pady=4)

# Aba Alertas
aba_alertas = ttk.Frame(notebook)
notebook.add(aba_alertas, text="Alertas")

tk.Label(aba_alertas, text="Codigo (ex: PETR4.SA):").grid(row=0, column=0, padx=5, pady=5, sticky="e")
campo_ticker_alerta = tk.Entry(aba_alertas)
campo_ticker_alerta.grid(row=0, column=1)

tk.Label(aba_alertas, text="Preco alvo:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
campo_alerta = tk.Entry(aba_alertas)
campo_alerta.grid(row=1, column=1)

tk.Label(aba_alertas, text="Disparar quando:").grid(row=2, column=0, padx=5, pady=5, sticky="e")
tipo_alerta = tk.StringVar(value="acima")
tk.Radiobutton(aba_alertas, text="Acima", variable=tipo_alerta, value="acima").grid(row=2, column=1, sticky="w")
tk.Radiobutton(aba_alertas, text="Abaixo", variable=tipo_alerta, value="abaixo").grid(row=3, column=1, sticky="w")

botao_criar_alerta = tk.Button(aba_alertas, text="Criar alerta", command=adicionar_alerta)
botao_criar_alerta.grid(row=4, column=0, columnspan=2, pady=5)
tk.Button(aba_alertas, text="Remover alerta", command=remover_alerta).grid(row=5, column=0, columnspan=2, pady=5)

alertas_na_lista = []
lista_alertas = tk.Listbox(aba_alertas, width=80)
lista_alertas.grid(row=6, column=0, columnspan=2, padx=10, pady=10)
tk.Label(aba_alertas, text="Os alertas sao conferidos sozinhos a cada 5 minutos e aparecem como notificacao do Windows,\nmesmo com o app minimizado (ele precisa estar aberto).").grid(row=7, column=0, columnspan=2)

# Aba Comparacao
aba_comp = ttk.Frame(notebook)
notebook.add(aba_comp, text="Comparacao")

tk.Label(aba_comp, text="Codigo 1:").grid(row=0, column=0, padx=5, pady=5)
comp_ticker1 = tk.Entry(aba_comp)
comp_ticker1.grid(row=0, column=1)

tk.Label(aba_comp, text="Codigo 2:").grid(row=1, column=0, padx=5, pady=5)
comp_ticker2 = tk.Entry(aba_comp)
comp_ticker2.grid(row=1, column=1)

linha_comp = tk.Frame(aba_comp)
linha_comp.grid(row=2, column=0, columnspan=2, pady=5)
botao_comparar = tk.Button(linha_comp, text="Comparar", command=comparar_ativos)
botao_comparar.pack(side=tk.LEFT)
periodo_comp = tk.StringVar(value="1 mes")
criar_seletor_periodo(linha_comp, periodo_comp, lambda: comparar_ativos() if ler_ticker(comp_ticker1) and ler_ticker(comp_ticker2) else None)

fig_comp, ax_comp = plt.subplots(figsize=(8, 4))
canvas_comp = FigureCanvasTkAgg(fig_comp, master=aba_comp)
canvas_comp.get_tk_widget().grid(row=3, column=0, columnspan=2, padx=10, pady=10)

# Aba Ranking
aba_ranking = ttk.Frame(notebook)
notebook.add(aba_ranking, text="Ranking")

tk.Label(aba_ranking, text="Codigos separados por virgula:").pack(pady=5)
ranking_tickers = tk.Entry(aba_ranking, width=80)
ranking_tickers.pack()
ranking_tickers.insert(0, "PETR4.SA,VALE3.SA,ITUB4.SA,BBDC4.SA,BTC-USD")

botao_ranking = tk.Button(aba_ranking, text="Calcular ranking", command=ranking_altas_baixas)
botao_ranking.pack(pady=5)

lista_ranking = tk.Listbox(aba_ranking, width=90)
lista_ranking.pack(padx=10, pady=10)

# Aba Simulador
aba_sim = ttk.Frame(notebook)
notebook.add(aba_sim, text="Simulador")

tk.Label(aba_sim, text="Taxa anual suposta (%):").grid(row=0, column=0, padx=5, pady=5)
sim_taxa = tk.Entry(aba_sim)
sim_taxa.grid(row=0, column=1)
sim_taxa.insert(0, "10")

tk.Label(aba_sim, text="Aporte mensal (R$):").grid(row=1, column=0, padx=5, pady=5)
sim_mensal = tk.Entry(aba_sim)
sim_mensal.grid(row=1, column=1)

tk.Label(aba_sim, text="Meses:").grid(row=2, column=0, padx=5, pady=5)
sim_meses = tk.Entry(aba_sim)
sim_meses.grid(row=2, column=1)

tk.Button(aba_sim, text="Simular", command=simular_aportes).grid(row=3, column=0, columnspan=2, pady=5)

label_sim_resultado = tk.Label(aba_sim, text="Resultado: -", font=("Arial", 12, "bold"))
label_sim_resultado.grid(row=4, column=0, columnspan=2, pady=10)

ttk.Separator(aba_sim, orient="horizontal").grid(row=5, column=0, columnspan=2, sticky="ew", padx=20, pady=10)
tk.Label(aba_sim, text="E se eu tivesse investido...", font=("Arial", 11, "bold")).grid(row=6, column=0, columnspan=2)

tk.Label(aba_sim, text="Codigo:").grid(row=7, column=0, padx=5, pady=5, sticky="e")
retro_ticker = tk.Entry(aba_sim, width=14)
retro_ticker.grid(row=7, column=1, sticky="w")
retro_ticker.insert(0, "PETR4.SA")

tk.Label(aba_sim, text="Valor (R$):").grid(row=8, column=0, padx=5, pady=5, sticky="e")
retro_valor = tk.Entry(aba_sim, width=14)
retro_valor.grid(row=8, column=1, sticky="w")
retro_valor.insert(0, "10.000")

tk.Label(aba_sim, text="Na data (DD/MM/AAAA):").grid(row=9, column=0, padx=5, pady=5, sticky="e")
retro_data = tk.Entry(aba_sim, width=14)
retro_data.grid(row=9, column=1, sticky="w")
retro_data.insert(0, "02/01/2025")

botao_retro = tk.Button(aba_sim, text="Simular retroativo", command=simular_retro)
botao_retro.grid(row=10, column=0, columnspan=2, pady=5)

label_retro_resultado = tk.Label(aba_sim, text="", font=("Arial", 11))
label_retro_resultado.grid(row=11, column=0, columnspan=2, pady=8)

# Aba Previsao
aba_prev = ttk.Frame(notebook)
notebook.add(aba_prev, text="Previsao com IA")

tk.Label(aba_prev, text="Codigo:").grid(row=0, column=0, padx=5, pady=5)
prev_ticker = tk.Entry(aba_prev)
prev_ticker.grid(row=0, column=1)

tk.Label(aba_prev, text="Dias de historico:").grid(row=1, column=0, padx=5, pady=5)
prev_dias = tk.Entry(aba_prev)
prev_dias.grid(row=1, column=1)
prev_dias.insert(0, "30")

botao_prever = tk.Button(aba_prev, text="Prever preco", command=prever_preco)
botao_prever.grid(row=2, column=0, columnspan=2, pady=5)

fig_prev, ax_prev = plt.subplots(figsize=(8, 4))
canvas_prev = FigureCanvasTkAgg(fig_prev, master=aba_prev)
canvas_prev.get_tk_widget().grid(row=3, column=0, columnspan=2, padx=10, pady=10)

label_prev_resultado = tk.Label(aba_prev, text="Previsao: -", font=("Arial", 12, "bold"))
label_prev_resultado.grid(row=4, column=0, columnspan=2)

figuras = [fig_mercado, fig, fig_resumo, fig_comp, fig_prev]

aplicar_tema()
atualizar_alertas()
ao_mudar_produto()
desenhar_resumo()
janela.after(100, agendar_atualizacoes)
mostrar_grafico_mercado("^BVSP")
if getattr(sys, "frozen", False) and iniciar_var.get():
    definir_inicializacao(True)
if "--minimizado" in sys.argv:
    janela.after(500, janela.iconify)
log.info("App iniciado (tema %s%s)", tema_atual[0], ", minimizado" if "--minimizado" in sys.argv else "")

janela.mainloop()