"""
investigar_campos_factura.py
----------------------------
Script investigativo para extraer facturas (Invoice) de QuickBooks por CADA UNA
de las 3 empresas y mostrar todos los campos que devuelve la API.

Objetivo: ver que campos reconoce QBO en una factura (CustomerMemo, PrivateNote,
etc.) para decidir cual usar como "observacion / nota al cliente" y sacar esa
notacion del calculo de lineas e items.

OJO: los campos que la API DEVUELVE en un GET no siempre son 100% identicos a los
que ACEPTA en un POST, pero CustomerMemo (mensaje visible en la factura) y
PrivateNote (nota interna) son campos estandar de lectura Y escritura: si
aparecen aca, se pueden mandar al crear la factura.

Sugerencia: mira una factura PARCIAL ya emitida (esas ya llevan CustomerMemo
poblado por el RPA) para ver exactamente como se ve ese campo con contenido.

Solo lectura (GET).
"""

import os
import json
import base64
import requests
from dotenv import load_dotenv

# ── Configuracion ────────────────────────────────────────────────────────────
load_dotenv()

PROD_BASE_URL = "https://quickbooks.api.intuit.com"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")

# Cuantas facturas traer por empresa para inspeccionar (1-5 es suficiente).
MAX_FACTURAS = 3

# Si queres inspeccionar UNA factura puntual (p.ej. una parcial que ya salio con
# nota), pone aca su Id de QBO y se leera SOLO esa, ignorando MAX_FACTURAS.
# Dejar en None para traer las mas recientes.
INVOICE_ID_PUNTUAL = None

# Las 3 empresas definidas en tu ecosistema
EMPRESAS = [
    {"nombre": "Soportexperto.com S.A.", "realm": "9130355360397996"},
    {"nombre": "Hardware y Network S.A.", "realm": "9130355360390096"},
    {"nombre": "Corporacion Latinoamericana T.I.", "realm": "9130355360394696"},
]


def refrescar_token(realm):
    """Refresca el token de una empresa y lo guarda."""
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Manejo seguro por si el realm no existe en el archivo
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


def investigar_facturas(realm, token, nombre_empresa):
    """Descarga factura(s) de la empresa y muestra su estructura completa."""
    if INVOICE_ID_PUNTUAL:
        sql = f"SELECT * FROM Invoice WHERE Id = '{INVOICE_ID_PUNTUAL}'"
    else:
        # Mas recientes primero, para ver facturas que ya salieron.
        sql = f"SELECT * FROM Invoice ORDERBY MetaData.LastUpdatedTime DESC MAXRESULTS {MAX_FACTURAS}"

    resp = _query(realm, token, sql)

    if resp.status_code != 200:
        print(f"  [ERROR {resp.status_code}] {resp.text[:200]}")
        return

    facturas = resp.json().get("QueryResponse", {}).get("Invoice", [])

    if not facturas:
        print("  No se encontraron facturas en esta empresa para analizar.")
        return

    for i, fac in enumerate(facturas, 1):
        print("\n" + "=" * 80)
        print(
            f"ESTRUCTURA DE FACTURA {i}/{len(facturas)} - {nombre_empresa.upper()}"
            f"  (Id {fac.get('Id')}  DocNumber {fac.get('DocNumber')})"
        )
        print("=" * 80)
        print(json.dumps(fac, indent=4, ensure_ascii=False))
        print("=" * 80)

        # Resumen rapido de los campos que nos interesan para la notacion.
        print("\n  --- CAMPOS RELEVANTES PARA LA NOTA / OBSERVACION ---")
        memo = fac.get("CustomerMemo")
        print(f"  CustomerMemo (nota visible en factura): {memo}")
        print(f"  PrivateNote  (nota interna, NO viaja):  {fac.get('PrivateNote')}")
        # Otros campos de texto que a veces se mapean a observaciones:
        print(
            f"  CustomerRef.name:                       "
            f"{(fac.get('CustomerRef') or {}).get('name')}"
        )
        print()


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print(
            "[ERROR] Faltan credenciales QBO_CLIENT_ID o QBO_CLIENT_SECRET en el .env."
        )
        return

    print("Iniciando investigacion de campos de FACTURA en las 3 empresas...\n")
    if INVOICE_ID_PUNTUAL:
        print(f"(Modo puntual: solo la factura Id {INVOICE_ID_PUNTUAL})\n")

    for emp in EMPRESAS:
        print(f"Analizando: {emp['nombre']} (Realm: {emp['realm']})")
        print("  Refrescando token...")

        token = refrescar_token(emp["realm"])

        if token:
            print("  Descargando factura(s) de muestra...")
            investigar_facturas(emp["realm"], token, emp["nombre"])
        else:
            print("  [SALTADA] No se pudo obtener token para esta empresa.\n")


if __name__ == "__main__":
    main()
