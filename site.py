# -*- coding: utf-8 -*-
# Versao web do Monitor de Investimentos.
# Mesma logica do app de desktop, via nucleo.py.
# Rodar: streamlit run site.py
import io
import os
import streamlit as st
import plotly.graph_objects as go
import yfinance as yf
import pandas as pd
from datetime import date, timedelta

# Na nuvem, a URL do Postgres vem dos Secrets do Streamlit (ou variavel DATABASE_URL).
try:
    if st.secrets.get("DATABASE_URL"):
        os.environ["DATABASE_URL"] = st.secrets["DATABASE_URL"]
except Exception:
    pass

from nucleo import *  # noqa: F401,F403,E402 - banco, cotacoes e calculos compartilhados

st.set_page_config(page_title="Monitor de Investimentos", page_icon="📈", layout="wide")
criar_tabelas()

# ---------------- Visual ----------------

st.markdown("""
<style>
.hero {
    padding: 1.2rem 1.4rem; margin-bottom: 1rem;
    background: linear-gradient(120deg, #131a2a 0%, #10233f 60%, #0d3a2e 100%);
    border: 1px solid #22304a; border-radius: 16px;
}
.hero h1 { margin: 0; font-size: 1.9rem; color: #e8eaf0; }
.hero p { margin: .3rem 0 0; color: #8ea2c0; font-size: .95rem; }
[data-testid="stMetric"] {
    background: #161b27; border: 1px solid #26304a;
    border-radius: 14px; padding: 14px 18px;
}
[data-testid="stMetric"]:hover { border-color: #4da3ff; }
[data-testid="stTabs"] button { font-size: 1rem; }
.secao { color: #8ea2c0; font-size: .85rem; text-transform: uppercase;
         letter-spacing: .12em; margin: 1.2rem 0 .4rem; }
</style>
""", unsafe_allow_html=True)

CORES = {"alta": "#34d17b", "baixa": "#ff5c6c", "linha": "#4da3ff",
         "cdi": "#34d17b", "ibov": "#ffb347", "aportes": "#8b93a8"}


def layout_dark(fig, altura=380, legenda=True):
    fig.update_layout(template="plotly_dark", height=altura,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      margin=dict(l=10, r=10, t=30, b=10), showlegend=legenda,
                      legend=dict(orientation="h", y=1.12))
    fig.update_xaxes(gridcolor="#232b3d")
    fig.update_yaxes(gridcolor="#232b3d")
    return fig

# ---------------- Login ----------------

def _secret(nome, padrao=""):
    try:
        return st.secrets.get(nome, padrao)
    except Exception:
        return padrao

ADMIN_LOGIN = _secret("admin_login", "")  # seu login vira admin do site
PIX_CHAVE = _secret("pix_chave", "")
PIX_VALOR = _secret("pix_valor", "R$ 19,90/mes")

if "usuario" not in st.session_state:
    st.markdown('<div class="hero"><h1>📈 Monitor de Investimentos</h1>'
                "<p>Carteira, renda fixa, IR e imposto — tudo num lugar so.</p></div>",
                unsafe_allow_html=True)
    aba_entrar, aba_cadastro = st.tabs(["Entrar", "Criar conta (7 dias gratis)"])
    with aba_entrar:
        with st.form("login"):
            login = st.text_input("Usuario")
            senha = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar", type="primary"):
                usuario = autenticar(login, senha)
                if usuario:
                    st.session_state["usuario"] = usuario
                    st.rerun()
                else:
                    st.error("Usuario ou senha incorretos.")
    with aba_cadastro:
        with st.form("cadastro"):
            login2 = st.text_input("Escolha um usuario")
            senha2 = st.text_input("Escolha uma senha", type="password")
            senha3 = st.text_input("Repita a senha", type="password")
            if st.form_submit_button("Criar conta"):
                if senha2 != senha3:
                    st.error("As senhas nao conferem.")
                else:
                    ok, msg = criar_usuario(login2, senha2)
                    if ok:
                        st.success(msg + " Agora entre na aba 'Entrar'.")
                    else:
                        st.error(msg)
    st.stop()

