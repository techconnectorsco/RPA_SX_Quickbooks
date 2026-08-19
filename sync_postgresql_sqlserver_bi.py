import psycopg2
import pyodbc

# ─── CONEXIÓN POSTGRESQL (origen, lectura) ──────────────────
PG_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "dbname": "facturacion_db",
    "user": "powerbi_ro",
    "password": 'P0w3rB1$Kirkby07',
}

# ─── CONEXIÓN SQL SERVER (destino, escritura) ───────────────
SQL_SERVER = "localhost"
SQL_DATABASE = "facturacion_bi"
SQL_USER = "etl_facturas"
SQL_PASSWORD = "SQL.server2026@"

SQL_CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    f"SERVER={SQL_SERVER};"
    f"DATABASE={SQL_DATABASE};"
    f"UID={SQL_USER};"
    f"PWD={SQL_PASSWORD};"
    "TrustServerCertificate=yes;"
)


def leer_pg(cur, tabla, columnas):
    cur.execute(f"SELECT {', '.join(columnas)} FROM {tabla};")
    return cur.fetchall()


def merge_empresas(sql_cur, filas):
    sql = """
        MERGE dbo.empresas AS destino
        USING (SELECT ? AS id, ? AS nombre, ? AS nombre_corto, ? AS realm_id,
                      ? AS activa, ? AS creado_en, ? AS actualizado_en) AS origen
        ON destino.id = origen.id
        WHEN MATCHED THEN UPDATE SET
            nombre = origen.nombre, nombre_corto = origen.nombre_corto,
            realm_id = origen.realm_id, activa = origen.activa,
            actualizado_en = origen.actualizado_en
        WHEN NOT MATCHED THEN INSERT
            (id, nombre, nombre_corto, realm_id, activa, creado_en, actualizado_en)
            VALUES (origen.id, origen.nombre, origen.nombre_corto, origen.realm_id,
                    origen.activa, origen.creado_en, origen.actualizado_en);
    """
    for f in filas:
        sql_cur.execute(sql, f)


def merge_facturas(sql_cur, filas):
    sql = """
        MERGE dbo.facturas_quickbooks AS destino
        USING (SELECT ? AS id, ? AS empresa_id, ? AS qbo_id, ? AS sync_token,
                      ? AS doc_number, ? AS txn_date, ? AS due_date,
                      ? AS qbo_create_time, ? AS qbo_last_updated_time,
                      ? AS qbo_customer_id, ? AS customer_name, ? AS bill_email,
                      ? AS free_form_address, ? AS currency, ? AS exchange_rate,
                      ? AS total_amt, ? AS home_total_amt, ? AS balance,
                      ? AS home_balance, ? AS print_status, ? AS email_status,
                      ? AS global_tax_calculation, ? AS sales_term_ref,
                      ? AS customer_memo, ? AS allow_ipn_payment,
                      ? AS allow_online_payment, ? AS allow_online_credit_card_payment,
                      ? AS allow_online_ach_payment, ? AS creado_en,
                      ? AS actualizado_en) AS origen
        ON destino.id = origen.id
        WHEN MATCHED THEN UPDATE SET
            sync_token = origen.sync_token, balance = origen.balance,
            home_balance = origen.home_balance, print_status = origen.print_status,
            email_status = origen.email_status, actualizado_en = origen.actualizado_en
        WHEN NOT MATCHED THEN INSERT
            (id, empresa_id, qbo_id, sync_token, doc_number, txn_date, due_date,
             qbo_create_time, qbo_last_updated_time, qbo_customer_id, customer_name,
             bill_email, free_form_address, currency, exchange_rate, total_amt,
             home_total_amt, balance, home_balance, print_status, email_status,
             global_tax_calculation, sales_term_ref, customer_memo, allow_ipn_payment,
             allow_online_payment, allow_online_credit_card_payment,
             allow_online_ach_payment, creado_en, actualizado_en)
            VALUES (origen.id, origen.empresa_id, origen.qbo_id, origen.sync_token,
                    origen.doc_number, origen.txn_date, origen.due_date,
                    origen.qbo_create_time, origen.qbo_last_updated_time,
                    origen.qbo_customer_id, origen.customer_name, origen.bill_email,
                    origen.free_form_address, origen.currency, origen.exchange_rate,
                    origen.total_amt, origen.home_total_amt, origen.balance,
                    origen.home_balance, origen.print_status, origen.email_status,
                    origen.global_tax_calculation, origen.sales_term_ref,
                    origen.customer_memo, origen.allow_ipn_payment,
                    origen.allow_online_payment, origen.allow_online_credit_card_payment,
                    origen.allow_online_ach_payment, origen.creado_en,
                    origen.actualizado_en);
    """
    for f in filas:
        sql_cur.execute(sql, f)


