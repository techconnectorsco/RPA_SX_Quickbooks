"""
investigar_campos_3_empresas.py
-------------------------------
Script investigativo para extraer UN cliente de QuickBooks por CADA UNA
de las 3 empresas y mostrar todos los campos que devuelve la API.

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


def investigar_cliente_completo(realm, token, nombre_empresa):
    """Descarga 1 solo cliente de la empresa especificada y muestra su estructura."""
    sql = "SELECT * FROM Customer MAXRESULTS 1"
    url = f"{PROD_BASE_URL}/v3/company/{realm}/query?query={requests.utils.quote(sql)}&minorversion=75"

    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"  [ERROR {resp.status_code}] {resp.text[:200]}")
        return

    clientes = resp.json().get("QueryResponse", {}).get("Customer", [])

    if not clientes:
        print("  No se encontraron clientes en esta empresa para analizar.")
        return

    cliente_muestra = clientes[0]

    print("\n" + "=" * 80)
    print(f"ESTRUCTURA DEL CLIENTE - {nombre_empresa.upper()}")
    print("=" * 80)
    print(json.dumps(cliente_muestra, indent=4, ensure_ascii=False))
    print("=" * 80 + "\n")


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print(
            "[ERROR] Faltan credenciales QBO_CLIENT_ID o QBO_CLIENT_SECRET en el .env."
        )
        return

    print("Iniciando investigación de campos en las 3 empresas...\n")

    for emp in EMPRESAS:
        print(f"Analizando: {emp['nombre']} (Realm: {emp['realm']})")
        print("  Refrescando token...")

        token = refrescar_token(emp["realm"])

        if token:
            print("  Descargando cliente de muestra...")
            investigar_cliente_completo(emp["realm"], token, emp["nombre"])
        else:
            print("  [SALTADA] No se pudo obtener token para esta empresa.\n")


if __name__ == "__main__":
    main()
