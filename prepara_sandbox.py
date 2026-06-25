"""
preparar_sandbox.py
-------------------
Prepara el SANDBOX para simular: crea (o encuentra si ya existen)
  - un cliente de prueba
  - el item de operaciones "CONTRATOS HORAS ADICIONALES"
e imprime los Id que vamos a usar en el RPA.

(El IVA 13% y la moneda CRC van en pasos aparte, son mas delicados.)

Solo sandbox. Refresca el token solo. Es idempotente (podes correrlo varias veces).
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

NOMBRE_CLIENTE_PRUEBA = "Cliente Prueba CR S.A."
NOMBRE_ITEM = "CONTRATOS HORAS ADICIONALES"
INCOME_ACCOUNT_ID = (
    "1"  # cuenta "Services" del sandbox (la vimos en la factura de prueba)
)


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


def crear(realm, token, entidad, payload):
    url = f"{SANDBOX_BASE_URL}/v3/company/{realm}/{entidad}?minorversion=75"
    return requests.post(url, headers=headers(token), json=payload, timeout=30)


def asegurar_cliente(realm, token):
    r = query(
        realm,
        token,
        f"SELECT * FROM Customer WHERE DisplayName = '{NOMBRE_CLIENTE_PRUEBA}'",
    )
    existentes = (
        r.json().get("QueryResponse", {}).get("Customer", [])
        if r.status_code == 200
        else []
    )
    if existentes:
        c = existentes[0]
        print(f"  Cliente ya existia -> Id {c['Id']}")
        return c["Id"]
    r = crear(realm, token, "customer", {"DisplayName": NOMBRE_CLIENTE_PRUEBA})
    if r.status_code not in (200, 201):
        print("  [ERROR cliente]", r.status_code, r.text[:300])
        return None
    c = r.json()["Customer"]
    print(f"  Cliente creado -> Id {c['Id']}")
    return c["Id"]


def asegurar_item(realm, token):
    r = query(realm, token, f"SELECT * FROM Item WHERE Name = '{NOMBRE_ITEM}'")
    existentes = (
        r.json().get("QueryResponse", {}).get("Item", [])
        if r.status_code == 200
        else []
    )
    if existentes:
        it = existentes[0]
        print(f"  Item ya existia -> Id {it['Id']}")
        return it["Id"]
    payload = {
        "Name": NOMBRE_ITEM,
        "Type": "Service",
        "IncomeAccountRef": {"value": INCOME_ACCOUNT_ID},
    }
    r = crear(realm, token, "item", payload)
    if r.status_code not in (200, 201):
        print("  [ERROR item]", r.status_code, r.text[:300])
        return None
    it = r.json()["Item"]
    print(f"  Item creado -> Id {it['Id']}")
    return it["Id"]


def main():
    print("Refrescando token...")
    realm, token = refrescar()

    print("\nCliente de prueba:")
    cid = asegurar_cliente(realm, token)

    print("\nItem de operaciones:")
    iid = asegurar_item(realm, token)

    print("\n" + "=" * 50)
    print("IDs para usar en el RPA (sandbox):")
    print(f"  cliente de prueba (qbo_customer_id) : {cid}")
    print(f"  item de operaciones (Id)            : {iid}")
    print("=" * 50)


if __name__ == "__main__":
    main()
