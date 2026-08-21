"""
facturacion_padre.py - Orquestador de sincronizacion de facturacion
====================================================================
Ejecuta en cadena:
  1. sync_facturas.py                 (QuickBooks  -> PostgreSQL)
  2. sync_postgresql_sqlserver_bi.py  (PostgreSQL  -> SQL Server)

El segundo SOLO corre si el primero termino bien.

Programador de tareas: ejecutar cada hora, Lunes a Viernes.
El propio script valida dia habil y ventana horaria (6,9,12,15,18 h)
con tolerancia de 5 minutos hacia adelante.

MODO PRUEBA:
  python facturacion_padre.py --test
    - NO valida hora (corre siempre).
    - NO manda nada a Teams.
    - Ejecuta ambos scripts en cadena para verificar la mecanica.
    - Igual respeta la regla: si el 1ro falla, no corre el 2do.
"""

import os
import sys
import subprocess
from datetime import datetime

# ── Configuracion ────────────────────────────────────────────────────────────
CARPETA = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(
    CARPETA, "venv", "Scripts", "python.exe"
)  # el mismo Python/venv con que se lanza el padre

SCRIPT_1 = os.path.join(CARPETA, "sync_facturas.py")  # QB -> PostgreSQL
SCRIPT_2 = os.path.join(
    CARPETA, "sync_postgresql_sqlserver_bi.py"
)  # PostgreSQL -> SQL Server

# Horas objetivo (reloj del sistema, 24h) y tolerancia hacia adelante
HORAS_OBJETIVO = [6, 9, 12, 15, 18]
TOLERANCIA_MIN = 5


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def es_dia_habil(ahora):
    # weekday(): lunes=0 ... domingo=6
    return ahora.weekday() < 5


def en_ventana_horaria(ahora):
    """True si estamos dentro de [hora_objetivo : hora_objetivo+5min]."""
    for h in HORAS_OBJETIVO:
        if ahora.hour == h and 0 <= ahora.minute <= TOLERANCIA_MIN:
            return True
    return False


def ejecutar_script(ruta):
    """Ejecuta un script hijo. Devuelve True si termino con codigo 0."""
    nombre = os.path.basename(ruta)
    if not os.path.exists(ruta):
        log(f"ERROR: no se encuentra el archivo {nombre}")
        return False

    log(f"--> Ejecutando {nombre} ...")
    resultado = subprocess.run(
        [PYTHON, ruta],
        cwd=CARPETA,  # corre en la carpeta correcta (para .env, rutas relativas)
    )
    if resultado.returncode == 0:
        log(f"    {nombre} termino OK.")
        return True
    else:
        log(f"    {nombre} FALLO (codigo {resultado.returncode}).")
        return False


def enviar_teams(ok, detalle):
    """Envia la tarjeta simple a Teams. Se importa aca para no exigir el
    modulo en modo --test."""
    try:
        from teams_notifier import enviar_tarjeta_simple
        from dotenv import load_dotenv

        load_dotenv(os.path.join(CARPETA, ".env"))
        webhook = os.getenv("TEAMS_WEBHOOK_URL")

        marca = datetime.now().strftime("%d/%m/%Y %H:%M")
        if ok:
            titulo = "Sincronizacion de facturacion - OK"
            mensaje = "La sincronizacion (QuickBooks -> PostgreSQL -> SQL Server) se completo correctamente."
        else:
            titulo = "Sincronizacion de facturacion - ERROR"
            mensaje = detalle

        enviar_tarjeta_simple(webhook, titulo, f"PRODUCCION  -  {marca}", mensaje)
    except Exception as e:
        log(f"[Teams] No se pudo notificar: {e}")


def correr_cadena(modo_test):
    """Ejecuta los dos scripts en cadena. Devuelve (ok, detalle)."""
    # Paso 1: QuickBooks -> PostgreSQL
    if not ejecutar_script(SCRIPT_1):
        detalle = "Fallo la sincronizacion QuickBooks -> PostgreSQL. No se ejecuto la copia a SQL Server."
        log(detalle)
        return False, detalle

    # Paso 2: PostgreSQL -> SQL Server (solo si el 1ro fue OK)
    if not ejecutar_script(SCRIPT_2):
        detalle = "La sincronizacion QuickBooks -> PostgreSQL fue OK, pero fallo la copia PostgreSQL -> SQL Server."
        log(detalle)
        return False, detalle

    log("Cadena completa: ambos scripts terminaron OK.")
    return True, "OK"


def main():
    modo_test = "--test" in sys.argv
    ahora = datetime.now()

    if modo_test:
        log("=== MODO PRUEBA (--test) ===")
        log("Sin validacion de hora. Sin envio a Teams. Solo se prueba la mecanica.")
        ok, detalle = correr_cadena(modo_test=True)
        log(f"=== Resultado de la prueba: {'OK' if ok else 'FALLO -> ' + detalle} ===")
        return

    # ── Modo real ──
    log(
        f"Ejecucion real. Fecha/hora del sistema: {ahora.strftime('%A %d/%m/%Y %H:%M')}"
    )

    if not es_dia_habil(ahora):
        log("Es fin de semana. No se ejecuta.")
        return

    if not en_ventana_horaria(ahora):
        log(
            f"Fuera de ventana horaria (objetivo: {HORAS_OBJETIVO}, tolerancia +{TOLERANCIA_MIN}min). No se ejecuta."
        )
        return

    log("Dia habil y dentro de ventana horaria. Iniciando cadena...")
    ok, detalle = correr_cadena(modo_test=False)
    enviar_teams(ok, detalle)
    log("Ejecucion finalizada.")


if __name__ == "__main__":
    main()
