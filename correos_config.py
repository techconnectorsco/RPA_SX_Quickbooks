"""
config_correos.py
-----------------
Un solo lugar para definir a QUIEN se envian los reportes de los RPA de
facturacion. Cambiar los correos aca, sin tocar el codigo de los RPA.

ESTADO ACTUAL: fase de verificacion.
  El To va SOLO a devs@techconnectors.co (el correo de Irving) para revisar como
  llega antes de que lo reciba la dueña de la empresa. NADA le llega a ella hasta
  que se agregue su correo abajo a proposito.
"""

# ── Destinatarios principales (To) ───────────────────────────────────────────
# Por ahora, solo el correo de verificacion. Cuando el reporte este aprobado,
# agregar aca el correo de la dueña, asi:
#     DESTINATARIOS = ["devs@techconnectors.co", "correo-de-la-duena@..."]
# o reemplazar por el de ella si ya no se quiere la copia de verificacion.
DESTINATARIOS = ["devs@techconnectors.co", "lucia.vargas@soportexperto.com"]

# ── Copia (CC) ───────────────────────────────────────────────────────────────
# Lista de correos en copia. Lista vacia [] = sin copia.
CC = [
    "omar.hernandez@soportexperto.com",
    "k.lindo@soportexperto.com",
    "lrivera@soportexperto.com",
]

# ── Por si algun dia fijos y operaciones deben ir a listas distintas ──────────
# Hoy los dos usan la misma lista de arriba. Si se necesita separar, cambiar
# estos valores; el resto del codigo ya los lee por separado.
DESTINATARIOS_FIJOS = DESTINATARIOS
DESTINATARIOS_OPERACIONES = DESTINATARIOS
CC_FIJOS = CC
CC_OPERACIONES = CC
