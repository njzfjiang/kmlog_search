import psycopg2
import json
import os
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    print("❌ DATABASE_URL未设置")
    exit(1)

try:
    conn = psycopg2.connect(DATABASE_URL, sslmode='require', connect_timeout=10)
    cursor = conn.cursor()
    print("✅ 已连接到Supabase")
    
    # 简单版本：只创建基础表，不用触发器（避免'chinese'配置问题）
    print("🗑️  清理旧表...")
    cursor.execute("DROP TABLE IF EXISTS messages CASCADE")
    conn.commit()
    
    print("📝 创建新表...")
    cursor.execute('''
        CREATE TABLE messages (
            id SERIAL PRIMARY KEY,
            timestamp TEXT,
            role TEXT,
            content TEXT,
            conversation_title TEXT,
            conversation_id TEXT,
            message_id TEXT UNIQUE
        )
    ''')
    
    # 读取数据
    print("📂 读取 cleaned_chats.jsonl...")
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
                msg['message_id']
            ))
    
    print(f"🚀 插入 {len(messages_to_insert)} 条消息...")
    cursor.executemany('''
        INSERT INTO messages (timestamp, role, content, conversation_title, conversation_id, message_id)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', messages_to_insert)
    
    conn.commit()
    print("✅ 数据导入完成！")
    
    # 创建简单的全文搜索索引（用English配置）
    print("🔍 创建全文搜索索引...")
    cursor.execute('''
        ALTER TABLE messages 
        ADD COLUMN IF NOT EXISTS content_ts tsvector
    ''')
    
    cursor.execute('''
        UPDATE messages 
        SET content_ts = to_tsvector('english', COALESCE(content, ''))
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS messages_content_ts_idx ON messages USING GiST(content_ts)
    ''')
    
    conn.commit()
    print("✅ 索引创建完成！")
    
    # 验证
    cursor.execute("SELECT COUNT(*) FROM messages")
    count = cursor.fetchone()[0]
    print(f"\n📊 最终统计：")
    print(f"   ✅ 共导入 {count} 条消息")
    print(f"   ✅ 已建立全文搜索索引（English配置）")
    
    conn.close()
    
except Exception as e:
    print(f"❌ 错误: {type(e).__name__}: {e}")
    exit(1)
