"""
investigar_ivas.py
------------------
Investiga por que el RPA dice que un impuesto (ej. 'Exento (Ventas)',
TaxRate 34) NO tiene un TaxCode ACTIVO en QuickBooks, aunque en el
desplegable de la webapp SI aparezca como opcion.

Contexto: la linea de una factura en QBO referencia un TaxCode, NO un
TaxRate. El RPA toma el impuesto que eligio el operador (un TaxRate, que es
lo que guarda la webapp) y lo traduce al TaxCode ACTIVO que lo contiene. Si
ese TaxRate no esta dentro de ningun TaxCode activo, no se puede facturar
con el y el RPA frena la factura.

Este script, por empresa, imprime:
  1) TODOS los TaxRate: Id, Nombre, Activo, Valor.
  2) TODOS los TaxCode: Id, Nombre, Activo, y que TaxRate(s) de venta envuelve.
  3) Analisis enfocado en 'exento' / 'exonerad'.
  4) Para los TaxRate que le indiques en TARGET_RATE_IDS, dice a que TaxCode
     ACTIVO resuelve (o avisa que no resuelve) — reproduce la logica del RPA.

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

EMPRESAS = [
    {"nombre": "Soportexperto.com S.A.", "realm": "9130355360397996"},
    {"nombre": "Hardware y Network S.A.", "realm": "9130355360390096"},
    {"nombre": "Corporacion Latinoamericana T.I.", "realm": "9130355360394696"},
]

# TaxRate Ids que querés rastrear puntualmente por empresa (el que dio error).
# El RPA se quejo de TaxRate 34 ('Exento ( Ventas)') en Soportexperto.
TARGET_RATE_IDS = {
    "9130355360397996": ["34"],   # Soportexperto
}


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


def investigar(realm, token, nombre_empresa):
    # ── TaxRate ──
    r = _query(realm, token, "SELECT * FROM TaxRate MAXRESULTS 1000")
    rates = r.json().get("QueryResponse", {}).get("TaxRate", []) if r.status_code == 200 else []
    rate_info = {}  # id -> (nombre, activo, valor)
    for tr in rates:
        rid = str(tr["Id"])
        try:
            val = round(float(tr.get("RateValue", 0)), 2)
        except (TypeError, ValueError):
            val = None
        rate_info[rid] = (tr.get("Name") or "", bool(tr.get("Active", True)), val)

    # ── TaxCode (con las tasas de venta que envuelve) ──
    r = _query(realm, token, "SELECT * FROM TaxCode MAXRESULTS 1000")
    codes = r.json().get("QueryResponse", {}).get("TaxCode", []) if r.status_code == 200 else []

    # rate_id -> lista de (taxcode_id, taxcode_nombre, taxcode_activo) que lo envuelven en VENTAS
    rate_en_codes = {}
    code_rows = []  # (id, nombre, activo, [rate_ids venta])
    for tc in codes:
        tcid = str(tc["Id"])
        tcnom = tc.get("Name") or ""
        tcact = bool(tc.get("Active", True))
        detalles = (tc.get("SalesTaxRateList") or {}).get("TaxRateDetail", [])
        rids = []
        for det in detalles:
            rid = str((det.get("TaxRateRef") or {}).get("value") or "")
            if rid:
                rids.append(rid)
                rate_en_codes.setdefault(rid, []).append((tcid, tcnom, tcact))
        code_rows.append((tcid, tcnom, tcact, rids))

    print("\n" + "=" * 78)
    print(f"  {nombre_empresa.upper()}  (realm {realm})")
    print("=" * 78)

    print(f"\n  TAX RATES ({len(rate_info)}):")
    print(f"  {'Id':>5}  {'Act':<4} {'Valor':>7}  Nombre")
    print("  " + "-" * 60)
    for rid, (nom, act, val) in sorted(rate_info.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        print(f"  {rid:>5}  {'Si' if act else 'NO':<4} {str(val):>7}  {nom}")

    print(f"\n  TAX CODES ({len(code_rows)}):")
    for tcid, tcnom, tcact, rids in sorted(code_rows, key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        envs = ", ".join(
            f"{rid}({rate_info.get(rid, ('?',))[0]})" for rid in rids
        ) or "(sin tasas de venta)"
        print(f"  [{tcid:>5}] {'Si' if tcact else 'NO':<3} '{tcnom}'  <- envuelve: {envs}")

    # ── Analisis exento / exonerado ──
    print("\n  --- EXENTO / EXONERADO ---")
    for rid, (nom, act, val) in rate_info.items():
        n = nom.lower()
        if "exento" in n or "exonerad" in n:
            donde = rate_en_codes.get(rid, [])
            activos = [c for c in donde if c[2]]
            print(f"  TaxRate [{rid}] '{nom}' (activo={act})")
            if donde:
                for tcid, tcnom, tcact in donde:
                    print(f"       dentro de TaxCode [{tcid}] '{tcnom}' (activo={tcact})")
            else:
                print("       NO esta dentro de ningun TaxCode.")
            print(f"       -> TaxCode ACTIVO que lo envuelve: "
                  f"{[c[0] for c in activos] or 'NINGUNO (por eso el RPA frena)'}")

    # ── TaxCode llamado exactamente 'exento' (el que usa el RPA para el check exonerado) ──
    exento_tc = [tcid for tcid, tcnom, tcact, _ in code_rows
                 if " ".join(tcnom.lower().split()) == "exento" and tcact]
    print(f"\n  TaxCode ACTIVO llamado exactamente 'Exento' (res['exento']): "
          f"{exento_tc or 'NINGUNO'}")

    # ── Resolucion puntual de los TaxRate objetivo ──
    objetivos = TARGET_RATE_IDS.get(realm, [])
    if objetivos:
        print("\n  --- RESOLUCION DE LOS TAXRATE OBJETIVO ---")
        for rid in objetivos:
            info = rate_info.get(rid)
            if not info:
                print(f"  TaxRate [{rid}]: NO EXISTE en esta empresa.")
                continue
            nom, act, val = info
            activos = [c for c in rate_en_codes.get(rid, []) if c[2]]
            print(f"  TaxRate [{rid}] '{nom}' (activo={act}, valor={val})")
            if activos:
                print(f"       OK -> resuelve al TaxCode activo {activos[0][0]} '{activos[0][1]}'")
            else:
                print("       ERROR -> no hay TaxCode ACTIVO que lo contenga. "
                      "Hay que activar/crear ese TaxCode en QBO, o mapear el "
                      "impuesto 'Exento' al TaxCode correcto en la base.")


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print("[ERROR] Faltan QBO_CLIENT_ID o QBO_CLIENT_SECRET en el .env.")
        return

    print("Investigando IVAs (TaxRate vs TaxCode) por empresa...")
    for emp in EMPRESAS:
        print(f"\nRefrescando token de {emp['nombre']}...")
        token = refrescar_token(emp["realm"])
        if token:
            investigar(emp["realm"], token, emp["nombre"])
        else:
            print(f"  [SALTADA] Sin token para {emp['nombre']}.")


if __name__ == "__main__":
    main()