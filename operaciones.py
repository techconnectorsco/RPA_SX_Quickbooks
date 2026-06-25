"""
rpa_operaciones.py  —  CODIGO MADRE del RPA de facturacion de OPERACIONES
=========================================================================
Lee las operaciones en estado 'aprobada' de la base, las factura en QuickBooks,
actualiza su estado a 'facturada' (guardando los datos que devuelve QBO) y deja
un log en PDF.

Un solo interruptor (ENTORNO) cambia entre SANDBOX y PRODUCCION.

SEGURIDAD:
  - En sandbox, la URL es de sandbox y todo va al cliente/item de prueba.
  - Antes de facturar, valida que la URL y el realm correspondan al entorno.
  - Solo toca operaciones 'aprobada' que NO tengan ya una factura (idempotente).
  - Cada operacion se confirma por separado: si una falla, no afecta a las demas.

Requiere (una vez):  pip install psycopg2-binary python-dotenv requests fpdf2
"""

import os
import sys
import json
import base64
import datetime
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURACION  —  lo unico que tocas para cambiar de entorno
# ════════════════════════════════════════════════════════════════════════════

ENTORNO = "sandbox"  # "sandbox"  o  "produccion"

LIMITE_FACTURAS = None  # None = todas las aprobadas.  1 = procesar solo UNA.
MONTO_FIJO_PRUEBA = None  # None = monto real.  Ej: 100 = forzar monto chico
# (para el primer disparo en produccion y que finanzas
#  lo anule con nota de credito).

# ── Entornos ────────────────────────────────────────────────────────────────
load_dotenv()
RUTA_ENV_WEBAPP = r"D:\Users\Usuario\Desktop\SX-Ecosystem\SX-Ecosystem\.env"
if os.path.exists(RUTA_ENV_WEBAPP):
    load_dotenv(RUTA_ENV_WEBAPP, override=False)

ENTORNOS = {
    "sandbox": {
        "base_url": "https://sandbox-quickbooks.api.intuit.com",
        "tokens_file": os.path.join("config", "tokens_sandbox.json"),
        "client_id": os.getenv("QBO_SANDBOX_CLIENT_ID"),
        "client_secret": os.getenv("QBO_SANDBOX_CLIENT_SECRET"),
        "realm_fijo": "9341456664539574",  # en sandbox todo va al unico sandbox
        "cliente_fijo": "58",  # cliente de prueba
        "item_operaciones": "19",  # item "CONTRATOS HORAS ADICIONALES"
        "taxcode": "4",  # IVA 13%
        "usar_moneda_real": False,  # sandbox no tiene multimoneda -> USD
    },
    "produccion": {
        "base_url": "https://quickbooks.api.intuit.com",
        "tokens_file": os.path.join("config", "tokens_empresas.json"),
        "client_id": os.getenv("QBO_CLIENT_ID"),
        "client_secret": os.getenv("QBO_CLIENT_SECRET"),
        "realm_fijo": None,  # usa el realm real de cada empresa
        "cliente_fijo": None,  # usa el qbo_customer_id de la operacion
        # TODO produccion: el Id del item "CONTRATOS HORAS ADICIONALES" POR empresa.
        #  (en Soportexperto lo vimos = "4"; los otros dos hay que buscarlos)
        "item_operaciones": {
            "9130355360397996": "4",  # Soportexperto (confirmado)
            "9130355360390096": "TODO",  # Hardware y Network
            "9130355360394696": "TODO",  # Corporacion Latinoamericana
        },
        "taxcode": None,  # TODO: Id del impuesto IVA13% en cada empresa
        "usar_moneda_real": True,
    },
}

CFG = ENTORNOS[ENTORNO]
DATABASE_URL = os.getenv("DATABASE_URL")

# Candado: realms de produccion (para validar que sandbox nunca los toque)
REALMS_PRODUCCION = {"9130355360397996", "9130355360394696", "9130355360390096"}


# ════════════════════════════════════════════════════════════════════════════
#  TOKENS  —  refresca solo, en ambos entornos
# ════════════════════════════════════════════════════════════════════════════