usuario = st.session_state["usuario"]
definir_usuario(usuario["login"])
criar_tabelas()  # garante estrutura e semeia a lista de mercado do usuario
eh_admin = usuario["login"] == ADMIN_LOGIN

if not conta_ativa(usuario) and not eh_admin:
    st.warning("Seu periodo de teste terminou 😢")
    st.markdown("Para continuar usando, pague a mensalidade via PIX:")
    if PIX_CHAVE:
        st.code(PIX_CHAVE)
    st.markdown("**Valor:** {} — depois envie o comprovante que sua conta e liberada em ate 24h.".format(PIX_VALOR))
    if st.button("Sair"):
        del st.session_state["usuario"]
        st.rerun()
    st.stop()

# ---------------- Dados ----------------

@st.cache_data(ttl=300)
def posicoes_agora(_usuario):
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
                       "variacao": cot["variacao"], "moeda": cot["moeda"], "valor_brl": valor,
                       "lucro_brl": valor - p["custo_brl"] + p["realizado_brl"],
                       "realizado_brl": p["realizado_brl"]})
        total += valor
        investido += p["custo_brl"]
    return {"linhas": linhas, "total": total, "investido": investido, "ops": operacoes}


@st.cache_data(ttl=300)
def renda_fixa_agora(_usuario):
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
def evolucao(_usuario):
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


@st.cache_data(ttl=120)
def mercado_agora(_usuario):
    resultado = []
    for ticker, nome in listar_mercado():
        cot = cotacao(ticker)
        resultado.append({"ticker": ticker, "nome": nome,
                          "preco": cot["preco"] if cot else None,
                          "var": cot["variacao"] if cot else None,
                          "moeda": cot["moeda"] if cot else ""})
    return resultado


@st.cache_data(ttl=3600)
def dividendos_12m(_usuario):
    operacoes = listar_operacoes()
    posicoes = calcular_posicoes(operacoes)
    linhas = []
    for ticker, p in posicoes.items():
        if p["q"] <= 0 or p["tipo"] in SEM_DIVIDENDOS:
            continue
        d = calcular_dividendos(serie_dividendos(ticker), operacoes, ticker)
        if d and d["por_acao"] > 0:
            linhas.append({"ticker": ticker, "por_acao": d["por_acao"],
                           "recebido": d["recebido"], "moeda": p["moeda"]})
    return linhas


def planilha_excel(dados, rf):
    buf = io.BytesIO()
    carteira = [{"Ticker": l["ticker"], "Tipo": l["tipo"], "Quantidade": l["qtd"],
                 "Preco medio": l["pm"], "Preco atual": l["preco"], "Moeda": l["moeda"],
                 "Valor R$": l["valor_brl"], "Lucro R$": l["lucro_brl"]}
                for l in dados["linhas"]]
    operacoes = [{"Data": o["data"], "Operacao": o["operacao"], "Ticker": o["ticker"],
                  "Quantidade": o["quantidade"], "Preco": o["preco"],
                  "Moeda": o["moeda"], "Cambio": o["cambio"]} for o in dados["ops"]]
    fixa = [{"Nome": a["nome"], "Produto": a["produto"], "Indexador": a["indexador"],
             "Taxa": a["taxa"], "Aplicado": a["valor"], "Data": a["data"],
             "Vencimento": a["vencimento"], "Bruto": a["resultado"]["bruto"],
             "IOF": a["resultado"]["iof"], "IR": a["resultado"]["imposto"],
             "Liquido": a["resultado"]["liquido"]} for a in rf]
    with pd.ExcelWriter(buf) as planilha:
        pd.DataFrame(carteira or [{}]).to_excel(planilha, sheet_name="Carteira", index=False)
        pd.DataFrame(operacoes or [{}]).to_excel(planilha, sheet_name="Operacoes", index=False)
        pd.DataFrame(fixa or [{}]).to_excel(planilha, sheet_name="Renda fixa", index=False)
    return buf.getvalue()


def registrar_operacao_site(ticker, operacao, dia, qtd, preco):
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


