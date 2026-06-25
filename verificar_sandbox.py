"""
verificar_sandbox.py
--------------------
Script de SOLO LECTURA para comprobar que el sandbox de QuickBooks responde
y para VER la forma exacta de los datos (clientes e items) que devuelve la API.

Pensado para correrse desde la raiz del proyecto:  python src/verificar_sandbox.py

BLINDAJE (a proposito, para no tocar produccion):
  1. La URL base es la de sandbox y NO se lee del .env.
  2. Si detecta un realm de PRODUCCION, aborta sin hacer nada.
  3. Lee un archivo de tokens SEPARADO (config/tokens_sandbox.json),
     nunca tokens_empresas.json (que son los de produccion).
  4. Solo hace peticiones GET. No crea, no modifica, no borra nada.
"""

import json
import os
import sys
import requests

# --- BLINDAJE 1: URL fija de sandbox (no se lee del .env a proposito) ---
SANDBOX_BASE_URL = "https://sandbox-quickbooks.api.intuit.com"

# --- BLINDAJE 2: realms de PRODUCCION que este script NUNCA debe tocar ---
REALMS_PRODUCCION = {
    "9130355360397996",  # Soportexperto.com S.A.
    "9130355360394696",  # Corporacion Latinoamericana de Tecnologia T.I. S.A.
    "9130355360390096",  # Hardware y Network S.A.
}

# Archivo de tokens EXCLUSIVO de sandbox (lo creas vos con el token del Playground)
TOKENS_SANDBOX_FILE = os.path.join("config", "tokens_sandbox.json")


def cargar_token_sandbox():
    """Carga realm_id y access_token del archivo de sandbox. Aborta si algo no calza."""
    if not os.path.exists(TOKENS_SANDBOX_FILE):
        print(f"[ERROR] No existe {TOKENS_SANDBOX_FILE}.")
        print("Crealo con este formato (token sacado del OAuth Playground):\n")
        print(
            json.dumps(
                {
                    "realm_id": "TU_REALM_DE_SANDBOX",
                    "access_token": "EL_ACCESS_TOKEN_DEL_PLAYGROUND",
                },
                indent=2,
            )
        )
        sys.exit(1)

    with open(TOKENS_SANDBOX_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    realm_id = str(data.get("realm_id", "")).strip()
    access_token = str(data.get("access_token", "")).strip()

    if not realm_id or not access_token:
        print("[ERROR] El archivo de tokens no tiene 'realm_id' o 'access_token'.")
        sys.exit(1)

    # --- BLINDAJE 3: si por error pusieron un realm de produccion, abortar ---
    if realm_id in REALMS_PRODUCCION:
        print("[ABORTADO] Ese realm es de PRODUCCION. Este script es solo sandbox.")
        sys.exit(1)

    return realm_id, access_token


def get(endpoint, realm_id, access_token):
    """Hace un GET de solo lectura contra el sandbox."""
    # --- BLINDAJE 4: confirmar que la URL es de sandbox antes de cada llamada ---
    assert "sandbox" in SANDBOX_BASE_URL, "La URL base no es de sandbox. Abortando."

    url = f"{SANDBOX_BASE_URL}/v3/company/{realm_id}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    return requests.get(url, headers=headers, timeout=30)


def consulta(sql, realm_id, access_token):
    """Helper para el endpoint /query (encodea el SQL correctamente)."""
    endpoint = "query?query=" + requests.utils.quote(sql)
    return get(endpoint, realm_id, access_token)


def main():
    realm_id, access_token = cargar_token_sandbox()

    print("=" * 60)
    print("VERIFICACION DE SANDBOX (solo lectura)")
    print(f"Base URL : {SANDBOX_BASE_URL}")
    print(f"Realm    : {realm_id}")
    print("=" * 60)

    # 1) Info de la empresa: prueba que el token y el sandbox responden
    print("\n[1] CompanyInfo ...")
    resp = get(f"companyinfo/{realm_id}", realm_id, access_token)
    if resp.status_code == 401:
        print("  Token vencido o invalido (401).")
        print("  Genera uno nuevo en el OAuth Playground y actualiza el archivo.")
        sys.exit(1)
    if resp.status_code != 200:
        print(f"  Error {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)

    info = resp.json().get("CompanyInfo", {})
    print(f"  OK -> Empresa: {info.get('CompanyName')}")
    print(f"        Pais   : {info.get('Country')}")
    print(f"        Moneda : {info.get('Currency', {}).get('value', 'N/D')}")

    # 2) Clientes: ver la forma del dato (el Id es el futuro quickbooks_customer_id)
    print("\n[2] Clientes (primeros 5) ...")
    resp = consulta("SELECT * FROM Customer MAXRESULTS 5", realm_id, access_token)
    if resp.status_code == 200:
        clientes = resp.json().get("QueryResponse", {}).get("Customer", [])
        for c in clientes:
            print(f"  Id={c.get('Id')}  DisplayName={c.get('DisplayName')}")
        if clientes:
            print("\n  --- JSON crudo del primer cliente (para disenar el mapeo) ---")
            print(json.dumps(clientes[0], indent=2, ensure_ascii=False))
    else:
        print(f"  Error {resp.status_code}: {resp.text[:300]}")

    # 3) Items/servicios: ver la forma (el Id es el futuro quickbooks_item_id)
    print("\n[3] Items / servicios (primeros 5) ...")
    resp = consulta("SELECT * FROM Item MAXRESULTS 5", realm_id, access_token)
    if resp.status_code == 200:
        items = resp.json().get("QueryResponse", {}).get("Item", [])
        for it in items:
            print(f"  Id={it.get('Id')}  Name={it.get('Name')}  Type={it.get('Type')}")
        if items:
            print("\n  --- JSON crudo del primer item ---")
            print(json.dumps(items[0], indent=2, ensure_ascii=False))
    else:
        print(f"  Error {resp.status_code}: {resp.text[:300]}")

    print("\n" + "=" * 60)
    print("Listo. Si llegaste hasta aqui, el sandbox responde y todo fue solo lectura.")
    print("=" * 60)


if __name__ == "__main__":
    main()
