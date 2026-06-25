"""
crear_factura_prueba_sandbox.py
-------------------------------
Crea UNA factura de prueba en el SANDBOX de QuickBooks y muestra lo que devuelve.
Refresca el token de sandbox SOLO (ya no hay que volver al Playground).

BLINDAJE:
  - URL fija de sandbox. No se lee del .env.
  - Aborta si el realm es de produccion.
  - Lee/escribe el token en config/tokens_sandbox.json.

Requiere en el .env:
  QBO_SANDBOX_CLIENT_ID=...
  QBO_SANDBOX_CLIENT_SECRET=...
Requiere en config/tokens_sandbox.json:
  { "realm_id": "9341456664539574", "refresh_token": "RT1-..." }
"""

import json
import os
import sys
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

# --- BLINDAJE 1: URL fija de sandbox ---
SANDBOX_BASE_URL = "https://sandbox-quickbooks.api.intuit.com"

# --- BLINDAJE 2: realms de PRODUCCION que este script NUNCA debe tocar ---
REALMS_PRODUCCION = {
    "9130355360397996",  # Soportexperto
    "9130355360394696",  # Corporacion Latinoamericana
    "9130355360390096",  # Hardware y Network
}

TOKENS_SANDBOX_FILE = os.path.join("config", "tokens_sandbox.json")

CLIENT_ID = os.getenv("QBO_SANDBOX_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_SANDBOX_CLIENT_SECRET")

# --- Datos de JUGUETE: cliente e item que YA existen en tu sandbox ---
CUSTOMER_ID_PRUEBA = "1"  # Amy's Bird Sanctuary
ITEM_ID_PRUEBA = "2"  # Hours
CANTIDAD = 2
PRECIO_UNITARIO = 50.0


def refrescar_token_sandbox():
    """Refresca el access_token del sandbox usando el refresh_token. Lo guarda."""
    if not (CLIENT_ID and CLIENT_SECRET):
        print(
            "[ERROR] Falta QBO_SANDBOX_CLIENT_ID / QBO_SANDBOX_CLIENT_SECRET en el .env."
        )
        sys.exit(1)
    if not os.path.exists(TOKENS_SANDBOX_FILE):
        print(f"[ERROR] No existe {TOKENS_SANDBOX_FILE}.")
        sys.exit(1)

    with open(TOKENS_SANDBOX_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    realm_id = str(data.get("realm_id", "")).strip()
    refresh_token = str(data.get("refresh_token", "")).strip()

    if realm_id in REALMS_PRODUCCION:
        print("[ABORTADO] Ese realm es de PRODUCCION. Este script es solo sandbox.")
        sys.exit(1)
    if not realm_id or not refresh_token:
        print("[ERROR] Falta realm_id o refresh_token en config/tokens_sandbox.json.")
        sys.exit(1)

    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"[ERROR refresco] {resp.status_code} {resp.text[:300]}")
        print(
            "Si dice invalid_grant, el refresh_token vencio: saca uno nuevo del Playground."
        )
        sys.exit(1)

    nuevos = resp.json()
    data["access_token"] = nuevos["access_token"]
    data["refresh_token"] = nuevos[
        "refresh_token"
    ]  # Intuit rota el refresh; hay que guardarlo
    with open(TOKENS_SANDBOX_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    print("    Token de sandbox refrescado.")
    return realm_id, nuevos["access_token"]


def main():
    print("=" * 60)
    print("CREANDO FACTURA DE PRUEBA EN SANDBOX")
    print("=" * 60)

    print("\n[1] Refrescando token...")
    realm_id, access_token = refrescar_token_sandbox()
    assert "sandbox" in SANDBOX_BASE_URL, "La URL no es de sandbox. Abortando."

    monto = round(CANTIDAD * PRECIO_UNITARIO, 2)
    factura = {
        "Line": [
            {
                "DetailType": "SalesItemLineDetail",
                "Amount": monto,
                "Description": "Factura de prueba creada por el RPA (sandbox)",
                "SalesItemLineDetail": {
                    "ItemRef": {"value": ITEM_ID_PRUEBA},
                    "Qty": CANTIDAD,
                    "UnitPrice": PRECIO_UNITARIO,
                },
            }
        ],
        "CustomerRef": {"value": CUSTOMER_ID_PRUEBA},
    }

    url = f"{SANDBOX_BASE_URL}/v3/company/{realm_id}/invoice?minorversion=75"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    print(
        f"[2] Creando factura (cliente {CUSTOMER_ID_PRUEBA}, item {ITEM_ID_PRUEBA}, monto {monto})..."
    )
    resp = requests.post(url, headers=headers, json=factura, timeout=30)

    if resp.status_code not in (200, 201):
        print(f"\n[ERROR {resp.status_code}]")
        print(resp.text[:800])
        sys.exit(1)

    inv = resp.json().get("Invoice", {})
    print("\n FACTURA CREADA. Esto es lo que devolvio QuickBooks:")
    print(f"  Id (qbo_invoice_id) : {inv.get('Id')}")
    print(f"  DocNumber           : {inv.get('DocNumber')}")
    print(f"  Total               : {inv.get('TotalAmt')}")
    print(f"  Cliente             : {inv.get('CustomerRef', {}).get('name')}")
    print(f"  Fecha               : {inv.get('TxnDate')}")

    print("\n--- JSON crudo completo de la respuesta ---")
    print(json.dumps(inv, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
