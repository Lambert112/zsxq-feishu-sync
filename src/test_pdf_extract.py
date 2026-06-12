"""Test PDF text extraction from a real ZSXQ file."""

import json, logging, os, sys, tempfile, time
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pdf_test")

MCP_URL = f"https://mcp.zsxq.com/topic/mcp?api_key={os.environ['ZSXQ_MCP_API_KEY']}"
GROUP_ID = os.environ["ZSXQ_GROUP_ID"]
_req_id = 0

def rpc(method, params=None):
    global _req_id; _req_id += 1
    r = requests.post(MCP_URL,
        json={"jsonrpc":"2.0","id":_req_id,"method":method,"params":params or {}},
        headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"},
        timeout=60)
    text = r.content.decode("utf-8")
    data = ""
    for block in text.split("\n\n"):
        for line in block.strip().split("\n"):
            if line.startswith("data: "): data += line[6:]
    return json.loads(data).get("result",{})

# Init
rpc("initialize", {"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}})
rpc("tools/list",{})

# Get latest topics
result = rpc("tools/call", {"name":"get_group_topics","arguments":{"group_id":GROUP_ID,"count":30}})
content = result.get("content",[])
brief = []
for item in content:
    if item.get("type")=="text":
        d = json.loads(item["text"])
        if isinstance(d,dict): brief.extend(d.get("topics_brief", d.get("topics",[])))

# Find topics with files
pdf_count = 0
for bt in brief:
    tid = bt.get("topic_id","")
    r2 = rpc("tools/call", {"name":"get_topic_info","arguments":{"topic_id":tid}})
    for item in r2.get("content",[]):
        if item.get("type")=="text":
            d = json.loads(item["text"])
            topic = d.get("topic", d)
            files_data = topic.get("files") or topic.get("file_list") or []
            if files_data:
                pdf_count += 1
                print(f"\nTopic {tid}: {len(files_data)} file(s)")
                for f in files_data[:1]:
                    fid = f.get("file_id","")
                    name = f.get("name","")
                    print(f"  File: {name} (id={fid})")

                    # Try download
                    for args in [
                        {"method":"GET","path":f"/v2/files/{fid}/download_url"},
                        {"method":"GET","path":f"/v2/files/{fid}"},
                    ]:
                        try:
                            r = rpc("tools/call", {"name":"call_zsxq_api","arguments":args})
                            for item2 in r.get("content",[]):
                                if item2.get("type")=="text":
                                    d2 = json.loads(item2["text"])
                                    if isinstance(d2,dict):
                                        body = d2.get("body",{}) or {}
                                        if isinstance(body,str): body={}
                                        rd = body.get("resp_data",{}) or {}
                                        if isinstance(rd,str): rd={}
                                        dl = rd.get("download_url","") or d2.get("download_url","")
                                        if dl:
                                            print(f"  Download URL: {dl[:100]}...")
                                            # Download
                                            resp = requests.get(dl, timeout=60)
                                            print(f"  Downloaded: {len(resp.content)} bytes")
                                            # Save
                                            path = f"/tmp/test_pdf.pdf"
                                            with open(path,"wb") as fh:
                                                fh.write(resp.content)
                                            print(f"  Saved to {path}")
                                        break
                        except Exception as e:
                            print(f"  Error: {e}")
                    break
        time.sleep(0.3)

    if pdf_count >= 2:
        break

print(f"\nTotal topics with files scanned: {pdf_count}")
