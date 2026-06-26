"""
rpa_operaciones.py  —  CODIGO MADRE del RPA de facturacion de OPERACIONES
=========================================================================
Lee las operaciones en estado 'aprobada' de la base, las factura en QuickBooks,
actualiza su estado a 'facturada' (guardando lo que devuelve QBO) y deja un PDF.

Un solo interruptor (ENTORNO) cambia entre SANDBOX y PRODUCCION.

IVA DINAMICO:
  - Cada linea trae su propio porcentaje_iva (cualquier valor) y flag exonerado.
  - El RPA busca en QuickBooks el impuesto cuyo PORCENTAJE coincide (por rate,
    no por nombre) y lo aplica a esa linea.
  - En sandbox, si no existe ese %, lo crea (es nuestro).
  - En produccion, lo BUSCA entre los que ya configuro el contador; NO crea
    impuestos en la contabilidad real (salvo que se habilite a proposito).

SEGURIDAD:
  - En sandbox la URL es de sandbox y todo va al cliente/item de prueba.
  - Solo toca operaciones 'aprobada' sin factura todavia (idempotente).
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
from generarlogpdf import ReporteFacturacionRPA

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURACION  —  lo unico que tocas para cambiar de entorno
# ════════════════════════════════════════════════════════════════════════════

ENTORNO = "sandbox"  # "sandbox"  o  "produccion"

LIMITE_FACTURAS = None  # None = todas las aprobadas.  1 = procesar solo UNA.
MONTO_FIJO_PRUEBA = None  # None = monto real.  Ej: 100 = forzar monto chico
# (primer disparo en produccion -> nota de credito).

# Si en PRODUCCION falta un impuesto del % que pide una linea:
#   False (recomendado) -> NO lo crea; marca error y avisa para que el contador
#                          lo configure. Nunca toca la contabilidad real solo.
#   True  -> lo crea (usar solo si sabes lo que haces).
CREAR_IMPUESTOS_FALTANTES = False

# Cache en memoria del mapa de impuestos por empresa: { realm: { porcentaje: taxCodeId } }
TAX_CODES_CACHE = {}

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
#  IMPUESTOS DINAMICOS  —  match por PORCENTAJE (rate), no por nombre
# ════════════════════════════════════════════════════════════════════════════


def _query_qbo(realm, token, sql):
    """GET a /query con la consulta correctamente codificada."""
    url = (
        f"{CFG['base_url']}/v3/company/{realm}/query"
        f"?query={requests.utils.quote(sql)}&minorversion=75"
    )
    return requests.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )


def _cargar_mapa_impuestos(realm, token):
    """Lee los impuestos YA configurados en la empresa y arma un mapa
    { porcentaje -> TaxCode Id }. Se hace una vez por realm (cache)."""
    if realm in TAX_CODES_CACHE:
        return TAX_CODES_CACHE[realm]

    # 1) TaxRate Id -> valor del porcentaje
    valor_de_rate = {}
    r = _query_qbo(realm, token, "SELECT * FROM TaxRate")
    if r.status_code == 200:
        for tr in r.json().get("QueryResponse", {}).get("TaxRate", []):
            try:
                valor_de_rate[tr["Id"]] = round(float(tr.get("RateValue", 0)), 2)
            except (TypeError, ValueError):
                pass

    # 2) TaxCode -> su porcentaje de venta (via su TaxRate)
    mapa = {}
    r = _query_qbo(realm, token, "SELECT * FROM TaxCode")
    if r.status_code == 200:
        for tc in r.json().get("QueryResponse", {}).get("TaxCode", []):
            detalles = (tc.get("SalesTaxRateList") or {}).get("TaxRateDetail", [])
            for det in detalles:
                rid = (det.get("TaxRateRef") or {}).get("value")
                if rid in valor_de_rate:
                    mapa.setdefault(valor_de_rate[rid], tc["Id"])  # 1er code con ese %
                    break

    TAX_CODES_CACHE[realm] = mapa
    return mapa


def _crear_taxcode(porcentaje, realm, token):
    """Crea un TaxCode con ese porcentaje (via taxservice). Devuelve su Id."""
    nombre = f"IVA {porcentaje}%"
    url = f"{CFG['base_url']}/v3/company/{realm}/taxservice/taxcode?minorversion=75"
    payload = {
        "TaxCode": nombre,
        "TaxRateDetails": [
            {
                "TaxRateName": nombre,
                "RateValue": porcentaje,
                "TaxAgencyId": "1",
                "TaxApplicableOn": "Sales",
            }
        ],
    }
    r = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if r.status_code in (200, 201):
        tcid = r.json().get("TaxCodeId")
        # refrescamos el cache para que la proxima linea lo encuentre
        TAX_CODES_CACHE.get(realm, {})[round(float(porcentaje), 2)] = tcid
        return tcid
    raise RuntimeError(
        f"No se pudo crear impuesto {porcentaje}%: {r.status_code} {r.text[:200]}"
    )


def taxcode_para(porcentaje, realm, token):
    """Devuelve el TaxCode Id para un porcentaje (dinamico, cualquier valor).
    'NON' si es exento o 0%."""
    if porcentaje is None:
        return "NON"
    pct = round(float(porcentaje), 2)
    if pct == 0:
        return "NON"

    mapa = _cargar_mapa_impuestos(realm, token)
    if pct in mapa:
        return mapa[pct]

    # No hay impuesto con ese % configurado en la empresa
    if ENTORNO == "sandbox" or CREAR_IMPUESTOS_FALTANTES:
        print(f"    impuesto {pct}% no existe; creandolo...")
        return _crear_taxcode(pct, realm, token)

    raise RuntimeError(
        f"La empresa no tiene configurado un impuesto del {pct}% en QuickBooks. "
        f"Pedile al contador que lo cree (o habilita CREAR_IMPUESTOS_FALTANTES)."
    )


# ════════════════════════════════════════════════════════════════════════════
#  CONSTRUIR Y ENVIAR LA FACTURA
# ════════════════════════════════════════════════════════════════════════════

MONEDA_QBO = {"Dólares": "USD", "Dolares": "USD", "Colones": "CRC"}


def item_para(realm):
    it = CFG["item_operaciones"]
    return it if isinstance(it, str) else it.get(realm)


def construir_factura(op, lineas, realm, token):
    customer = CFG["cliente_fijo"] or op["qbo_customer_id"]
    item_id = item_para(realm)
    if not item_id or item_id == "TODO":
        raise RuntimeError("Falta configurar el item de operaciones para esta empresa.")

    lineas_qbo = []
    codigos_gravados = set()  # taxcode ids usados (para el global del sandbox)

    def agregar(amount, qty, unit, desc, porcentaje, exonerado):
        if exonerado:
            code = "NON"
        else:
            code = taxcode_para(porcentaje, realm, token)
            if code not in ("NON", "TAX"):
                codigos_gravados.add(code)
        # En produccion la linea lleva el Id real del impuesto (QBO calcula).
        # En sandbox la linea va "TAX"/"NON" y el impuesto se inyecta global.
        if exonerado:
            line_ref = "NON"
        elif ENTORNO == "sandbox":
            line_ref = "TAX"
        else:
            line_ref = str(code)
        lineas_qbo.append(
            {
                "DetailType": "SalesItemLineDetail",
                "Amount": round(float(amount), 2),
                "Description": desc,
                "SalesItemLineDetail": {
                    "ItemRef": {"value": item_id},
                    "Qty": float(qty),
                    "UnitPrice": float(unit),
                    "TaxCodeRef": {"value": line_ref},
                },
            }
        )

    if MONTO_FIJO_PRUEBA is not None:
        agregar(
            MONTO_FIJO_PRUEBA,
            1,
            MONTO_FIJO_PRUEBA,
            "FACTURA DE PRUEBA - anular con nota de credito",
            13,
            False,
        )
    else:
        for ln in lineas:
            agregar(
                ln["total_linea"],
                ln["horas_trabajadas"],
                ln["monto_por_hora"],
                ln["descripcion"],
                ln["porcentaje_iva"],
                ln["exonerado"],
            )

    factura = {"Line": lineas_qbo, "CustomerRef": {"value": str(customer)}}

    # SANDBOX: aplica UN impuesto global (limitacion del sandbox gringo).
    #   - 1 solo porcentaje  -> exacto.
    #   - varios porcentajes -> usa uno (en sandbox no se puede mas; en
    #     produccion cada linea lleva el suyo y QBO calcula bien).
    if ENTORNO == "sandbox" and codigos_gravados:
        factura["TxnTaxDetail"] = {
            "TxnTaxCodeRef": {"value": str(next(iter(codigos_gravados)))}
        }
        if len(codigos_gravados) > 1:
            print(
                "    [aviso sandbox] la operacion mezcla varios % de IVA; en sandbox "
                "se aplica uno solo. En produccion sale correcto."
            )

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
    if CFG["realm_fijo"]:
        return CFG["realm_fijo"]
    return op.get("realm_id")


def main():
    validar_entorno()
    print("=" * 60)
    print(f"RPA OPERACIONES  —  ENTORNO: {ENTORNO.upper()}")
    if MONTO_FIJO_PRUEBA is not None:
        print(f"   *** MODO PRUEBA: monto fijo {MONTO_FIJO_PRUEBA} ***")
    if LIMITE_FACTURAS is not None:
        print(f"   *** LIMITE: {LIMITE_FACTURAS} factura(s) ***")
    print("=" * 60)

    # Inicializamos tu orquestador de reportes
    reporte = ReporteFacturacionRPA(entorno=ENTORNO)

    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        operaciones = leer_operaciones_aprobadas(conn)

        if LIMITE_FACTURAS is not None:
            operaciones = operaciones[:LIMITE_FACTURAS]
        print(f"\nOperaciones a facturar: {len(operaciones)}\n")

        tokens = {}

        for op in operaciones:
            oid = op["id"]
            realm = realm_de(op)
            cliente_lbl = op.get("compania_facturadora", "")

            # 1. Error: Empresa sin Realm
            if not realm or realm == "TODO":
                marcar_error(conn, oid, "Empresa sin realm_id (no factura por QBO)")

                # Registramos el error en tu reporte
                reporte.registrar_operacion(
                    op_id=oid,
                    compania=cliente_lbl,
                    factura_num="-",
                    lineas=[],
                    status="ERR",
                    error_msg="Empresa sin realm_id",
                )
                print(f"  [SKIP] {oid}: empresa sin realm")
                continue

            # Blindaje
            if ENTORNO == "sandbox" and realm in REALMS_PRODUCCION:
                sys.exit(
                    "BLINDAJE: en sandbox apareció un realm de produccion. Abortando."
                )

            # 2. Error: Sin cliente QBO
            customer = CFG["cliente_fijo"] or op["qbo_customer_id"]
            if not customer:
                marcar_error(conn, oid, "Operacion sin qbo_customer_id")

                # Registramos el error en tu reporte
                reporte.registrar_operacion(
                    op_id=oid,
                    compania=cliente_lbl,
                    factura_num="-",
                    lineas=[],
                    status="ERR",
                    error_msg="Sin qbo_customer_id",
                )
                print(f"  [SKIP] {oid}: sin qbo_customer_id")
                continue

            # Proceso de facturación activa
            try:
                if realm not in tokens:
                    tokens[realm] = get_access_token(realm)

                lineas = leer_lineas(conn, oid)
                factura = construir_factura(op, lineas, realm, tokens[realm])
                inv = enviar_factura(realm, tokens[realm], factura)

                marcar_facturada(conn, oid, inv["Id"], inv.get("DocNumber"))

                # 3. ÉXITO: Registramos la operación completada en tu reporte
                reporte.registrar_operacion(
                    op_id=oid,
                    compania=cliente_lbl,
                    factura_num=inv.get("DocNumber", "-"),
                    lineas=lineas,  # Pasamos las líneas leídas de la BD
                    status="OK",
                )
                print(
                    f"  [OK]   {oid}: factura {inv.get('DocNumber')} total {inv.get('TotalAmt')}"
                )

            except Exception as e:
                marcar_error(conn, oid, e)

                # 4. Error dinámico en el proceso de envío
                reporte.registrar_operacion(
                    op_id=oid,
                    compania=cliente_lbl,
                    factura_num="-",
                    lineas=[],
                    status="ERR",
                    error_msg=str(e),
                )
                print(f"  [ERR]  {oid}: {e}")

    except Exception as e_db:
        print(f"Error crítico en la conexión o lectura de la Base de Datos: {e_db}")

    finally:
        if conn is not None:
            conn.close()

    # LLAMADA CORRECTA: Compila los datos acumulados y escribe el PDF físico
    ruta_pdf = reporte.exportar_pdf()

    # Resumen final usando los contadores internos de tu propia clase
    print("\n" + "=" * 60)
    print(f"Listo. Facturadas: {reporte.exitosas}   Errores: {reporte.errores}")
    if ruta_pdf:
        print(f"Log PDF: {ruta_pdf}")
    print("=" * 60)


if __name__ == "__main__":
    main()
