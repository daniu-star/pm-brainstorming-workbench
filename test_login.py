import json, urllib.request, urllib.error, time

API = "https://123chenjunliang-pm-brainstorm-workbench.hf.space"
phone = "13800138001"

print("=== Step 1: Send SMS code ===")
req = urllib.request.Request(
    f"{API}/api/auth/sms/send",
    data=json.dumps({"phone": phone}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
        print(f"  Response: {json.dumps(data, ensure_ascii=False)}")
        code = data.get("code", "")
        hint = data.get("hint", "")
        print(f"  Code: {code}")
        print(f"  Hint: {hint}")
except urllib.error.HTTPError as e:
    print(f"  HTTP Error: {e.code}")
    print(f"  Body: {e.read().decode()}")
    exit(1)

if not code:
    print("  No code returned, SMS was sent successfully")
    exit(0)

print()
print("=== Step 2: Verify SMS code ===")
time.sleep(1)
req2 = urllib.request.Request(
    f"{API}/api/auth/sms/verify",
    data=json.dumps({"phone": phone, "code": code}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST"
)
try:
    with urllib.request.urlopen(req2, timeout=30) as resp:
        data2 = json.loads(resp.read())
        token = data2.get("token", "")
        print(f"  Token: {token[:30]}...")
        user = data2.get("user", {})
        print(f"  User: {json.dumps(user, ensure_ascii=False)}")
        print()
        print("LOGIN FLOW: SUCCESS!")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"  HTTP Error: {e.code}")
    print(f"  Body: {body}")
    print()
    print("LOGIN FLOW: FAILED!")

print()
print("=== Step 3: Test with wrong code ===")
req3 = urllib.request.Request(
    f"{API}/api/auth/sms/send",
    data=json.dumps({"phone": "13900139002"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST"
)
try:
    with urllib.request.urlopen(req3, timeout=30) as resp:
        data3 = json.loads(resp.read())
        code3 = data3.get("code", "")
except:
    pass

time.sleep(1)
req4 = urllib.request.Request(
    f"{API}/api/auth/sms/verify",
    data=json.dumps({"phone": "13900139002", "code": "000000"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST"
)
try:
    with urllib.request.urlopen(req4, timeout=30) as resp:
        print("  Wrong code accepted - BUG!")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"  Wrong code rejected (expected): {body}")
