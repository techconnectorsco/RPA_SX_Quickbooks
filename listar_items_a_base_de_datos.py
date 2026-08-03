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

# ── Modo de ejecucion ────────────────────────────────────────────────────────
# True  = SOLO CONSULTA: baja los items y los muestra, pero NO toca la base.
#         Sirve para verificar que el encargado de QuickBooks ya cargo los
#         servicios nuevos ANTES de escribir nada en la tabla `servicios`.
# False = ademas de mostrar, guarda/actualiza en la tabla `servicios`.
SOLO_CONSULTA = True

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
    """Guarda o actualiza los items en la tabla `servicios`.
    Distingue insertados de actualizados usando la clausula RETURNING con
    xmax (0 = fila nueva, distinto de 0 = fila que ya existia y se actualizo).
    Hace UN commit por empresa: si algo falla, esa empresa no queda a medias."""
    cur = conn.cursor()
    insertados = 0
    actualizados = 0
    try:
        for it in items:
            qbo_id = str(it.get("Id", ""))
            if not qbo_id:
                continue

            nombre = it.get("Name", "")
            fqn = it.get("FullyQualifiedName", "")
            descripcion = fqn if fqn and fqn != nombre else it.get("Type", "")
            activa = bool(it.get("Active", True))

            cur.execute(
                """
                INSERT INTO servicios (nombre, descripcion, activa, qbo_item_id, empresa_id)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (nombre, empresa_id) DO UPDATE SET
                    descripcion = EXCLUDED.descripcion,
                    activa = EXCLUDED.activa,
                    qbo_item_id = EXCLUDED.qbo_item_id,
                    actualizado_en = NOW()
                RETURNING (xmax = 0) AS es_nuevo
                """,
                (nombre, descripcion, activa, qbo_id, empresa_id),
            )
            es_nuevo = cur.fetchone()[0]
            if es_nuevo:
                insertados += 1
            else:
                actualizados += 1
        conn.commit()  # un solo commit por empresa
    except Exception as e:
        conn.rollback()
        print(f"  [ERROR BD] Se revirtio esta empresa: {e}")
        insertados = actualizados = 0
    finally:
        cur.close()
    return insertados, actualizados


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
    modo = (
        "SOLO CONSULTA (no escribe en la base)"
        if SOLO_CONSULTA
        else "CONSULTA + GUARDADO"
    )
    print(f"ITEMS EN QUICKBOOKS (PRODUCCION)  —  {modo}")
    print("=" * 70)

    # Solo se conecta a la base si de verdad se va a escribir.
    conn = None
    if not SOLO_CONSULTA:
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

        if not SOLO_CONSULTA:
            print("\n  Guardando en la tabla servicios...")
            ins, act = guardar_items_bd(items, conn, emp["empresa_id"])
            print(f"  Insertados: {ins}   Actualizados: {act}")

        print(f"\n  Total items en {emp['nombre']}: {len(items)}")
        total += len(items)

    if conn:
        conn.close()
    print("\n" + "=" * 70)
    print(f"Listo. {total} items en total (las 3 empresas).")
    if SOLO_CONSULTA:
        print("Fue SOLO CONSULTA: no se escribio nada en la base.")
        print("Cuando confirmes que los servicios estan bien, pone")
        print("SOLO_CONSULTA = False y volve a correr para guardarlos.")
    print("=" * 70)


if __name__ == "__main__":
    main()
