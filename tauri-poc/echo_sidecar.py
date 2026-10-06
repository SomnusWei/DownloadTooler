"""POC-3 echo sidecar

模拟 Dy page_bridge 行协议：
- stdin 读入一行
- 若以 ==DYC_BRIDGE_REQ== 开头，解析 JSON，回 ==DYC_BRIDGE_RES== + JSON
- exit 命令退出
"""
import sys
import json

BRIDGE_REQ = "==DYC_BRIDGE_REQ=="
BRIDGE_RES = "==DYC_BRIDGE_RES=="

print("[sidecar] 启动成功", flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    print(f"[sidecar] 收到: {line[:80]}", flush=True)

    if line.startswith(BRIDGE_REQ):
        payload = line[len(BRIDGE_REQ):].strip()
        try:
            req = json.loads(payload)
        except json.JSONDecodeError as e:
            err = {"id": None, "ok": False, "error": f"json parse: {e}"}
            print(f"{BRIDGE_RES} {json.dumps(err)}", flush=True)
            continue
        # 回复响应
        res = {
            "id": req.get("id"),
            "ok": True,
            "text": f"echo: {req.get('url', '')[:50]}",
        }
        print(f"{BRIDGE_RES} {json.dumps(res)}", flush=True)
    elif line == "exit":
        break
    else:
        print(f"[sidecar] 未知行: {line[:50]}", flush=True)

print("[sidecar] 退出", flush=True)
