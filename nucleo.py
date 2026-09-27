# -*- coding: utf-8 -*-
# Nucleo compartilhado: banco de dados, cotacoes e calculos.
# Usado pelo app de desktop (app.py) e pelo site (site.py).
import json
import re
import sqlite3
import yfinance as yf
import pandas as pd
import os
import sys
import shutil
import socket
import time
import logging
import requests
from logging.handlers import RotatingFileHandler
from datetime import datetime, date, timedelta

if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

pasta_dados = os.path.join(os.environ.get("APPDATA", base_dir), "MonitorInvestimentos")
os.makedirs(pasta_dados, exist_ok=True)
caminho_banco = os.path.join(pasta_dados, "investimentos.db")
banco_antigo = os.path.join(base_dir, "investimentos.db")
if not os.path.exists(caminho_banco) and os.path.exists(banco_antigo):
    shutil.copy(banco_antigo, caminho_banco)

log = logging.getLogger("monitor")
log.setLevel(logging.INFO)
_handler = RotatingFileHandler(os.path.join(pasta_dados, "monitor.log"), maxBytes=500_000, backupCount=1, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_handler)

# ---------------- Banco: SQLite local ou Postgres/Supabase ----------------
# Para usar a nuvem: defina a variavel DATABASE_URL, ou salve a URL de conexao
# num arquivo nuvem.txt dentro de %APPDATA%\MonitorInvestimentos.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    for _cand in (os.path.join(pasta_dados, "nuvem.txt"), os.path.join(base_dir, "nuvem.txt")):
        if os.path.exists(_cand):
            DATABASE_URL = open(_cand, encoding="utf-8").read().strip()
            break
USAR_PG = DATABASE_URL.startswith("postgres")
if USAR_PG:
    import psycopg2

_ON_CONFLICT = {"config": ["chave"], "historico_patrimonio": ["data"], "indices": ["serie", "data"]}


def adaptar_sql(sql):
    """Traduz SQL escrito para SQLite para Postgres quando USAR_PG esta ligado."""
    if not USAR_PG:
        return sql
    s = sql
    m = re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]*)\)", s, re.I)
    if m:
        tabela = m.group(1)
        cols = [c.strip() for c in m.group(2).split(",")]
        pk = _ON_CONFLICT.get(tabela, [])
        s = re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO", "INSERT INTO", s, flags=re.I)
        updates = [c for c in cols if c not in pk]
        if pk and updates:
            s += " ON CONFLICT ({}) DO UPDATE SET {}".format(
                ", ".join(pk), ", ".join(c + " = EXCLUDED." + c for c in updates))
        elif pk:
            s += " ON CONFLICT ({}) DO NOTHING".format(", ".join(pk))
        else:
            s += " ON CONFLICT DO NOTHING"
    return s.replace("?", "%s")


SIMBOLOS = {"BRL": "R$", "USD": "US$", "EUR": "EUR", "GBP": "GBP"}

MERCADO_PADRAO = [
    ("^BVSP", "Ibovespa"),
    ("BRL=X", "Dolar"),
    ("EURBRL=X", "Euro"),
    ("^GSPC", "S&P 500 (EUA)"),
    ("BTC-USD", "Bitcoin"),
    ("ETH-USD", "Ethereum"),
    ("PETR4.SA", "Petrobras"),
    ("VALE3.SA", "Vale"),
    ("ITUB4.SA", "Itau"),
    ("BBDC4.SA", "Bradesco"),
    ("BBAS3.SA", "Banco do Brasil"),
    ("WEGE3.SA", "WEG"),
]

TIPOS = [("acao", "Acao"), ("fii", "FII"), ("etf", "ETF"), ("bdr", "BDR"), ("exterior", "Exterior"), ("ouro", "Ouro"), ("cripto", "Cripto")]
NOME_TIPO = dict(TIPOS)
CODIGO_TIPO = {nome: codigo for codigo, nome in TIPOS}
SEM_DIVIDENDOS = ("cripto", "ouro")
ETFS_CONHECIDOS = {"BOVA11", "BOVV11", "IVVB11", "SPXI11", "SMAL11", "HASH11", "NASD11", "DIVO11", "PIBB11", "XINA11", "ECOO11", "IMAB11", "FIXA11", "BBSD11"}
UNITS_CONHECIDAS = {"TAEE11", "SANB11", "KLBN11", "ALUP11", "BPAC11", "ENGI11", "SAPR11", "IGTI11", "RNEW11", "BRBI11", "AESB11"}

