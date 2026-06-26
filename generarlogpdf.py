"""
generarpdf.py - Soportexperto
Generación de Log de Control Ejecutivo para el RPA de Facturación de Operaciones.

Este módulo separa la lógica de reporteo visual del script principal de ejecución.
"""

import os
import tempfile
from datetime import datetime
from typing import Dict, List
from fpdf import FPDF

# Intentar importar qrcode para mantener la validación empresarial
try:
    import qrcode

    QR_DISPONIBLE = True
except ImportError:
    QR_DISPONIBLE = False

# =============================================================================
# CONSTANTES Y PALETA DE COLORES DE SOPORTEXPERTO
# =============================================================================
AZUL_CORP = (11, 27, 70)  # Azul oscuro ejecutivo
AZUL_ACCENTO = (40, 120, 200)  # Azul moderno
AZUL_FOOTER = (50, 65, 95)
VERDE_OK = (40, 167, 69)
ROJO_ERR = (220, 53, 69)
GRIS_TEXTO = (90, 90, 90)
GRIS_FONDO = (248, 249, 250)


def formato_moneda(valor: float) -> str:
    """Formato estándar con comas para miles y puntos para decimales."""
    if valor is None:
        valor = 0.0
    return f"{valor:,.2f}"


def generar_qr_verificacion(stats: Dict, fecha: str, hora: str) -> str:
    """Genera un código QR temporal con el resumen de la corrida del RPA."""
    if not QR_DISPONIBLE:
        return None

    verificacion_code = f"SE-RPA-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    contenido = [
        "==============================",
        "      SOPORTEXPERTO SX",
        "    Log de Operaciones RPA",
        "==============================",
        f"Fecha: {fecha} | {hora}",
        "",
        f"Procesadas OK: {stats.get('exitosas', 0)}",
        f"Errores: {stats.get('errores', 0)}",
        f"Total Facturado: {formato_moneda(stats.get('monto_total', 0.0))}",
        "",
        "==============================",
        f"Verificación: {verificacion_code}",
    ]

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data("\n".join(contenido))
    qr.make(fit=True)

    img_qr = qr.make_image(fill_color="black", back_color="white")
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    img_qr.save(temp_file.name)
    temp_file.close()

    return temp_file.name


