"""Create a sample Bitable to demo the multi-dimensional table format."""

import json, logging, os, sys, time
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bitable_sample")

APP_ID = os.environ["FEISHU_APP_ID"]
APP_SECRET = os.environ["FEISHU_APP_SECRET"]
FOLDER_TOKEN = os.environ["FEISHU_FOLDER_TOKEN"]
BASE = "https://open.feishu.cn/open-apis"


def get_token():
    r = requests.post(f"{BASE}/auth/v3/tenant_access_token/internal",
                      json={"app_id": APP_ID, "app_secret": APP_SECRET}, timeout=15)
    r.raise_for_status()
    return r.json()["tenant_access_token"]


def feishu(method, path, body=None):
    h = {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json; charset=utf-8"}
    r = requests.request(method, f"{BASE}{path}", json=body, headers=h, timeout=30)
    d = r.json()
    if d.get("code", -1) != 0:
        logger.error("Feishu error: code=%s msg=%s", d.get("code"), d.get("msg"))
        raise Exception(f"[{d.get('code')}] {d.get('msg')}")
    return d.get("data", {})


def main():
    logger.info("=== 创建 Bitable 样板 ===")

    # 1. Create bitable app (no folder_token to avoid perm issues)
    r = feishu("POST", "/bitable/v1/apps", {
        "name": "知识星球同步 - 样板",
    })
    app_token = r["app"]["app_token"]
    logger.info("Bitable app: %s", app_token)

    # 2. Create table with fields
    r2 = feishu("POST", f"/bitable/v1/apps/{app_token}/tables", {
        "table": {
            "name": "帖子",
            "fields": [
                {"field_name": "时间", "type": 1},
                {"field_name": "日期", "type": 1},
                {"field_name": "正文", "type": 1},
                {"field_name": "topic_id", "type": 1},
            ],
        }
    })
    table_id = r2["table"]["table_id"]

    # 3. Add sample records
    samples = [
        {"时间": "09:42", "日期": "2026-06-12", "正文": "神经吧\n其他票都涨很好\n能科kj居然给我资金砸跌停了\n谁在看着我的账户炒股？\n\n我要换一个账号了\n这个股是我昨天刚加的", "topic_id": "demo-001"},
        {"时间": "23:27", "日期": "2026-06-11", "正文": "做一个简单的估值推理\n为什么我说电子特气就炒好短波段\n不大适合追高或者长拿呢\n因为估值不低了\n\n拿中船特气我来举个例子...", "topic_id": "demo-002"},
        {"时间": "23:03", "日期": "2026-06-11", "正文": "美股今日看似有反弹\n其实也就是一些大科技股\n纳指是跌的\n\n和我A一模一样\n就是极端的分化行情", "topic_id": "demo-003"},
        {"时间": "22:03", "日期": "2026-06-10", "正文": "关于姐妹兄弟的事儿\n我觉得没啥哈\n球友也是有自己的思考\n我觉得有时候是要听不同声音的\n\n说点实在的\n明日A如果起来了\n还是要注意下风险", "topic_id": "demo-004"},
        {"时间": "14:31", "日期": "2026-06-10", "正文": "先总结一句：\n稀有气体、钨、氟、磷仅适合周期波段博弈，不具备长期稳定高毛利、强定价权、稀缺壁垒的核心优势。\n\n再学习\n脑子里的东西不怕多", "topic_id": "demo-005"},
    ]

    r3 = feishu("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create", {
        "records": [{"fields": s} for s in samples],
    })

    url = f"https://larkcommunity.feishu.cn/base/{app_token}"
    print(f"\n{'='*60}")
    print(f"✅ Bitable 样板创建完成")
    print(f"   链接: {url}")
    print(f"   App Token: {app_token}")
    print(f"   Table ID:  {table_id}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
