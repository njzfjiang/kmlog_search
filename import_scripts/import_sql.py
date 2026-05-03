import sqlite3
import json
import os

# 1. 连接/创建一个叫 chat_search.db 的文件
conn = sqlite3.connect('chat_search.db')
cursor = conn.cursor()

# 清理旧数据（如果重复运行）
cursor.execute("DROP TABLE IF EXISTS messages_fts")
cursor.execute("DROP TABLE IF EXISTS messages")

# 2. 建表（存放所有消息）
cursor.execute('''
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        role TEXT,
        content TEXT,
        conversation_title TEXT,
        conversation_id TEXT,
        message_id TEXT UNIQUE,
        kind TEXT DEFAULT 'chat'
    )
''')

# 3. 【核心】建立全文检索索引 (FTS5)。它会拷贝一份 content 列，专门用于超高速搜索。
cursor.execute('''
    CREATE VIRTUAL TABLE messages_fts USING fts5(
        content,  -- 只对聊天内容建索引
        content=messages,  -- 告诉它，数据源头是 messages 表
        content_rowid=id
    )
''')

# 4. 读数据，把清洗后的消息批量插入（更快）
if not os.path.exists('cleaned_chats.jsonl'):
    print("错误：cleaned_chats.jsonl 文件不存在！请先运行 clean.py")
    exit(1)

messages_to_insert = []
with open('cleaned_chats.jsonl', 'r', encoding='utf-8') as f:
    for line in f:
        msg = json.loads(line)
        messages_to_insert.append((
            msg['timestamp'], 
            msg['role'], 
            msg['content'], 
            msg['conversation_title'], 
            msg['conversation_id'], 
            msg['message_id'],
            msg.get('kind') or 'chat'
        ))

# 批量插入（大幅提速）
cursor.executemany('''
    INSERT INTO messages (timestamp, role, content, conversation_title, conversation_id, message_id, kind)
    VALUES (?, ?, ?, ?, ?, ?, ?)
''', messages_to_insert)

# 5. 把新数据同步到FTS索引里
cursor.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")

conn.commit()
print(f"✅ 数据库构建完成！")
print(f"   📊 共导入 {len(messages_to_insert)} 条消息到 chat_search.db")
print(f"   🔍 已建立全文搜索索引（FTS5）")

conn.close()