# =============================================================================
# CLASE PDF - DISEÑO Y LAYOUT VISUAL (Horizontal / Legal para máximo detalle)
# =============================================================================
class LogFacturacionPDF(FPDF):

    def __init__(self, entorno: str, logo_path: str = "img/SX.png"):
        # Usamos formato Horizontal (L) y Legal para poder meter todas las columnas de las líneas detalladas
        super().__init__(orientation="L", unit="mm", format="Legal")
        self.entorno = entorno.upper()
        self.logo_path = logo_path
        self.qr_path = None
        self.datos_proceso = {}
        self.set_auto_page_break(auto=True, margin=25)
        self.set_margins(10, 10, 10)

    def set_qr_path(self, qr_path: str):
        self.qr_path = qr_path

    def header(self):
        # Logo de Soportexperto
        if self.logo_path and os.path.exists(self.logo_path):
            self.image(self.logo_path, 15, 8, 40)

        # Título Corporativo
        self.set_font("Arial", "B", 16)
        self.set_text_color(*AZUL_CORP)
        self.set_y(10)
        self.cell(0, 8, "SOPORTEXPERTO", 0, 1, "C")

        self.set_font("Arial", "B", 12)
        self.set_text_color(*AZUL_ACCENTO)
        self.cell(
            0,
            6,
            f"LOG DE CONTROL DE FACTURACIÓN RPA - ENTORNO: {self.entorno}",
            0,
            1,
            "C",
        )

        # Fechas y metadatos (Top Derecha)
        self.set_font("Arial", "", 10)
        self.set_text_color(*GRIS_TEXTO)
        fecha = self.datos_proceso.get("fecha", datetime.now().strftime("%d/%m/%Y"))
        hora = self.datos_proceso.get("hora", datetime.now().strftime("%I:%M %p"))
        self.set_xy(-110, 10)
        self.cell(60, 5, f"Fecha Ejecución: {fecha}", 0, 1, "R")
        self.set_xy(-110, 15)
        self.cell(60, 5, f"Hora Ejecución: {hora}", 0, 1, "R")

        # QR de Validación corporativa
        if self.qr_path and os.path.exists(self.qr_path):
            self.image(self.qr_path, self.w - 42, 5, 30)
            self.set_font("Arial", "I", 6)
            self.set_text_color(*GRIS_TEXTO)
            self.set_xy(self.w - 44, 36)
            self.cell(34, 3, "Validación Interna RPA", 0, 0, "C")

        # Línea divisoria elegante
        self.set_draw_color(*AZUL_ACCENTO)
        self.set_line_width(0.6)
        self.line(10, 41, self.w - 10, 41)
        self.set_y(45)

    def footer(self):
        self.set_y(-23)
        # Decoración inferior
        self.set_fill_color(*AZUL_ACCENTO)
        self.rect(0, self.h - 24, self.w, 2, "F")
        self.set_fill_color(*AZUL_FOOTER)
        self.rect(0, self.h - 22, self.w, 22, "F")

        self.set_text_color(255, 255, 255)
        self.set_font("Arial", "B", 10)
        self.set_y(-17)
        self.cell(
            0,
            5,
            "Oficina de Transformación Digital | SoporteXperto",
            0,
            1,
            "C",
        )
        self.set_font("Arial", "I", 8)
        self.cell(
            0, 5, "SOPORTEXPERTO.COM | Monitoreo de Procesos QuickBooks", 0, 1, "C"
        )

        # Paginación
        self.set_font("Arial", "I", 9)
        self.set_xy(-25, -13)
        self.cell(15, 5, f"Pág {self.page_no()}", 0, 0, "R")

    def agregar_resumen(self, stats: Dict):
        """Bloque superior tipo Dashboard Ejecutivo."""
        self.set_font("Arial", "B", 11)
        self.set_text_color(*AZUL_CORP)
        self.cell(0, 6, "1. RESUMEN DE COMPROBACIÓN EJECUTIVA", 0, 1, "L")
        self.ln(1)

        y_pos = self.get_y()
        self.set_fill_color(*GRIS_FONDO)
        self.rect(10, y_pos, self.w - 20, 20, "F")
        self.set_fill_color(*AZUL_CORP)
        self.rect(10, y_pos, 2, 20, "F")

        self.set_xy(15, y_pos + 3)
        self.set_font("Arial", "B", 10)
        self.set_text_color(0, 0, 0)

        # Datos en columnas alineadas
        self.cell(65, 6, f"Total Solicitudes: {stats.get('total', 0)}", 0, 0)

        self.set_text_color(*VERDE_OK)
        self.cell(65, 6, f"Facturadas [OK]: {stats.get('exitosas', 0)}", 0, 0)

        self.set_text_color(*ROJO_ERR)
        self.cell(65, 6, f"Con Error [ERR]: {stats.get('errores', 0)}", 0, 0)

        self.set_text_color(0, 0, 0)
        self.cell(
            0,
            6,
            f"Monto Total Procesado: {formato_moneda(stats.get('monto_total', 0))} USD",
            0,
            1,
        )

        self.set_x(15)
        self.set_font("Arial", "I", 9)
        self.set_text_color(*GRIS_TEXTO)
        self.cell(
            0,
            5,
            f"Duración de corrida: {stats.get('duracion', 'N/A')}",
            0,
            1,
        )
        self.ln(6)

    def agregar_detalle_operaciones(self, operaciones: List[Dict]):
        """Genera la tabla super detallada mapeando facturas y líneas internas."""
        self.set_font("Arial", "B", 11)
        self.set_text_color(*AZUL_CORP)
        self.cell(0, 6, "2. DESGLOSE GRANULAR DE OPERACIONES Y LÍNEAS", 0, 1, "L")
        self.ln(1)

        # Columnas estructurales ajustadas para el ancho Legal Horizontal (355.6 mm de ancho total)
        # Anchos suman 335 mm (deja márgenes perfectos)
        anchos = [65, 35, 25, 100, 20, 25, 25, 40]
        headers = [
            "ID Operación / UUID",
            "Compañía",
            "Factura QBO",
            "Línea: Descripción del Servicio",
            "Horas",
            "Precio/H",
            "Total Línea",
            "Estado Sync",
        ]

        def imprimir_headers():
            self.set_font("Arial", "B", 9)
            self.set_fill_color(230, 235, 245)
            self.set_text_color(*AZUL_CORP)
            for i, h in enumerate(headers):
                self.cell(anchos[i], 6, h, 1, 0, "C", True)
            self.ln()

        imprimir_headers()

        self.set_font("Arial", "", 8)
        self.set_text_color(0, 0, 0)

        for op in operaciones:
            lineas = op.get("lineas", [])
            num_lineas = len(lineas) if lineas else 1

            # Si nos estamos quedando sin espacio en la página, salto preventivo
            if self.get_y() > self.h - 35:
                self.add_page()
                imprimir_headers()

            y_antes_de_op = self.get_y()

            # Determinar el color de estado general de la operación
            color_estado = VERDE_OK if op.get("status") == "OK" else ROJO_ERR

            # Iterar por cada una de las líneas para imprimir la cuadrícula detallada
            for idx, ln in enumerate(lineas):
                if self.get_y() > self.h - 25:
                    self.add_page()
                    imprimir_headers()

                # Celdas que cambian por línea
                # Calculamos el X de inicio de fila para mantener control estricto de columnas
                self.set_x(10)

                # 1. ID Operación (Solo se pone en la primera línea o repetido elegantemente)
                self.set_font("Arial", "B" if idx == 0 else "", 7)
                self.cell(anchos[0], 6, op.get("id") if idx == 0 else '"', 1, 0, "C")

                # 2. Compañía
                self.set_font("Arial", "", 7)
                comp = (op.get("compania") or "")[:20]
                self.cell(anchos[1], 6, comp if idx == 0 else '"', 1, 0, "L")

                # 3. Documento / Factura Nro
                self.set_font("Arial", "B", 8)
                self.cell(
                    anchos[2],
                    6,
                    str(op.get("factura", "-")) if idx == 0 else '"',
                    1,
                    0,
                    "C",
                )

                # ---- DATOS DE LA LÍNEA ESPECÍFICA ----
                self.set_font("Arial", "", 8)
                desc_linea = (ln.get("descripcion") or "")[:60]

                desc_linea = desc_linea.encode("latin-1", "replace").decode("latin-1")

                self.cell(anchos[3], 6, f" -> {desc_linea}", 1, 0, "L")
                self.cell(anchos[4], 6, str(ln.get("horas_trabajadas", 0)), 1, 0, "C")
                self.cell(
                    anchos[5], 6, formato_moneda(ln.get("monto_por_hora", 0)), 1, 0, "R"
                )
                self.cell(
                    anchos[6], 6, formato_moneda(ln.get("total_linea", 0)), 1, 0, "R"
                )

                # 8. Estado individual / Logs de Error
                self.set_font("Arial", "B", 8)
                self.set_text_color(*color_estado)
                txt_status = (
                    op.get("error_msg")
                    if op.get("status") != "OK"
                    else "Sincronizado OK"
                )
                txt_status = txt_status.encode("latin-1", "replace").decode("latin-1")
                self.cell(anchos[7], 6, txt_status[:24] if idx == 0 else '"', 1, 1, "C")
                self.set_text_color(0, 0, 0)


