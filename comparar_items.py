"""
comparar_items.py
-----------------
Investiga y compara los Items y Clases de QuickBooks (PRODUCCION)
contra la tabla 'servicios' de la base de datos local.

No modifica NADA en QuickBooks ni en la Base de Datos (Solo Lectura).
"""

import os
import json
import base64
import sys

import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()

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
    """Refresca el token OAuth2 para la empresa indicada."""
    if not os.path.exists(TOKENS_FILE):
        print(f"  [ERROR] No existe {TOKENS_FILE}")
        return None

    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    empresa = data.get("empresas", {}).get(realm)
    if not empresa:
        print(f"  [ERROR] Realm {realm} no encontrado en {TOKENS_FILE}")
        return None

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
        print(f"  [ERROR refresco] {resp.status_code}: {resp.text[:200]}")
        return None

    nuevos = resp.json()
    empresa["access_token"] = nuevos["access_token"]
    empresa["refresh_token"] = nuevos["refresh_token"]

    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return nuevos["access_token"]


def consultar_qbo(realm, token, query):
    """Ejecuta una consulta SQL genérica a la API de QBO."""
    url = (
        f"{PROD_BASE_URL}/v3/company/{realm}/query"
        f"?query={requests.utils.quote(query)}&minorversion=75"
    )
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"  [ERROR QBO API {resp.status_code}] {resp.text[:200]}")
        return []

    q_res = resp.json().get("QueryResponse", {})
    # Retornar la primera lista encontrada en la respuesta (Item, Class, etc.)
    for k, v in q_res.items():
        if isinstance(v, list):
            return v
    return []


def obtener_servicios_bd(conn, empresa_id):
    """Consulta los servicios registrados en la base de datos local para esa empresa."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, nombre, qbo_item_id, activa, descripcion 
        FROM servicios 
        WHERE empresa_id = %s
    """,
        (empresa_id,),
    )
    rows = cur.fetchall()
    cur.close()

    # Retorna diccionario indexado por nombre para facilitar comparaciones
    return {
        r[1]
        .strip()
        .lower(): {
            "id": r[0],
            "nombre": r[1],
            "qbo_item_id": r[2],
            "activa": r[3],
            "descripcion": r[4],
        }
        for r in rows
    }


def investigar_y_comparar(emp, token, conn):
    print(f"\n======================================================================")
    print(f" EMPRESA: {emp['nombre']} (Realm: {emp['realm']})")
    print(f"======================================================================")

    # 1. Consultar Clases en QBO
    clases_qbo = consultar_qbo(emp["realm"], token, "SELECT * FROM Class")
    print(
        f"\n--- 1. CLASES CONFIGURADAS EN QUICKBOOKS ({len(clases_qbo)} encontradas) ---"
    )
    if clases_qbo:
        for cl in clases_qbo:
            cid = cl.get("Id", "?")
            cname = cl.get("Name", "")
            cactive = "Activa" if cl.get("Active", True) else "INACTIVA"
            cfqn = cl.get("FullyQualifiedName", "")
            print(f"  [Class ID: {cid:<4}] {cname:<30} | {cactive:<8} | FQN: {cfqn}")
    else:
        print(
            "  (No se encontraron Clases o la empresa no utiliza el módulo de Clases)"
        )

    # 2. Consultar Items en QBO
    items_qbo = consultar_qbo(
        emp["realm"], token, "SELECT * FROM Item STARTPOSITION 1 MAXRESULTS 500"
    )
    print(f"\n--- 2. ITEMS EN QUICKBOOKS ({len(items_qbo)} encontrados) ---")

    # 3. Consultar la BD local
    servicios_bd = obtener_servicios_bd(conn, emp["empresa_id"])
    print(f"--- 3. SERVICIOS EN BD LOCAL ({len(servicios_bd)} registrados) ---")

    # 4. Comparación directa
    print(f"\n--- 4. COMPARACION Y DISCREPANCIAS ---")

    qbo_by_name = {}
    for it in items_qbo:
        qbo_id = str(it.get("Id", ""))
        name = it.get("Name", "").strip()
        tipo = it.get("Type", "")
        active = bool(it.get("Active", True))
        qbo_by_name[name.lower()] = {
            "id": qbo_id,
            "nombre": name,
            "tipo": tipo,
            "activo": active,
            "raw": it,
        }

    # Analizar items que están en QBO
    coincidencias = 0
    desalineados_id = 0

    print("\n  [VERIFICANDO ITEMS DE QBO VS BASE DE DATOS]:")
    for name_lower, qbo_data in qbo_by_name.items():
        if name_lower in servicios_bd:
            bd_data = servicios_bd[name_lower]
            match_id = str(bd_data["qbo_item_id"]) == str(qbo_data["id"])
            if match_id:
                coincidencias += 1
                print(
                    f"   OK -> '{qbo_data['nombre']}' | QBO ID: {qbo_data['id']} == BD qbo_item_id: {bd_data['qbo_item_id']}"
                )
            else:
                desalineados_id += 1
                print(
                    f"  [DISCREPANCIA ID] -> '{qbo_data['nombre']}': En QBO es ID {qbo_data['id']} pero en BD tiene qbo_item_id = '{bd_data['qbo_item_id']}'"
                )
        else:
            print(
                f"  [NO EN BD] -> '{qbo_data['nombre']}' (QBO ID: {qbo_data['id']}, Tipo: {qbo_data['tipo']}) existe en QBO pero NO en la BD local."
            )

    # Analizar items que están en BD pero no en QBO
    print("\n  [VERIFICANDO SERVICIOS EN BD QUE NO ESTAN EN QBO]:")
    faltantes_qbo = 0
    for name_lower, bd_data in servicios_bd.items():
        if name_lower not in qbo_by_name:
            faltantes_qbo += 1
            print(
                f"  [NO EN QBO] -> '{bd_data['nombre']}' (BD ID: {bd_data['id']}, qbo_item_id actual: '{bd_data['qbo_item_id']}') existe en BD pero NO se halló en QBO."
            )

    print(f"\n  Resumen Empresa {emp['nombre']}:")
    print(f"   - Total QBO: {len(items_qbo)} | Total BD: {len(servicios_bd)}")
    print(f"   - Coincidencias correctas: {coincidencias}")
    print(f"   - Con ID desalineado: {desalineados_id}")
    print(f"   - Faltantes en QBO: {faltantes_qbo}")


def main():
    if not (CLIENT_ID and CLIENT_SECRET and DATABASE_URL):
        print(
            "[ERROR] Verifique que QBO_CLIENT_ID, QBO_CLIENT_SECRET y DATABASE_URL estén en el .env"
        )
        sys.exit(1)

    try:
        conn = psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"[ERROR BD] Conexión fallida: {e}")
        sys.exit(1)

    print("======================================================================")
    print(" REPORTE DE INVESTIGACION: ITEMS Y CLASES DE QUICKBOOKS VS BD LOCAL")
    print("======================================================================")

    for emp in EMPRESAS:
        token = refrescar_token(emp["realm"])
        if token:
            investigar_y_comparar(emp, token, conn)
        else:
            print(f"\n[SALTANDO {emp['nombre']}] No se pudo obtener Access Token.")

    conn.close()
    print("\n======================================================================")
    print(" Proceso finalizado.")
    print("======================================================================")


if __name__ == "__main__":
    main()
