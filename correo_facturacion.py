"""
correo_facturacion.py
---------------------
Envio de correos del RPA de facturacion (fijos y operaciones) por Microsoft
Graph, con el MISMO patron que el resto de SX: token via MSAL y envio por la
API de Graph desde boot@soportexperto.com.

Es un modulo PROPIO del RPA de facturacion, separado del sendEmail.py de otros
proyectos, para no acoplarlos. La diferencia con aquel: aca se agrega el envio
CON ADJUNTO (el PDF del reporte), que Graph soporta mandando el archivo como
fileAttachment con su contenido en base64.

Requiere en el .env (mismas credenciales de la app de Azure):
    TENANT_ID=...
    CLIENT_ID=...
    VALUE=...            (el client secret; se llama asi en el patron de SX)

Requiere:  pip install msal requests python-decouple
"""

import os
import base64

import requests
from msal import ConfidentialClientApplication

try:
    # Mismo lector que usan los otros RPA de SX.
    from decouple import config as _config

    def _env(clave, default=None):
        return _config(clave, default=default)

except Exception:
    # Respaldo: si no esta python-decouple, leer del entorno.
    def _env(clave, default=None):
        return os.getenv(clave, default)


TENANT_ID = _env("TENANT_ID")
CLIENT_ID = _env("CLIENT_ID")
CLIENT_SECRET = _env("VALUE")

SENDER_EMAIL = "boot@soportexperto.com"


class EmailSenderFacturacion:
    """Envia correos por Microsoft Graph. Instancia = un token nuevo."""

    def __init__(self):
        self.token = self._get_access_token()

    def _get_access_token(self):
        if not (TENANT_ID and CLIENT_ID and CLIENT_SECRET):
            raise RuntimeError(
                "Faltan credenciales de correo en el .env (TENANT_ID, CLIENT_ID, "
                "VALUE). Sin ellas no se puede enviar el reporte por correo."
            )
        authority = f"https://login.microsoftonline.com/{TENANT_ID}"
        app = ConfidentialClientApplication(
            client_id=CLIENT_ID,
            client_credential=CLIENT_SECRET,
            authority=authority,
        )
        resp = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if "access_token" not in resp:
            raise RuntimeError(f"No se pudo obtener token de correo: {resp}")
        return resp["access_token"]

    def enviar(self, to_email, subject, body_html, cc_email=None, adjunto_path=None):
        """Envia un correo HTML, opcionalmente con UN archivo adjunto (el PDF).

        to_email  : str o lista de correos.
        adjunto_path : ruta a un archivo local que se adjunta (o None).
        Devuelve True si Graph acepto el envio (202), False si no.
        """
        if isinstance(to_email, str):
            to_email = [to_email]
        to_recipients = [
            {"emailAddress": {"address": a.strip()}} for a in to_email if a
        ]

        message = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": to_recipients,
        }
        if cc_email:
            # Acepta CC como un solo correo (str) o como lista de correos.
            cc_lista = [cc_email] if isinstance(cc_email, str) else list(cc_email)
            message["ccRecipients"] = [
                {"emailAddress": {"address": a.strip()}} for a in cc_lista if a
            ]

        # Adjuntar el PDF si se paso una ruta valida.
        if adjunto_path and os.path.exists(adjunto_path):
            with open(adjunto_path, "rb") as f:
                contenido_b64 = base64.b64encode(f.read()).decode("utf-8")
            message["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": os.path.basename(adjunto_path),
                    "contentType": "application/pdf",
                    "contentBytes": contenido_b64,
                }
            ]
        elif adjunto_path:
            print(
                f"[correo] Aviso: no se encontro el adjunto {adjunto_path}, "
                f"se envia el correo sin PDF."
            )

        url = f"https://graph.microsoft.com/v1.0/users/{SENDER_EMAIL}/sendMail"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        resp = requests.post(
            url,
            headers=headers,
            json={"message": message, "saveToSentItems": "true"},
            timeout=60,
        )
        if resp.status_code == 202:
            _cc_txt = ""
            if cc_email:
                _cc = cc_email if isinstance(cc_email, str) else ", ".join(cc_email)
                _cc_txt = f" (CC: {_cc})"
            print(f"[correo] Enviado a {', '.join(to_email)}" + _cc_txt)
            return True
        print(f"[correo] Error {resp.status_code}: {resp.text[:300]}")
        return False