def fig_cotacao(ticker, periodo, operacoes):
    hist = buscar_historico(ticker, periodo)
    if hist is None:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=list(hist.index), y=list(hist["Close"]),
                             mode="lines", name=ticker,
                             line=dict(color=CORES["linha"], width=2),
                             fill="tozeroy", fillcolor="rgba(77,163,255,.07)"))
    datas = {d.date().isoformat(): i for i, d in enumerate(hist.index)}
    for op in operacoes:
        if op["ticker"] != ticker or op["data"] not in datas:
            continue
        i = datas[op["data"]]
        eh_compra = op["operacao"] == "compra"
        fig.add_trace(go.Scatter(x=[hist.index[i]], y=[hist["Close"].iloc[i]], mode="markers",
                                 name="Compra" if eh_compra else "Venda", showlegend=True,
                                 marker=dict(symbol="triangle-up" if eh_compra else "triangle-down",
                                             size=13, color=CORES["alta"] if eh_compra else CORES["baixa"],
                                             line=dict(color="#0e1117", width=1))))
    fig.update_layout(title=ticker, yaxis_title="Preco")
    return layout_dark(fig)


def fig_pizza(por_tipo):
    fig = go.Figure(go.Pie(labels=list(por_tipo), values=list(por_tipo.values()),
                           hole=0.55, textinfo="label+percent",
                           marker=dict(colors=["#4da3ff", "#34d17b", "#ffb347", "#ff5c6c",
                                               "#a06bff", "#3fd4d4", "#f76fb8", "#9ecf5c"])))
    fig.update_layout(title="Distribuicao do patrimonio")
    return layout_dark(fig, altura=400, legenda=False)


# ---------------- Interface ----------------

