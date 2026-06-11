"""Create a sample Bitable with real ZSXQ topics including images and files."""

import json, logging, os, sys, time
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bitable_sample")

APP_ID = os.environ["FEISHU_APP_ID"]
APP_SECRET = os.environ["FEISHU_APP_SECRET"]
MCP_API_KEY = os.environ["ZSXQ_MCP_API_KEY"]
GROUP_ID = os.environ["ZSXQ_GROUP_ID"]

BASE = "https://open.feishu.cn/open-apis"
MCP_URL = f"https://mcp.zsxq.com/topic/mcp?api_key={MCP_API_KEY}"


def get_token():
    r = requests.post(f"{BASE}/auth/v3/tenant_access_token/internal",
                      json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=15)
    r.raise_for_status()
    return r.json()["tenant_access_token"]


def feishu(method, path, body=None):
    h = {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json; charset=utf-8"}
    r = requests.request(method, f"{BASE}{path}", json=body, headers=h, timeout=60)
    d = r.json()
    if d.get("code", -1) != 0:
        logger.error("Feishu error: code=%s msg=%s", d.get("code"), d.get("msg"))
        raise Exception(f"[{d.get('code')}] {d.get('msg')}")
    return d.get("data", {})


def download_file(url, dest_path):
    try:
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        return os.path.getsize(dest_path) > 0
    except Exception:
        return False


def upload_media(file_path, filename, parent_type="bitable_image", parent_node=""):
    """Upload to Feishu Drive, return file_token."""
    token = get_token()
    size = os.path.getsize(file_path)
    url = f"{BASE}/drive/v1/medias/upload_all"
    data = {"file_name": filename, "size": str(size)}
    if parent_type:
        data["parent_type"] = parent_type
    if parent_node:
        data["parent_node"] = parent_node
    with open(file_path, "rb") as f:
        r = requests.post(url, headers={"Authorization": f"Bearer {token}"},
                          data=data, files={"file": (filename, f, "application/octet-stream")},
                          timeout=120)
    r.raise_for_status()
    d = r.json()
    if d.get("code", -1) != 0:
        logger.warning("Upload failed for %s: %s", filename, d.get("msg"))
        return None
    return d["data"]["file_token"]


def zsxq_rpc(method, params):
    payload = {"jsonrpc": "2.0", "id": int(time.time()*1000), "method": method, "params": params}
    r = requests.post(MCP_URL, json=payload,
                      headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"},
                      timeout=120)
    text = r.content.decode("utf-8")
    data_str = ""
    for block in text.split("\n\n"):
        for line in block.strip().split("\n"):
            if line.startswith("data: "):
                data_str += line[6:]
    if not data_str:
        raise Exception("Empty SSE response")
    result = json.loads(data_str)
    if "error" in result:
        raise Exception(result["error"].get("message","MCP error"))
    return result.get("result", {})


def fetch_topics(limit=5):
    zsxq_rpc("initialize", {"protocolVersion":"2024-11-05","capabilities":{},
                             "clientInfo":{"name":"bitable-sample","version":"1.0"}})
    zsxq_rpc("tools/list")

    result = zsxq_rpc("tools/call", {"name":"get_group_topics",
                       "arguments":{"group_id":GROUP_ID, "count":min(limit,30)}})
    content = result.get("content", [])
    brief = []
    for item in content:
        if item.get("type") == "text":
            try:
                d = json.loads(item["text"])
                if isinstance(d, dict):
                    brief.extend(d.get("topics_brief", d.get("topics", [])))
            except json.JSONDecodeError:
                pass

    # Enrich with details
    topics = []
    for bt in brief[:limit]:
        tid = bt.get("topic_id","")
        try:
            r = zsxq_rpc("tools/call", {"name":"get_topic_info","arguments":{"topic_id":tid}})
            for item in r.get("content",[]):
                if item.get("type")=="text":
                    d = json.loads(item["text"])
                    if isinstance(d, dict) and "topic" in d:
                        topics.append(d["topic"])
                        break
        except Exception:
            topics.append(bt)
        time.sleep(0.3)

    topics.sort(key=lambda t: t.get("create_time",""), reverse=True)
    return topics


def extract_images(topic):
    images = []
    def _collect(d, depth=0):
        if depth>3 or not isinstance(d,dict): return
        for key in ("images","image_list","pictures"):
            vals = d.get(key)
            if isinstance(vals, list): images.extend(vals)
        for sub in ("talk","question","answer"):
            if isinstance(d.get(sub),dict): _collect(d[sub], depth+1)
    _collect(topic)

    out = []
    for img in images:
        if not isinstance(img, dict): continue
        url = img.get("large_url") or img.get("original_url") or img.get("url","")
        name = img.get("name","") or url.split("/")[-1].split("?")[0] or "image.jpg"
        if url and url not in {i["url"] for i in out}:
            out.append({"url":url,"name":name})
    return out


def extract_files(topic):
    files = []
    def _collect(d, depth=0):
        if depth>3 or not isinstance(d,dict): return
        for key in ("files","file_list","attachments"):
            vals = d.get(key)
            if isinstance(vals, list): files.extend(vals)
        for sub in ("talk","question","answer"):
            if isinstance(d.get(sub),dict): _collect(d[sub], depth+1)
    _collect(topic)

    out = []
    for f in files:
        if not isinstance(f, dict): continue
        url = f.get("download_url") or f.get("url","")
        fid = f.get("file_id","")
        if not url and fid: url = f"zsxq://file/{fid}"
        name = f.get("name","") or f.get("file_name","") or url.split("/")[-1].split("?")[0]
        if url and url not in {x["url"] for x in out}:
            out.append({"url":url,"name":name,"file_id":fid})
    return out


def main():
    logger.info("=== 创建 Bitable 样板 ===")
    os.makedirs("temp", exist_ok=True)

    # 1. Create bitable
    r = feishu("POST", "/bitable/v1/apps", {"name": "知识星球同步 - 样板"})
    app_token = r["app"]["app_token"]
    logger.info("Bitable: %s", app_token)

    # 2. Create table
    r2 = feishu("POST", f"/bitable/v1/apps/{app_token}/tables", {
        "table": {
            "name": "帖子",
            "fields": [
                {"field_name": "时间", "type": 1},
                {"field_name": "日期", "type": 1},
                {"field_name": "正文", "type": 1},
                {"field_name": "图片", "type": 17},
                {"field_name": "文件", "type": 17},
                {"field_name": "topic_id", "type": 1},
            ],
        }
    })
    table_id = r2["table_id"]

    # 3. Fetch real topics
    logger.info("拉取 ZSXQ 帖子...")
    topics = fetch_topics(5)

    # 4. Build records with uploaded images/files
    records = []
    for t in topics:
        create_time = t.get("create_time","")
        date_str = create_time[:10] if "T" in create_time else create_time.split(" ")[0]
        time_str = create_time[11:16] if "T" in create_time else create_time.split(" ")[-1][:5]
        text = t.get("talk",{}).get("text","") or t.get("question",{}).get("text","") or ""
        if len(text) > 5000:
            text = text[:5000] + "\n...[截断]"

        # Upload images
        img_tokens = []
        for img in extract_images(t)[:10]:
            url = img["url"]
            name = img["name"]
            local = f"temp/{name}"
            if download_file(url, local):
                ft = upload_media(local, name, parent_type="bitable_image")
                os.remove(local)
                if ft:
                    img_tokens.append({"file_token": ft})
                    logger.info("  Image uploaded: %s -> %s", name, ft)
                time.sleep(0.5)

        # Upload files (download via ZSXQ MCP)
        file_tokens = []
        for f in extract_files(t)[:5]:
            name = f["name"]
            fid = f.get("file_id","")
            local = f"temp/{name}"

            # Try downloading via ZSXQ file API
            downloaded = False
            if fid:
                for args in [
                    {"method":"GET","path":f"/v2/files/{fid}/download_url"},
                    {"method":"GET","path":f"/v2/files/{fid}"},
                ]:
                    try:
                        r = zsxq_rpc("tools/call", {"name":"call_zsxq_api","arguments":args})
                        for item in r.get("content",[]):
                            if item.get("type")=="text":
                                d = json.loads(item["text"])
                                if isinstance(d, dict):
                                    body = d.get("body",{}) or {}
                                    if isinstance(body, str): body = {}
                                    rd = body.get("resp_data",{}) or {}
                                    if isinstance(rd, str): rd = {}
                                    dl = rd.get("download_url","") or d.get("download_url","")
                                    if dl:
                                        if download_file(dl, local):
                                            downloaded = True
                        if downloaded: break
                    except Exception:
                        continue

            if downloaded:
                ft = upload_media(local, name, parent_type="bitable_file")
                os.remove(local)
                if ft:
                    file_tokens.append({"file_token": ft})
                    logger.info("  File uploaded: %s -> %s", name, ft)
                time.sleep(0.5)

        records.append({"fields": {
            "时间": time_str,
            "日期": date_str,
            "正文": text or "(无文本)",
            "图片": img_tokens if img_tokens else None,
            "文件": file_tokens if file_tokens else None,
            "topic_id": t.get("topic_id",""),
        }})

    # 5. Insert records
    BATCH = 50
    for i in range(0, len(records), BATCH):
        batch = records[i:i+BATCH]
        feishu("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
               {"records": batch})
        logger.info("Inserted %d records", len(batch))

    # 6. Done
    url = f"https://larkcommunity.feishu.cn/base/{app_token}"
    print(f"\n{'='*60}")
    print(f"✅ Bitable 样板创建完成")
    print(f"   链接: {url}")
    print(f"   App Token: {app_token}")
    print(f"   Table ID:  {table_id}")
    print(f"   记录数: {len(records)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
