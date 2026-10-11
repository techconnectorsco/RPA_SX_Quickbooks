"""
investigar_qb_hyn.py
--------------------
Investigacion ONE-SHOT contra QuickBooks PRODUCCION, realm de Hardware & Network.
Solo lectura. Nada de escritura, nada de facturas.

Que investiga:
  1) Facturas recientes (ultimas 10) — muestra TODOS los CustomField con nombre
     y valor. Objetivo: ubicar el campo que Lucia llama "Cotizacion" y donde
     ella guarda el numero del pedido/cotizacion del CRM.
  2) Items activos de H&N — para confirmar el item 157 (Venta de Servicios y
     Proyectos) y ver si hay otros que podamos usar como match futuro.
  3) TaxCodes y TaxRates activos — para que el servicio de match en SvelteKit
     sepa que IDs existen (ej. el "Exento" de H&N tiene Id 22) y el porcentaje
     que resuelve cada uno.

Corre en el VPS (donde esta el .env de produccion). Blindaje: hardcoded a
produccion y al realm de H&N, ignora QBO_ENTORNO.
"""

import os
import json
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG HARDCODEADA (produccion H&N, solo lectura) ──────────────────────
BASE_URL = "https://quickbooks.api.intuit.com"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")
REALM_HYN = "9130355360390096"  # Hardware y Network S.A.
NOMBRE_EMPRESA = "Hardware y Network S.A."

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")

# Facturas recientes a traer para inspeccionar CustomFields
MAX_FACTURAS = 10

# Trae facturas anteriores a esta fecha (para asegurar que ya pasaron por
# Mondragon y tienen el consecutivo definitivo). Ajusta a hace >24h.
FACTURAS_ANTES_DE = "2026-10-10"


# ─── TOKEN ──────────────────────────────────────────────────────────────────
def refrescar_token():
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    empresa = data["empresas"][REALM_HYN]
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
        raise SystemExit(f"[ERROR refresco token] {resp.status_code} {resp.text[:200]}")
    nuevos = resp.json()
    empresa["access_token"] = nuevos["access_token"]
    empresa["refresh_token"] = nuevos["refresh_token"]
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return nuevos["access_token"]


def _query(token, sql):
    url = (
        f"{BASE_URL}/v3/company/{REALM_HYN}/query"
        f"?query={requests.utils.quote(sql)}&minorversion=75"
    )
    return requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )


# ─── 1) FACTURAS: buscar el campo "Cotizacion" ──────────────────────────────
def investigar_facturas(token):
    print("\n" + "=" * 78)
    print("  1) FACTURAS RECIENTES — Buscar campo 'Cotizacion'")
    print("=" * 78)
    print(f"  (ultimas {MAX_FACTURAS} facturas anteriores a {FACTURAS_ANTES_DE})\n")

    sql = (
        f"SELECT * FROM Invoice "
        f"WHERE TxnDate < '{FACTURAS_ANTES_DE}' "
        f"ORDERBY TxnDate DESC MAXRESULTS {MAX_FACTURAS}"
    )
    resp = _query(token, sql)
    if resp.status_code != 200:
        print(f"  [ERROR {resp.status_code}] {resp.text[:300]}")
        return

    facturas = resp.json().get("QueryResponse", {}).get("Invoice", [])
    if not facturas:
        print("  No se encontraron facturas anteriores a la fecha de corte.")
        return

    print(f"  {len(facturas)} facturas encontradas.\n")

    # Agregamos un diccionario para contar en que CustomField aparece algo
    # con pinta de "cotizacion" (COT, QUO, ORD) para identificar el campo.
    posibles_campos_cotizacion = {}

    for fac in facturas:
        inv_id = fac.get("Id")
        docnum = fac.get("DocNumber")
        cliente = (fac.get("CustomerRef") or {}).get("name", "?")
        txn_date = fac.get("TxnDate", "?")
        total = fac.get("TotalAmt", 0)

        print(f"  ────────────────────────────────────────────────")
        print(f"  Invoice Id: {inv_id}   DocNumber: {docnum}")
        print(f"  Fecha: {txn_date}   Total: {total}")
        print(f"  Cliente: {cliente}")

        campos = fac.get("CustomField", [])
        if not campos:
            print("  CustomFields: (ninguno)")
        else:
            print("  CustomFields:")
            for cf in campos:
                nombre = (cf.get("Name") or "").strip()
                defid = cf.get("DefinitionId")
                valor = cf.get("StringValue", "")
                marca = ""
                if valor:
                    val_up = valor.upper()
                    if any(p in val_up for p in ("COT", "QUO", "ORD")):
                        marca = "  <-- posible COTIZACION"
                        posibles_campos_cotizacion[nombre] = (
                            posibles_campos_cotizacion.get(nombre, 0) + 1
                        )
                print(f"     [Def {defid}] '{nombre}' = '{valor}'{marca}")

    print("\n  ─── RESUMEN ───")
    if posibles_campos_cotizacion:
        print(
            "  Campos que aparecen con valores tipo COT/QUO/ORD (posibles 'Cotizacion'):"
        )
        for nombre, cant in sorted(
            posibles_campos_cotizacion.items(), key=lambda x: -x[1]
        ):
            print(f"     '{nombre}' aparecio {cant} vez(es) con dato que parece codigo")
    else:
        print("  No se detectaron campos con pinta de 'Cotizacion' en esta muestra.")
        print("  Probar aumentando MAX_FACTURAS o cambiando FACTURAS_ANTES_DE.")


