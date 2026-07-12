"""
supabase_manager.py - RPA QuickBooks Facturacion
Reporta cada ejecucion a Supabase (tabla 'ejecuciones'), subiendo el PDF del
log al bucket y guardando las metricas de la corrida. Mismo patron que los
otros RPAs del grupo.

UUIDs de automatizacion (registrados en Supabase):
  - Operaciones     -> ID_RPA_OPERACIONES
  - Contratos Fijos -> ID_RPA_FIJOS
Cada RPA pasa el suyo al llamar finalizar_y_reportar(..., automatizacion_id=...).
"""

import os
from datetime import datetime, timezone
from conexion_supabase import supabase_db, subir_archivo_bucket

# ── UUIDs de las automatizaciones en Supabase ────────────────────────────────
ID_RPA_OPERACIONES = "a4f8c1e2-3b6d-4e9a-8c72-1f0d5a9b2c34"
ID_RPA_FIJOS = "b7e2d9a4-8c15-4f3b-9d60-2a4e7c1b8f56"


def verificar_estado_rpa():
    """Consulta si el RPA esta activo en Supabase.

    Por ahora esta FORZADO a ACTIVO (siempre ejecuta). El interruptor remoto
    (poder apagar el RPA desde Supabase) queda preparado como mejora futura:
    cuando se implemente, aca se consultaria el campo 'esta_activa' de la
    automatizacion y se devolveria ese valor.
    """
    try:
        print("[info] Estado del RPA: ACTIVO (siempre ejecutar).")
        return True
    except Exception as e:
        print(f"[aviso] Error verificando estado: {e}. Continuando por defecto...")
        return True


def finalizar_y_reportar(
    status_global,
    ruta_pdf_local=None,
    automatizacion_id=ID_RPA_OPERACIONES,
    subcarpeta="operaciones",
):
    """Sube el log PDF (si lo hay), consolida metricas y registra la ejecucion.

    automatizacion_id / subcarpeta permiten reportar a distintos RPAs
    (operaciones / contratos fijos) desde este mismo modulo.
    """
    print("[Supabase] Iniciando reporte final...")

    url_log_publica = None
    if ruta_pdf_local and os.path.exists(ruta_pdf_local):
        nombre_archivo_nube = (
            f"QuickBooks/{subcarpeta}/{datetime.now().strftime('%Y/%m')}/"
            f"log_{datetime.now().strftime('%d_%H%M%S')}.pdf"
        )
        url_log_publica = subir_archivo_bucket(
            "logs-rpa", ruta_pdf_local, nombre_archivo_nube
        )

    datos_ejecucion = {
        "automatizacion_id": automatizacion_id,
        "fecha_inicio": datetime.now(timezone.utc).isoformat(),
        "fecha_fin": datetime.now(timezone.utc).isoformat(),
        "estado": "Fallido" if status_global.get("error_critico") else "Exitoso",
        "metricas": status_global,
        "log_salida": url_log_publica or status_global.get("observaciones", ""),
    }

    try:
        res = supabase_db.table("ejecuciones").insert(datos_ejecucion).execute()
        print("[Supabase] Ejecucion reportada correctamente.")
        # Devolvemos tambien la URL del PDF para que el RPA pueda pasarla a Teams
        return {"data": res.data, "url_pdf": url_log_publica}
    except Exception as e:
        print(f"[Supabase] Error al insertar ejecucion: {e}")
        return {"data": None, "url_pdf": url_log_publica}
