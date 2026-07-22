import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

try:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # 1. Ver qué columnas tiene la tabla 'clases'
    cur.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'clases';
    """)
    columnas = cur.fetchall()

    print("==================================================")
    print(" ESTRUCTURA DE LA TABLA 'clases'")
    print("==================================================")
    if columnas:
        for col, tipo in columnas:
            print(f" - {col:<20} ({tipo})")
    else:
        print(" [!] No se encontró la tabla 'clases'. Revisa el nombre exacto.")

    # 2. Ver 5 registros de muestra
    print("\n==================================================")
    print(" PRIMEROS 5 REGISTROS")
    print("==================================================")
    cur.execute("SELECT * FROM clases LIMIT 5;")
    filas = cur.fetchall()

    if filas and columnas:
        nombres_cols = [c[0] for c in columnas]
        for f in filas:
            reg = dict(zip(nombres_cols, f))
            print(reg)
    else:
        print(" (Tabla vacía o no existe)")

    cur.close()
    conn.close()

except Exception as e:
    print(f"[ERROR BD] {e}")
