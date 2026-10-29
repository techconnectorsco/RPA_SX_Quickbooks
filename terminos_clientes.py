"""
investigar_terminos.py
----------------------
Investiga el campo "Terminos / Condiciones de pago" de QuickBooks, por cada
una de las 3 empresas que facturan.

Por que existe: el RPA crea las facturas por API y NUNCA mapeo el campo
SalesTermRef (el termino de pago). QuickBooks NO hereda el termino del cliente
al crear la factura por API (igual que pasaba con el correo / BillEmail), asi
que las facturas salen con un termino distinto al que el cliente tiene
configurado, y la Fecha de vencimiento sale mal.

Este script muestra, por empresa:
  1) TODOS los Term (el "desplegable"): Id, Nombre, Activo y dias de
     vencimiento. ESE Id es el que hay que mapear.
  2) Un muestreo de clientes con su SalesTermRef (que termino tiene cargado
     cada cliente por defecto), y cuantos clientes NO tienen termino asignado.

Solo lectura (GET). No crea ni modifica nada en QuickBooks.
"""

import os
import json
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

PROD_BASE_URL = "https://quickbooks.api.intuit.com"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")

# Cuantos clientes (con termino) listar como ejemplo por empresa.
MAX_EJEMPLOS_CLIENTE = 15

EMPRESAS = [
    {"nombre": "Soportexperto.com S.A.", "realm": "9130355360397996"},
    {"nombre": "Hardware y Network S.A.", "realm": "9130355360390096"},
    {"nombre": "Corporacion Latinoamericana T.I.", "realm": "9130355360394696"},
]


def refrescar_token(realm):
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if realm not in data.get("empresas", {}):
        print(f"  [ERROR] El realm {realm} no existe en {TOKENS_FILE}.")
        return None
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
    empresa["refresh_token"] = nuevos["refresh_token"]
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return nuevos["access_token"]


def _query(realm, token, sql):
    url = (
        f"{PROD_BASE_URL}/v3/company/{realm}/query"
        f"?query={requests.utils.quote(sql)}&minorversion=75"
    )
    return requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )


def listar_terminos(realm, token):
    """1) El desplegable: todos los Term con su Id y dias de vencimiento."""
    resp = _query(realm, token, "SELECT * FROM Term MAXRESULTS 1000")
    if resp.status_code != 200:
        print(f"  [ERROR Term {resp.status_code}] {resp.text[:200]}")
        return {}

    terminos = resp.json().get("QueryResponse", {}).get("Term", [])
    print(f"\n  TERMINOS (desplegable) — {len(terminos)} encontrados:")
    print("  " + "-" * 70)
    print(f"  {'Id':>4}  {'Activo':<7} {'DiasVenc':<9} Nombre")
    print("  " + "-" * 70)

    mapa_id_nombre = {}
    for t in terminos:
        tid = t.get("Id")
        nombre = t.get("Name") or ""
        activo = "Si" if t.get("Active", True) else "NO"
        # Term "STANDARD" trae DueDays; "DATE_DRIVEN" trae DayOfMonthDue.
        due = t.get("DueDays")
        due_txt = str(due) if due is not None else "-"
        mapa_id_nombre[str(tid)] = nombre
        print(f"  {tid:>4}  {activo:<7} {due_txt:<9} {nombre}")

    return mapa_id_nombre


def muestrear_clientes(realm, token, mapa_terminos):
    """2) Que termino tiene cargado cada cliente (SalesTermRef)."""
    resp = _query(realm, token, "SELECT * FROM Customer MAXRESULTS 1000")
    if resp.status_code != 200:
        print(f"  [ERROR Customer {resp.status_code}] {resp.text[:200]}")
        return

    clientes = resp.json().get("QueryResponse", {}).get("Customer", [])
    con_termino = []
    sin_termino = 0
    for c in clientes:
        ref = c.get("SalesTermRef") or {}
        term_id = ref.get("value")
        if term_id:
            con_termino.append(
                (
                    c.get("DisplayName") or "",
                    str(term_id),
                    ref.get("name") or mapa_terminos.get(str(term_id), "?"),
                )
            )
        else:
            sin_termino += 1

    print(f"\n  CLIENTES — {len(clientes)} en total:")
    print(f"     con termino asignado : {len(con_termino)}")
    print(f"     SIN termino asignado : {sin_termino}")

    if con_termino:
        print(f"\n  Ejemplos (primeros {MAX_EJEMPLOS_CLIENTE} con termino):")
        print("  " + "-" * 70)
        for nombre, tid, tnombre in con_termino[:MAX_EJEMPLOS_CLIENTE]:
            print(f"     {nombre[:45]:<45} -> [{tid}] {tnombre}")


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print("[ERROR] Faltan QBO_CLIENT_ID o QBO_CLIENT_SECRET en el .env.")
        return

    print("Investigando TERMINOS / CONDICIONES DE PAGO por empresa...")

    for emp in EMPRESAS:
        print("\n" + "=" * 78)
        print(f"  {emp['nombre'].upper()}  (realm {emp['realm']})")
        print("=" * 78)
        print("  Refrescando token...")
        token = refrescar_token(emp["realm"])
        if not token:
            print(f"  [SALTADA] Sin token para {emp['nombre']}.")
            continue

        mapa = listar_terminos(emp["realm"], token)
        muestrear_clientes(emp["realm"], token, mapa)

    print("\n" + "=" * 78)
    print("  LISTO. Anota el Id de cada termino que vas a mapear.")
    print("  Fijate: si un cliente tiene termino y la factura sale con otro,")
    print("  es porque el RPA no esta mandando SalesTermRef al crear la factura.")
    print("=" * 78)


if __name__ == "__main__":
    main()
