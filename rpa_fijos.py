"""
rpa_fijos.py  —  CODIGO MADRE del RPA de facturacion de CONTRATOS FIJOS
========================================================================
Corre TODOS los dias (idealmente al mediodia hora CR). Cada dia:

  1. Busca EMISIONES en estado 'Lista' que aun no tengan qbo_invoice_id.
  2. Solo del MES ACTUAL (si corre en julio, solo emisiones de julio).
  3. Solo si HOY es el dia de emision del contrato (dia_emision), ajustado
     al mes: si el contrato pide 31 y el mes tiene 30, se usa el 30; si pide
     29/30/31 y es febrero, se usa el ultimo dia de febrero.
  4. (Las 'no_facturar' nunca llegan: quedan en estado 'No facturar', no 'Lista'.)

Para cada emision que pasa los 4 filtros, arma la factura segun su tipo:
  - facturar_completo -> todas las lineas del contrato (IVA dinamico por linea).
  - porcentaje        -> UNA linea por el % del total + nota en la factura.
  - monto_parcial     -> UNA linea por el monto exacto + nota en la factura.

Si la emision tiene monedaInvertida = true, factura en la moneda CONTRARIA
del contrato, resolviendo el tipo de cambio (venta) del dia con tipo_cambio.py,
y guarda la tasa usada en tipo_cambio_usado.

En QuickBooks SOLO CREA la factura (no la envia ni la transmite a Hacienda).
La encargada la revisa, la envia al cliente y le da ruta a Hacienda.

Un solo interruptor (ENTORNO) cambia entre SANDBOX y PRODUCCION.

Requiere (una vez):  pip install psycopg2-binary python-dotenv requests fpdf2
                     (y para tipo_cambio real:  pip install bccr pandas)
"""

import os
import sys
import json
import base64
import calendar
import datetime
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

from tipo_cambio import obtener_tipo_cambio

# ════════════════════════════════════════════════════════════════════════════
#  CONFIGURACION
# ════════════════════════════════════════════════════════════════════════════

# El ENTORNO ya NO se escribe aca: se lee de la variable QBO_ENTORNO del .env de
# CADA maquina. Asi el mismo .py corre en local (sandbox) y en el VPS (produccion)
# sin editar una sola linea. Default 'sandbox' = blindaje.
#   .env local ->  QBO_ENTORNO=sandbox     |     .env VPS -> QBO_ENTORNO=produccion
# --> se define ENTORNO mas abajo, despues de cargar el .env.

LIMITE_FACTURAS = None  # None = todas.  1 = procesar solo UNA (util al pasar a prod).
IGNORAR_DIA_EMISION = (
    False  # True = ignora el filtro del dia (util SOLO para probar en sandbox).
)

# Si en PRODUCCION falta un impuesto del % que pide una linea:
#   False (recomendado) -> NO lo crea; marca error. True -> lo crea.
CREAR_IMPUESTOS_FALTANTES = False

TAX_CODES_CACHE = {}

# ── Entornos ────────────────────────────────────────────────────────────────
load_dotenv()
RUTA_ENV_WEBAPP = r"D:\Users\Usuario\Desktop\SX-Ecosystem\SX-Ecosystem\.env"
if os.path.exists(RUTA_ENV_WEBAPP):
    load_dotenv(RUTA_ENV_WEBAPP, override=False)

# Entorno tomado del .env de la maquina (default sandbox = blindaje).
ENTORNO = os.getenv("QBO_ENTORNO", "sandbox").strip().lower()

