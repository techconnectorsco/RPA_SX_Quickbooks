import os
import json
import base64
import sys
import time
import requests
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

PROD_BASE_URL = "https://quickbooks.api.intuit.com"
TOKENS_FILE = os.path.join("config", "tokens_empresas.json")
CLIENT_ID = os.getenv("QBO_CLIENT_ID")
CLIENT_SECRET = os.getenv("QBO_CLIENT_SECRET")
DATABASE_URL = os.getenv("DATABASE_URL")

# Mismos IDs de tu script de clientes
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
        "nombre": "Corporacion Latinoamericana T.I.",
        "realm": "9130355360394696",
        "empresa_id": "01d18328-dccf-493b-aca7-05c5d74900a0",
    },
]


def refrescar_token(realm):
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
        return None
    nuevos = resp.json()
    empresa["access_token"] = nuevos["access_token"]
    empresa["refresh_token"] = nuevos["refresh_token"]
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return nuevos["access_token"]


def descargar_facturas(realm, token):
    facturas, inicio, lote = [], 1, 100
    print(f"    Iniciando descarga paginada...")

    while True:
        # Imprimimos progreso para no sentir que se quedó pegado
        print(f"    -> Solicitando registros {inicio} a {inicio + lote - 1}...")

        sql = f"SELECT * FROM Invoice STARTPOSITION {inicio} MAXRESULTS {lote}"
        url = f"{PROD_BASE_URL}/v3/company/{realm}/query?query={requests.utils.quote(sql)}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=60,  # Aumentamos un poco el timeout por seguridad
        )

        if resp.status_code != 200:
            print(f"    [ERROR] QuickBooks respondió: {resp.status_code}")
            break

        lote_res = resp.json().get("QueryResponse", {}).get("Invoice", [])
        if not lote_res:
            print("    -> No se encontraron más registros.")
            break

        facturas.extend(lote_res)
        print(f"    -> Recibidas {len(lote_res)} facturas en este lote.")

        if len(lote_res) < lote:
            break

        inicio += lote

        # --- AQUÍ ESTÁ LA PAUSA ---
        # Dormimos 2 segundos entre cada llamada para ser amables con la API
        time.sleep(2)

    return facturas


def guardar(conn, empresa_id, facturas):
    cur = conn.cursor()
    procesadas = 0

    for f in facturas:
        qbo_id = f.get("Id")

        # 1. UPSERT de la Cabecera
        insert_factura_sql = """
            INSERT INTO facturas_quickbooks (
                empresa_id, qbo_id, sync_token, doc_number, txn_date, due_date, 
                qbo_create_time, qbo_last_updated_time, qbo_customer_id, customer_name, 
                bill_email, free_form_address, currency, exchange_rate, total_amt, 
                home_total_amt, balance, home_balance, print_status, email_status, 
                global_tax_calculation, sales_term_ref, customer_memo, allow_ipn_payment, 
                allow_online_payment, allow_online_credit_card_payment, allow_online_ach_payment
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (qbo_id) DO UPDATE SET 
                sync_token = EXCLUDED.sync_token,
                balance = EXCLUDED.balance,
                home_balance = EXCLUDED.home_balance,
                print_status = EXCLUDED.print_status,
                email_status = EXCLUDED.email_status,
                actualizado_en = NOW()
            RETURNING id;
        """

        datos = (
            empresa_id,
            qbo_id,
            f.get("SyncToken"),
            f.get("DocNumber"),
            f.get("TxnDate"),
            f.get("DueDate"),
            f.get("MetaData", {}).get("CreateTime"),
            f.get("MetaData", {}).get("LastUpdatedTime"),
            f.get("CustomerRef", {}).get("value"),
            f.get("CustomerRef", {}).get("name"),
            f.get("BillEmail", {}).get("Address"),
            f.get("BillAddr", {}).get("FreeFormAddress"),
            f.get("CurrencyRef", {}).get("value"),
            f.get("ExchangeRate"),
            f.get("TotalAmt", 0),
            f.get("HomeTotalAmt", 0),
            f.get("Balance", 0),
            f.get("HomeBalance", 0),
            f.get("PrintStatus"),
            f.get("EmailStatus"),
            f.get("GlobalTaxCalculation"),
            f.get("SalesTermRef", {}).get("value"),
            f.get("CustomerMemo", {}).get("value"),
            f.get("AllowIPNPayment", False),
            f.get("AllowOnlinePayment", False),
            f.get("AllowOnlineCreditCardPayment", False),
            f.get("AllowOnlineACHPayment", False),
        )

        cur.execute(insert_factura_sql, datos)
        factura_db_id = cur.fetchone()[0]

        # 2. Reemplazar líneas
        cur.execute(
            "DELETE FROM lineas_facturas_quickbooks WHERE factura_qbo_id = %s",
            (factura_db_id,),
        )

        lineas = f.get("Line", [])
        datos_lineas = []
        for ln in lineas:
            # Solo guardamos líneas que tengan detalle de venta
            if "SalesItemLineDetail" in ln:
                sd = ln.get("SalesItemLineDetail", {})
                datos_lineas.append(
                    (
                        factura_db_id,
                        ln.get("LineNum"),
                        ln.get("Description"),
                        ln.get("Amount", 0),
                        ln.get("DetailType"),
                        sd.get("ItemRef", {}).get("value"),
                        sd.get("ItemRef", {}).get("name"),
                        sd.get("Qty"),
                        sd.get("UnitPrice"),
                        sd.get("TaxCodeRef", {}).get("value"),
                    )
                )

        if datos_lineas:
            insert_lineas_sql = """
                INSERT INTO lineas_facturas_quickbooks (
                    factura_qbo_id, line_number, description, amount, detail_type, 
                    item_ref_value, item_ref_name, qty, unit_price, tax_code_ref_value
                ) VALUES %s
            """
            execute_values(cur, insert_lineas_sql, datos_lineas)

        procesadas += 1

    conn.commit()
    return procesadas


def main():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        for emp in EMPRESAS:
            token = refrescar_token(emp["realm"])
            if not token:
                print('sin token')
                continue

            facturas = descargar_facturas(emp["realm"], token)
            n = guardar(conn, emp["empresa_id"], facturas)
            print(f"Sincronizado {emp['nombre']}: {n} facturas.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
