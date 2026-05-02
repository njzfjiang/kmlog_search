import json

# 读取文件
with open('chats.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

all_messages = []
messages_by_id = {msg['id']: msg for msg in data.get('messages', [])}

# 遍历所有对话窗口
for conv in data.get('conversations', []):
    conv_title = conv.get('title', 'Untitled')
    conv_id = conv.get('id', 'unknown')
    
    # 通过messageIds查找该对话的消息
    for msg_id in conv.get('messageIds', []):
        msg = messages_by_id.get(msg_id)
        if not msg or not msg.get('content') or msg.get('role') not in ['user', 'assistant']:
            continue
        
        clean_row = {
            'timestamp': msg.get('timestamp', ''),
            'role': msg.get('role'),
            'content': msg.get('content').replace('\n', ' ').replace('  ', ' '),
            'conversation_title': conv_title,
            'conversation_id': conv_id,
            'message_id': msg.get('id')
        }
        all_messages.append(clean_row)

# 保存为jsonl
with open('cleaned_chats.jsonl', 'w', encoding='utf-8') as f:
    for msg in all_messages:
        f.write(json.dumps(msg, ensure_ascii=False) + '\n')

print(f"处理完成！共提取 {len(all_messages)} 条有效消息。")