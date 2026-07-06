"""
listar_impuestos.py
--------------------
Reporte INVESTIGATIVO de los impuestos configurados en QuickBooks para las 3
empresas que facturan. Sirve para tener claro, con nuestros propios ojos, que
TaxCode / TaxRate existen en cada empresa y a que porcentaje corresponden.

Es EXACTAMENTE la informacion que los RPAs usan por dentro (match por rate),
pero mostrada de forma legible para revision humana.

*** SOLO SE EJECUTA EN EL VPS ***
  Consulta PRODUCCION y refresca el token de produccion (tokens_empresas.json).
  Si se corre en local se rotarian los tokens del VPS. Por eso, ademas, este
  script exige QBO_ENTORNO=produccion para siquiera arrancar (blindaje).

Seguridad (igual que sync_clientes.py / listar_items.py):
  - En QuickBooks SOLO lee (GET). No crea ni modifica NADA.
  - No toca la base de datos.
  - Refresca el token de cada empresa (Intuit rota el refresh -> se guarda).

Requiere:  pip install requests python-dotenv
"""

import os
import sys
import json
import base64

import requests
from dotenv import load_dotenv

# ── Configuracion ────────────────────────────────────────────────────────────
load_dotenv()

# Blindaje: este script es de PRODUCCION y VPS. Exige el entorno explicito.
ENTORNO = os.getenv("QBO_ENTORNO", "sandbox").strip().lower()

PROD_BASE_URL = "https://quickbooks.api.intuit.com"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")

EMPRESAS = [
    {"nombre": "Soportexperto.com S.A.", "realm": "9130355360397996"},
    {"nombre": "Hardware y Network S.A.", "realm": "9130355360390096"},
    {
        "nombre": "Corporacion Latinoamericana T.I. (Laitcorp)",
        "realm": "9130355360394696",
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
    empresa["refresh_token"] = nuevos["refresh_token"]  # Intuit rota; hay que guardarlo
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return nuevos["access_token"]


def _query(realm, token, sql):
    """GET a /query (solo lectura). Devuelve el QueryResponse o {}."""
    url = (
        f"{PROD_BASE_URL}/v3/company/{realm}/query"
        f"?query={requests.utils.quote(sql)}&minorversion=75"
    )
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"  [ERROR {resp.status_code}] {resp.text[:200]}")
        return {}
    return resp.json().get("QueryResponse", {})


def cargar_impuestos(realm, token):
    """Devuelve (rates, codes, agencies) tal como los da QuickBooks."""
    rates = _query(realm, token, "SELECT * FROM TaxRate").get("TaxRate", [])
    codes = _query(realm, token, "SELECT * FROM TaxCode").get("TaxCode", [])
    agencies = _query(realm, token, "SELECT * FROM TaxAgency").get("TaxAgency", [])
    return rates, codes, agencies


def mostrar(nombre, rates, codes, agencies):
    """Imprime el detalle de impuestos de una empresa de forma legible."""
    # 1) Mapa TaxRate Id -> (nombre, porcentaje)
    rate_info = {}
    for tr in rates:
        try:
            pct = float(tr.get("RateValue", 0))
        except (TypeError, ValueError):
            pct = None
        rate_info[tr.get("Id")] = (tr.get("Name", ""), pct, tr.get("Active", True))

    # 2) Agencias (Id -> nombre)
    agencia_nombre = {a.get("Id"): a.get("DisplayName", "") for a in agencies}

    print("\n" + "-" * 70)
    print(f"  TAX CODES (lo que se aplica a cada linea de factura)")
    print("-" * 70)
    if not codes:
        print("  (sin TaxCodes o no se pudieron leer)")
    for tc in sorted(codes, key=lambda c: str(c.get("Id"))):
        tcid = tc.get("Id")
        nombre_tc = tc.get("Name", "")
        activo = "activo" if tc.get("Active", True) else "INACTIVO"
        # Porcentaje(s) de venta asociados a este TaxCode (via su TaxRate)
        detalles = (tc.get("SalesTaxRateList") or {}).get("TaxRateDetail", [])
        pcts = []
        for det in detalles:
            rid = (det.get("TaxRateRef") or {}).get("value")
            if rid in rate_info:
                _, pct, _ = rate_info[rid]
                if pct is not None:
                    pcts.append(f"{pct:g}%")
        pcts_txt = ", ".join(pcts) if pcts else "sin tasa de venta"
        print(f"  TaxCode Id {tcid:<4} | {activo:<8} | {pcts_txt:<18} | {nombre_tc}")

    print("\n  TAX RATES (las tasas numericas configuradas)")
    print("  " + "-" * 68)
    if not rates:
        print("  (sin TaxRates)")
    for rid, (nombre_r, pct, activo) in sorted(
        rate_info.items(), key=lambda x: str(x[0])
    ):
        act = "activo" if activo else "INACTIVO"
        pct_txt = f"{pct:g}%" if pct is not None else "?"
        print(f"  TaxRate Id {rid:<4} | {act:<8} | {pct_txt:<7} | {nombre_r}")

    if agencia_nombre:
        print("\n  TAX AGENCIES (a quien se le declara)")
        print("  " + "-" * 68)
        for aid, an in sorted(agencia_nombre.items(), key=lambda x: str(x[0])):
            print(f"  Agencia Id {aid:<4} | {an}")


def main():
    # Blindaje de entorno: solo produccion (VPS).
    if ENTORNO != "produccion":
        sys.exit(
            "BLINDAJE: este script es SOLO para produccion (VPS).\n"
            "Detectado QBO_ENTORNO='%s'. Para evitar rotar por error los tokens\n"
            "del VPS desde local, solo corre con QBO_ENTORNO=produccion." % ENTORNO
        )
    if not (CLIENT_ID and CLIENT_SECRET):
        sys.exit("[ERROR] Falta QBO_CLIENT_ID / QBO_CLIENT_SECRET en el .env.")
    if not os.path.exists(TOKENS_FILE):
        sys.exit(f"[ERROR] No encuentro {TOKENS_FILE}.")

    print("=" * 70)
    print("REPORTE DE IMPUESTOS EN QUICKBOOKS (PRODUCCION, SOLO LECTURA)")
    print("=" * 70)

    for emp in EMPRESAS:
        print(f"\n=== {emp['nombre']}  (realm {emp['realm']}) ===")
        print("  Refrescando token...")
        token = refrescar_token(emp["realm"])
        if not token:
            print("  [SALTADA] no se pudo refrescar el token.")
            continue
        print("  Consultando impuestos (solo lectura)...")
        rates, codes, agencies = cargar_impuestos(emp["realm"], token)
        mostrar(emp["nombre"], rates, codes, agencies)

    print("\n" + "=" * 70)
    print("Listo. Revisa que cada empresa tenga el/los porcentaje(s) que factura.")
    print("Recorda: los RPAs hacen match por PORCENTAJE (rate), no por nombre.")
    print("=" * 70)


if __name__ == "__main__":
    main()
