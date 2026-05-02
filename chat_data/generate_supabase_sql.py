import json
import os

# 模拟PostgreSQL连接和导入逻辑（用于本地测试）
# 不依赖实际网络连接

def simulate_import_to_supabase(jsonl_file='cleaned_chats.jsonl'):
    """
    模拟导入逻辑，生成迁移SQL脚本
    """
    if not os.path.exists(jsonl_file):
        print("❌ 错误：cleaned_chats.jsonl 文件不存在！")
        exit(1)
    
    # 生成SQL脚本
    sql_commands = []
    
    # 建表
    sql_commands.append('''
-- Supabase PostgreSQL 建表脚本
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    timestamp TEXT,
    role TEXT,
    content TEXT,
    conversation_title TEXT,
    conversation_id TEXT,
    message_id TEXT UNIQUE
);

-- 添加全文搜索列
ALTER TABLE messages 
ADD COLUMN content_ts tsvector;

-- 创建自动更新函数
CREATE OR REPLACE FUNCTION update_content_ts()
RETURNS TRIGGER AS $$
BEGIN
    NEW.content_ts := to_tsvector('chinese', COALESCE(NEW.content, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 创建触发器
CREATE TRIGGER content_ts_trigger BEFORE INSERT OR UPDATE ON messages
FOR EACH ROW EXECUTE FUNCTION update_content_ts();

-- 创建GiST索引
CREATE INDEX messages_content_ts_idx ON messages USING GiST(content_ts);
''')
    
    # 读取JSONL数据
    messages_count = 0
    insert_batch = []
    
    with open(jsonl_file, 'r', encoding='utf-8') as f:
        for line in f:
            msg = json.loads(line)
            timestamp = msg['timestamp'].replace("'", "''")
            role = msg['role'].replace("'", "''")
            content = msg['content'].replace("'", "''")
            conv_title = msg['conversation_title'].replace("'", "''")
            conv_id = msg['conversation_id'].replace("'", "''")
            msg_id = msg['message_id'].replace("'", "''")
            
            insert_batch.append(f"('{timestamp}', '{role}', '{content}', '{conv_title}', '{conv_id}', '{msg_id}')")
            messages_count += 1
    
    # 生成批量INSERT语句（每1000条一个）
    batch_size = 1000
    for i in range(0, len(insert_batch), batch_size):
        batch = insert_batch[i:i+batch_size]
        insert_sql = f'''
INSERT INTO messages (timestamp, role, content, conversation_title, conversation_id, message_id)
VALUES {', '.join(batch)};
'''
        sql_commands.append(insert_sql)
    
    # 输出统计
    output_file = 'import_to_supabase.sql'
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(sql_commands))
    
    print(f"✅ 迁移脚本已生成！")
    print(f"   📊 共处理 {messages_count} 条消息")
    print(f"   📄 SQL脚本保存到: {output_file}")
    print(f"\n📝 使用方法：")
    print(f"   1. 登录 Supabase → SQL Editor")
    print(f"   2. 创建新query")
    print(f"   3. 复制 {output_file} 的内容粘贴进去")
    print(f"   4. 点击 'Run'")
    
    return output_file, messages_count

if __name__ == "__main__":
    simulate_import_to_supabase()
