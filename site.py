# -*- coding: utf-8 -*-
# Versao web (leitura) do Monitor de Investimentos.
# Mesma logica do app de desktop, via nucleo.py.
# Rodar: streamlit run site.py
import streamlit as st
import matplotlib.pyplot as plt
import yfinance as yf
import pandas as pd
from datetime import date, timedelta
from nucleo import *  # noqa: F401,F403 - banco, cotacoes e calculos compartilhados

st.set_page_config(page_title="Monitor de Investimentos", page_icon="📈", layout="wide")
criar_tabelas()

# Senha: variavel de ambiente SITE_SENHA, secrets do Streamlit ou config "senha_site".
senha_certa = os.environ.get("SITE_SENHA", "")
if not senha_certa:
    try:
        senha_certa = st.secrets.get("senha", "")
    except Exception:
        pass
if not senha_certa:
    senha_certa = ler_config("senha_site", "")
if senha_certa:
    if st.text_input("Senha de acesso", type="password") != senha_certa:
        st.stop()


@st.cache_data(ttl=300)
def posicoes_agora():
    operacoes = listar_operacoes()
    posicoes = calcular_posicoes(operacoes)
    linhas, total, investido = [], 0.0, 0.0
    for ticker, p in posicoes.items():
        if p["q"] <= 0:
            continue
        cot = cotacao(ticker)
        if cot is None:
            continue
        taxa = taxa_para_real(cot["moeda"]) or 1.0
        valor = p["q"] * cot["preco"] * taxa
        linhas.append({"ticker": ticker, "tipo": NOME_TIPO.get(p["tipo"], p["tipo"]),
                       "qtd": p["q"], "pm": p["custo"] / p["q"], "preco": cot["preco"],
                       "moeda": cot["moeda"], "valor_brl": valor,
                       "lucro_brl": valor - p["custo_brl"] + p["realizado_brl"],
                       "realizado_brl": p["realizado_brl"]})
        total += valor
        investido += p["custo_brl"]
    return {"linhas": linhas, "total": total, "investido": investido,
            "ops": operacoes}


@st.cache_data(ttl=300)
def renda_fixa_agora():
    aplicacoes = listar_renda_fixa()
    if not aplicacoes:
        return []
    inicio = date.fromisoformat(min(a["data"] for a in aplicacoes))
    indices = {}
    for nome in {"cdi"} | {a["indexador"] for a in aplicacoes if a["indexador"] != "pre"}:
        indices[nome], _ = obter_indices(nome, inicio)
    for a in aplicacoes:
        a["resultado"] = calcular_renda_fixa(a, indices, date.today().isoformat())
    return aplicacoes


@st.cache_data(ttl=600)
def evolucao():
    registros = consultar("SELECT data, valor, investido FROM historico_patrimonio ORDER BY data")
    if not registros:
        return None
    inicio = date.fromisoformat(registros[0][0]) - timedelta(days=10)
    cdi, _ = obter_indices("cdi", inicio)
    ibov = []
    try:
        hist = yf.Ticker("^BVSP").history(start=inicio.isoformat())
        ibov = [(d.date().isoformat(), float(v)) for d, v in hist["Close"].items()]
    except Exception:
        pass
    return {"registros": registros, "rent": rentabilidades_benchmark(registros, cdi, ibov)}


def registrar_operacao_site(ticker, operacao, dia, qtd, preco):
    """Mesmas regras do app: ticker tem que existir, venda nao pode passar da posicao.
    Devolve mensagem de erro (str) ou None em caso de sucesso."""
    cot = cotacao(ticker)
    if cot is None:
        return motivo_falha(ticker)
    moeda = cot["moeda"] or "BRL"
    tipo = sugerir_tipo(ticker)
    cambio = None
    if moeda != "BRL":
        cambio = cambio_na_data(moeda, dia)
        if cambio is None:
            return "Nao achei o cambio de {} em {}.".format(moeda, formatar_data(dia))
    nova = {"id": 10**12, "ticker": ticker, "tipo_ativo": tipo, "operacao": operacao,
            "data": dia, "quantidade": qtd, "preco": preco, "moeda": moeda,
            "cambio": cambio, "importada": 0}
    try:
        calcular_posicoes(listar_operacoes() + [nova])
    except ValueError as e:
        return str(e)
    executar("INSERT INTO operacoes (ticker, tipo_ativo, operacao, data, quantidade, preco, moeda, cambio, importada)"
             " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)",
             (ticker, tipo, operacao, dia, qtd, preco, moeda, cambio))
    return None


def registrar_renda_fixa_site(nome, produto, indexador, taxa, valor, data_ini, vencimento):
    if indexador not in PRODUTOS_RF[produto]["indexadores"]:
        return "Esse produto nao aceita o indexador " + indexador
    executar("INSERT INTO renda_fixa (nome, produto, indexador, taxa, valor, data, vencimento)"
             " VALUES (?, ?, ?, ?, ?, ?, ?)",
             (nome, produto, indexador, taxa, valor, data_ini, vencimento or None))
    return None


