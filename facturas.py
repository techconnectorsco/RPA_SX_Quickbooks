"""
investigar_facturas.py
------------------------
Script de diagnóstico para consultar el volumen total de facturas,
fechas históricas (primera y última) y los campos disponibles en QuickBooks,
sin necesidad de descargar toda la base de datos.
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

EMPRESAS = [
    {
        "nombre": "Laitcorp",
        "realm": "9130355360394696",
    },
    {
        "nombre": "Soportexperto.com S.A.",
        "realm": "9130355360397996",
    },
    {
        "nombre": "Hardware y Network S.A.",
        "realm": "9130355360390096",
    },
]


def refrescar_token(realm):
    """Refresca el token de una empresa y lo guarda."""
    try:
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] No se encuentra el archivo {TOKENS_FILE}")
        return None

    if realm not in data.get("empresas", {}):
        print(f"  [ERROR] El realm {realm} no existe en tokens.")
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


def ejecutar_query_qbo(realm, token, sql):
    """Ejecuta una consulta SQL arbitraria en la API de QuickBooks."""
    url = f"{PROD_BASE_URL}/v3/company/{realm}/query?query={requests.utils.quote(sql)}&minorversion=75"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    return resp


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print("[ERROR] Faltan credenciales QBO_CLIENT_ID / QBO_CLIENT_SECRET en .env")
        return

    print("=" * 80)
    print(" INVESTIGACIÓN DE FACTURAS EN QUICKBOOKS ".center(80, "="))
    print("=" * 80)

    for emp in EMPRESAS:
        print(f"\n🏢 EMPRESA: {emp['nombre']}")
        print("-" * 80)

        token = refrescar_token(emp["realm"])
        if not token:
            print("  [SALTADA] No se pudo autenticar.")
            continue

        # 1. Obtener la cantidad total de facturas
        res_count = ejecutar_query_qbo(
            emp["realm"], token, "SELECT count(*) FROM Invoice"
        )
        if res_count.status_code == 200:
            total_facturas = (
                res_count.json().get("QueryResponse", {}).get("totalCount", 0)
            )
            print(f"  📊 Total de facturas generadas: {total_facturas}")
        else:
            print(f"  [ERROR] Al contar facturas: {res_count.text[:100]}")
            continue

        if total_facturas == 0:
            print("  No hay facturas en esta empresa.")
            continue

        # 2. Obtener la factura más antigua (Primera factura)
        # Usamos ORDERBY ASC y MAXRESULTS 1 para traer solo el primer registro de la historia
        res_asc = ejecutar_query_qbo(
            emp["realm"],
            token,
            "SELECT * FROM Invoice ORDERBY MetaData.CreateTime ASC STARTPOSITION 1 MAXRESULTS 1",
        )
        if res_asc.status_code == 200:
            primera = res_asc.json().get("QueryResponse", {}).get("Invoice", [])
            if primera:
                fecha_primera = primera[0].get("MetaData", {}).get("CreateTime", "N/A")
                print(f"  📅 Fecha de la PRIMERA factura: {fecha_primera}")

                # Aprovechamos este registro para extraer todos los campos disponibles
                campos = list(primera[0].keys())
                campos_str = ", ".join(campos)
        else:
            print("  [ERROR] No se pudo obtener la primera factura.")

        # 3. Obtener la factura más reciente (Última factura)
        # Usamos ORDERBY DESC para traer la última generada
        res_desc = ejecutar_query_qbo(
            emp["realm"],
            token,
            "SELECT * FROM Invoice ORDERBY MetaData.CreateTime DESC STARTPOSITION 1 MAXRESULTS 1",
        )
        if res_desc.status_code == 200:
            ultima = res_desc.json().get("QueryResponse", {}).get("Invoice", [])
            if ultima:
                fecha_ultima = ultima[0].get("MetaData", {}).get("CreateTime", "N/A")
                print(f"  📅 Fecha de la ÚLTIMA factura:  {fecha_ultima}")

        # Mostrar los campos descubiertos
        print("\n  🔍 Campos disponibles en el objeto Invoice:")
        print(f"  {campos_str}")
        print("=" * 80)


if __name__ == "__main__":
    main()
