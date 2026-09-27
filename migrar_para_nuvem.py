# -*- coding: utf-8 -*-
# Migra o banco local (SQLite) para a nuvem (Postgres/Supabase).
# Uso: defina DATABASE_URL ou salve a URL em nuvem.txt (em %APPDATA%\MonitorInvestimentos)
#      e rode:  python migrar_para_nuvem.py
import sqlite3
import sys
import nucleo

if not nucleo.USAR_PG:
    sys.exit("DATABASE_URL nao aponta para Postgres. Defina a variavel ou crie nuvem.txt.")

origem = sqlite3.connect(nucleo.caminho_banco)
nucleo.criar_tabelas()  # cria as tabelas na nuvem se nao existirem
destino = nucleo.conectar()
cur = destino.cursor()

TABELAS = ["carteira", "alertas", "mercado_lista", "config",
           "historico_patrimonio", "operacoes", "renda_fixa", "indices"]

for tabela in TABELAS:
    try:
        cols = [d[0] for d in origem.execute("SELECT * FROM " + tabela + " LIMIT 0").description]
        linhas = origem.execute("SELECT * FROM " + tabela).fetchall()
    except sqlite3.OperationalError:
        print(tabela, "- tabela nao existe no banco local, pulando")
        continue
    if not linhas:
        print(tabela, "- vazia")
        continue
    ph = ", ".join(["%s"] * len(cols))
    cur.executemany("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING".format(
        tabela, ", ".join(cols), ph), linhas)
    print(tabela, "-", len(linhas), "linhas copiadas")

# recalibra os contadores de id (o Postgres nao avanca sozinho quando o id vem junto)
for tabela in ("alertas", "mercado_lista", "operacoes", "renda_fixa", "carteira"):
    cur.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                "COALESCE((SELECT MAX(id) FROM " + tabela + "), 1))", (tabela,))

destino.commit()
destino.close()
origem.close()
print("Migracao concluida. App e site agora podem usar o banco da nuvem.")