# ─── 2) ITEMS activos ───────────────────────────────────────────────────────
def investigar_items(token):
    print("\n" + "=" * 78)
    print("  2) ITEMS ACTIVOS — Confirmar item 157 y ver alternativas")
    print("=" * 78)

    resp = _query(
        token,
        "SELECT * FROM Item WHERE Active = true ORDERBY Name MAXRESULTS 500",
    )
    if resp.status_code != 200:
        print(f"  [ERROR {resp.status_code}] {resp.text[:300]}")
        return

    items = resp.json().get("QueryResponse", {}).get("Item", [])
    print(f"  {len(items)} items activos.\n")

    for it in items:
        iid = it.get("Id")
        nombre = it.get("Name", "")
        tipo = it.get("Type", "")
        desc = (it.get("Description") or "")[:60]
        marca = "  <-- ITEM DEFAULT H&N" if iid == "157" else ""
        print(f"    Id {iid:>5}  [{tipo:<10}]  {nombre:<50}  {desc}{marca}")


# ─── 3) TaxCodes y TaxRates activos ─────────────────────────────────────────
def investigar_impuestos(token):
    print("\n" + "=" * 78)
    print("  3) TAXCODES Y TAXRATES ACTIVOS — Para el resolvedor de impuestos")
    print("=" * 78)

    # TaxRates
    print("\n  --- TAX RATES ---")
    resp = _query(token, "SELECT * FROM TaxRate MAXRESULTS 100")
    if resp.status_code == 200:
        rates = resp.json().get("QueryResponse", {}).get("TaxRate", [])
        print(f"  {len(rates)} TaxRates:\n")
        for tr in sorted(rates, key=lambda x: x.get("Name", "")):
            rid = tr.get("Id")
            nombre = tr.get("Name", "")
            activo = tr.get("Active", True)
            pct = tr.get("RateValue", 0)
            marca = "" if activo else "  [INACTIVO]"
            print(f"    Id {rid:>5}  {pct:>6}%  {nombre}{marca}")

    # TaxCodes
    print("\n  --- TAX CODES ---")
    resp = _query(token, "SELECT * FROM TaxCode MAXRESULTS 100")
    if resp.status_code == 200:
        codes = resp.json().get("QueryResponse", {}).get("TaxCode", [])
        print(f"  {len(codes)} TaxCodes:\n")
        for tc in sorted(codes, key=lambda x: x.get("Name", "")):
            cid = tc.get("Id")
            nombre = tc.get("Name", "")
            activo = tc.get("Active", True)
            marca_act = "" if activo else "  [INACTIVO]"
            marca_ex = "  <-- EXENTO" if nombre.lower().strip() == "exento" else ""
            detalles = (tc.get("SalesTaxRateList") or {}).get("TaxRateDetail", [])
            rate_ids = [str((d.get("TaxRateRef") or {}).get("value")) for d in detalles]
            print(
                f"    Id {cid:>5}  '{nombre}'{marca_act}{marca_ex}"
                f"      TaxRates que lo componen: {rate_ids}"
            )


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        raise SystemExit("[ERROR] Faltan QBO_CLIENT_ID o QBO_CLIENT_SECRET en el .env.")

    print("=" * 78)
    print("  INVESTIGACION QUICKBOOKS — HARDWARE & NETWORK (produccion)")
    print(f"  Realm: {REALM_HYN}")
    print("  MODO: SOLO LECTURA — nada se escribe, nada se crea, nada se factura.")
    print("=" * 78)

    print("\nRefrescando token de H&N...")
    token = refrescar_token()
    print("  OK")

    investigar_facturas(token)
    investigar_items(token)
    investigar_impuestos(token)

    print("\n" + "=" * 78)
    print("  LISTO. Guarda este output; lo necesitamos para armar el servicio de")
    print("  match en SvelteKit (hyn-match.ts) y para el payload del RPA.")
    print("=" * 78)


if __name__ == "__main__":
    main()