def grafico_operacoes(ticker, periodo, operacoes):
    hist = buscar_historico(ticker, periodo)
    if hist is None:
        return None
    fig, ax = plt.subplots(figsize=(9, 4))
    datas = [d.date().isoformat() for d in hist.index]
    ax.plot(datas, hist["Close"], color="#2196f3")
    posicoes_x = {d: i for i, d in enumerate(datas)}
    for op in operacoes:
        if op["ticker"] != ticker or op["data"] not in posicoes_x:
            continue
        i = posicoes_x[op["data"]]
        marca, cor = ("^", "#2e9e44") if op["operacao"] == "compra" else ("v", "#e53935")
        ax.scatter(datas[i], hist["Close"].iloc[i], marker=marca, color=cor, s=90, zorder=5)
    ax.set_title(ticker)
    ax.tick_params(axis="x", rotation=45, labelsize=8)
    fig.tight_layout()
    return fig


titulo, atualizar = st.columns([5, 1])
titulo.title("Monitor de Investimentos")
if atualizar.button("Atualizar dados"):
    st.cache_data.clear()
    st.rerun()

aba_carteira, aba_rf, aba_resumo, aba_cotacao, aba_ir, aba_mercado, aba_sim = st.tabs(
    ["Carteira", "Renda Fixa", "Resumo", "Cotacao", "Imposto de Renda", "Mercado", "Simulador"])

dados = posicoes_agora()
rf = renda_fixa_agora()
total_rf = sum(a["resultado"]["liquido"] for a in rf if a.get("resultado"))
patrimonio = dados["total"] + total_rf

with aba_carteira:
    c1, c2, c3 = st.columns(3)
    c1.metric("Patrimonio total", formatar_preco(patrimonio, "BRL"))
    c2.metric("Investido", formatar_preco(dados["investido"] + sum(
        a["valor"] for a in rf), "BRL"))
    c3.metric("Lucro", formatar_preco(patrimonio - dados["investido"] - sum(
        a["valor"] for a in rf), "BRL"))
    if dados["linhas"]:
        tabela = pd.DataFrame([{"Ticker": l["ticker"], "Tipo": l["tipo"],
                                "Qtd": formatar_qtd(l["qtd"]),
                                "Preco medio": formatar_preco(l["pm"], l["moeda"]),
                                "Preco atual": formatar_preco(l["preco"], l["moeda"]),
                                "Valor (R$)": formatar_br(l["valor_brl"]),
                                "Lucro (R$)": formatar_br(l["lucro_brl"])}
                               for l in dados["linhas"]])
        st.dataframe(tabela, width="stretch", hide_index=True)
    else:
        st.info("Carteira vazia ou sem conexao para buscar precos.")

    with st.expander("Registrar operacao"):
        with st.form("form_operacao"):
            c1, c2, c3 = st.columns(3)
            f_ticker = c1.text_input("Codigo (ex: PETR4.SA, AAPL, BTC-USD)").strip().upper()
            f_op = c2.selectbox("Operacao", ["compra", "venda"])
            f_data = c3.date_input("Data", value=date.today())
            c4, c5 = st.columns(2)
            f_qtd = c4.text_input("Quantidade")
            f_preco = c5.text_input("Preco unitario")
            if st.form_submit_button("Registrar"):
                try:
                    qtd = float(f_qtd.replace(",", "."))
                    preco = float(f_preco.replace("R$", "").strip().replace(",", "."))
                except (ValueError, AttributeError):
                    st.error("Quantidade ou preco invalidos")
                else:
                    if not f_ticker or qtd <= 0 or preco <= 0 or f_data > date.today():
                        st.error("Confira o codigo, quantidade, preco e a data (nao pode ser futura)")
                    else:
                        erro = registrar_operacao_site(f_ticker, f_op, f_data.isoformat(), qtd, preco)
                        if erro:
                            st.error(erro)
                        else:
                            st.success("Operacao registrada!")
                            st.cache_data.clear()
                            st.rerun()

    with st.expander("Historico de operacoes"):
        ops = list(reversed(dados["ops"]))
        if ops:
            tabela_ops = pd.DataFrame([{"id": o["id"], "Data": formatar_data(o["data"]),
                                        "Operacao": o["operacao"], "Ticker": o["ticker"],
                                        "Qtd": formatar_qtd(o["quantidade"]),
                                        "Preco": formatar_preco(o["preco"], o["moeda"] or "BRL")}
                                       for o in ops])
            st.dataframe(tabela_ops.drop(columns="id"), width="stretch", hide_index=True)
            apagar = st.multiselect("Excluir operacao (id)", [o["id"] for o in ops])
            if st.button("Excluir selecionadas"):
                for op_id in apagar:
                    executar("DELETE FROM operacoes WHERE id = ?", (op_id,))
                st.cache_data.clear()
                st.rerun()
        else:
            st.info("Nenhuma operacao registrada.")

