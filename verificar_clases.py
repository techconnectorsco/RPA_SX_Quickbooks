import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

print("--- CONTENIDO DE LA TABLA CLASES EN BD LOCAL ---")
cur.execute("""
    SELECT c.id, c.nombre, c.qbo_class_id, c.servicio_id, s.nombre as servicio_nombre
    FROM clases c
    LEFT JOIN servicios s ON c.servicio_id = s.id
    LIMIT 30;
""")

for row in cur.fetchall():
    print(
        f"ID BD: {row[0]} | Clase: '{row[1]}' | QBO Class ID: '{row[2]}' | Servicio Vinculado: '{row[4]}'"
    )

cur.close()
conn.close()
