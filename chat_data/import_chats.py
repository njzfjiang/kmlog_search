import json
from datetime import datetime, timezone

INPUT_FILE = "conversations.json"   # 改这里
OUTPUT_FILE = "chatgpt_incremental.jsonl"
CUTOFF_TIMESTAMP = datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp()

def extract_messages(mapping):
    """递归遍历mapping树，返回所有叶子消息"""
    results = []
    for node_id, node in mapping.items():
        message = node.get("message")
        if not message:
            continue
        author = message.get("author", {})
        role = author.get("role")
        if role not in ("user", "assistant"):
            continue
        
        content = message.get("content", {})
        parts = content.get("parts", [])
        text = " ".join(str(p) for p in parts if isinstance(p, str)).strip()
        if not text:
            continue
        
        create_time = message.get("create_time")
        if create_time is None:
            continue
        
        results.append({
            "timestamp": datetime.fromtimestamp(create_time, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "role": role,
            "content": text,
            "message_id": message.get("id"),
            "conversation_id": node.get("id")  # 临时占位，后面会补上真正的 conversation_id
        })
    return results

def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    all_messages = []
    conversations = data if isinstance(data, list) else data.get("conversations", [])
    
    for conv in conversations:
        mapping = conv.get("mapping", {})
        conv_id = conv.get("conversation_id") or conv.get("id", "unknown")
        conv_title = conv.get("title", "Untitled")
        
        msgs = extract_messages(mapping)
        for msg in msgs:
            msg["conversation_id"] = conv_id
            msg["conversation_title"] = conv_title
            # 过滤 2026-02-01 之前的
            ts = datetime.strptime(msg["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            if ts >= CUTOFF_TIMESTAMP:
                all_messages.append(msg)
    
    # 按时间排序
    all_messages.sort(key=lambda x: x["timestamp"])
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for msg in all_messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    
    print(f"完成！共导出 {len(all_messages)} 条消息（2026-02-01 之后）")

if __name__ == "__main__":
    main()