import psycopg2
import pyodbc
from decimal import Decimal
import time

# ─── CONEXIÓN POSTGRESQL (origen, lectura) ──────────────────
PG_CONFIG = {
    "host": "localhost",
    "port": "5432",
    "dbname": "facturacion_db",
    "user": "powerbi_ro",
    "password": "P0w3rB1$Kirkby07",
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

LOTE = 1000  # filas por lote


def leer_pg(cur, tabla, columnas):
    print(f"  Ejecutando consulta en {tabla}... (puede tardar unos segundos)")
    cur.execute(f"SELECT {', '.join(columnas)} FROM {tabla};")
    filas = cur.fetchall()
    print(f"  Leidas {len(filas)} filas de {tabla}. Convirtiendo...")
    convertidas = []
    for fila in filas:
        convertidas.append(
            tuple(float(v) if isinstance(v, Decimal) else v for v in fila)
        )
    print(f"  Listo para escribir {len(convertidas)} filas en SQL Server.")
    return convertidas


def chunk(lista, n):
    for i in range(0, len(lista), n):
        yield lista[i : i + n]


def sincronizar(sql, tabla, columnas, filas, update_cols):
    if not filas:
        print(f"  {tabla}: sin filas.")
        return

    cur = sql.cursor()
    cur.fast_executemany = True

    # Forzar que las columnas de texto se traten como NVARCHAR(MAX)
    # para evitar el truncado con fast_executemany
    tipos = []
    for c in columnas:
        if c in (
            "customer_memo",
            "free_form_address",
            "description",
            "customer_name",
            "item_ref_name",
            "bill_email",
        ):
            tipos.append((pyodbc.SQL_WVARCHAR, 0, 0))  # 0 = MAX
        else:
            tipos.append(None)
    cur.setinputsizes(tipos)
    cur.fast_executemany = True

    cols_list = ", ".join(columnas)
    placeholders = ", ".join(["?"] * len(columnas))
    set_clause = ", ".join([f"destino.{c} = origen.{c}" for c in update_cols])
    insert_cols = ", ".join(columnas)
    insert_vals = ", ".join([f"origen.{c}" for c in columnas])

    total_filas = len(filas)
    total = 0
    inicio = time.time()
    print(f"  --- Escribiendo {total_filas} filas en {tabla} (lotes de {LOTE}) ---")

    for lote in chunk(filas, LOTE):
        cur.execute(f"SELECT TOP 0 {cols_list} INTO #tmp FROM dbo.{tabla};")
        cur.executemany(
            f"INSERT INTO #tmp ({cols_list}) VALUES ({placeholders});", lote
        )
        cur.execute(f"""
            MERGE dbo.{tabla} AS destino
            USING #tmp AS origen
            ON destino.id = origen.id
            WHEN MATCHED THEN UPDATE SET {set_clause}
            WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals});
        """)
        cur.execute("DROP TABLE #tmp;")
        sql.commit()
        total += len(lote)
        pct = (total / total_filas) * 100
        transcurrido = time.time() - inicio
        print(f"  {tabla}: {total}/{total_filas} ({pct:.1f}%) - {transcurrido:.0f}s")

    print(f"  --- {tabla} completada en {time.time() - inicio:.0f}s ---")
    cur.close()


def main():
    print("Conectando a PostgreSQL...")
    pg = psycopg2.connect(**PG_CONFIG)
    pg_cur = pg.cursor()

    print("Conectando a SQL Server...")
    sql = pyodbc.connect(SQL_CONN_STR)

    # ── Definición de columnas ──
    cols_empresas = [
        "id",
        "nombre",
        "nombre_corto",
        "realm_id",
        "activa",
        "creado_en",
        "actualizado_en",
    ]
    cols_facturas = [
        "id",
        "empresa_id",
        "qbo_id",
        "sync_token",
        "doc_number",
        "txn_date",
        "due_date",
        "qbo_create_time",
        "qbo_last_updated_time",
        "qbo_customer_id",
        "customer_name",
        "bill_email",
        "free_form_address",
        "currency",
        "exchange_rate",
        "total_amt",
        "home_total_amt",
        "balance",
        "home_balance",
        "print_status",
        "email_status",
        "global_tax_calculation",
        "sales_term_ref",
        "customer_memo",
        "allow_ipn_payment",
        "allow_online_payment",
        "allow_online_credit_card_payment",
        "allow_online_ach_payment",
        "creado_en",
        "actualizado_en",
    ]
    cols_lineas = [
        "id",
        "factura_qbo_id",
        "line_number",
        "description",
        "amount",
        "detail_type",
        "item_ref_value",
        "item_ref_name",
        "qty",
        "unit_price",
        "tax_code_ref_value",
        "creado_en",
    ]

    # Al actualizar, refrescamos TODOS los campos menos el id (la llave).
    upd_empresas = [c for c in cols_empresas if c != "id"]
    upd_facturas = [c for c in cols_facturas if c != "id"]
    upd_lineas = [c for c in cols_lineas if c != "id"]

    # ── Sincronizar en orden (respeta llaves foráneas) ──
    print("Leyendo empresas de PostgreSQL...")
    empresas = leer_pg(pg_cur, "empresas", cols_empresas)
    sincronizar(sql, "empresas", cols_empresas, empresas, upd_empresas)

    print("Leyendo facturas de PostgreSQL...")
    facturas = leer_pg(pg_cur, "facturas_quickbooks", cols_facturas)
    sincronizar(sql, "facturas_quickbooks", cols_facturas, facturas, upd_facturas)

    print("Leyendo lineas de PostgreSQL...")
    lineas = leer_pg(pg_cur, "lineas_facturas_quickbooks", cols_lineas)
    sincronizar(sql, "lineas_facturas_quickbooks", cols_lineas, lineas, upd_lineas)

    pg_cur.close()
    pg.close()
    sql.close()
    print("\nSincronizacion completa.")


if __name__ == "__main__":
    main()