with st.sidebar:
    st.markdown("## 📈 Monitor")
    st.caption("Logado como **{}**".format(usuario["login"]))
    st.divider()
    if st.button("🔄 Atualizar dados", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.caption("Os precos ficam em cache por 5 minutos.")
    st.divider()
    if st.button("Sair da conta"):
        del st.session_state["usuario"]
        st.rerun()

st.markdown('<div class="hero"><h1>Monitor de Investimentos</h1>'
            '<p>Carteira, renda fixa e patrimonio em tempo real</p></div>',
            unsafe_allow_html=True)

nomes_abas = ["💼 Carteira", "🏦 Renda Fixa", "📊 Resumo", "💱 Cotação", "🧾 Imposto de Renda",
              "🌐 Mercado", "🧪 Simulador", "🔔 Alertas"]
if eh_admin:
    nomes_abas.append("⚙️ Admin")
abas = st.tabs(nomes_abas)
aba_carteira, aba_rf, aba_resumo, aba_cotacao, aba_ir, aba_mercado, aba_sim, aba_alertas = abas[:8]
aba_admin = abas[8] if eh_admin else None

dados = posicoes_agora(usuario)
rf = renda_fixa_agora(usuario)
total_rf = sum(a["resultado"]["liquido"] for a in rf if a.get("resultado"))
patrimonio = dados["total"] + total_rf
investido_total = dados["investido"] + sum(a["valor"] for a in rf)
lucro_total = patrimonio - investido_total

with aba_carteira:
    c1, c2, c3 = st.columns(3)
    c1.metric("Patrimônio total", formatar_preco(patrimonio, "BRL"))
    c2.metric("Investido", formatar_preco(investido_total, "BRL"))
    c3.metric("Lucro", formatar_preco(lucro_total, "BRL"),
              delta=formatar_var(round(lucro_total / investido_total * 100, 1)) if investido_total else None)
    st.markdown('<div class="secao">Posições</div>', unsafe_allow_html=True)
    if dados["linhas"]:
        tabela = pd.DataFrame([{"Ticker": l["ticker"], "Tipo": l["tipo"],
                                "Qtd": formatar_qtd(l["qtd"]),
                                "Preço médio": formatar_preco(l["pm"], l["moeda"]),
                                "Preço atual": formatar_preco(l["preco"], l["moeda"]),
                                "Hoje": formatar_var(l["variacao"]),
                                "Valor (R$)": formatar_br(l["valor_brl"]),
                                "Lucro (R$)": formatar_br(l["lucro_brl"])}
                               for l in dados["linhas"]])
        st.dataframe(tabela, width="stretch", hide_index=True)
    else:
        st.info("Carteira vazia ou sem conexão para buscar preços.")

    with st.expander("➕ Registrar operação"):
        with st.form("form_operacao"):
            c1, c2, c3 = st.columns(3)
            f_ticker = c1.text_input("Código (ex: PETR4.SA, AAPL, BTC-USD)").strip().upper()
            f_op = c2.selectbox("Operação", ["compra", "venda"])
            f_data = c3.date_input("Data", value=date.today())
            c4, c5 = st.columns(2)
            f_qtd = c4.text_input("Quantidade")
            f_preco = c5.text_input("Preço unitário")
            if st.form_submit_button("Registrar", width="stretch"):
                try:
                    qtd = float(f_qtd.replace(",", "."))
                    preco = float(f_preco.replace("R$", "").strip().replace(",", "."))
                except (ValueError, AttributeError):
                    st.error("Quantidade ou preço inválidos")
                else:
                    if not f_ticker or qtd <= 0 or preco <= 0 or f_data > date.today():
                        st.error("Confira o código, quantidade, preço e a data (não pode ser futura)")
                    else:
                        erro = registrar_operacao_site(f_ticker, f_op, f_data.isoformat(), qtd, preco)
                        if erro:
                            st.error(erro)
                        else:
                            st.success("Operação registrada!")
                            st.cache_data.clear()
                            st.rerun()

    with st.expander("📜 Histórico de operações"):
        ops = list(reversed(dados["ops"]))
        if ops:
            tabela_ops = pd.DataFrame([{"id": o["id"], "Data": formatar_data(o["data"]),
                                        "Operação": o["operacao"], "Ticker": o["ticker"],
                                        "Qtd": formatar_qtd(o["quantidade"]),
                                        "Preço": formatar_preco(o["preco"], o["moeda"] or "BRL")}
                                       for o in ops])
            st.dataframe(tabela_ops.drop(columns="id"), width="stretch", hide_index=True)
            apagar = st.multiselect("Excluir operação (id)", [o["id"] for o in ops])
            if st.button("Excluir selecionadas"):
                for op_id in apagar:
                    executar("DELETE FROM operacoes WHERE id = ?", (op_id,))
                st.cache_data.clear()
                st.rerun()
        else:
            st.info("Nenhuma operação registrada.")

with aba_rf:
    if rf:
        tabela_rf = pd.DataFrame([{"Nome": a["nome"], "Produto": a["produto"],
                                   "Taxa": a["taxa"], "Aplicado": formatar_preco(a["valor"], "BRL"),
                                   "Bruto": formatar_preco(a["resultado"]["bruto"], "BRL"),
                                   "IOF": formatar_preco(a["resultado"]["iof"], "BRL"),
                                   "IR": formatar_preco(a["resultado"]["imposto"], "BRL"),
                                   "Líquido": formatar_preco(a["resultado"]["liquido"], "BRL"),
                                   "Vencimento": formatar_data(a["vencimento"]) if a["vencimento"] else "-"}
                                  for a in rf])
        st.dataframe(tabela_rf, width="stretch", hide_index=True)
        limite = (date.today() + timedelta(days=7)).isoformat()
        for a in rf:
            if a["vencimento"] and a["vencimento"] <= limite:
                dias = (date.fromisoformat(a["vencimento"]) - date.today()).days
                if dias < 0:
                    st.warning("⚠️ {} venceu em {}. Líquido: {}".format(
                        a["nome"], formatar_data(a["vencimento"]),
                        formatar_preco(a["resultado"]["liquido"], "BRL")))
                else:
                    st.warning("⚠️ {} vence em {} dia(s) ({}). Líquido estimado: {}".format(
                        a["nome"], dias, formatar_data(a["vencimento"]),
                        formatar_preco(a["resultado"]["liquido"], "BRL")))
    else:
        st.info("Nenhuma aplicação de renda fixa cadastrada.")

    with st.expander("➕ Nova aplicação"):
        r_produto = st.selectbox("Produto", list(PRODUTOS_RF))
        with st.form("form_rf"):
            r_nome = st.text_input("Nome (ex: CDB Banco X)").strip()
            c3, c4 = st.columns(2)
            indexadores_ok = PRODUTOS_RF[r_produto]["indexadores"]
            r_indexador = c3.selectbox("Indexador", indexadores_ok,
                                       format_func=lambda i: INDEXADORES[i])
            r_taxa = c4.text_input("Taxa (ex: 110 para 110% do CDI, 12 para 12% a.a.)")
            c5, c6, c7 = st.columns(3)
            r_valor = c5.text_input("Valor aplicado (R$)")
            r_data = c6.date_input("Data da aplicação")
            r_venc = c7.date_input("Vencimento (opcional)", value=None)
            if st.form_submit_button("Adicionar", width="stretch"):
                try:
                    taxa = float(r_taxa.replace(",", "."))
                    valor = float(r_valor.replace("R$", "").strip().replace(",", "."))
                except (ValueError, AttributeError):
                    st.error("Taxa ou valor inválidos")
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
                            st.success("Aplicação cadastrada!")
                            st.cache_data.clear()
                            st.rerun()

with aba_resumo:
    evo = evolucao(usuario)
    if evo:
        datas = [d for d, v, i in evo["registros"]]
        valores = [v for d, v, i in evo["registros"]]
        investidos = [i for d, v, i in evo["registros"]]
        cdi_pct, ibov_pct = evo["rent"]
        fig = go.Figure()
        base = valores[0]
        fig.add_trace(go.Scatter(x=datas, y=[v / base * 100 - 100 for v in valores],
                                 name="Carteira", line=dict(color=CORES["linha"], width=2.5)))
        fig.add_trace(go.Scatter(x=datas, y=[v / investidos[0] * 100 - 100 for v in investidos],
                                 name="Aportes", line=dict(color=CORES["aportes"], dash="dash")))
        if cdi_pct:
            fig.add_trace(go.Scatter(x=datas, y=cdi_pct, name="CDI",
                                     line=dict(color=CORES["cdi"])))
        if ibov_pct:
            fig.add_trace(go.Scatter(x=datas, y=ibov_pct, name="Ibovespa",
                                     line=dict(color=CORES["ibov"])))
        fig.update_layout(title="Rentabilidade acumulada (%)", yaxis_title="%")
        st.plotly_chart(layout_dark(fig), width="stretch")
    else:
        st.info("O histórico do patrimônio ainda não tem registros (o app desktop salva um por dia).")

    por_tipo = {}
    for l in dados["linhas"]:
        por_tipo[l["tipo"]] = por_tipo.get(l["tipo"], 0) + l["valor_brl"]
    for a in rf:
        grupo = PRODUTOS_RF[a["produto"]]["grupo"]
        por_tipo[grupo] = por_tipo.get(grupo, 0) + a["resultado"]["liquido"]
    if por_tipo:
        st.plotly_chart(fig_pizza(por_tipo), width="stretch")

    meta = float(ler_config("meta_patrimonio", "0") or 0)
    st.markdown('<div class="secao">Meta de patrimônio</div>', unsafe_allow_html=True)
    c_meta1, c_meta2 = st.columns([3, 1])
    nova_meta = c_meta1.text_input("Meta (R$)", value=formatar_br(meta) if meta else "",
                                 label_visibility="collapsed", placeholder="Defina sua meta em R$")
    if c_meta2.button("Definir meta", width="stretch"):
        try:
            salvar_config("meta_patrimonio", str(float(
                nova_meta.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
                if "," in nova_meta else nova_meta.replace("R$", "").strip())))
            st.cache_data.clear()
            st.rerun()
        except (ValueError, AttributeError):
            st.error("Meta inválida")
    if meta > 0:
        pct = min(patrimonio / meta * 100, 100)
        st.progress(pct / 100)
        st.caption("Meta: {} — {:.1f}% atingido".format(formatar_preco(meta, "BRL"), pct))

    st.markdown('<div class="secao">Rebalanceamento</div>', unsafe_allow_html=True)
    metas = metas_distribuicao()
    c_r1, c_r2, c_r3 = st.columns([2, 1, 1])
    grupos = sorted(set(list(por_tipo) + list(metas)))
    grupo_sel = c_r1.selectbox("Tipo", grupos, key="reb_grupo") if grupos else None
    pct_sel = c_r2.text_input("Meta % (0 remove)", key="reb_pct")
    if c_r3.button("Salvar meta %", width="stretch") and grupo_sel:
        try:
            pct = float(pct_sel.replace(",", "."))
        except (ValueError, AttributeError):
            st.error("Percentual inválido")
        else:
            if pct <= 0:
                metas.pop(grupo_sel, None)
            else:
                metas[grupo_sel] = pct
            salvar_config("meta_distribuicao", json.dumps(metas))
            st.rerun()
    if metas:
        linhas_reb = []
        for nome in sorted(set(list(por_tipo) + list(metas))):
            atual = por_tipo.get(nome, 0) / patrimonio * 100 if patrimonio else 0
            m = metas.get(nome)
            dif = "-"
            if m is not None:
                dif_brl = (m - atual) / 100 * patrimonio
                dif = "equilibrado" if abs(dif_brl) < 1 else \
                    ("comprar ~" if dif_brl > 0 else "vender ~") + formatar_preco(abs(dif_brl), "BRL")
            linhas_reb.append({"Tipo": nome, "Atual": formatar_br(round(atual, 1)) + "%",
                               "Meta": "-" if m is None else formatar_br(m) + "%",
                               "Diferença": dif})
        st.dataframe(pd.DataFrame(linhas_reb), width="stretch", hide_index=True)

    st.markdown('<div class="secao">Dividendos (12 meses)</div>', unsafe_allow_html=True)
    divs = dividendos_12m(usuario)
    if divs:
        st.dataframe(pd.DataFrame([{"Ativo": d["ticker"],
                                    "Por cota": formatar_preco(d["por_acao"], d["moeda"]),
                                    "Recebido (est.)": formatar_preco(d["recebido"], d["moeda"])}
                                   for d in divs]), width="stretch", hide_index=True)
        st.caption("Só conta quem tinha o ativo antes da data-com. Valores antes de impostos.")
    else:
        st.info("Nenhum dividendo no período.")

    st.divider()
    st.download_button("⬇️ Baixar planilha Excel", planilha_excel(dados, rf),
                       "minha_carteira.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with aba_cotacao:
    tickers = sorted({op["ticker"] for op in dados["ops"]})
    for t in ["PETR4.SA", "BTC-USD", "^BVSP"]:
        if t not in tickers:
            tickers.append(t)
    c1, c2 = st.columns([2, 1])
    escolhido = c1.selectbox("Ativo", tickers)
    periodo = c2.selectbox("Período", list(PERIODOS), index=3)
    cot = cotacao(escolhido)
    if cot:
        st.metric(escolhido, formatar_preco(cot["preco"], cot["moeda"]),
                  delta=formatar_var(cot["variacao"]))
    fig3 = fig_cotacao(escolhido, PERIODOS[periodo], dados["ops"])
    if fig3:
        st.plotly_chart(fig3, width="stretch")
    else:
        st.warning("Sem histórico para " + escolhido)

with aba_ir:
    st.caption("Estimativa simplificada: não considera day trade, prejuízo acumulado nem operações fora do app.")
    meses = apuracao_mensal(dados["ops"])
    if meses:
        linhas_ir = [{"Mês": m["mes"], "Tipo": NOME_TIPO.get(d["tipo"], d["tipo"]),
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
    linhas_m = mercado_agora(usuario)
    tabela_m = pd.DataFrame([{"Ativo": m["nome"], "Ticker": m["ticker"],
                              "Preço": formatar_preco(m["preco"], m["moeda"]) if m["preco"] else "-",
                              "Variação": formatar_var(m["var"])}
                             for m in linhas_m])
    st.dataframe(tabela_m, width="stretch", hide_index=True)
    escolha_m = st.selectbox("Ver gráfico", [m["ticker"] for m in linhas_m],
                             format_func=lambda t: next(m["nome"] for m in linhas_m if m["ticker"] == t))
    if escolha_m:
        fig_m = fig_cotacao(escolha_m, "3mo", dados["ops"])
        if fig_m:
            st.plotly_chart(fig_m, width="stretch")

with aba_sim:
    st.subheader("E se eu tivesse investido...")
    c1, c2, c3 = st.columns(3)
    s_ticker = c1.text_input("Código", "PETR4.SA").strip().upper()
    s_valor = c2.text_input("Valor (R$)", "10.000")
    s_data = c3.date_input("Na data", value=date(date.today().year - 1, 1, 2))
    if st.button("Simular", width="stretch"):
        try:
            valor = float(s_valor.replace("R$", "").strip().replace(",", "."))
        except (ValueError, AttributeError):
            st.error("Valor inválido")
        else:
            if s_data >= date.today() or valor <= 0 or not s_ticker:
                st.error("Confira o código, o valor e uma data passada")
            else:
                with st.spinner("Buscando histórico..."):
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
                                  delta=formatar_var(round((hoje_ativo / valor - 1) * 100, 1)))
                        m2.metric("No CDI hoje", formatar_preco(hoje_cdi, "BRL"),
                                  delta=formatar_var(round((hoje_cdi / valor - 1) * 100, 1)))
                        st.caption("Sem dividendos nem câmbio para ativos em outra moeda. Passado não prevê o futuro.")

with aba_alertas:
    st.caption("Os alertas disparam no app de desktop (ele precisa estar aberto). Aqui você cria e remove.")
    with st.form("form_alerta"):
        a1, a2, a3 = st.columns(3)
        al_ticker = a1.text_input("Código (ex: PETR4.SA)").strip().upper()
        al_preco = a2.text_input("Preço alvo")
        al_tipo = a3.selectbox("Disparar quando", ["acima", "abaixo"])
        if st.form_submit_button("Criar alerta", width="stretch"):
            try:
                pa = float(al_preco.replace(",", "."))
            except (ValueError, AttributeError):
                st.error("Preço alvo inválido")
            else:
                if not al_ticker or pa <= 0:
                    st.error("Confira o código e o preço alvo")
                else:
                    cot = cotacao(al_ticker)
                    if cot is None:
                        st.error(motivo_falha(al_ticker))
                    else:
                        executar("INSERT INTO alertas (ticker, preco_alvo, tipo, ativo) VALUES (?, ?, ?, 1)",
                                 (al_ticker, pa, al_tipo))
                        st.success("Alerta criado!")
                        st.rerun()

    alertas = listar_alertas()
    if alertas:
        linhas_al = [{"id": a[0], "Ativo": a[1],
                      "Alvo": formatar_br(a[2]),
                      "Dispara": "acima de" if a[3] == "acima" else "abaixo de",
                      "Status": "ativo" if a[4] else "disparado"}
                     for a in alertas]
        st.dataframe(pd.DataFrame(linhas_al).drop(columns="id"), width="stretch", hide_index=True)
        remover = st.multiselect("Remover alerta (id)", [a[0] for a in alertas])
        if st.button("Remover selecionados"):
            for al_id in remover:
                executar("DELETE FROM alertas WHERE id = ?", (al_id,))
            st.rerun()
    else:
        st.info("Nenhum alerta cadastrado.")


if aba_admin is not None:
    with aba_admin:
        st.markdown('<div class="secao">Usuarios cadastrados</div>', unsafe_allow_html=True)
        usuarios = listar_usuarios()
        if usuarios:
            hoje = date.today().isoformat()
            linhas_u = []
            for login, criado, ativo_ate in usuarios:
                status = "admin" if login == ADMIN_LOGIN else ("ativo" if ativo_ate and ativo_ate >= hoje else "vencido")
                linhas_u.append({"Login": login, "Criado em": criado or "-",
                                 "Acesso ate": formatar_data(ativo_ate) if ativo_ate else "-",
                                 "Status": status})
            st.dataframe(pd.DataFrame(linhas_u), width="stretch", hide_index=True)
            st.markdown('<div class="secao">Renovar acesso (apos PIX)</div>', unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            alvo = c1.selectbox("Usuario", [u[0] for u in usuarios])
            dias = c2.number_input("Dias", min_value=1, value=30)
            if st.button("Ativar/Renovar"):
                nova = renovar_usuario(alvo, dias)
                st.success("{} liberado ate {}.".format(alvo, formatar_data(nova)))
        else:
            st.info("Nenhum usuario cadastrado ainda.")