with aba_rf:
    if rf:
        tabela_rf = pd.DataFrame([{"Nome": a["nome"], "Produto": a["produto"],
                                   "Taxa": a["taxa"], "Aplicado": formatar_preco(a["valor"], "BRL"),
                                   "Bruto": formatar_preco(a["resultado"]["bruto"], "BRL"),
                                   "IOF": formatar_preco(a["resultado"]["iof"], "BRL"),
                                   "IR": formatar_preco(a["resultado"]["imposto"], "BRL"),
                                   "Liquido": formatar_preco(a["resultado"]["liquido"], "BRL"),
                                   "Vencimento": formatar_data(a["vencimento"]) if a["vencimento"] else "-"}
                                  for a in rf])
        st.dataframe(tabela_rf, width="stretch", hide_index=True)
    else:
        st.info("Nenhuma aplicacao de renda fixa cadastrada.")

    with st.expander("Nova aplicacao"):
        r_produto = st.selectbox("Produto", list(PRODUTOS_RF))
        with st.form("form_rf"):
            c1, c2 = st.columns(2)
            r_nome = c1.text_input("Nome (ex: CDB Banco X)").strip()
            c3, c4 = st.columns(2)
            indexadores_ok = PRODUTOS_RF[r_produto]["indexadores"]
            r_indexador = c3.selectbox("Indexador", indexadores_ok,
                                       format_func=lambda i: INDEXADORES[i])
            r_taxa = c4.text_input("Taxa (ex: 110 para 110% do CDI, 12 para 12% a.a.)")
            c5, c6, c7 = st.columns(3)
            r_valor = c5.text_input("Valor aplicado (R$)")
            r_data = c6.date_input("Data da aplicacao")
            r_venc = c7.date_input("Vencimento (opcional)", value=None)
            if st.form_submit_button("Adicionar"):
                try:
                    taxa = float(r_taxa.replace(",", "."))
                    valor = float(r_valor.replace("R$", "").strip().replace(",", "."))
                except (ValueError, AttributeError):
                    st.error("Taxa ou valor invalidos")
                else:
                    if not r_nome or valor <= 0 or r_data > date.today():
                        st.error("Confira o nome, o valor e a data")
                    else:
                        erro = registrar_renda_fixa_site(
                            r_nome, r_produto, r_indexador, taxa, valor,
                            r_data.isoformat(),
                            r_venc.isoformat() if r_venc else None)
                        if erro:
                            st.error(erro)
                        else:
                            st.success("Aplicacao cadastrada!")
                            st.cache_data.clear()
                            st.rerun()

with aba_resumo:
    evo = evolucao()
    if evo:
        datas = [d for d, v, i in evo["registros"]]
        valores = [v for d, v, i in evo["registros"]]
        investidos = [i for d, v, i in evo["registros"]]
        cdi_pct, ibov_pct = evo["rent"]
        fig, ax = plt.subplots(figsize=(9, 4))
        base = valores[0]
        ax.plot(datas, [v / base * 100 - 100 for v in valores], label="Carteira", color="#2196f3")
        ax.plot(datas, [v / investidos[0] * 100 - 100 for v in investidos], label="Aportes", color="#999999", linestyle="--")
        if cdi_pct:
            ax.plot(datas, cdi_pct, label="CDI", color="#2e9e44")
        if ibov_pct:
            ax.plot(datas, ibov_pct, label="Ibovespa", color="#e53935")
        ax.set_ylabel("Rentabilidade (%)")
        ax.legend()
        ax.tick_params(axis="x", rotation=45, labelsize=8)
        fig.tight_layout()
        st.pyplot(fig)
    else:
        st.info("O historico do patrimonio ainda nao tem registros (o app desktop salva um por dia).")

    if dados["linhas"]:
        por_tipo = {}
        for l in dados["linhas"]:
            por_tipo[l["tipo"]] = por_tipo.get(l["tipo"], 0) + l["valor_brl"]
        for a in rf:
            grupo = PRODUTOS_RF[a["produto"]]["grupo"]
            por_tipo[grupo] = por_tipo.get(grupo, 0) + a["resultado"]["liquido"]
        fig2, ax2 = plt.subplots(figsize=(5, 4))
        ax2.pie(list(por_tipo.values()), labels=list(por_tipo.keys()), autopct="%1.1f%%")
        st.pyplot(fig2)

