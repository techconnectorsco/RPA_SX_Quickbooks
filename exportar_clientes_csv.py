"""
exportar_clientes_csv.py
------------------------
Script de extracción de clientes de QuickBooks para análisis y migración.
Lee todos los clientes de las 3 empresas y genera un archivo CSV con la
nueva estructura propuesta para la base de datos.

SEGURIDAD:
  - Solo lectura (GET) en QuickBooks.
  - No requiere conexión a la base de datos local (evita bloqueos o alteraciones).
  - Manejo seguro de campos anidados (JSON).
"""

import os
import json
import base64
import requests
import csv
from dotenv import load_dotenv

# ── Configuracion ────────────────────────────────────────────────────────────
load_dotenv()

PROD_BASE_URL = "https://quickbooks.api.intuit.com"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")
CSV_OUTPUT_FILE = "clientes_quickbooks_export.csv"

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")

# Las 3 empresas con sus UUID de base de datos y Realm IDs
EMPRESAS = [
    {
        "nombre": "Laitcorp",
        "realm": "9130355360394696",
        "uuid": "01d18328-dccf-493b-aca7-05c5d74900a0",
    },
    {
        "nombre": "Soportexperto.com S.A.",
        "realm": "9130355360397996",
        "uuid": "ec006548-c1a1-4212-aaf5-605041ce7d3e",
    },
    {
        "nombre": "Hardware y Network S.A.",
        "realm": "9130355360390096",
        "uuid": "fc3e4394-5954-41d7-b502-5b38db52fae5",
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


def descargar_clientes_completos(realm, token):
    """Baja TODOS los clientes de una empresa manejando la paginación."""
    clientes, inicio, lote = [], 1, 100
    while True:
        sql = f"SELECT * FROM Customer STARTPOSITION {inicio} MAXRESULTS {lote}"
        url = f"{PROD_BASE_URL}/v3/company/{realm}/query?query={requests.utils.quote(sql)}&minorversion=75"

        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"  [ERROR API {resp.status_code}] {resp.text[:200]}")
            break

        lote_res = resp.json().get("QueryResponse", {}).get("Customer", [])
        if not lote_res:
            break

        clientes.extend(lote_res)
        if len(lote_res) < lote:
            break
        inicio += lote

    return clientes


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print("[ERROR] Faltan credenciales QBO_CLIENT_ID / QBO_CLIENT_SECRET en .env")
        return

    print("=" * 70)
    print("EXTRACCIÓN DE CLIENTES QBO A CSV")
    print("=" * 70)

    # Definir los encabezados del CSV exactamente como los solicitaste
    encabezados = [
        "uuid_empresa",
        "nombre_empresa",
        "qbo_customer_id",
        "display_name",
        "qbo_moneda",
        "qbo_email",
        "qbo_telefono_primary",
        "qbo_telefono_movil",
        "qbo_terminos_pago",
        "activo",
        "qbo_notas",
        "qbo_last_update",
    ]

    total_registros = 0

    # Abrir el archivo CSV para escritura
    with open(CSV_OUTPUT_FILE, mode="w", newline="", encoding="utf-8") as archivo_csv:
        escritor = csv.DictWriter(archivo_csv, fieldnames=encabezados)
        escritor.writeheader()

        for emp in EMPRESAS:
            print(f"\nProcesando: {emp['nombre']}")
            print("  Refrescando token...")
            token = refrescar_token(emp["realm"])

            if not token:
                print("  [SALTADA] No se pudo autenticar.")
                continue

            print("  Descargando clientes...")
            clientes_qbo = descargar_clientes_completos(emp["realm"], token)
            print(f"  Se encontraron {len(clientes_qbo)} clientes. Exportando a CSV...")

            for c in clientes_qbo:
                # Limpieza de notas: quitar saltos de línea para que no rompan visualmente el CSV
                notas_crudas = c.get("Notes", "")
                notas_limpias = (
                    notas_crudas.replace("\n", " ").replace("\r", " ").strip()
                    if notas_crudas
                    else ""
                )

                # Armar el diccionario para la fila del CSV
                fila = {
                    "uuid_empresa": emp["uuid"],
                    "nombre_empresa": emp["nombre"],
                    "qbo_customer_id": c.get("Id", ""),
                    "display_name": c.get("DisplayName", ""),
                    "qbo_moneda": (c.get("CurrencyRef") or {}).get("value", ""),
                    "qbo_email": (c.get("PrimaryEmailAddr") or {}).get("Address", ""),
                    "qbo_telefono_primary": (c.get("PrimaryPhone") or {}).get(
                        "FreeFormNumber", ""
                    ),
                    "qbo_telefono_movil": (c.get("Mobile") or {}).get(
                        "FreeFormNumber", ""
                    ),
                    "qbo_terminos_pago": (c.get("SalesTermRef") or {}).get("name", ""),
                    "activo": c.get("Active", True),
                    "qbo_notas": notas_limpias,
                    "qbo_last_update": (c.get("MetaData") or {}).get(
                        "LastUpdatedTime", ""
                    ),
                }

                escritor.writerow(fila)
                total_registros += 1

    print("\n" + "=" * 70)
    print(f"PROCESO TERMINADO. Se exportaron {total_registros} clientes en total.")
    print(f"Archivo guardado como: {CSV_OUTPUT_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    main()