def get_access_token(realm):
    """Refresca y devuelve el access_token del realm dado, segun el entorno."""
    f = CFG["tokens_file"]
    with open(f, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if ENTORNO == "sandbox":
        nodo = data  # un solo token
        refresh_token = nodo["refresh_token"]
    else:
        nodo = data["empresas"][realm]  # token por empresa
        refresh_token = nodo["refresh_token"]

    auth = base64.b64encode(
        f"{CFG['client_id']}:{CFG['client_secret']}".encode()
    ).decode()
    r = requests.post(
        "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"No se pudo refrescar token ({r.status_code}): {r.text[:200]}"
        )

    nuevos = r.json()
    nodo["access_token"] = nuevos["access_token"]
    nodo["refresh_token"] = nuevos["refresh_token"]
    with open(f, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return nuevos["access_token"]


# ════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS
# ════════════════════════════════════════════════════════════════════════════


def leer_operaciones_aprobadas(conn):
    """Operaciones 'aprobada' sin factura todavia, con el realm de su empresa."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT o.id, o.qbo_customer_id, o.compania_facturadora, o.moneda,
                   o.descripcion_factura, e.realm_id
            FROM operaciones o
            LEFT JOIN empresas e ON e.nombre = o.compania_facturadora
            WHERE o.estado = 'aprobada' AND o.qbo_invoice_id IS NULL
            ORDER BY o.creado_en
        """)
        return cur.fetchall()


def leer_lineas(conn, operacion_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT descripcion, horas_trabajadas, monto_por_hora, total_linea,
                   porcentaje_iva, exonerado
            FROM lineas_operacion
            WHERE operacion_id = %s
            ORDER BY orden
        """,
            (operacion_id,),
        )
        return cur.fetchall()


def marcar_facturada(conn, operacion_id, qbo_invoice_id, qbo_doc_number):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE operaciones
            SET estado = 'facturada', qbo_invoice_id = %s, qbo_doc_number = %s,
                facturado_en = now(), qbo_sync_error = NULL
            WHERE id = %s AND estado = 'aprobada'
        """,
            (qbo_invoice_id, qbo_doc_number, operacion_id),
        )
    conn.commit()


def marcar_error(conn, operacion_id, error):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE operaciones SET qbo_sync_error = %s WHERE id = %s",
            (str(error)[:500], operacion_id),
        )
    conn.commit()


# ════════════════════════════════════════════════════════════════════════════
#  CONSTRUIR Y ENVIAR LA FACTURA
# ════════════════════════════════════════════════════════════════════════════

MONEDA_QBO = {"Dólares": "USD", "Dolares": "USD", "Colones": "CRC"}


def item_para(realm):
    it = CFG["item_operaciones"]
    return it if isinstance(it, str) else it.get(realm)


def construir_factura(op, lineas, realm):
    customer = CFG["cliente_fijo"] or op["qbo_customer_id"]
    item_id = item_para(realm)

    lineas_qbo = []
    if MONTO_FIJO_PRUEBA is not None:
        # Modo prueba: una sola linea con monto chico (para nota de credito)
        lineas_qbo.append(
            {
                "DetailType": "SalesItemLineDetail",
                "Amount": float(MONTO_FIJO_PRUEBA),
                "Description": "FACTURA DE PRUEBA - anular con nota de credito",
                "SalesItemLineDetail": {
                    "ItemRef": {"value": item_id},
                    "Qty": 1,
                    "UnitPrice": float(MONTO_FIJO_PRUEBA),
                    "TaxCodeRef": {"value": "TAX"},
                },
            }
        )
    else:
        for ln in lineas:
            gravable = "NON" if ln["exonerado"] else "TAX"
            lineas_qbo.append(
                {
                    "DetailType": "SalesItemLineDetail",
                    "Amount": float(ln["total_linea"]),
                    "Description": ln["descripcion"],
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": item_id},
                        "Qty": float(ln["horas_trabajadas"]),
                        "UnitPrice": float(ln["monto_por_hora"]),
                        "TaxCodeRef": {"value": gravable},
                    },
                }
            )

    factura = {"Line": lineas_qbo, "CustomerRef": {"value": str(customer)}}

    if CFG["taxcode"]:
        factura["TxnTaxDetail"] = {"TxnTaxCodeRef": {"value": str(CFG["taxcode"])}}

    if CFG["usar_moneda_real"] and op.get("moneda"):
        cur = MONEDA_QBO.get(op["moneda"])
        if cur:
            factura["CurrencyRef"] = {"value": cur}

    return factura


def enviar_factura(realm, token, factura):
    url = f"{CFG['base_url']}/v3/company/{realm}/invoice?minorversion=75"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=factura, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"QBO {r.status_code}: {r.text[:300]}")
    return r.json()["Invoice"]


# ════════════════════════════════════════════════════════════════════════════
#  LOG EN PDF
# ════════════════════════════════════════════════════════════════════════════


def generar_pdf(resultados):
    try:
        from fpdf import FPDF
    except ImportError:
        print("[aviso] fpdf2 no instalado; salto el PDF. (pip install fpdf2)")
        return None

    os.makedirs("logs", exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta = os.path.join("logs", f"facturacion_{ENTORNO}_{ts}.pdf")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, f"RPA Operaciones - Log de facturacion ({ENTORNO})", ln=1)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Fecha: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}", ln=1)
    ok = sum(1 for r in resultados if r["ok"])
    pdf.cell(
        0,
        6,
        f"Total: {len(resultados)}   Facturadas: {ok}   Errores: {len(resultados)-ok}",
        ln=1,
    )
    pdf.ln(3)

    for r in resultados:
        pdf.set_font("Helvetica", "B", 9)
        estado = "OK" if r["ok"] else "ERROR"
        pdf.multi_cell(
            0, 5, f"[{estado}] Operacion {r['operacion_id']}  ({r['cliente']})"
        )
        pdf.set_font("Helvetica", "", 8)
        if r["ok"]:
            pdf.multi_cell(
                0,
                5,
                f"   Factura QBO Id {r['qbo_invoice_id']} / DocNumber {r['qbo_doc_number']} / Total {r['total']}",
            )
        else:
            pdf.multi_cell(0, 5, f"   {r['error']}")
        pdf.ln(1)

    pdf.output(ruta)
    return ruta