with aba_cotacao:
    tickers = sorted({op["ticker"] for op in dados["ops"]}) or ["PETR4.SA"]
    c1, c2 = st.columns(2)
    escolhido = c1.selectbox("Ativo", tickers + [t for t in ["PETR4.SA", "BTC-USD"] if t not in tickers])
    periodo = c2.selectbox("Periodo", list(PERIODOS), index=3)
    cot = cotacao(escolhido)
    if cot:
        st.metric(escolhido, formatar_preco(cot["preco"], cot["moeda"]),
                  formatar_var(cot["variacao"]))
    fig3 = grafico_operacoes(escolhido, PERIODOS[periodo], dados["ops"])
    if fig3:
        st.pyplot(fig3)
    else:
        st.warning("Sem historico para " + escolhido)

with aba_ir:
    st.caption("Estimativa simplificada: nao considera day trade, prejuizo acumulado nem operacoes fora do app.")
    meses = apuracao_mensal(dados["ops"])
    if meses:
        linhas_ir = [{"Mes": m["mes"], "Tipo": NOME_TIPO.get(d["tipo"], d["tipo"]),
                      "Vendas (R$)": formatar_br(d["vendido"]),
                      "Lucro (R$)": formatar_br(d["lucro"]),
                      "Regra": d["regra"], "IR (R$)": formatar_br(d["imposto"])}
                     for m in meses for d in m["detalhe"]]
        st.dataframe(pd.DataFrame(linhas_ir), width="stretch", hide_index=True)
        st.metric("IR estimado acumulado",
                  formatar_preco(sum(m["ir_estimado"] for m in meses), "BRL"))
    else:
        st.info("Nenhuma venda registrada ainda.")

with aba_mercado:
    @st.cache_data(ttl=120)
    def mercado_agora():
        resultado = []
        for ticker, nome in listar_mercado():
            cot = cotacao(ticker)
            resultado.append({"ticker": ticker, "nome": nome,
                              "preco": cot["preco"] if cot else None,
                              "var": cot["variacao"] if cot else None,
                              "moeda": cot["moeda"] if cot else ""})
        return resultado

    linhas_m = mercado_agora()
    tabela_m = pd.DataFrame([{"Ativo": m["nome"], "Ticker": m["ticker"],
                              "Preco": formatar_preco(m["preco"], m["moeda"]) if m["preco"] else "-",
                              "Variacao": formatar_var(m["var"])}
                             for m in linhas_m])
    st.dataframe(tabela_m, width="stretch", hide_index=True)
    escolha_m = st.selectbox("Ver grafico", [m["ticker"] for m in linhas_m],
                             format_func=lambda t: next(m["nome"] for m in linhas_m if m["ticker"] == t))
    if escolha_m:
        fig_m = grafico_operacoes(escolha_m, "3mo", dados["ops"])
        if fig_m:
            st.pyplot(fig_m)

with aba_sim:
    st.subheader("E se eu tivesse investido...")
    c1, c2, c3 = st.columns(3)
    s_ticker = c1.text_input("Codigo", "PETR4.SA").strip().upper()
    s_valor = c2.text_input("Valor (R$)", "10.000")
    s_data = c3.date_input("Na data", value=date(date.today().year - 1, 1, 2))
    if st.button("Simular"):
        try:
            valor = float(s_valor.replace("R$", "").strip().replace(",", "."))
        except (ValueError, AttributeError):
            st.error("Valor invalido")
        else:
            if s_data >= date.today() or valor <= 0 or not s_ticker:
                st.error("Confira o codigo, o valor e uma data passada")
            else:
                with st.spinner("Buscando historico..."):
                    hist = buscar_historico_desde(s_ticker, s_data.isoformat())
                if hist is None:
                    st.error(motivo_falha(s_ticker))
                else:
                    closes = hist["Close"].dropna()
                    if len(closes) < 2:
                        st.error("Dados insuficientes depois dessa data")
                    else:
                        cot = cotacao(s_ticker)
                        moeda = cot["moeda"] if cot else "BRL"
                        hoje_ativo = valor / float(closes.iloc[0]) * float(closes.iloc[-1])
                        cdi, _ = obter_indices("cdi", s_data)
                        fator = 1.0
                        for d, v, f in cdi:
                            if s_data.isoformat() <= d:
                                fator *= 1 + v / 100
                        hoje_cdi = valor * fator
                        m1, m2 = st.columns(2)
                        m1.metric("Em " + s_ticker + " hoje", formatar_preco(hoje_ativo, moeda),
                                  formatar_var(round((hoje_ativo / valor - 1) * 100, 1)))
                        m2.metric("No CDI hoje", formatar_preco(hoje_cdi, "BRL"),
                                  formatar_var(round((hoje_cdi / valor - 1) * 100, 1)))
                        st.caption("Sem dividendos nem cambio para ativos em outra moeda. Passado nao preve o futuro.")
