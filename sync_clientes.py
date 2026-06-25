"""
sync_clientes_mirror.py
-----------------------
Llena la tabla `clientes_quickbooks` con los clientes REALES de QuickBooks,
de las 3 empresas que facturan. Esa tabla es el "espejo" de QuickBooks: la
webapp la leera para que el operador elija un cliente que SIEMPRE existe en QB.

Seguridad:
  - En QuickBooks SOLO lee (GET). No crea ni modifica nada alla.
  - En la base escribe SOLO en `clientes_quickbooks` (UPSERT). No toca el resto.
  - Refresca el token de cada empresa solo (no mas 401 a mano).
  - Es idempotente: corrélo cuantas veces quieras; actualiza lo que cambio.

Antes de correr: crea la tabla `clientes_quickbooks` (el SQL que te pase aparte).
Requiere (ya instalado): psycopg2-binary, python-dotenv, requests
"""

import os
import json
import base64
import sys

import requests
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

# ── Configuracion ────────────────────────────────────────────────────────────
load_dotenv()  # .env de esta carpeta (QBO_CLIENT_ID / QBO_CLIENT_SECRET)
RUTA_ENV_WEBAPP = r"D:\Users\Usuario\Desktop\SX-Ecosystem\SX-Ecosystem\.env"
if os.path.exists(RUTA_ENV_WEBAPP):
    load_dotenv(RUTA_ENV_WEBAPP, override=False)  # de aqui sale DATABASE_URL

PROD_BASE_URL = "https://quickbooks.api.intuit.com"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")

# Las 3 empresas que facturan: realm de QuickBooks -> id de empresa en la webapp
EMPRESAS = [
    {
        "nombre": "Soportexperto.com S.A.",
        "realm": "9130355360397996",
        "empresa_id": "ec006548-c1a1-4212-aaf5-605041ce7d3e",
    },
    {
        "nombre": "Hardware y Network S.A.",
        "realm": "9130355360390096",
        "empresa_id": "fc3e4394-5954-41d7-b502-5b38db52fae5",
    },
    {
        "nombre": "Corporacion Latinoamericana T.I.",
        "realm": "9130355360394696",
        "empresa_id": "01d18328-dccf-493b-aca7-05c5d74900a0",
    },
]


def refrescar_token(realm):
    """Refresca el token de una empresa y lo guarda. Devuelve el access_token nuevo."""
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    empresa = data["empresas"][realm]
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"grant_type": "refresh_token", "refresh_token": empresa["refresh_token"]},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"  [ERROR refresco] {resp.status_code} {resp.text[:200]}")
        return None
    nuevos = resp.json()
    empresa["access_token"] = nuevos["access_token"]
    empresa["refresh_token"] = nuevos[
        "refresh_token"
    ]  # Intuit rota el refresh; hay que guardarlo
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return nuevos["access_token"]


def descargar_clientes(realm, token):
    """Baja TODOS los clientes de una empresa (solo lectura, paginado)."""
    clientes, inicio, lote = [], 1, 100
    while True:
        sql = f"SELECT * FROM Customer STARTPOSITION {inicio} MAXRESULTS {lote}"
        url = f"{PROD_BASE_URL}/v3/company/{realm}/query?query={requests.utils.quote(sql)}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  [ERROR {resp.status_code}] {resp.text[:200]}")
            break
        lote_res = resp.json().get("QueryResponse", {}).get("Customer", [])
        if not lote_res:
            break
        clientes.extend(lote_res)
        if len(lote_res) < lote:
            break
        inicio += lote
    return clientes


def guardar(conn, empresa_id, clientes):
    """UPSERT de los clientes en la tabla espejo. Devuelve cuantos proceso."""
    filas = []
    for c in clientes:
        email = (c.get("PrimaryEmailAddr") or {}).get("Address")
        filas.append(
            (
                empresa_id,
                c.get("Id"),
                c.get("DisplayName") or "",
                c.get("CompanyName"),
                email,
                bool(c.get("Active", True)),
            )
        )
    if not filas:
        return 0
    sql = """
        INSERT INTO clientes_quickbooks
            (empresa_id, qbo_customer_id, display_name, company_name, email, activo, sincronizado_en)
        VALUES %s
        ON CONFLICT (empresa_id, qbo_customer_id) DO UPDATE SET
            display_name    = EXCLUDED.display_name,
            company_name    = EXCLUDED.company_name,
            email           = EXCLUDED.email,
            activo          = EXCLUDED.activo,
            sincronizado_en = now();
    """
    cur = conn.cursor()
    execute_values(cur, sql, filas, template="(%s, %s, %s, %s, %s, %s, now())")
    conn.commit()
    return len(filas)


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print("[ERROR] Falta QBO_CLIENT_ID / QBO_CLIENT_SECRET en el .env.")
        sys.exit(1)
    if not DATABASE_URL:
        print("[ERROR] Falta DATABASE_URL (revisa RUTA_ENV_WEBAPP).")
        sys.exit(1)

    print("=" * 60)
    print("SYNC ESPEJO DE CLIENTES (QuickBooks -> clientes_quickbooks)")
    print("=" * 60)

    conn = psycopg2.connect(DATABASE_URL)
    total = 0
    try:
        for emp in EMPRESAS:
            print(f"\n=== {emp['nombre']} ===")
            print("  Refrescando token...")
            token = refrescar_token(emp["realm"])
            if not token:
                print("  [SALTADA] no se pudo refrescar el token.")
                continue
            print("  Descargando clientes (solo lectura)...")
            clientes = descargar_clientes(emp["realm"], token)
            print(f"  {len(clientes)} clientes en QuickBooks.")
            n = guardar(conn, emp["empresa_id"], clientes)
            print(f"  Guardados/actualizados en la tabla espejo: {n}")
            total += n
    finally:
        conn.close()

    print("\n" + "=" * 60)
    print(f"Listo. {total} clientes en total en clientes_quickbooks.")
    print("=" * 60)


if __name__ == "__main__":
    main()
