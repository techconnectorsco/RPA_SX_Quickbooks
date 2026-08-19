"""
investigar_consecutivo.py
-------------------------
Localiza EN QUE CAMPO de QuickBooks quedo el consecutivo real de Hacienda,
por cada una de las 3 empresas, usando facturas VIEJAS (ya procesadas por
Mondragon: de ayer/antier, con >24h para garantizar que el consecutivo ya
fue devuelto).

Por que existe: al crear la factura el RPA guarda un DocNumber preliminar
(ej. 75623573). Mondragon, minutos despues, reescribe el DocNumber al
formato definitivo (FE-xxxx) y/o llena un CustomField con el consecutivo
largo (ej. 00200001010000004633). PERO ese CustomField cambia de nombre y
posicion entre empresas, y en algunas el numero quedo en "COTIZACION" en vez
de "No. Consecutivo". Este script muestra TODOS los CustomField con su nombre
y valor para las 3 empresas, y asi decidimos de que campo lo saca el RPA en
cada una.

Solo lectura (GET).
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

# Cuantas facturas viejas traer por empresa. Con 5 se ve el patron claro.
MAX_FACTURAS = 5

# Trae facturas ANTERIORES a esta fecha (para asegurar que ya pasaron por
# Mondragon). Formato 'YYYY-MM-DD'. Ajusta a ayer/antier segun necesites.
FACTURAS_ANTES_DE = "2026-08-18"

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


def investigar(realm, token, nombre_empresa):
    # Facturas anteriores a la fecha de corte, mas recientes primero.
    sql = (
        f"SELECT * FROM Invoice "
        f"WHERE TxnDate < '{FACTURAS_ANTES_DE}' "
        f"ORDERBY TxnDate DESC MAXRESULTS {MAX_FACTURAS}"
    )
    resp = _query(realm, token, sql)
    if resp.status_code != 200:
        print(f"  [ERROR {resp.status_code}] {resp.text[:200]}")
        return

    facturas = resp.json().get("QueryResponse", {}).get("Invoice", [])
    if not facturas:
        print("  No se encontraron facturas anteriores a la fecha de corte.")
        return

    print("\n" + "=" * 78)
    print(f"  {nombre_empresa.upper()}  (realm {realm})")
    print("=" * 78)

    for fac in facturas:
        inv_id = fac.get("Id")
        docnum = fac.get("DocNumber")
        cliente = (fac.get("CustomerRef") or {}).get("name", "?")
        memo = (fac.get("CustomerMemo") or {}).get("value", "")

        print(f"\n  Invoice Id: {inv_id}   DocNumber: {docnum}")
        print(f"  Cliente: {cliente}")

        # TODOS los CustomField con su nombre, DefinitionId y valor.
        print("  CustomFields:")
        campos = fac.get("CustomField", [])
        if not campos:
            print("     (ninguno)")
        for cf in campos:
            nombre = (cf.get("Name") or "").strip()
            defid = cf.get("DefinitionId")
            valor = cf.get("StringValue", "")
            marca = "  <-- TIENE DATO" if valor else ""
            print(f"     [Def {defid}] '{nombre}' = '{valor}'{marca}")

        # CustomerMemo suele traer la Clave (50 digitos) + TC.
        primera_linea_memo = memo.split("\n")[0] if memo else ""
        print(f"  CustomerMemo (1a linea): {primera_linea_memo}")

    # Resumen: que campo tiene pinta de consecutivo en esta empresa.
    print("\n  --- PISTA PARA EL RPA ---")
    print("  Busca arriba cual CustomField trae el numero largo tipo")
    print("  00X00001010000XXXXXX (ese es el consecutivo de Hacienda).")
    print("  Anota el NOMBRE del campo (no el DefinitionId, que cambia).")


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print("[ERROR] Faltan QBO_CLIENT_ID o QBO_CLIENT_SECRET en el .env.")
        return

    print("Investigando donde quedo el consecutivo de Hacienda por empresa...")
    print(f"(Facturas anteriores a {FACTURAS_ANTES_DE}, {MAX_FACTURAS} por empresa)")

    for emp in EMPRESAS:
        print(f"\nRefrescando token de {emp['nombre']}...")
        token = refrescar_token(emp["realm"])
        if token:
            investigar(emp["realm"], token, emp["nombre"])
        else:
            print(f"  [SALTADA] Sin token para {emp['nombre']}.")


if __name__ == "__main__":
    main()
