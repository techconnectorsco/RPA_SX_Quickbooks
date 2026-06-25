"""
ver_facturas_produccion.py
--------------------------
SOLO LECTURA. Trae las ultimas facturas de UNA empresa de PRODUCCION
(Soportexperto) para VER como QuickBooks arma las lineas de una factura.

Por que es seguro:
  - Solo hace peticiones GET. No crea, no modifica, no borra NADA.
  - El riesgo de produccion es ESCRIBIR; LEER es seguro.
  - Esta clavado a un solo realm (Soportexperto). No toca las otras empresas.

Como correrlo:
  Desde la carpeta donde esta tu app.py / tokens_empresas.json:
      python ver_facturas_produccion.py
  Si el token esta vencido (error 401), corre antes:
      python renovar_token.py
  y volve a intentar.
"""

import json
import os
import sys
import requests

# --- PRODUCCION, solo lectura ---
PROD_BASE_URL = "https://quickbooks.api.intuit.com"

# Realm de Soportexperto.com S.A. (el UNICO que este script consulta)
REALM_SOPORTEXPERTO = "9130355360397996"
NOMBRE_EMPRESA = "Soportexperto.com S.A."

TOKENS_FILE = os.path.join("config", "tokens_empresas.json")

# Cuantas facturas traer para inspeccionar
CUANTAS = 10


def cargar_access_token():
    if not os.path.exists(TOKENS_FILE):
        print(f"[ERROR] No encuentro {TOKENS_FILE} en esta carpeta.")
        print("Corre este script desde la carpeta donde esta tu app.py.")
        sys.exit(1)

    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    empresa = data.get("empresas", {}).get(REALM_SOPORTEXPERTO)
    if not empresa:
        print(f"[ERROR] No encuentro el realm {REALM_SOPORTEXPERTO} en {TOKENS_FILE}.")
        sys.exit(1)

    token = empresa.get("access_token")
    if not token:
        print("[ERROR] La empresa no tiene access_token guardado.")
        sys.exit(1)

    return token


def get(endpoint, token):
    """GET de solo lectura contra produccion (Soportexperto)."""
    url = f"{PROD_BASE_URL}/v3/company/{REALM_SOPORTEXPERTO}/{endpoint}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    return requests.get(url, headers=headers, timeout=30)


def mostrar_factura(inv, indice):
    print("\n" + "-" * 60)
    print(f"FACTURA #{indice}   DocNumber: {inv.get('DocNumber', 'N/D')}")
    cliente = inv.get("CustomerRef", {})
    print(f"  Cliente : {cliente.get('name')}  (Id={cliente.get('value')})")
    print(f"  Fecha   : {inv.get('TxnDate')}")
    print(
        f"  Total   : {inv.get('TotalAmt')}  {inv.get('CurrencyRef', {}).get('value', '')}"
    )
    print("  Lineas:")
    for linea in inv.get("Line", []):
        # Solo las lineas de detalle (lo que se cobra)
        if linea.get("DetailType") != "SalesItemLineDetail":
            continue
        det = linea.get("SalesItemLineDetail", {})
        item = det.get("ItemRef", {})
        clase = det.get("ClassRef", {})
        print(f"    - Item: {item.get('name')}  (Id={item.get('value')})")
        print(f"      Descripcion: {linea.get('Description')}")
        print(
            f"      Cant: {det.get('Qty')}   Precio: {det.get('UnitPrice')}   Monto: {linea.get('Amount')}"
        )
        if clase:
            print(f"      Class: {clase.get('name')}  (Id={clase.get('value')})")


def main():
    token = cargar_access_token()

    print("=" * 60)
    print(f"FACTURAS DE PRODUCCION (SOLO LECTURA) - {NOMBRE_EMPRESA}")
    print(f"Realm: {REALM_SOPORTEXPERTO}")
    print("=" * 60)

    sql = f"SELECT * FROM Invoice MAXRESULTS {CUANTAS}"
    resp = get("query?query=" + requests.utils.quote(sql), token)

    if resp.status_code == 401:
        print("\n[401] El token de produccion esta vencido.")
        print("Corre primero:  python renovar_token.py")
        print("y luego volve a correr este script.")
        sys.exit(1)

    if resp.status_code != 200:
        print(f"\n[ERROR {resp.status_code}] {resp.text[:400]}")
        sys.exit(1)

    facturas = resp.json().get("QueryResponse", {}).get("Invoice", [])
    if not facturas:
        print("\nNo se encontraron facturas.")
        return

    for i, inv in enumerate(facturas, 1):
        mostrar_factura(inv, i)

    # JSON crudo de la PRIMERA factura, para ver TODOS los campos disponibles
    print("\n" + "=" * 60)
    print("JSON CRUDO DE LA PRIMERA FACTURA (todos los campos):")
    print("=" * 60)
    print(json.dumps(facturas[0], indent=2, ensure_ascii=False))

    # Resumen: que Items aparecen (esto responde la pregunta del item generico)
    items_vistos = {}
    for inv in facturas:
        for linea in inv.get("Line", []):
            if linea.get("DetailType") == "SalesItemLineDetail":
                item = linea.get("SalesItemLineDetail", {}).get("ItemRef", {})
                nombre = item.get("name", "?")
                items_vistos[nombre] = items_vistos.get(nombre, 0) + 1

    print("\n" + "=" * 60)
    print("ITEMS QUE APARECEN EN ESTAS FACTURAS (y cuantas veces):")
    for nombre, veces in sorted(items_vistos.items(), key=lambda x: -x[1]):
        print(f"  {veces:>3}x  {nombre}")
    print("=" * 60)


if __name__ == "__main__":
    main()
