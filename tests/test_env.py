import os
from dotenv import load_dotenv

load_dotenv()

client_id = os.getenv('QBO_CLIENT_ID')
client_secret = os.getenv('QBO_CLIENT_SECRET')

print(f"CLIENT_ID: {client_id}")
print(f"CLIENT_SECRET: {client_secret}")
print(f"Longitud CLIENT_ID: {len(client_id) if client_id else 'None'}")
print(f"Longitud CLIENT_SECRET: {len(client_secret) if client_secret else 'None'}")