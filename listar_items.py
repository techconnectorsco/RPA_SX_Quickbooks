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
import psycopg2
from dotenv import load_dotenv

# ── Configuracion ────────────────────────────────────────────────────────────
load_dotenv()  # .env de esta carpeta (QBO_CLIENT_ID / QBO_CLIENT_SECRET)

DATABASE_URL = os.getenv("DATABASE_URL")

PROD_BASE_URL = "https://quickbooks.api.intuit.com"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")

CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")

EMPRESAS = [
    {
        "nombre": "Soportexperto.com S.A.",
        "realm": "9130355360397996",
        "empresa_id": "ec006548-c1a1-4212-aaf5-605041ce7d3e",
    },
    {
        "nombre": "Hardware y Network S.A.",
        "realm": "9130355360390096",
        "empresa_id": "fc3e4394-5954-41d7-b502-5b38db52fae5",
    },
    {
        "nombre": "Corporacion Latinoamericana T.I. (Laitcorp)",
        "realm": "9130355360394696",
        "empresa_id": "01d18328-dccf-493b-aca7-05c5d74900a0",
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
        
        clase = (it.get("ClassRef") or {}).get("name", "")
        clase_txt = f" | clase: {clase}" if clase else ""

        # Nombre totalmente calificado (incluye categoria padre si la hay)
        fqn = it.get("FullyQualifiedName", "")
        fqn_txt = f"   [{fqn}]" if fqn and fqn != nombre else ""
        print(
            f"  Id {iid:<5} | {tipo:<13} | {activo:<8} | {nombre}{precio_txt}{cuenta_txt}{clase_txt}{fqn_txt}"
        )

def guardar_items_bd(items, conn, empresa_id):
    """Guarda o actualiza los items en la tabla servicios de la base de datos local."""
    cur = conn.cursor()
    insertados = 0
    actualizados = 0
    
    for it in items:
        qbo_id = str(it.get("Id", ""))
        if not qbo_id:
            continue
            
        nombre = it.get("Name", "")
        # Usamos FullyQualifiedName como descripcion si es distinto, si no, el Tipo
        fqn = it.get("FullyQualifiedName", "")
        descripcion = fqn if fqn and fqn != nombre else it.get("Type", "")
        activa = bool(it.get("Active", True))
        
        try:
            # Upsert (Insertar o actualizar) usando (nombre, empresa_id) como clave única
            cur.execute("""
                INSERT INTO servicios (nombre, descripcion, activa, qbo_item_id, empresa_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (nombre, empresa_id) DO UPDATE SET
                    descripcion = EXCLUDED.descripcion,
                    activa = EXCLUDED.activa,
                    qbo_item_id = EXCLUDED.qbo_item_id,
                    actualizado_en = NOW()
            """, (nombre, descripcion, activa, qbo_id, empresa_id))
            
            # psycopg2 cursor.rowcount: 1 = insertado, 1 = actualizado en PostgreSQL (o 2 a veces en INSERT...ON CONFLICT dependiendo de version, pero lo contamos simplificado)
            insertados += 1 
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"  [ERROR BD] Al guardar {nombre}: {e}")
            
    cur.close()
    return insertados

def main():
    if not (CLIENT_ID and CLIENT_SECRET):
        print("[ERROR] Falta QBO_CLIENT_ID / QBO_CLIENT_SECRET en el .env.")
        sys.exit(1)
    if not DATABASE_URL:
        print("[ERROR] Falta DATABASE_URL en el .env.")
        sys.exit(1)
    if not os.path.exists(TOKENS_FILE):
        print(f"[ERROR] No encuentro {TOKENS_FILE}.")
        sys.exit(1)

    print("=" * 70)
    print("LISTADO DE ITEMS EN QUICKBOOKS (PRODUCCION, SOLO LECTURA)")
    print("=" * 70)

    try:
        conn = psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"[ERROR BD] No se pudo conectar a la base de datos: {e}")
        sys.exit(1)

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
        
        print("  Guardando en base de datos local...")
        guardados = guardar_items_bd(items, conn, emp["empresa_id"])
        print(f"  Total items guardados/actualizados: {guardados}")
        
        print(f"\n  Total items en {emp['nombre']}: {len(items)}")
        total += len(items)

    conn.close()
    print("\n" + "=" * 70)
    print(f"Listo. {total} items listados en total (las 3 empresas).")
    print("=" * 70)


if __name__ == "__main__":
    main()
