import requests
import json

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

# Crear factura para "Amy's Bird Sanctuary" (Customer ID: 1)
factura = {
    "Line": [
        {
            "Amount": 150.00,
            "DetailType": "SalesItemLineDetail",
            "SalesItemLineDetail": {
                "ItemRef": {
                    "value": "1",
                    "name": "Services"
                }
            },
            "Description": "Servicio de prueba - Factura automática"
        }
    ],
    "CustomerRef": {
        "value": "1"
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
    print("=" * 50)
    print("✅ FACTURA CREADA EXITOSAMENTE!")
    print("=" * 50)
    print(f"Número de factura: {invoice.get('DocNumber')}")
    print(f"ID: {invoice.get('Id')}")
    print(f"Total: ${invoice.get('TotalAmt')}")
    print(f"Cliente: {invoice.get('CustomerRef', {}).get('name')}")
    print("=" * 50)
else:
    print(f"❌ Error: {response.status_code}")
    print(response.text)