PRODUTOS_RF = {
    "CDB": {"indexadores": ["pre", "cdi", "ipca"], "isento": False, "grupo": "CDB"},
    "LCI": {"indexadores": ["pre", "cdi", "ipca"], "isento": True, "grupo": "LCI/LCA"},
    "LCA": {"indexadores": ["pre", "cdi", "ipca"], "isento": True, "grupo": "LCI/LCA"},
    "Tesouro Selic": {"indexadores": ["selic"], "isento": False, "grupo": "Tesouro Direto"},
    "Tesouro Prefixado": {"indexadores": ["pre"], "isento": False, "grupo": "Tesouro Direto"},
    "Tesouro IPCA+": {"indexadores": ["ipca"], "isento": False, "grupo": "Tesouro Direto"},
    "Poupanca": {"indexadores": ["poupanca"], "isento": True, "grupo": "Poupanca"},
}
INDEXADORES = {"pre": "Prefixado (% ao ano)", "cdi": "% do CDI", "selic": "% da Selic", "ipca": "IPCA + (% ao ano)", "poupanca": "Rendimento da poupanca"}
CODIGO_INDEXADOR = {nome: codigo for codigo, nome in INDEXADORES.items()}
SERIES_BCB = {"cdi": 12, "selic": 11, "ipca": 433, "poupanca": 195}

# IOF regressivo sobre o rendimento, para resgates com menos de 30 dias (dias 1 a 29)
IOF_REGRESSIVO = [96, 93, 90, 86, 83, 80, 76, 73, 70, 66, 63, 60, 56, 53, 50,
                  46, 43, 40, 36, 33, 30, 26, 23, 20, 16, 13, 10, 6, 3]

PERIODOS = {"1 semana": "5d", "1 mes": "1mo", "3 meses": "3mo", "6 meses": "6mo", "1 ano": "1y", "5 anos": "5y"}


# ---------------- Banco de dados ----------------

def conectar():
    if USAR_PG:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect(caminho_banco)

def _traduzir_ddl(sql):
    if USAR_PG:
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                          "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
    return sql

def _exec(conexao, sql, parametros=()):
    sql = _traduzir_ddl(adaptar_sql(sql))
    if USAR_PG:
        cur = conexao.cursor()
        cur.execute(sql, parametros)
        return cur
    return conexao.execute(sql, parametros)

def _executemany(conexao, sql, linhas):
    sql = adaptar_sql(sql)
    if USAR_PG:
        conexao.cursor().executemany(sql, linhas)
    else:
        conexao.executemany(sql, linhas)

