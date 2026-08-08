import urllib.request
import json

url = 'http://localhost:8000/api/v1/vision/analyze'

# 1. Login
auth_req = urllib.request.Request(
    'http://localhost:8000/api/v1/auth/login',
    data=json.dumps({'username':'priya','password':'forge2026'}).encode(),
    headers={'Content-Type':'application/json'}
)
token = json.loads(urllib.request.urlopen(auth_req).read())['access_token']

# 2. Test tires-UC6.png
boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
body1 = (
    f'--{boundary}\r\n'
    'Content-Disposition: form-data; name="component_type"\r\n\r\n'
    'auto_detect\r\n'
    f'--{boundary}\r\n'
    'Content-Disposition: form-data; name="image"; filename="tires-UC6.png"\r\n'
    'Content-Type: image/png\r\n\r\n'
    '\x89PNG\r\n\x1a\nTEST_CONTINENTAL_UC6_TIRE_DATA_BYTES_PROFILE'
    f'\r\n--{boundary}--\r\n'
).encode('latin1')

req1 = urllib.request.Request(url, data=body1, headers={
    'Content-Type': f'multipart/form-data; boundary={boundary}',
    'Authorization': f'Bearer {token}'
})

res1 = urllib.request.urlopen(req1)
data1 = json.loads(res1.read())
print("=== TIRE UPLOAD RESULT (tires-UC6.png) ===")
print(json.dumps(data1, indent=2))

# 3. Test brake_disc.png
body2 = (
    f'--{boundary}\r\n'
    'Content-Disposition: form-data; name="component_type"\r\n\r\n'
    'auto_detect\r\n'
    f'--{boundary}\r\n'
    'Content-Disposition: form-data; name="image"; filename="brake_disc.png"\r\n'
    'Content-Type: image/png\r\n\r\n'
    '\x89PNG\r\n\x1a\nTEST_BRAKE_DISC_ROTOR_DATA_BYTES'
    f'\r\n--{boundary}--\r\n'
).encode('latin1')

req2 = urllib.request.Request(url, data=body2, headers={
    'Content-Type': f'multipart/form-data; boundary={boundary}',
    'Authorization': f'Bearer {token}'
})

res2 = urllib.request.urlopen(req2)
data2 = json.loads(res2.read())
print("\n=== BRAKE DISC UPLOAD RESULT (brake_disc.png) ===")
print(json.dumps(data2, indent=2))
