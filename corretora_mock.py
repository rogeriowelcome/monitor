"""Corretora simulada em memoria - mesma interface de corretora.py, sem rede.

Serve para exercitar o ciclo de ordens offline: saldo, ordem a mercado,
ordem limite (executa se o preco simulado cruzar o limite) e cancelamento.
O estado reseta a cada reinicio do app.
"""

import itertools
import random

from corretora import BinanceErro, novo_client_id

_saldos = {"USDT": 100000.0, "BTC": 0.5, "ETH": 10.0}
_ordens = {}
_ids = itertools.count(1000)

# Preco simulado fixo por simbolo (o livro de ofertas "de mentira")
PRECOS = {"BTCUSDT": 84000.0, "ETHUSDT": 2700.0, "BNBUSDT": 600.0, "SOLUSDT": 190.0}


def _base(symbol):
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def _preco(symbol):
    if symbol not in PRECOS:
        raise BinanceErro("Simulado -1121: simbolo invalido " + symbol)
    # pequeno jitter para parecer mercado vivo
    return PRECOS[symbol] * (1 + random.uniform(-0.002, 0.002))


def sincronizar_relogio():
    return 0


def saldos(api_key=None, secret=None):
    return [{"asset": a, "free": "{:.8f}".format(v), "locked": "0"}
            for a, v in _saldos.items() if v > 0]


def _executar(ordem, preco_executado):
    qtd = float(ordem["origQty"])
    base = _base(ordem["symbol"])
    if ordem["side"] == "BUY":
        custo = qtd * preco_executado
        if _saldos.get("USDT", 0) < custo:
            raise BinanceErro("Simulado -2010: saldo de USDT insuficiente")
        _saldos["USDT"] -= custo
        _saldos[base] = _saldos.get(base, 0) + qtd
    else:
        if _saldos.get(base, 0) < qtd:
            raise BinanceErro("Simulado -2010: saldo de {} insuficiente".format(base))
        _saldos[base] -= qtd
        _saldos["USDT"] = _saldos.get("USDT", 0) + qtd * preco_executado
    ordem["status"] = "FILLED"
    ordem["executedQty"] = ordem["origQty"]
    ordem["avgPrice"] = "{:.2f}".format(preco_executado)


def enviar_ordem(api_key, secret, symbol, side, tipo, quantidade, preco=None,
                 so_validar=False, client_id=None):
    if so_validar:
        return {"preview": True,
                "params": {"symbol": symbol, "side": side, "type": tipo,
                           "quantity": quantidade, "price": preco},
                "signature": "(simulado, sem assinatura)"}
    mercado = _preco(symbol)
    ordem = {"orderId": next(_ids), "symbol": symbol, "side": side, "type": tipo,
             "origQty": str(quantidade), "price": str(preco or "0"),
             "status": "NEW", "executedQty": "0",
             "clientOrderId": client_id or novo_client_id()}
    _ordens[ordem["orderId"]] = ordem
    try:
        if tipo == "MARKET":
            _executar(ordem, mercado)
        elif tipo == "LIMIT":
            limite = float(preco)
            cruza = (side == "BUY" and limite >= mercado) or (side == "SELL" and limite <= mercado)
            if cruza:
                # executa ao preco de mercado ou melhor - nunca pior que o limite
                _executar(ordem, min(limite, mercado) if side == "BUY" else max(limite, mercado))
        else:
            _ordens.pop(ordem["orderId"], None)
            raise BinanceErro("Simulado -1116: tipo de ordem invalido " + tipo)
    except BinanceErro:
        _ordens.pop(ordem["orderId"], None)
        raise
    return ordem


def ordens_abertas(api_key=None, secret=None, symbol=None):
    # mercado andou? ordens limite que agora cruzam executam
    for o in list(_ordens.values()):
        if o["status"] != "NEW":
            continue
        mercado = _preco(o["symbol"])
        limite = float(o["price"])
        if (o["side"] == "BUY" and limite >= mercado) or (o["side"] == "SELL" and limite <= mercado):
            try:
                _executar(o, min(limite, mercado) if o["side"] == "BUY" else max(limite, mercado))
            except BinanceErro:
                pass
    return [o for o in _ordens.values() if o["status"] == "NEW" and (not symbol or o["symbol"] == symbol)]


def ultimas_ordens(api_key=None, secret=None, symbol=None, limite=50):
    todas = sorted(_ordens.values(), key=lambda o: -o["orderId"])
    if symbol:
        todas = [o for o in todas if o["symbol"] == symbol]
    return todas[:limite]


def cancelar_ordem(api_key, secret, symbol, order_id):
    ordem = _ordens.get(order_id)
    if ordem is None or ordem["symbol"] != symbol or ordem["status"] != "NEW":
        raise BinanceErro("Simulado -2013: ordem nao encontrada")
    ordem["status"] = "CANCELED"
    return ordem