# ════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════


def validar_entorno():
    if ENTORNO not in ENTORNOS:
        sys.exit(f"ENTORNO invalido: {ENTORNO}")
    if ENTORNO == "sandbox" and "sandbox" not in CFG["base_url"]:
        sys.exit("BLINDAJE: en sandbox la URL debe ser de sandbox.")
    if not CFG["client_id"] or not CFG["client_secret"]:
        sys.exit("Faltan las llaves (client_id/secret) en el .env para este entorno.")
    if not DATABASE_URL:
        sys.exit("Falta DATABASE_URL.")


def realm_de(op):
    """Realm a usar: en sandbox el fijo; en produccion el de la empresa."""
    if CFG["realm_fijo"]:
        return CFG["realm_fijo"]
    return op.get("realm_id")


def main():
    validar_entorno()
    print("=" * 60)
    print(f"RPA OPERACIONES  —  ENTORNO: {ENTORNO.upper()}")
    if MONTO_FIJO_PRUEBA is not None:
        print(f"  *** MODO PRUEBA: monto fijo {MONTO_FIJO_PRUEBA} ***")
    if LIMITE_FACTURAS is not None:
        print(f"  *** LIMITE: {LIMITE_FACTURAS} factura(s) ***")
    print("=" * 60)

    conn = psycopg2.connect(DATABASE_URL)
    resultados = []
    try:
        operaciones = leer_operaciones_aprobadas(conn)
        if LIMITE_FACTURAS is not None:
            operaciones = operaciones[:LIMITE_FACTURAS]
        print(f"\nOperaciones a facturar: {len(operaciones)}\n")

        # cache de tokens por realm (para no refrescar de mas)
        tokens = {}

        for op in operaciones:
            oid = op["id"]
            realm = realm_de(op)
            cliente_lbl = op.get("compania_facturadora", "")

            # Validaciones antes de facturar
            if not realm or realm == "TODO":
                marcar_error(conn, oid, "Empresa sin realm_id (no factura por QBO)")
                resultados.append(
                    {
                        "ok": False,
                        "operacion_id": oid,
                        "cliente": cliente_lbl,
                        "error": "Empresa sin realm_id",
                    }
                )
                print(f"  [SKIP] {oid}: empresa sin realm")
                continue
            if ENTORNO == "sandbox" and realm in REALMS_PRODUCCION:
                sys.exit(
                    "BLINDAJE: en sandbox apareció un realm de produccion. Abortando."
                )
            customer = CFG["cliente_fijo"] or op["qbo_customer_id"]
            if not customer:
                marcar_error(conn, oid, "Operacion sin qbo_customer_id")
                resultados.append(
                    {
                        "ok": False,
                        "operacion_id": oid,
                        "cliente": cliente_lbl,
                        "error": "Sin qbo_customer_id",
                    }
                )
                print(f"  [SKIP] {oid}: sin qbo_customer_id")
                continue

            try:
                if realm not in tokens:
                    tokens[realm] = get_access_token(realm)
                lineas = leer_lineas(conn, oid)
                factura = construir_factura(op, lineas, realm)
                inv = enviar_factura(realm, tokens[realm], factura)

                marcar_facturada(conn, oid, inv["Id"], inv.get("DocNumber"))
                resultados.append(
                    {
                        "ok": True,
                        "operacion_id": oid,
                        "cliente": cliente_lbl,
                        "qbo_invoice_id": inv["Id"],
                        "qbo_doc_number": inv.get("DocNumber"),
                        "total": inv.get("TotalAmt"),
                    }
                )
                print(
                    f"  [OK]   {oid}: factura {inv.get('DocNumber')} total {inv.get('TotalAmt')}"
                )
            except Exception as e:
                marcar_error(conn, oid, e)
                resultados.append(
                    {
                        "ok": False,
                        "operacion_id": oid,
                        "cliente": cliente_lbl,
                        "error": str(e),
                    }
                )
                print(f"  [ERR]  {oid}: {e}")
    finally:
        conn.close()

    ruta_pdf = generar_pdf(resultados)
    ok = sum(1 for r in resultados if r["ok"])
    print("\n" + "=" * 60)
    print(f"Listo. Facturadas: {ok}   Errores: {len(resultados)-ok}")
    if ruta_pdf:
        print(f"Log PDF: {ruta_pdf}")
    print("=" * 60)


if __name__ == "__main__":
    main()