# =============================================================================
# MANEJADOR / COLECTOR ORQUESTADOR (La clase que vas a llamar desde operaciones.py)
# =============================================================================
class ReporteFacturacionRPA:

    def __init__(self, entorno: str):
        self.entorno = entorno
        self.inicio_time = datetime.now()
        self.operaciones_procesadas = []
        self.monto_acumulado = 0.0
        self.exitosas = 0
        self.errores = 0

    def registrar_operacion(
        self,
        op_id: str,
        compania: str,
        factura_num: str,
        lineas: List[Dict],
        status: str,
        error_msg: str = "",
    ):
        """Agrega los datos recolectados de una operación al lote del reporte."""
        total_op = (
            sum(float(ln.get("total_linea", 0 or 0.0)) for ln in lineas)
            if lineas
            else 0.0
        )

        if status == "OK":
            self.exitosas += 1
            self.monto_acumulado += total_op
        else:
            self.errores += 1

        self.operaciones_procesadas.append(
            {
                "id": op_id,
                "compania": compania,
                "factura": factura_num,
                "lineas": lineas,
                "status": status,
                "error_msg": error_msg,
            }
        )

    def exportar_pdf(self, directorio_salida: str = "logs") -> str:
        """Compila toda la información recolectada y genera el archivo físico PDF."""
        fin_time = datetime.now()
        duracion = fin_time - self.inicio_time
        duracion_str = f"{int(duracion.total_seconds() // 60)}m {int(duracion.total_seconds() % 60)}s"

        stats = {
            "total": len(self.operaciones_procesadas),
            "exitosas": self.exitosas,
            "errores": self.errores,
            "monto_total": self.monto_acumulado,
            "duracion": duracion_str,
        }

        # Generar QR
        qr_file = generar_qr_verificacion(
            stats,
            self.inicio_time.strftime("%d/%m/%Y"),
            self.inicio_time.strftime("%I:%M %p"),
        )

        pdf = LogFacturacionPDF(entorno=self.entorno)
        pdf.datos_proceso = {
            "fecha": self.inicio_time.strftime("%d/%m/%Y"),
            "hora": self.inicio_time.strftime("%I:%M %p"),
        }

        if qr_file:
            pdf.set_qr_path(qr_file)

        pdf.add_page()
        pdf.agregar_resumen(stats)
        pdf.agregar_detalle_operaciones(self.operaciones_procesadas)

        # Formatear nombre de archivo final
        timestamp = self.inicio_time.strftime("%Y%m%d_%H%M%S")
        os.makedirs(directorio_salida, exist_ok=True)
        nombre_archivo = f"facturacion_{self.entorno.lower()}_{timestamp}.pdf"
        ruta_completa = os.path.join(directorio_salida, nombre_archivo)

        pdf.output(ruta_completa)

        # Borrar QR temporal
        if qr_file and os.path.exists(qr_file):
            try:
                os.remove(qr_file)
            except:
                pass

        return ruta_completa
