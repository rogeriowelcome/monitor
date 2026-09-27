"""Cliente minimo para a testnet de futuros da Binance (dinheiro de mentira, API de verdade).

Roda em https://testnet.binancefuture.com - a testnet SPOT (binance.vision) e
bloqueada no Brasil, a de futuros nao. A assinatura e identica a da API real:
todo endpoint "assinado" recebe `timestamp` e `signature` =
HMAC-SHA256(query_string, api_secret) na query, mais o header X-MBX-APIKEY.

Crie a conta gratis (login com GitHub) em https://testnet.binancefuture.com
"""

import hashlib
import hmac
import time
import urllib.parse
import uuid

import requests

BASE_URL = "https://testnet.binancefuture.com"
OFFSET_MS = [0]  # diferenca entre o relogio do servidor e o local


class BinanceErro(Exception):
    """Erro devolvido pela API (codigo + mensagem) ou de rede."""


# ---------------- Parte pura (facil de testar) ----------------

def montar_query(params):
    """Serializa parametros no formato que a Binance espera (sem valores None)."""
    return urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None])


def assinar(params, secret):
    """Devolve a query string completa, com &signature=<hmac> no final."""
    query = montar_query(params)
    assinatura = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + "&signature=" + assinatura


def novo_client_id():
    """Idempotencia: o mesmo client id nunca executa a mesma ordem duas vezes."""
    return "mon-" + uuid.uuid4().hex[:20]


# ---------------- Rede ----------------

def sincronizar_relogio():
    """Guarda o offset do relogio do servidor (a Binance rejeita timestamps tortos)."""
    r = requests.get(BASE_URL + "/fapi/v1/time", timeout=10)
    r.raise_for_status()
    OFFSET_MS[0] = int(r.json()["serverTime"]) - int(time.time() * 1000)
    return OFFSET_MS[0]


def request(method, path, params=None, api_key=None, secret=None, assinada=False):
    params = dict(params or {})
    url = BASE_URL + path
    headers = {}
    if assinada:
        params.setdefault("recvWindow", 10000)
        params["timestamp"] = int(time.time() * 1000) + OFFSET_MS[0]
        url += "?" + assinar(params, secret or "")
        headers["X-MBX-APIKEY"] = api_key or ""
    else:
        query = montar_query(params)
        if query:
            url += "?" + query
    try:
        r = requests.request(method, url, headers=headers, timeout=15)
    except requests.RequestException as e:
        raise BinanceErro("Sem conexao com a testnet: " + str(e))
    try:
        dados = r.json()
    except ValueError:
        raise BinanceErro("Resposta invalida da testnet (HTTP {})".format(r.status_code))
    if r.status_code >= 400:
        raise BinanceErro("Binance {}: {}".format(dados.get("code", r.status_code),
                                                  dados.get("msg", r.text[:200])))
    return dados


# ---------------- Operacoes ----------------

def saldos(api_key, secret):
    """GET /fapi/v2/balance -> lista so o que nao esta zerado."""
    linhas = request("GET", "/fapi/v2/balance", api_key=api_key, secret=secret, assinada=True)
    return [{"asset": b["asset"], "free": b["balance"], "locked": b.get("availableBalance", "0")}
            for b in linhas if float(b["balance"]) > 0]


def enviar_ordem(api_key, secret, symbol, side, tipo, quantidade, preco=None,
                 so_validar=False, client_id=None):
    params = {"symbol": symbol, "side": side, "type": tipo,
              "quantity": quantidade,
              "newClientOrderId": client_id or novo_client_id()}
    if tipo == "LIMIT":
        params["timeInForce"] = "GTC"
        params["price"] = preco
    if so_validar:
        # futuros nao tem endpoint de teste; monta a requisicao assinada sem enviar
        params["timestamp"] = int(time.time() * 1000) + OFFSET_MS[0]
        params.setdefault("recvWindow", 10000)
        query = assinar(params, secret or "")
        return {"preview": True, "params": params, "signature": query.rsplit("=", 1)[1]}
    return request("POST", "/fapi/v1/order", params, api_key=api_key, secret=secret, assinada=True)


def ordens_abertas(api_key, secret, symbol=None):
    params = {"symbol": symbol} if symbol else {}
    return request("GET", "/fapi/v1/openOrders", params, api_key=api_key, secret=secret, assinada=True)


def ultimas_ordens(api_key, secret, symbol, limite=20):
    return request("GET", "/fapi/v1/allOrders",
                   {"symbol": symbol, "limit": limite},
                   api_key=api_key, secret=secret, assinada=True)


def cancelar_ordem(api_key, secret, symbol, order_id):
    return request("DELETE", "/fapi/v1/order",
                   {"symbol": symbol, "orderId": order_id},
                   api_key=api_key, secret=secret, assinada=True)