def criar_tabelas():
    conexao = conectar()
    _exec(conexao, '''
        CREATE TABLE IF NOT EXISTS carteira (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            quantidade REAL,
            preco_medio REAL,
            tipo TEXT
        )
    ''')
    _exec(conexao, '''
        CREATE TABLE IF NOT EXISTS alertas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            preco_alvo REAL,
            tipo TEXT,
            ativo INTEGER
        )
    ''')
    _exec(conexao, '''
        CREATE TABLE IF NOT EXISTS mercado_lista (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE,
            nome TEXT
        )
    ''')
    _exec(conexao, '''
        CREATE TABLE IF NOT EXISTS historico_patrimonio (
            data TEXT PRIMARY KEY,
            valor REAL,
            investido REAL
        )
    ''')
    _exec(conexao, "CREATE TABLE IF NOT EXISTS config (chave TEXT PRIMARY KEY, valor TEXT)")
    if USAR_PG:
        colunas = [c[0] for c in _exec(conexao,
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'carteira'").fetchall()]
    else:
        colunas = [c[1] for c in _exec(conexao, "PRAGMA table_info(carteira)").fetchall()]
    if "moeda" not in colunas:
        _exec(conexao, "ALTER TABLE carteira ADD COLUMN moeda TEXT")
    if _exec(conexao, "SELECT COUNT(*) FROM mercado_lista").fetchone()[0] == 0:
        _executemany(conexao, "INSERT INTO mercado_lista (ticker, nome) VALUES (?, ?)", MERCADO_PADRAO)
    _exec(conexao, '''
        CREATE TABLE IF NOT EXISTS operacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            tipo_ativo TEXT,
            operacao TEXT,
            data TEXT,
            quantidade REAL,
            preco REAL,
            moeda TEXT,
            cambio REAL,
            importada INTEGER DEFAULT 0
        )
    ''')
    if not _exec(conexao, "SELECT valor FROM config WHERE chave = 'migrou_operacoes'").fetchone():
        hoje = date.today().isoformat()
        for ticker, q, pm, tipo, moeda in _exec(conexao, "SELECT ticker, quantidade, preco_medio, tipo, moeda FROM carteira").fetchall():
            _exec(conexao, "INSERT INTO operacoes (ticker, tipo_ativo, operacao, data, quantidade, preco, moeda, cambio, importada) VALUES (?, ?, 'compra', ?, ?, ?, ?, NULL, 1)",
                  (ticker, tipo, hoje, q, pm, moeda))
        _exec(conexao, "INSERT INTO config (chave, valor) VALUES ('migrou_operacoes', '1')")
    if not _exec(conexao, "SELECT valor FROM config WHERE chave = 'migrou_tipos'").fetchone():
        for id_, ticker in _exec(conexao, "SELECT id, ticker FROM operacoes WHERE tipo_ativo = 'acao'").fetchall():
            _exec(conexao, "UPDATE operacoes SET tipo_ativo = ? WHERE id = ?", (sugerir_tipo(ticker), id_))
        _exec(conexao, "INSERT INTO config (chave, valor) VALUES ('migrou_tipos', '1')")
    _exec(conexao, '''
        CREATE TABLE IF NOT EXISTS renda_fixa (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            produto TEXT,
            indexador TEXT,
            taxa REAL,
            valor REAL,
            data TEXT,
            vencimento TEXT
        )
    ''')
    _exec(conexao, '''
        CREATE TABLE IF NOT EXISTS indices (
            serie TEXT,
            data TEXT,
            valor REAL,
            data_fim TEXT,
            PRIMARY KEY (serie, data)
        )
    ''')
    conexao.commit()
    conexao.close()

def sugerir_tipo(ticker):
    t = ticker.upper()
    if t.endswith("-USD") or t.endswith("-BRL"):
        return "cripto"
    if t.startswith("GC=") or t == "GOLD11.SA":
        return "ouro"
    if not t.endswith(".SA"):
        return "exterior"
    base = t[:-3]
    if base[-2:] in ("32", "33", "34", "35", "39"):
        return "bdr"
    if base.endswith("11"):
        if base in ETFS_CONHECIDOS:
            return "etf"
        return "acao" if base in UNITS_CONHECIDAS else "fii"
    return "acao"

def consultar(sql, parametros=()):
    conexao = conectar()
    try:
        return _exec(conexao, sql, parametros).fetchall()
    finally:
        conexao.close()

def executar(sql, parametros=()):
    conexao = conectar()
    try:
        _exec(conexao, sql, parametros)
        conexao.commit()
    finally:
        conexao.close()

COLUNAS_OPERACAO = ("id", "ticker", "tipo_ativo", "operacao", "data", "quantidade", "preco", "moeda", "cambio", "importada")

def listar_operacoes():
    linhas = consultar("SELECT " + ", ".join(COLUNAS_OPERACAO) + " FROM operacoes ORDER BY data, id")
    return [dict(zip(COLUNAS_OPERACAO, linha)) for linha in linhas]

def listar_alertas():
    return consultar("SELECT * FROM alertas")

def listar_mercado():
    return consultar("SELECT ticker, nome FROM mercado_lista ORDER BY id")

def ler_config(chave, padrao):
    linha = consultar("SELECT valor FROM config WHERE chave = ?", (chave,))
    return linha[0][0] if linha else padrao

def salvar_config(chave, valor):
    executar("INSERT OR REPLACE INTO config (chave, valor) VALUES (?, ?)", (chave, valor))

# ---------------- Dados do mercado ----------------

def cotacao(ticker):
    try:
        fi = yf.Ticker(ticker).fast_info
        preco = fi["last_price"]
        anterior = fi["previous_close"]
        if preco is None or preco != preco:
            return None
        variacao = round((preco - anterior) / anterior * 100, 2) if anterior else None
        return {"preco": float(preco), "variacao": variacao, "moeda": fi["currency"] or ""}
    except Exception:
        return None

def buscar_historico_desde(ticker, inicio_iso):
    try:
        h = yf.Ticker(ticker).history(start=inicio_iso)
        return h if len(h) else None
    except Exception:
        return None

def buscar_historico(ticker, periodo="1mo"):
    try:
        historico = yf.Ticker(ticker).history(period=periodo)
        historico = historico.dropna(subset=["Close"])
        return historico if not historico.empty else None
    except Exception:
        return None

_cache_cambio = {}

def taxa_para_real(moeda):
    if moeda in ("BRL", "", None):
        return 1.0
    guardado = _cache_cambio.get(moeda)
    if guardado and time.time() - guardado[1] < 600:
        return guardado[0]
    cot = cotacao(moeda + "BRL=X")
    if cot is None:
        return guardado[0] if guardado else None
    _cache_cambio[moeda] = (cot["preco"], time.time())
    return cot["preco"]

_cache_cambio_historico = {}

def cambio_na_data(moeda, data_iso):
    if moeda in ("BRL", "", None):
        return 1.0
    chave = (moeda, data_iso)
    if chave in _cache_cambio_historico:
        return _cache_cambio_historico[chave]
    dia = date.fromisoformat(data_iso)
    try:
        hist = yf.Ticker(moeda + "BRL=X").history(start=dia - timedelta(days=7), end=dia + timedelta(days=1))
        hist = hist.dropna(subset=["Close"])
    except Exception:
        return None
    if hist.empty:
        return None
    _cache_cambio_historico[chave] = float(hist["Close"].iloc[-1])
    return _cache_cambio_historico[chave]

_cache_dividendos = {}

def serie_dividendos(ticker):
    guardado = _cache_dividendos.get(ticker)
    if guardado and guardado[1] == date.today():
        return guardado[0]
    try:
        serie = yf.Ticker(ticker).dividends
    except Exception:
        return None
    _cache_dividendos[ticker] = (serie, date.today())
    return serie

def baixar_serie_bcb(codigo, inicio):
    url = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{}/dados?formato=json&dataInicial={}&dataFinal={}".format(
        codigo, inicio.strftime("%d/%m/%Y"), date.today().strftime("%d/%m/%Y"))
    for tentativa in range(3):
        try:
            resposta = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            dados = resposta.json()
            if not isinstance(dados, list):
                return []
            return [(datetime.strptime(d["data"], "%d/%m/%Y").date().isoformat(), float(d["valor"]),
                     datetime.strptime(d["dataFim"], "%d/%m/%Y").date().isoformat() if d.get("dataFim") else None) for d in dados]
        except Exception:
            log.warning("Banco Central: falha na serie %s (tentativa %d)", codigo, tentativa + 1)
            time.sleep(2)
    return None

_ultima_busca_indice = {}

def obter_indices(nome, inicio):
    primeira, ultima = consultar("SELECT MIN(data), MAX(data) FROM indices WHERE serie = ?", (nome,))[0]
    if primeira is None or inicio.isoformat() < primeira:
        baixar_de = inicio - timedelta(days=45)
    elif ultima < (date.today() - timedelta(days=1)).isoformat():
        baixar_de = date.fromisoformat(ultima)
    else:
        baixar_de = None
    ok = True
    ultima_busca = _ultima_busca_indice.get(nome)
    if baixar_de and (primeira is None or inicio.isoformat() < primeira or not ultima_busca or time.time() - ultima_busca > 3600):
        _ultima_busca_indice[nome] = time.time()
        dados = baixar_serie_bcb(SERIES_BCB[nome], baixar_de)
        if dados is None:
            ok = primeira is not None and inicio.isoformat() >= primeira
        elif dados:
            conexao = conectar()
            try:
                _executemany(conexao, "INSERT OR REPLACE INTO indices (serie, data, valor, data_fim) VALUES (?, ?, ?, ?)",
                             [(nome,) + linha for linha in dados])
                conexao.commit()
            finally:
                conexao.close()
    linhas = consultar("SELECT data, valor, data_fim FROM indices WHERE serie = ? AND data >= ? ORDER BY data",
                       (nome, (inicio - timedelta(days=45)).isoformat()))
    return linhas, ok

# ---------------- Calculos da carteira ----------------

def calcular_posicoes(operacoes, cambio_padrao=None):
    posicoes = {}
    for op in operacoes:
        p = posicoes.setdefault(op["ticker"], {"q": 0.0, "custo": 0.0, "custo_brl": 0.0, "realizado_brl": 0.0,
                                               "moeda": op["moeda"], "tipo": op["tipo_ativo"]})
        cambio = op["cambio"] if op["cambio"] is not None else (cambio_padrao or {}).get(op["moeda"], 1.0)
        q = op["quantidade"]
        if op["operacao"] == "compra":
            p["q"] += q
            p["custo"] += q * op["preco"]
            p["custo_brl"] += q * op["preco"] * cambio
            continue
        if q > p["q"] + 1e-9:
            raise ValueError("Em {} voce vendeu {} de {}, mas so tinha {}.".format(
                formatar_data(op["data"]), formatar_qtd(q), op["ticker"], formatar_qtd(p["q"])))
        fracao = q / p["q"]
        custo_da_venda_brl = p["custo_brl"] * fracao
        p["realizado_brl"] += q * op["preco"] * cambio - custo_da_venda_brl
        p["custo"] -= p["custo"] * fracao
        p["custo_brl"] -= custo_da_venda_brl
        p["q"] -= q
        if p["q"] < 1e-9:
            p["q"] = p["custo"] = p["custo_brl"] = 0.0
    return posicoes

def ordenar_operacoes(operacoes):
    return sorted(operacoes, key=lambda o: (o["data"], o["id"] if o["id"] is not None else float("inf")))

def quantidade_em(operacoes, ticker, dia_iso):
    q = 0.0
    for op in operacoes:
        if op["ticker"] == ticker and op["data"] < dia_iso:
            q += op["quantidade"] if op["operacao"] == "compra" else -op["quantidade"]
    return max(q, 0.0)

# ---------------- Apuracao de IR (estimativa simplificada) ----------------

LIMITE_ISENCAO_ACAO = 20000.0   # vendas de acoes no mes (mercado a vista)
LIMITE_ISENCAO_CRIPTO = 35000.0 # vendas de cripto no mes

def regra_ir(tipo, vendas_mes_por_tipo):
    """Devolve (aliquota %, descricao da regra) para o lucro realizado do tipo no mes."""
    if tipo == "acao":
        isento = vendas_mes_por_tipo.get("acao", 0.0) <= LIMITE_ISENCAO_ACAO
        return (0.0, "isento (vendas do mes <= 20 mil)") if isento else (15.0, "15%")
    if tipo == "cripto":
        isento = vendas_mes_por_tipo.get("cripto", 0.0) <= LIMITE_ISENCAO_CRIPTO
        return (0.0, "isento (vendas do mes <= 35 mil)") if isento else (15.0, "15%")
    if tipo == "fii":
        return 20.0, "20% (FII nao tem isencao)"
    if tipo == "exterior":
        return 15.0, "15% (carne-leao, regra simplificada)"
    return 15.0, "15%"

def apuracao_mensal(operacoes):
    """Lucro realizado e IR estimado por mes, agrupado por tipo de ativo.

    Usa preco medio em reais no momento de cada venda. Regras simplificadas:
    acao isenta ate 20 mil de vendas/mes, cripto ate 35 mil, FII sempre 20%.
    """
    estado = {}  # ticker -> [quantidade, custo_brl]
    por_mes = {}  # "AAAA-MM" -> {"vendas": {tipo: brl}, "lucro": {tipo: brl}}
    for op in ordenar_operacoes(operacoes):
        p = estado.setdefault(op["ticker"], [0.0, 0.0])
        cambio = op["cambio"] or 1.0
        if op["operacao"] == "compra":
            p[0] += op["quantidade"]
            p[1] += op["quantidade"] * op["preco"] * cambio
            continue
        q = op["quantidade"]
        if p[0] <= 0:
            continue
        pm = p[1] / p[0]
        vendido = q * op["preco"] * cambio
        tipo = op["tipo_ativo"]
        mes = op["data"][:7]
        m = por_mes.setdefault(mes, {"vendas": {}, "lucro": {}})
        m["vendas"][tipo] = m["vendas"].get(tipo, 0.0) + vendido
        m["lucro"][tipo] = m["lucro"].get(tipo, 0.0) + (vendido - pm * q)
        p[0] -= q
        p[1] -= pm * q
    resultado = []
    for mes in sorted(por_mes):
        m = por_mes[mes]
        detalhe = []
        ir_total = 0.0
        for tipo in sorted(m["lucro"]):
            aliquota, regra = regra_ir(tipo, m["vendas"])
            imposto = max(m["lucro"][tipo], 0.0) * aliquota / 100
            ir_total += imposto
            detalhe.append({"tipo": tipo, "vendido": m["vendas"][tipo], "lucro": m["lucro"][tipo],
                            "regra": regra, "imposto": imposto})
        resultado.append({"mes": mes, "detalhe": detalhe, "ir_estimado": ir_total})
    return resultado

def aliquota_ir(dias_corridos):
    if dias_corridos <= 180:
        return 22.5
    if dias_corridos <= 360:
        return 20.0
    if dias_corridos <= 720:
        return 17.5
    return 15.0

def aliquota_iof(dias_corridos, produto):
    if produto == "Poupanca" or dias_corridos >= 30:
        return 0.0
    return IOF_REGRESSIVO[max(dias_corridos, 1) - 1]

def primeiro_aniversario_poupanca(dia):
    if dia.day <= 28:
        return dia
    return (dia.replace(day=1) + timedelta(days=32)).replace(day=1)

def calcular_renda_fixa(aplicacao, indices, hoje):
    inicio = aplicacao["data"]
    fim = min(hoje, aplicacao["vencimento"]) if aplicacao["vencimento"] else hoje
    taxa = aplicacao["taxa"] or 0
    indexador = aplicacao["indexador"]
    dias_uteis = sum(1 for d, v, f in indices.get("cdi", []) if inicio <= d < fim)
    fator = 1.0
    if indexador in ("cdi", "selic"):
        for d, v, f in indices[indexador]:
            if inicio <= d < fim:
                fator *= 1 + v / 100 * taxa / 100
    elif indexador == "pre":
        fator = (1 + taxa / 100) ** (dias_uteis / 252)
    elif indexador == "ipca":
        ini, fi = date.fromisoformat(inicio), date.fromisoformat(fim)
        for d, v, f in indices["ipca"]:
            mes_ini = date.fromisoformat(d)
            mes_fim = (mes_ini + timedelta(days=32)).replace(day=1)
            dias_no_periodo = (min(fi, mes_fim) - max(ini, mes_ini)).days
            if dias_no_periodo > 0:
                fator *= (1 + v / 100) ** (dias_no_periodo / (mes_fim - mes_ini).days)
        fator *= (1 + taxa / 100) ** (dias_uteis / 252)
    elif indexador == "poupanca":
        rendimentos = {d: (v, f) for d, v, f in indices["poupanca"]}
        aniversario = primeiro_aniversario_poupanca(date.fromisoformat(inicio)).isoformat()
        while aniversario in rendimentos and rendimentos[aniversario][1] and rendimentos[aniversario][1] <= fim:
            valor, proximo = rendimentos[aniversario]
            fator *= 1 + valor / 100
            aniversario = proximo
    bruto = aplicacao["valor"] * fator
    rendimento = bruto - aplicacao["valor"]
    dias = (date.fromisoformat(fim) - date.fromisoformat(inicio)).days
    aliq_iof = aliquota_iof(dias, aplicacao["produto"])
    iof = max(rendimento, 0) * aliq_iof / 100
    aliquota = 0.0 if PRODUTOS_RF[aplicacao["produto"]]["isento"] else aliquota_ir(dias)
    imposto = max(rendimento - iof, 0) * aliquota / 100
    return {"bruto": bruto, "rendimento": rendimento, "aliquota": aliquota, "imposto": imposto,
            "iof": iof, "aliquota_iof": aliq_iof, "liquido": bruto - iof - imposto,
            "vencido": bool(aplicacao["vencimento"]) and aplicacao["vencimento"] <= hoje}

def calcular_dividendos(serie, operacoes, ticker):
    if serie is None:
        return None
    resultado = {"por_acao": 0.0, "recebido": 0.0}
    if len(serie) == 0:
        return resultado
    corte = pd.Timestamp.now(tz=serie.index.tz) - pd.Timedelta(days=365)
    for data_com, valor in serie[serie.index >= corte].items():
        resultado["por_acao"] += float(valor)
        resultado["recebido"] += float(valor) * quantidade_em(operacoes, ticker, data_com.date().isoformat())
    return resultado

def tem_internet():
    try:
        socket.create_connection(("query1.finance.yahoo.com", 443), timeout=3).close()
        return True
    except OSError:
        return False

def motivo_falha(ticker):
    if not tem_internet():
        return "Sem conexao com a internet. Verifique sua rede e tente de novo."
    return "Nao encontrei o codigo '" + ticker + "'.\nConfira se esta certo (ex: PETR4.SA, AAPL, BTC-USD)."

# ---------------- Utilidades ----------------

def formatar_br(valor):
    return "{:,.2f}".format(valor).replace(",", "X").replace(".", ",").replace("X", ".")

def formatar_preco(valor, moeda):
    simbolo = SIMBOLOS.get(moeda, moeda)
    return (simbolo + " " if simbolo else "") + formatar_br(valor)

def formatar_qtd(q):
    texto = "{:,.8f}".format(q).rstrip("0").rstrip(".")
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")

def formatar_data(data_iso):
    return date.fromisoformat(data_iso).strftime("%d/%m/%Y")

def ler_data(texto):
    try:
        return datetime.strptime(texto.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None

def formatar_var(variacao):
    if variacao is None:
        return "-"
    return ("+" if variacao >= 0 else "") + str(variacao).replace(".", ",") + "%"

def simbolo_do_ativo(ticker, moeda):
    return "" if ticker.startswith("^") else SIMBOLOS.get(moeda, moeda)


COLUNAS_RF = ("id", "nome", "produto", "indexador", "taxa", "valor", "data", "vencimento")

def listar_renda_fixa():
    linhas = consultar("SELECT " + ", ".join(COLUNAS_RF) + " FROM renda_fixa ORDER BY data, id")
    return [dict(zip(COLUNAS_RF, linha)) for linha in linhas]

def ler_valor_em_reais(texto):
    t = texto.strip().replace(" ", "").replace("R$", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[1]) == 3):
        t = t.replace(".", "")
    return float(t)


def metas_distribuicao():
    try:
        return json.loads(ler_config("meta_distribuicao", "{}") or "{}")
    except (TypeError, ValueError):
        return {}


def rentabilidades_benchmark(registros, cdi, ibov):
    """% acumulado do CDI e do Ibovespa desde a primeira data dos registros.
    Devolve (cdi_pct, ibov_pct), listas alinhadas aos registros; cada uma pode ser None."""
    datas = [d for d, v, i in registros]
    d0 = datas[0]
    cdi_pct = ibov_pct = None
    if cdi:
        ordenada = sorted((d, v) for d, v, f in cdi)
        base = 1.0
        i = 0
        while i < len(ordenada) and ordenada[i][0] < d0:
            base *= 1 + ordenada[i][1] / 100
            i += 1
        cdi_pct = []
        fator = base
        for alvo in datas:
            while i < len(ordenada) and ordenada[i][0] < alvo:
                fator *= 1 + ordenada[i][1] / 100
                i += 1
            cdi_pct.append(fator / base * 100 - 100)
    if ibov:
        ordenada = sorted(ibov)
        i = 0
        ultimo = None
        while i < len(ordenada) and ordenada[i][0] <= d0:
            ultimo = ordenada[i][1]
            i += 1
        if ultimo is None and ordenada:
            ultimo = ordenada[0][1]
            i = 1
        if ultimo:
            base = ultimo
            ibov_pct = []
            for alvo in datas:
                while i < len(ordenada) and ordenada[i][0] <= alvo:
                    ultimo = ordenada[i][1]
                    i += 1
                ibov_pct.append(ultimo / base * 100 - 100)
    return cdi_pct, ibov_pct
