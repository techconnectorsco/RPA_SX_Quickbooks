"""
listar_items.py
---------------
Lista TODOS los items (productos/servicios) de las 3 empresas que facturan,
directamente desde QuickBooks de PRODUCCION. Sirve para identificar el Id del
item de cada empresa que los RPAs deben usar (hoy en 'item_operaciones').

Seguridad (igual que sync_clientes.py):
  - En QuickBooks SOLO lee (GET). No crea ni modifica NADA alla.
  - No toca la base de datos.
  - Refresca el token de cada empresa solo (Intuit rota el refresh -> se guarda).
  - Es de solo consulta: corrélo cuantas veces quieras.

Muestra por cada item: Id, Nombre, Tipo, Activo, Precio (si tiene) y la cuenta
de ingresos asociada, para que sea facil reconocer cual es el de contratos/horas.

Requiere (ya instalado): psycopg2-binary NO hace falta aqui; solo requests y dotenv.
    pip install python-dotenv requests
"""

import os
import json
import base64
import sys

import requests
from dotenv import load_dotenv

# ── Configuracion ────────────────────────────────────────────────────────────
load_dotenv()  # .env de esta carpeta (QBO_CLIENT_ID / QBO_CLIENT_SECRET)
RUTA_ENV_WEBAPP = r"D:\Users\Usuario\Desktop\SX-Ecosystem\SX-Ecosystem\.env"
if os.path.exists(RUTA_ENV_WEBAPP):
    load_dotenv(RUTA_ENV_WEBAPP, override=False)

PROD_BASE_URL = "https://quickbooks.api.intuit.com"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")

# Las 3 empresas que facturan: realm de QuickBooks -> nombre legible
EMPRESAS = [
    {"nombre": "Soportexperto.com S.A.", "realm": "9130355360397996"},
    {"nombre": "Hardware y Network S.A.", "realm": "9130355360390096"},
    {
        "nombre": "Corporacion Latinoamericana T.I. (Laitcorp)",
        "realm": "9130355360394696",
    },
]


def refrescar_token(realm):
    """Refresca el token de una empresa y lo guarda. Devuelve el access_token nuevo."""
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
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
    empresa["refresh_token"] = nuevos[
        "refresh_token"
    ]  # Intuit rota el refresh; hay que guardarlo
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return nuevos["access_token"]


def descargar_items(realm, token):
    """Baja TODOS los items de una empresa (solo lectura, paginado)."""
    items, inicio, lote = [], 1, 100
    while True:
        sql = f"SELECT * FROM Item STARTPOSITION {inicio} MAXRESULTS {lote}"
        url = (
            f"{PROD_BASE_URL}/v3/company/{realm}/query"
            f"?query={requests.utils.quote(sql)}&minorversion=75"
        )
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"  [ERROR {resp.status_code}] {resp.text[:200]}")
            break
        lote_res = resp.json().get("QueryResponse", {}).get("Item", [])
        if not lote_res:
            break
        items.extend(lote_res)
        if len(lote_res) < lote:
            break
        inicio += lote
    return items


def mostrar_items(items):
    """Imprime los items de forma legible y ordenada por Id (numerico)."""

    def _id_num(it):
        try:
            return int(it.get("Id", 0))
        except (TypeError, ValueError):
            return 0

    for it in sorted(items, key=_id_num):
        iid = it.get("Id", "?")
        nombre = it.get("Name", "")
        tipo = it.get("Type", "")  # Service, Inventory, NonInventory, etc.
        activo = "activo" if it.get("Active", True) else "INACTIVO"
        precio = it.get("UnitPrice")
        precio_txt = f" | precio {precio}" if precio not in (None, "") else ""
        cuenta = (it.get("IncomeAccountRef") or {}).get("name", "")
        cuenta_txt = f" | cuenta: {cuenta}" if cuenta else ""
        # Nombre totalmente calificado (incluye categoria padre si la hay)
        fqn = it.get("FullyQualifiedName", "")
        fqn_txt = f"   [{fqn}]" if fqn and fqn != nombre else ""
        print(
            f"  Id {iid:<5} | {tipo:<13} | {activo:<8} | {nombre}{precio_txt}{cuenta_txt}{fqn_txt}"
        )


def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print("[ERROR] Falta QBO_CLIENT_ID / QBO_CLIENT_SECRET en el .env.")
        sys.exit(1)
    if not os.path.exists(TOKENS_FILE):
        print(f"[ERROR] No encuentro {TOKENS_FILE}.")
        sys.exit(1)

    print("=" * 70)
    print("LISTADO DE ITEMS EN QUICKBOOKS (PRODUCCION, SOLO LECTURA)")
    print("=" * 70)

    total = 0
    for emp in EMPRESAS:
        print(f"\n=== {emp['nombre']}  (realm {emp['realm']}) ===")
        print("  Refrescando token...")
        token = refrescar_token(emp["realm"])
        if not token:
            print("  [SALTADA] no se pudo refrescar el token.")
            continue
        print("  Descargando items (solo lectura)...\n")
        items = descargar_items(emp["realm"], token)
        if not items:
            print("  (sin items o no se pudieron leer)")
            continue
        mostrar_items(items)
        print(f"\n  Total items en {emp['nombre']}: {len(items)}")
        total += len(items)

    print("\n" + "=" * 70)
    print(f"Listo. {total} items listados en total (las 3 empresas).")
    print("=" * 70)


if __name__ == "__main__":
    main()
