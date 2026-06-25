"""
preparar_iva_sandbox.py
-----------------------
Intenta crear un impuesto del 13% (IVA) en el sandbox y crea una factura de
prueba aplicandolo, para VER si QuickBooks calcula el 13% o no.

Solo sandbox. Refresca el token solo.

Si ya creaste el cliente (Id 58) e item (Id 19) con preparar_sandbox.py,
estan puestos abajo como constantes.
"""

import json
import os
import sys
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

SANDBOX_BASE_URL = "https://sandbox-quickbooks.api.intuit.com"
REALMS_PRODUCCION = {"9130355360397996", "9130355360394696", "9130355360390096"}
TOKENS_SANDBOX_FILE = os.path.join("config", "tokens_sandbox.json")

CLIENT_ID = os.getenv("QBO_SANDBOX_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_SANDBOX_CLIENT_SECRET")

CUSTOMER_ID = "58"  # Cliente Prueba CR S.A.
ITEM_ID = "19"  # CONTRATOS HORAS ADICIONALES
CANTIDAD = 2
PRECIO_UNITARIO = 50.0


def refrescar():
    if not (CLIENT_ID and CLIENT_SECRET):
        print(
            "[ERROR] Faltan QBO_SANDBOX_CLIENT_ID / QBO_SANDBOX_CLIENT_SECRET en el .env."
        )
        sys.exit(1)
    with open(TOKENS_SANDBOX_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    realm = str(data.get("realm_id", "")).strip()
    rt = str(data.get("refresh_token", "")).strip()
    if realm in REALMS_PRODUCCION:
        print("[ABORTADO] Ese realm es de PRODUCCION. Solo sandbox.")
        sys.exit(1)
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"grant_type": "refresh_token", "refresh_token": rt},
        timeout=30,
    )
    if r.status_code != 200:
        print("[ERROR refresco]", r.status_code, r.text[:200])
        sys.exit(1)
    n = r.json()
    data["access_token"] = n["access_token"]
    data["refresh_token"] = n["refresh_token"]
    with open(TOKENS_SANDBOX_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return realm, n["access_token"]


def headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def query(realm, token, sql):
    url = (
        f"{SANDBOX_BASE_URL}/v3/company/{realm}/query?query={requests.utils.quote(sql)}"
    )
    return requests.get(url, headers=headers(token), timeout=30)


def buscar_taxcode_13(realm, token):
    """Busca un TaxCode existente que ya tenga 13%. Devuelve su Id o None."""
    r = query(realm, token, "SELECT * FROM TaxCode")
    if r.status_code != 200:
        return None
    for tc in r.json().get("QueryResponse", {}).get("TaxCode", []):
        # buscamos por nombre que mencione 13
        if "13" in (tc.get("Name") or ""):
            return tc.get("Id")
    return None


def crear_taxcode_13(realm, token):
    """Crea un TaxCode 13% via el recurso taxservice."""
    url = f"{SANDBOX_BASE_URL}/v3/company/{realm}/taxservice/taxcode?minorversion=75"
    payload = {
        "TaxCode": "IVA 13%",
        "TaxRateDetails": [
            {
                "TaxRateName": "IVA 13%",
                "RateValue": 13,
                "TaxAgencyId": "1",
                "TaxApplicableOn": "Sales",
            }
        ],
    }
    r = requests.post(url, headers=headers(token), json=payload, timeout=30)
    print(f"  taxservice respondio {r.status_code}")
    print("  " + r.text[:500])
    if r.status_code in (200, 201):
        data = r.json()
        # la respuesta trae el TaxCodeId
        tcid = data.get("TaxCodeId") or data.get("TaxCode", {}).get("Id")
        return tcid
    return None


def crear_factura(realm, token, taxcode_id):
    monto = round(CANTIDAD * PRECIO_UNITARIO, 2)
    factura = {
        "Line": [
            {
                "DetailType": "SalesItemLineDetail",
                "Amount": monto,
                "Description": "Prueba de IVA 13% (sandbox)",
                "SalesItemLineDetail": {
                    "ItemRef": {"value": ITEM_ID},
                    "Qty": CANTIDAD,
                    "UnitPrice": PRECIO_UNITARIO,
                    "TaxCodeRef": {"value": "TAX"},  # marca la linea como gravable
                },
            }
        ],
        "CustomerRef": {"value": CUSTOMER_ID},
    }
    if taxcode_id:
        factura["TxnTaxDetail"] = {"TxnTaxCodeRef": {"value": str(taxcode_id)}}

    url = f"{SANDBOX_BASE_URL}/v3/company/{realm}/invoice?minorversion=75"
    r = requests.post(url, headers=headers(token), json=factura, timeout=30)
    if r.status_code not in (200, 201):
        print(f"  [ERROR factura {r.status_code}] {r.text[:400]}")
        return
    inv = r.json()["Invoice"]
    print(f"\n  Factura creada -> Id {inv.get('Id')}  DocNumber {inv.get('DocNumber')}")
    print(f"  Subtotal (sin imp.) : {inv.get('Line', [{}])[-1].get('Amount')}")
    print(f"  Impuesto calculado  : {inv.get('TxnTaxDetail', {}).get('TotalTax')}")
    print(f"  Total               : {inv.get('TotalAmt')}")
    print("\n  --- TxnTaxDetail completo ---")
    print("  " + json.dumps(inv.get("TxnTaxDetail", {}), indent=2, ensure_ascii=False))


def main():
    print("Refrescando token...")
    realm, token = refrescar()

    print("\n[1] Buscando si ya hay un impuesto 13%...")
    tcid = buscar_taxcode_13(realm, token)
    if tcid:
        print(f"  Ya existe, Id {tcid}")
    else:
        print("  No existe. Intentando crearlo...")
        tcid = crear_taxcode_13(realm, token)
        print(f"  TaxCode 13% -> Id {tcid}")

    print("\n[2] Creando factura de prueba con impuesto...")
    crear_factura(realm, token, tcid)

    print("\nMira el 'Impuesto calculado': si dice ~13, el sandbox simula el IVA.")


if __name__ == "__main__":
    main()