ENTORNOS = {
    "sandbox": {
        "base_url": "https://sandbox-quickbooks.api.intuit.com",
        "tokens_file": os.path.join("config", "tokens_sandbox.json"),
        "client_id": os.getenv("QBO_SANDBOX_CLIENT_ID"),
        "client_secret": os.getenv("QBO_SANDBOX_CLIENT_SECRET"),
        "realm_fijo": "9341456664539574",
        "cliente_fijo": "58",
        "item_operaciones": "19",
        "usar_moneda_real": False,
    },
    "produccion": {
        "base_url": "https://quickbooks.api.intuit.com",
        "tokens_file": os.path.join("config", "tokens_empresas.json"),
        "client_id": os.getenv("QBO_CLIENT_ID"),
        "client_secret": os.getenv("QBO_CLIENT_SECRET"),
        "realm_fijo": None,
        "cliente_fijo": None,
        # Item con el que se factura cada empresa en produccion (confirmado
        # contra el catalogo real de QuickBooks de cada una):
        #   - Soportexperto y Laitcorp: "CONTRATOS HORAS ADICIONALES".
        #   - Hardware y Network NO tiene ese item; se usa "Venta de Servicios
        #     y Proyectos" (Id 157). Casi nunca se factura contratos por esta
        #     empresa, pero queda cubierto.
        # NOTA (Fase 2): hoy el item es FIJO por empresa; luego se hara dinamico
        # segun el tipo de servicio/clase de cada linea.
        "item_operaciones": {
            "9130355360397996": "3",  # Soportexperto  -> CONTRATOS HORAS ORDINARIAS
            "9130355360390096": "157",  # Hardware y Network -> Venta de Servicios y Proyectos
            "9130355360394696": "4",  # Laitcorp       -> CONTRATOS HORAS ORDINARIAS
        },
        "usar_moneda_real": True,
    },
}

if ENTORNO not in ENTORNOS:
    sys.exit(f"QBO_ENTORNO invalido: '{ENTORNO}'. Use 'sandbox' o 'produccion'.")

CFG = ENTORNOS[ENTORNO]
DATABASE_URL = os.getenv("DATABASE_URL")
REALMS_PRODUCCION = {"9130355360397996", "9130355360394696", "9130355360390096"}
MONEDA_QBO = {"Dólares": "USD", "Dolares": "USD", "Colones": "CRC"}


# ════════════════════════════════════════════════════════════════════════════
#  TOKENS
# ════════════════════════════════════════════════════════════════════════════


def get_access_token(realm):
    f = CFG["tokens_file"]
    with open(f, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    nodo = data if ENTORNO == "sandbox" else data["empresas"][realm]
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


def leer_emisiones_listas(conn, periodo):
    """
    Emisiones en 'Lista' del mes indicado (periodo 'YYYY-MM'), sin factura aun,
    con los datos del contrato que el RPA necesita.
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                em.id                AS emision_id,
                em.mes_facturado,
                em.estado_emision,
                em.porcentaje_real,
                em.monto_real,
                em.descripcion_factura AS emision_descripcion,
                em.moneda_invertida,
                c.id                 AS contrato_id,
                c.qbo_customer_id,
                c.compania_facturadora,
                c.moneda,
                c.dia_emision,
                c.descripcion_factura AS contrato_descripcion,
                e.realm_id
            FROM emisiones_cronograma em
            JOIN contratos_cronograma c ON c.id = em.contrato_id
            LEFT JOIN empresas e ON e.nombre = c.compania_facturadora
            WHERE em.estado = 'Lista'
              AND em.qbo_invoice_id IS NULL
              AND em.mes_facturado = %s
            ORDER BY c.compania_facturadora
        """,
            (periodo,),
        )
        return cur.fetchall()


def leer_lineas_contrato(conn, contrato_id):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT descripcion, cantidad, monto_por_unidad, total_linea,
                   porcentaje_iva, exonerado
            FROM lineas_contrato
            WHERE contrato_id = %s
            ORDER BY orden
        """,
            (contrato_id,),
        )
        return cur.fetchall()


def marcar_emitida(conn, emision_id, inv, tipo_cambio_usado):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE emisiones_cronograma
            SET estado = 'Emitida',
                qbo_invoice_id = %s,
                qbo_doc_number = %s,
                facturado_en = now(),
                fecha_emision = %s,
                tipo_cambio_usado = %s,
                qbo_sync_error = NULL
            WHERE id = %s AND estado = 'Lista'
        """,
            (
                inv["Id"],
                inv.get("DocNumber"),
                datetime.date.today().isoformat(),
                tipo_cambio_usado,
                emision_id,
            ),
        )
    conn.commit()


def marcar_error(conn, emision_id, error):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE emisiones_cronograma SET qbo_sync_error = %s WHERE id = %s",
            (str(error)[:500], emision_id),
        )
    conn.commit()


# ════════════════════════════════════════════════════════════════════════════
#  DIA DE EMISION  —  ajustado al mes
# ════════════════════════════════════════════════════════════════════════════


