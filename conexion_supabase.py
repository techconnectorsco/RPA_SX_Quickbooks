"""
conexion_supabase.py - RPA QuickBooks Facturacion
Inicializa el cliente de Supabase y sube archivos al Storage.
(Copia del patron usado en los otros RPAs; lee credenciales del .env.)

Requiere:  pip install supabase python-dotenv
.env necesita:  SUPABASE_URL  y  SUPABASE_SERVICE_ROLE_KEY
"""

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def obtener_cliente_supabase() -> Client:
    """Inicializa y retorna el cliente de conexion a Supabase."""
    url: str = os.environ.get("SUPABASE_URL")
    # Service Role Key: permisos de administrador en el backend (RPA)
    key: str = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        raise ValueError(
            "Error: Faltan las credenciales SUPABASE_URL o "
            "SUPABASE_SERVICE_ROLE_KEY en el archivo .env"
        )

    cliente: Client = create_client(url, key)
    return cliente


def subir_archivo_bucket(
    nombre_bucket: str, ruta_archivo_local: str, ruta_destino_supabase: str
):
    """Sube un archivo a un bucket de Supabase Storage y devuelve su URL publica."""
    try:
        with open(ruta_archivo_local, "rb") as f:
            supabase_db.storage.from_(nombre_bucket).upload(
                file=f,
                path=ruta_destino_supabase,
                file_options={"content-type": "application/pdf"},
            )

        url_publica = supabase_db.storage.from_(nombre_bucket).get_public_url(
            ruta_destino_supabase
        )
        print(f"[OK] Archivo subido: {url_publica}")
        return url_publica

    except Exception as e:
        print(f"[ERROR] No se pudo subir el archivo a Supabase Storage: {e}")
        return None


# Instancia global reutilizable en otros modulos
supabase_db = obtener_cliente_supabase()