def merge_lineas(sql_cur, filas):
    sql = """
        MERGE dbo.lineas_facturas_quickbooks AS destino
        USING (SELECT ? AS id, ? AS factura_qbo_id, ? AS line_number,
                      ? AS description, ? AS amount, ? AS detail_type,
                      ? AS item_ref_value, ? AS item_ref_name, ? AS qty,
                      ? AS unit_price, ? AS tax_code_ref_value, ? AS creado_en) AS origen
        ON destino.id = origen.id
        WHEN MATCHED THEN UPDATE SET
            description = origen.description, amount = origen.amount,
            qty = origen.qty, unit_price = origen.unit_price
        WHEN NOT MATCHED THEN INSERT
            (id, factura_qbo_id, line_number, description, amount, detail_type,
             item_ref_value, item_ref_name, qty, unit_price, tax_code_ref_value, creado_en)
            VALUES (origen.id, origen.factura_qbo_id, origen.line_number,
                    origen.description, origen.amount, origen.detail_type,
                    origen.item_ref_value, origen.item_ref_name, origen.qty,
                    origen.unit_price, origen.tax_code_ref_value, origen.creado_en);
    """
    for f in filas:
        sql_cur.execute(sql, f)


def main():
    print("Conectando a PostgreSQL...")
    pg = psycopg2.connect(**PG_CONFIG)
    pg_cur = pg.cursor()

    print("Conectando a SQL Server...")
    sql = pyodbc.connect(SQL_CONN_STR)
    sql_cur = sql.cursor()
    sql_cur.fast_executemany = False

    cols_empresas = ["id", "nombre", "nombre_corto", "realm_id", "activa",
                     "creado_en", "actualizado_en"]
    cols_facturas = ["id", "empresa_id", "qbo_id", "sync_token", "doc_number",
                     "txn_date", "due_date", "qbo_create_time", "qbo_last_updated_time",
                     "qbo_customer_id", "customer_name", "bill_email", "free_form_address",
                     "currency", "exchange_rate", "total_amt", "home_total_amt", "balance",
                     "home_balance", "print_status", "email_status", "global_tax_calculation",
                     "sales_term_ref", "customer_memo", "allow_ipn_payment",
                     "allow_online_payment", "allow_online_credit_card_payment",
                     "allow_online_ach_payment", "creado_en", "actualizado_en"]
    cols_lineas = ["id", "factura_qbo_id", "line_number", "description", "amount",
                   "detail_type", "item_ref_value", "item_ref_name", "qty", "unit_price",
                   "tax_code_ref_value", "creado_en"]

    print("Leyendo empresas...")
    empresas = leer_pg(pg_cur, "empresas", cols_empresas)
    merge_empresas(sql_cur, empresas)
    sql.commit()
    print(f"  {len(empresas)} empresas sincronizadas.")

    print("Leyendo facturas...")
    facturas = leer_pg(pg_cur, "facturas_quickbooks", cols_facturas)
    print(f"  {len(facturas)} facturas. Escribiendo en SQL Server...")
    merge_facturas(sql_cur, facturas)
    sql.commit()
    print("  Facturas sincronizadas.")

    print("Leyendo lineas...")
    lineas = leer_pg(pg_cur, "lineas_facturas_quickbooks", cols_lineas)
    print(f"  {len(lineas)} lineas. Escribiendo en SQL Server...")
    merge_lineas(sql_cur, lineas)
    sql.commit()
    print("  Lineas sincronizadas.")

    pg_cur.close(); pg.close()
    sql_cur.close(); sql.close()
    print("Sincronizacion completa.")


if __name__ == "__main__":
    main()