def dia_objetivo_este_mes(dia_emision, hoy):
    """
    Ajusta el dia_emision del contrato al mes actual:
    si el dia pedido no existe este mes (31 en un mes de 30, o 29/30/31 en
    febrero), devuelve el ultimo dia real del mes. Si no hay dia_emision,
    devuelve None.
    """
    if not dia_emision:
        return None
    ultimo_dia = calendar.monthrange(hoy.year, hoy.month)[1]
    return min(int(dia_emision), ultimo_dia)


# ════════════════════════════════════════════════════════════════════════════
#  IMPUESTOS DINAMICOS  (mismo criterio que operaciones: match por rate)
# ════════════════════════════════════════════════════════════════════════════


def _query_qbo(realm, token, sql):
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
    if realm in TAX_CODES_CACHE:
        return TAX_CODES_CACHE[realm]
    valor_de_rate = {}
    r = _query_qbo(realm, token, "SELECT * FROM TaxRate")
    if r.status_code == 200:
        for tr in r.json().get("QueryResponse", {}).get("TaxRate", []):
            try:
                valor_de_rate[tr["Id"]] = round(float(tr.get("RateValue", 0)), 2)
            except (TypeError, ValueError):
                pass
    mapa = {}
    r = _query_qbo(realm, token, "SELECT * FROM TaxCode")
    if r.status_code == 200:
        for tc in r.json().get("QueryResponse", {}).get("TaxCode", []):
            detalles = (tc.get("SalesTaxRateList") or {}).get("TaxRateDetail", [])
            for det in detalles:
                rid = (det.get("TaxRateRef") or {}).get("value")
                if rid in valor_de_rate:
                    mapa.setdefault(valor_de_rate[rid], tc["Id"])
                    break
    TAX_CODES_CACHE[realm] = mapa
    return mapa


def _crear_taxcode(porcentaje, realm, token):
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
        TAX_CODES_CACHE.get(realm, {})[round(float(porcentaje), 2)] = tcid
        return tcid
    raise RuntimeError(
        f"No se pudo crear impuesto {porcentaje}%: {r.status_code} {r.text[:200]}"
    )


def taxcode_para(porcentaje, realm, token):
    if porcentaje is None:
        return "NON"
    pct = round(float(porcentaje), 2)
    if pct == 0:
        return "NON"
    mapa = _cargar_mapa_impuestos(realm, token)
    if pct in mapa:
        return mapa[pct]
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


def item_para(realm):
    it = CFG["item_operaciones"]
    return it if isinstance(it, str) else it.get(realm)


def convertir_monto(monto, moneda_contrato, invertir, tc_venta):
    """
    Devuelve el monto en la moneda en que se va a facturar.
    Si no se invierte, el monto queda igual (moneda del contrato).
    Si se invierte:
      - contrato USD -> factura CRC: multiplica por venta.
      - contrato CRC -> factura USD: divide por venta.
    """
    if not invertir or not tc_venta:
        return round(float(monto), 2)
    if moneda_contrato in ("Dólares", "Dolares"):  # USD -> CRC
        return round(float(monto) * tc_venta, 2)
    else:  # CRC -> USD
        return round(float(monto) / tc_venta, 2)


def moneda_factura(moneda_contrato, invertir):
    """Moneda (USD/CRC) en la que se emite la factura."""
    base = MONEDA_QBO.get(moneda_contrato, "USD")
    if not invertir:
        return base
    return "CRC" if base == "USD" else "USD"


