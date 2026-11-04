"""
listar_impuestos.py
--------------------
Reporte INVESTIGATIVO de los impuestos configurados en QuickBooks para las 3
empresas que facturan. Sirve para tener claro, con nuestros propios ojos, que
TaxCode / TaxRate existen en cada empresa y a que porcentaje corresponden.

Es EXACTAMENTE la informacion que los RPAs usan por dentro (match por rate),
pero mostrada de forma legible para revision humana.

*** SOLO SE EJECUTA EN EL VPS ***
  Consulta PRODUCCION. Exige QBO_ENTORNO=produccion para siquiera arrancar.

*** NO ESCRIBE tokens_empresas.json ***
  Usa el access_token que ya esta guardado (dura 1 hora). Si vencio, refresca
  EN MEMORIA y reintenta una sola vez, sin guardar nada. Asi este script se
  puede correr cuantas veces haga falta sin romper la cadena de tokens que usa
  el RPA de produccion.

Seguridad:
  - En QuickBooks SOLO lee (GET). No crea ni modifica NADA.
  - No toca la base de datos.
  - No rota ni guarda tokens.

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


# ════════════════════════════════════════════════════════════════════════════
#  TOKENS  —  solo lectura del archivo; el refresco es en memoria
# ════════════════════════════════════════════════════════════════════════════

_TOKENS = {}  # realm -> access_token vivo durante esta corrida


def obtener_token(realm, forzar_refresco=False):
    """access_token valido para el realm, SIN escribir tokens_empresas.json.

    Camino normal: devuelve el access_token ya guardado (QuickBooks lo da con
    1 hora de vida y el RPA lo renueva en cada corrida), asi no se rota nada.
    Solo si ese token vencio (401) se refresca, y el refresh_token nuevo se
    usa en memoria y se descarta: el guardado sigue sirviendo porque Intuit
    mantiene vigente el anterior durante su ventana de gracia.
    """
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

    # 1) Reusar el access_token guardado (no rota nada).
    if not forzar_refresco and empresa.get("access_token"):
        _TOKENS[realm] = empresa["access_token"]
        return _TOKENS[realm]

    # 2) Vencido: refresco EN MEMORIA. El archivo NO se toca.
    if not empresa.get("refresh_token"):
        print(f"  [ERROR] El realm {realm} no tiene refresh_token guardado.")
        return None

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

    print("  (access_token vencido: refrescado en memoria, archivo intacto)")
    _TOKENS[realm] = resp.json()["access_token"]
    return _TOKENS[realm]


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
    """GET a /query (solo lectura). Devuelve el QueryResponse o {}.

    Si el access_token guardado vencio, refresca en memoria y reintenta 1 vez.
    """
    token = obtener_token(realm)
    if not token:
        return {}

    resp = _get(realm, token, sql)
    if resp.status_code == 401:
        token = obtener_token(realm, forzar_refresco=True)
        if not token:
            return {}
        resp = _get(realm, token, sql)

    if resp.status_code != 200:
        print(f"  [ERROR {resp.status_code}] {resp.text[:200]}")
        return {}
    return resp.json().get("QueryResponse", {})


# ════════════════════════════════════════════════════════════════════════════
#  REPORTE
# ════════════════════════════════════════════════════════════════════════════


def cargar_impuestos(realm):
    """Devuelve (rates, codes, agencies) tal como los da QuickBooks."""
    rates = _query(realm, "SELECT * FROM TaxRate").get("TaxRate", [])
    codes = _query(realm, "SELECT * FROM TaxCode").get("TaxCode", [])
    agencies = _query(realm, "SELECT * FROM TaxAgency").get("TaxAgency", [])
    return rates, codes, agencies


def mostrar(nombre, rates, codes, agencies):
    """Imprime el detalle de impuestos de una empresa de forma legible."""
    # 1) Mapa TaxRate Id -> (nombre, porcentaje, activo)
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
    print("  TAX CODES (lo que se aplica a cada linea de factura)")
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
            "Detectado QBO_ENTORNO='%s'. Corre solo con QBO_ENTORNO=produccion."
            % ENTORNO
        )
    if not (CLIENT_ID and CLIENT_SECRET):
        sys.exit("[ERROR] Falta QBO_CLIENT_ID / QBO_CLIENT_SECRET en el .env.")
    if not os.path.exists(TOKENS_FILE):
        sys.exit(f"[ERROR] No encuentro {TOKENS_FILE}.")

    print("=" * 70)
    print("REPORTE DE IMPUESTOS EN QUICKBOOKS (PRODUCCION, SOLO LECTURA)")
    print("(no se escribe tokens_empresas.json)")
    print("=" * 70)

    for emp in EMPRESAS:
        print(f"\n=== {emp['nombre']}  (realm {emp['realm']}) ===")
        print("  Consultando impuestos (solo lectura)...")
        rates, codes, agencies = cargar_impuestos(emp["realm"])
        mostrar(emp["nombre"], rates, codes, agencies)

    print("\n" + "=" * 70)
    print("Listo. Revisa que cada empresa tenga el/los porcentaje(s) que factura.")
    print("Recorda: los RPAs hacen match por PORCENTAJE (rate), no por nombre.")
    print("=" * 70)


if __name__ == "__main__":
    main()
