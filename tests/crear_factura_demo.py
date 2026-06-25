import requests
import json
from datetime import datetime, timedelta

# Cargar tokens
with open('tokens.json', 'r') as f:
    tokens = json.load(f)

ACCESS_TOKEN = tokens['access_token']
REALM_ID = tokens['realm_id']
BASE_URL = f"https://sandbox-quickbooks.api.intuit.com/v3/company/{REALM_ID}"

headers = {
    'Authorization': f'Bearer {ACCESS_TOKEN}',
    'Accept': 'application/json',
    'Content-Type': 'application/json'
}

# Fechas
hoy = datetime.now().strftime("%Y-%m-%d")
vencimiento = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

# Factura profesional con múltiples líneas
factura = {
    "Line": [
        {
            "Amount": 500.00,
            "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {
                "ItemRef": {"value": "1", "name": "Services"},
                "Qty": 5,
                "UnitPrice": 100
            },
            "Description": "Consultoría técnica - 5 horas"
        },
        {
            "Amount": 250.00,
            "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {
                "ItemRef": {"value": "1", "name": "Services"},
                "Qty": 2.5,
                "UnitPrice": 100
            },
            "Description": "Desarrollo de integración API"
        },
        {
            "Amount": 75.00,
            "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {
                "ItemRef": {"value": "1", "name": "Services"},
                "Qty": 1,
                "UnitPrice": 75
            },
            "Description": "Soporte técnico mensual"
        }
    ],
    "CustomerRef": {
        "value": "5"  # Dukes Basketball Camp
    },
    "TxnDate": hoy,
    "DueDate": vencimiento,
    "PrivateNote": "Factura generada automáticamente via API",
    "CustomerMemo": {
        "value": "Gracias por su preferencia. Pago a 30 días."
    }
}

# Enviar la factura
response = requests.post(
    f"{BASE_URL}/invoice",
    headers=headers,
    json=factura
)

if response.status_code == 200:
    data = response.json()
    invoice = data.get('Invoice', {})
    print("=" * 60)
    print("✅ FACTURA DEMO CREADA EXITOSAMENTE!")
    print("=" * 60)
    print(f"Número de factura: #{invoice.get('DocNumber')}")
    print(f"ID interno: {invoice.get('Id')}")
    print(f"Fecha: {invoice.get('TxnDate')}")
    print(f"Vencimiento: {invoice.get('DueDate')}")
    print(f"Cliente: {invoice.get('CustomerRef', {}).get('name')}")
    print("-" * 60)
    print("DETALLE:")
    for line in invoice.get('Line', []):
        if line.get('DetailType') == 'SalesItemLineDetail':
            print(f"  • {line.get('Description')}: ${line.get('Amount')}")
    print("-" * 60)
    print(f"TOTAL: ${invoice.get('TotalAmt')}")
    print("=" * 60)
else:
    print(f"❌ Error: {response.status_code}")
    print(response.text)