def construir_factura(em, lineas, realm, token, tc_venta):
    customer = CFG["cliente_fijo"] or em["qbo_customer_id"]
    item_id = item_para(realm)
    if not item_id or item_id == "TODO":
        raise RuntimeError("Falta configurar el item de contratos para esta empresa.")

    invertir = bool(em.get("moneda_invertida"))
    moneda_contrato = em.get("moneda") or "Dólares"
    tipo = em["estado_emision"]
    desc = (
        em.get("emision_descripcion")
        or em.get("contrato_descripcion")
        or "Servicios contratados"
    )

    lineas_qbo = []
    codigos_gravados = set()
    nota_factura = None

    def ref_impuesto(porcentaje, exonerado):
        if exonerado:
            return "NON"
        code = taxcode_para(porcentaje, realm, token)
        if code not in ("NON", "TAX"):
            codigos_gravados.add(code)
        return "TAX" if ENTORNO == "sandbox" else str(code)

    if tipo == "facturar_completo":
        for ln in lineas:
            amount = convertir_monto(
                ln["total_linea"], moneda_contrato, invertir, tc_venta
            )
            lineas_qbo.append(
                {
                    "DetailType": "SalesItemLineDetail",
                    "Amount": amount,
                    "Description": ln["descripcion"],
                    "SalesItemLineDetail": {
                        "ItemRef": {"value": item_id},
                        "Qty": float(ln["cantidad"]),
                        "UnitPrice": convertir_monto(
                            ln["monto_por_unidad"], moneda_contrato, invertir, tc_venta
                        ),
                        "TaxCodeRef": {
                            "value": ref_impuesto(ln["porcentaje_iva"], ln["exonerado"])
                        },
                    },
                }
            )
    else:
        # porcentaje o monto_parcial -> UNA sola linea + nota explicativa
        base_total = sum(
            float(l["total_linea"]) for l in lineas
        )  # subtotal del contrato
        if tipo == "porcentaje":
            pct = float(em.get("porcentaje_real") or 0)
            monto_base = base_total * pct / 100.0
            nota_factura = (
                f"Facturacion parcial: {pct:g}% del contrato mensual "
                f"segun acuerdo con el cliente."
            )
        else:  # monto_parcial
            monto_base = float(em.get("monto_real") or 0)
            nota_factura = (
                "Facturacion parcial por monto acordado con el cliente "
                "para este periodo."
            )

        # IVA: se usa el % de la primera linea gravada como referencia del cobro parcial.
        primera_gravada = next((l for l in lineas if not l["exonerado"]), None)
        pct_iva = primera_gravada["porcentaje_iva"] if primera_gravada else None
        exon = primera_gravada is None

        amount = convertir_monto(monto_base, moneda_contrato, invertir, tc_venta)
        lineas_qbo.append(
            {
                "DetailType": "SalesItemLineDetail",
                "Amount": amount,
                "Description": desc,
                "SalesItemLineDetail": {
                    "ItemRef": {"value": item_id},
                    "Qty": 1,
                    "UnitPrice": amount,
                    "TaxCodeRef": {"value": ref_impuesto(pct_iva, exon)},
                },
            }
        )

    factura = {"Line": lineas_qbo, "CustomerRef": {"value": str(customer)}}

    # Nota visible en la factura (para porcentaje / parcial)
    if nota_factura:
        factura["CustomerMemo"] = {"value": nota_factura}

    # Sandbox: impuesto global (limitacion del sandbox gringo)
    if ENTORNO == "sandbox" and codigos_gravados:
        factura["TxnTaxDetail"] = {
            "TxnTaxCodeRef": {"value": str(next(iter(codigos_gravados)))}
        }
        if len(codigos_gravados) > 1:
            print(
                "    [aviso sandbox] mezcla de % de IVA; en sandbox se aplica uno solo."
            )

    # Moneda: en produccion mandamos la moneda de la factura (invertida o no)
    if CFG["usar_moneda_real"]:
        factura["CurrencyRef"] = {"value": moneda_factura(moneda_contrato, invertir)}

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
    ruta = os.path.join("logs", f"contratos_{ENTORNO}_{ts}.pdf")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, f"RPA Contratos Fijos - Log ({ENTORNO})", ln=1)
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
        pdf.multi_cell(0, 5, f"[{estado}] {r['cliente']}  ({r['tipo']})")
        pdf.set_font("Helvetica", "", 8)
        if r["ok"]:
            extra = f" | TC {r['tc']}" if r.get("tc") else ""
            pdf.multi_cell(
                0,
                5,
                f"   Factura QBO Id {r['qbo_invoice_id']} / DocNumber {r['qbo_doc_number']} / Total {r['total']}{extra}",
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


def realm_de(em):
    if CFG["realm_fijo"]:
        return CFG["realm_fijo"]
    return em.get("realm_id")


def main():
    validar_entorno()
    hoy = datetime.date.today()
    periodo = f"{hoy.year}-{hoy.month:02d}"

    print("=" * 60)
    print(f"RPA CONTRATOS FIJOS  —  ENTORNO: {ENTORNO.upper()}")
    print(f"Fecha: {hoy.isoformat()}   Periodo: {periodo}")
    if LIMITE_FACTURAS is not None:
        print(f"   *** LIMITE: {LIMITE_FACTURAS} factura(s) ***")
    if IGNORAR_DIA_EMISION:
        print("   *** IGNORANDO dia_emision (modo prueba) ***")
    print("=" * 60)

    conn = psycopg2.connect(DATABASE_URL)
    resultados = []
    tc_dia = None  # se consulta solo si hace falta (alguna emision invertida)

    try:
        emisiones = leer_emisiones_listas(conn, periodo)
        print(f"\nEmisiones 'Lista' de {periodo}: {len(emisiones)}")

        # Filtro por dia de emision
        a_procesar = []
        for em in emisiones:
            objetivo = dia_objetivo_este_mes(em.get("dia_emision"), hoy)
            if IGNORAR_DIA_EMISION or (objetivo is not None and hoy.day == objetivo):
                a_procesar.append(em)
            else:
                print(
                    f"  [espera] {em['compania_facturadora']}: dia objetivo {objetivo}, hoy {hoy.day}"
                )

        if LIMITE_FACTURAS is not None:
            a_procesar = a_procesar[:LIMITE_FACTURAS]

        print(f"A facturar hoy: {len(a_procesar)}\n")

        tokens = {}
        for em in a_procesar:
            eid = em["emision_id"]
            realm = realm_de(em)
            cliente_lbl = em.get("compania_facturadora", "")
            tipo = em["estado_emision"]

            if not realm or realm == "TODO":
                marcar_error(conn, eid, "Empresa sin realm_id")
                resultados.append(
                    {
                        "ok": False,
                        "cliente": cliente_lbl,
                        "tipo": tipo,
                        "error": "Empresa sin realm_id",
                    }
                )
                print(f"  [SKIP] {cliente_lbl}: empresa sin realm")
                continue
            if ENTORNO == "sandbox" and realm in REALMS_PRODUCCION:
                sys.exit(
                    "BLINDAJE: en sandbox aparecio un realm de produccion. Abortando."
                )
            customer = CFG["cliente_fijo"] or em["qbo_customer_id"]
            if not customer:
                marcar_error(conn, eid, "Contrato sin qbo_customer_id")
                resultados.append(
                    {
                        "ok": False,
                        "cliente": cliente_lbl,
                        "tipo": tipo,
                        "error": "Sin qbo_customer_id",
                    }
                )
                print(f"  [SKIP] {cliente_lbl}: sin qbo_customer_id")
                continue

            try:
                # Tipo de cambio: solo si esta emision invierte moneda
                tc_venta = None
                if em.get("moneda_invertida"):
                    if tc_dia is None:
                        tc_dia = obtener_tipo_cambio()
                        print(
                            f"  Tipo de cambio del dia: venta {tc_dia['venta']} ({tc_dia['fuente']})"
                        )
                    tc_venta = tc_dia["venta"]

                if realm not in tokens:
                    tokens[realm] = get_access_token(realm)
                lineas = leer_lineas_contrato(conn, em["contrato_id"])
                factura = construir_factura(em, lineas, realm, tokens[realm], tc_venta)
                inv = enviar_factura(realm, tokens[realm], factura)

                marcar_emitida(conn, eid, inv, tc_venta)
                resultados.append(
                    {
                        "ok": True,
                        "cliente": cliente_lbl,
                        "tipo": tipo,
                        "qbo_invoice_id": inv["Id"],
                        "qbo_doc_number": inv.get("DocNumber"),
                        "total": inv.get("TotalAmt"),
                        "tc": tc_venta,
                    }
                )
                print(
                    f"  [OK]   {cliente_lbl} ({tipo}): factura {inv.get('DocNumber')} total {inv.get('TotalAmt')}"
                )
            except Exception as e:
                marcar_error(conn, eid, e)
                resultados.append(
                    {"ok": False, "cliente": cliente_lbl, "tipo": tipo, "error": str(e)}
                )
                print(f"  [ERR]  {cliente_lbl}: {e}")
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
