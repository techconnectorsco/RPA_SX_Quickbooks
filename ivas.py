"""
investigar_ivas.py
------------------
Pregunta concreta que resuelve: ¿el TaxCode objetivo (SX = 23, "Exento") esta
ACTIVO en QuickBooks EN VIVO?

Por que importa: desde el arreglo del desplegable, cuando el operador elige
"Exento" la linea guarda qbo_id=23 con objeto=TaxCode. El RPA, para objeto
TaxCode, solo necesita que ese id este en el set de TaxCodes ACTIVOS que trae
de QBO en vivo. Si 23 esta activo -> factura la linea Exento. Si no -> la frena.
"""

import os
import json
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

PROD_BASE_URL = "https://quickbooks.api.intuit.com"
# Ruta absoluta para asegurar que lea el mismo archivo que el RPA y renovador:
TOKENS_FILE = r"C:\RPA_SX_Quickbooks\config\tokens_empresas.json"

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")

EMPRESAS = [
    {"nombre": "Soportexperto.com S.A.", "realm": "9130355360397996"},
    {"nombre": "Hardware y Network S.A.", "realm": "9130355360390096"},
    {"nombre": "Corporacion Latinoamericana T.I.", "realm": "9130355360394696"},
]

# TaxCode que debe estar ACTIVO en vivo, por empresa.
TARGET_TAXCODE = {
    "9130355360397996": "23",  # Soportexperto -> "Exento"
}


# ════════════════════════════════════════════════════════════════════════════
#  TOKENS  —  solo lectura del archivo
# ════════════════════════════════════════════════════════════════════════════

_TOKENS = {}  # realm -> access_token vivo durante esta corrida


def obtener_token(realm, forzar_refresco=False):
    """access_token valido para el realm, SIN escribir tokens_empresas.json."""
    if not forzar_refresco and realm in _TOKENS:
        return _TOKENS[realm]

    try:
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [ERROR] No se pudo leer {TOKENS_FILE}: {e}")
        return None

    empresa = data.get("empresas", {}).get(realm)
    if empresa is None:
        print(f"  [ERROR] El realm {realm} no existe en {TOKENS_FILE}.")
        return None

    # Reusar el access_token guardado (no rota nada).
    if not forzar_refresco and empresa.get("access_token"):
        _TOKENS[realm] = empresa["access_token"]
        return _TOKENS[realm]

    return None


def _get(realm, token, sql):
    url = (
        f"{PROD_BASE_URL}/v3/company/{realm}/query"
        f"?query={requests.utils.quote(sql)}&minorversion=75"
    )
    return requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )


def _query(realm, sql):
    """GET a /query. Si el token guardado vencio, falla sin refrescar."""
    token = obtener_token(realm)
    if not token:
        return None
    r = _get(realm, token, sql)
    if r.status_code == 401:
        print("  [AVISO] El token falló con 401. No se intentará refrescar en memoria.")
    return r


# ════════════════════════════════════════════════════════════════════════════
#  INVESTIGACION
# ════════════════════════════════════════════════════════════════════════════


def investigar(realm, nombre_empresa):
    print("\n" + "=" * 78)
    print(f"  {nombre_empresa.upper()}  (realm {realm})")
    print("=" * 78)

    # ── TaxRate: solo para poder anotar nombre/valor de lo que envuelve el code ──
    r = _query(realm, "SELECT * FROM TaxRate MAXRESULTS 1000")
    if r is None:
        print("  [SALTADA] sin token para esta empresa.")
        return
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
    r = _query(realm, "SELECT * FROM TaxCode MAXRESULTS 1000")
    if r is None or r.status_code != 200:
        print(f"  [ERROR TaxCode {r.status_code if r is not None else '-'}]")
        if r is not None:
            print(f"  {r.text[:200]}")
        return
    codes = r.json().get("QueryResponse", {}).get("TaxCode", [])

    code_info = {}  # id -> (nombre, activo, [rate_ids de venta])
    activos_ids = set()  # EXACTAMENTE el set que usa el RPA
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

    # Chequeo EXACTO del RPA: ¿el id esta en el set de activos?
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
    print("(solo lectura; no se escribe tokens_empresas.json)")
    for emp in EMPRESAS:
        investigar(emp["realm"], emp["nombre"])


if __name__ == "__main__":
    main()
