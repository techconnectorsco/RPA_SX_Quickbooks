import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()

print("--- BUSCANDO EN TABLA clientes_quickbooks ---")
cur.execute("""
    SELECT id, empresa_id, qbo_customer_id, display_name, company_name, activo, sincronizado_en
    FROM clientes_quickbooks
    WHERE LOWER(display_name) LIKE '%techconnector%' 
       OR LOWER(company_name) LIKE '%techconnector%';
""")

filas = cur.fetchall()
if filas:
    for f in filas:
        print(f"Encontrado | ID QBO: {f[2]} | Nombre: '{f[3]}' | Empresa WebApp: {f[1]} | Activo: {f[5]}")
else:
    print("[!] No se encontró ningún cliente con ese nombre en PostgreSQL.")

cur.close()
conn.close()