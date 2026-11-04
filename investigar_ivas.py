"""
investigar_exento.py
--------------------
Pregunta concreta que resuelve: ¿el TaxCode objetivo (SX = 23, "Exento") esta
ACTIVO en QuickBooks EN VIVO?

Por que importa: desde el arreglo del desplegable, cuando el operador elige
"Exento" la linea guarda qbo_id=23 con objeto=TaxCode. El RPA, para objeto
TaxCode, solo necesita que ese id este en el set de TaxCodes ACTIVOS que trae
de QBO en vivo. Si 23 esta activo -> factura la linea Exento. Si no -> la frena.

Este script, por empresa:
  1) Lista los TaxCodes ACTIVOS en vivo (id | nombre).
  2) Da un VEREDICTO para el TaxCode objetivo: existe / activo / que tasa(s) de
     venta envuelve y a que %.
  3) Reproduce el chequeo del RPA: ¿el id objetivo esta en el set de activos?

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

# TaxCode que debe estar ACTIVO en vivo, por empresa (el que manda el desplegable
# cuando se elige "Exento"). Por ahora solo SX. Agrega los otros cuando toque.
TARGET_TAXCODE = {
    "9130355360397996": "23",  # Soportexperto -> "Exento"
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
    print("\n" + "=" * 78)
    print(f"  {nombre_empresa.upper()}  (realm {realm})")
    print("=" * 78)

    # ── TaxRate: solo para poder anotar nombre/valor de lo que envuelve el code ──
    r = _query(realm, token, "SELECT * FROM TaxRate MAXRESULTS 1000")
    if r.status_code != 200:
        print(f"  [ERROR TaxRate {r.status_code}] {r.text[:200]}")
        return
    rate_info = {}  # id -> (nombre, valor)
    for tr in r.json().get("QueryResponse", {}).get("TaxRate", []):
        try:
            val = round(float(tr.get("RateValue", 0)), 2)
        except (TypeError, ValueError):
            val = None
        rate_info[str(tr["Id"])] = (tr.get("Name") or "", val)

    # ── TaxCode ──
    r = _query(realm, token, "SELECT * FROM TaxCode MAXRESULTS 1000")
    if r.status_code != 200:
        print(f"  [ERROR TaxCode {r.status_code}] {r.text[:200]}")
        return
    codes = r.json().get("QueryResponse", {}).get("TaxCode", [])

    code_info = {}  # id -> (nombre, activo, [rate_ids de venta])
    activos_ids = set()  # EXACTAMENTE el set que usa el RPA (res["taxcodes"])
    for tc in codes:
        tcid = str(tc["Id"])
        tcnom = tc.get("Name") or ""
        tcact = bool(tc.get("Active", True))
        rids = []
        for det in (tc.get("SalesTaxRateList") or {}).get("TaxRateDetail", []):
            rid = str((det.get("TaxRateRef") or {}).get("value") or "")
            if rid:
                rids.append(rid)
        code_info[tcid] = (tcnom, tcact, rids)
        if tcact:
            activos_ids.add(tcid)

    # 1) Lista compacta de TaxCodes ACTIVOS
    print(f"\n  TaxCodes ACTIVOS en vivo ({len(activos_ids)}):")
    for tcid in sorted(activos_ids, key=lambda x: int(x) if x.isdigit() else 0):
        print(f"     {tcid:>4} | {code_info[tcid][0]}")

    # 2) y 3) Veredicto para el objetivo
    objetivo = TARGET_TAXCODE.get(realm)
    if not objetivo:
        print("\n  (sin TaxCode objetivo configurado para esta empresa)")
        return

    print(f"\n  --- VEREDICTO: TaxCode objetivo [{objetivo}] ---")
    if objetivo not in code_info:
        print(f"     NO EXISTE el TaxCode {objetivo} en vivo. -> ERROR, revisar id.")
        return

    nom, act, rids = code_info[objetivo]
    envs = (
        ", ".join(
            f"{rid}({rate_info.get(rid, ('?',))[0]})={rate_info.get(rid, ('', None))[1]}%"
            for rid in rids
        )
        or "(sin tasas de venta)"
    )
    print(f"     Nombre     : '{nom}'")
    print(f"     Activo     : {'SI' if act else 'NO'}")
    print(f"     Envuelve   : {envs}")

    # Chequeo EXACTO del RPA para objeto=TaxCode: ¿el id esta en el set de activos?
    puede = objetivo in activos_ids
    print(
        f"\n     Chequeo RPA (objeto=TaxCode): ¿'{objetivo}' en TaxCodes activos? "
        f"{'SI' if puede else 'NO'}"
    )
    if puede:
        print("     >> OK: el RPA PODRA facturar la linea Exento con este codigo.")
    else:
        print("     >> ERROR: el RPA FRENARA la linea Exento. Hay que REACTIVAR ese")
        print("        TaxCode en QuickBooks (con visto bueno de contabilidad), o")
        print("        usar otro codigo 0% activo.")


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print("[ERROR] Faltan QBO_CLIENT_ID o QBO_CLIENT_SECRET en el .env.")
        return

    print("Verificando TaxCode 'Exento' en vivo por empresa...")
    for emp in EMPRESAS:
        print(f"\nRefrescando token de {emp['nombre']}...")
        token = refrescar_token(emp["realm"])
        if token:
            investigar(emp["realm"], token, emp["nombre"])
        else:
            print(f"  [SALTADA] Sin token para {emp['nombre']}.")


if __name__ == "__main__":
    main()
