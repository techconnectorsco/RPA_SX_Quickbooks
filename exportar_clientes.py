"""
exportar_datos_clientes.py
--------------------------
Genera un archivo SQL portable con los datos de clientes_quickbooks,
para que tu companero lo importe en SU base.

Es portable: NO depende de que los UUID de `empresas` sean iguales en ambas
bases. Cada cliente se inserta resolviendo la empresa por su realm_id (estable).

Genera el archivo:  datos_clientes_quickbooks.sql
"""

import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
RUTA_ENV_WEBAPP = r"D:\Users\Usuario\Desktop\SX-Ecosystem\SX-Ecosystem\.env"
if os.path.exists(RUTA_ENV_WEBAPP):
    load_dotenv(RUTA_ENV_WEBAPP, override=False)

DATABASE_URL = os.getenv("DATABASE_URL")
SALIDA = "datos_clientes_quickbooks.sql"

# realm_id de cada empresa facturadora (para dejarlos seteados en la otra base)
REALMS = {
    "Soportexperto.com S.A.": "9130355360397996",
    "Hardware y Network S.A.": "9130355360390096",
    "Corporación Latinoamericana de Tecnología T.I. S.A.": "9130355360394696",
}


def q(v):
    """Convierte un valor Python a literal SQL seguro."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    return "'" + str(v).replace("'", "''") + "'"


def main():
    if not DATABASE_URL:
        print("[ERROR] No encontre DATABASE_URL.")
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        SELECT e.realm_id, cq.qbo_customer_id, cq.display_name,
               cq.company_name, cq.email, cq.activo
        FROM clientes_quickbooks cq
        JOIN empresas e ON e.id = cq.empresa_id
        ORDER BY e.realm_id, cq.display_name;
    """)
    filas = cur.fetchall()
    conn.close()

    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write("-- Datos de clientes_quickbooks (generado automaticamente)\n")
        f.write("-- Correr DESPUES de migrar (la tabla debe existir ya).\n\n")

        f.write("-- 1) Setear el realm_id de cada empresa facturadora\n")
        for nombre, realm in REALMS.items():
            f.write(
                f"UPDATE empresas SET realm_id = '{realm}' WHERE nombre = {q(nombre)};\n"
            )

        f.write("\n-- 2) Cargar los clientes (resuelve la empresa por realm_id)\n")
        for realm_id, qbo_id, display, company, email, activo in filas:
            f.write(
                "INSERT INTO clientes_quickbooks "
                "(empresa_id, qbo_customer_id, display_name, company_name, email, activo) "
                f"SELECT e.id, {q(qbo_id)}, {q(display)}, {q(company)}, {q(email)}, {q(activo)} "
                f"FROM empresas e WHERE e.realm_id = {q(realm_id)} "
                "ON CONFLICT (empresa_id, qbo_customer_id) DO NOTHING;\n"
            )

    print(f"Listo. Archivo generado: {SALIDA}  ({len(filas)} clientes)")


if __name__ == "__main__":
    main()
