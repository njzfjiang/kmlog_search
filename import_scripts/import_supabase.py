import psycopg2
from psycopg2.extras import execute_values
import json
import os
import sys
from dotenv import load_dotenv
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
JSONL_FILE = PROJECT_ROOT / 'chat_data' / 'chatgpt_incremental_deduped.jsonl'
TEXT_SEARCH_CONFIG = 'english'
BATCH_SIZE = 1000
SEARCH_VECTOR_BATCH_SIZE = 500
REQUIRED_FIELDS = {
    'timestamp',
    'role',
    'content',
    'conversation_title',
    'conversation_id',
    'message_id',
}


def mask_database_url(database_url):
    try:
        parsed = urlsplit(database_url)
        host = parsed.hostname or ''
        port = f":{parsed.port}" if parsed.port else ''
        username = parsed.username or ''
        userinfo = f"{username}:***@" if username else "***@"
        return urlunsplit((parsed.scheme, f"{userinfo}{host}{port}", parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return "<无法解析 connection string，已隐藏>"

# 从 .env 文件加载Supabase连接信息
# 格式：postgres://user:password@pooler.supabase.com:5432/postgres
load_dotenv(PROJECT_ROOT / '.env')
load_dotenv(PROJECT_ROOT / 'servers' / '.env')
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    print("❌ 错误：DATABASE_URL 环境变量未设置")
    print("   请在 .env 文件中填入：")
    print("   DATABASE_URL=postgres://user:password@aws-region.pooler.supabase.com:5432/postgres")
    sys.exit(1)

# 先确认本地数据可读，再动远程数据库。
if not JSONL_FILE.exists():
    print(f"❌ 错误：{JSONL_FILE} 文件不存在！请先运行 chat_data/dedupe_jsonl.py")
    sys.exit(1)

messages_to_insert = []
try:
    with JSONL_FILE.open('r', encoding='utf-8') as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue

            msg = json.loads(line)
            missing_fields = REQUIRED_FIELDS - msg.keys()
            if missing_fields:
                missing = ', '.join(sorted(missing_fields))
                raise ValueError(f"第 {line_number} 行缺少字段: {missing}")

            messages_to_insert.append((
                msg['timestamp'],
                msg['role'],
                msg['content'],
                msg['conversation_title'],
                msg['conversation_id'],
                msg['message_id']
            ))
except (json.JSONDecodeError, ValueError) as e:
    print(f"❌ 读取 {JSONL_FILE} 失败: {e}")
    sys.exit(1)

if not messages_to_insert:
    print(f"❌ 错误：{JSONL_FILE} 没有可导入的消息")
    sys.exit(1)

print(f"🔗 正在连接到 Supabase...")
print(f"   Connection String (隐藏密码): {mask_database_url(DATABASE_URL)}")

# 连接Supabase PostgreSQL（带重试）
max_retries = 3
connection_params = {
    'connect_timeout': 10,
    'keepalives': 1,
    'keepalives_idle': 30,
    'keepalives_interval': 10,
    'keepalives_count': 5,
    'sslmode': 'require'  # Supabase需要SSL
}

for attempt in range(max_retries):
    try:
        print(f"🔄 尝试连接 (第 {attempt+1}/{max_retries} 次)...")
        conn = psycopg2.connect(DATABASE_URL, **connection_params)
        cursor = conn.cursor()
        
        # 测试连接
        cursor.execute("SELECT 1")
        print("✅ 已连接到Supabase")
        break
    except psycopg2.OperationalError as e:
        error_msg = str(e)
        print(f"⚠️  连接失败: {error_msg[:100]}")
        
        if attempt < max_retries - 1:
            print(f"   等待3秒后重试...")
            time.sleep(3)
        else:
            print(f"\n❌ 最终连接失败")
            print(f"\n💡 排查步骤：")
            if "could not translate host name" in error_msg:
                print(f"   ❌ DNS解析失败")
                print(f"      - 检查网络连接")
                print(f"      - 尝试: ping 8.8.8.8")
            elif "password authentication failed" in error_msg or "Tenant or user not found" in error_msg:
                print(f"   ❌ 认证失败")
                print(f"      - 检查connection string中的用户名和密码")
                print(f"      - pooler连接格式: postgres://postgres.PROJECT_ID:PASSWORD@aws-region.pooler.supabase.com:5432/postgres")
            elif "SSL" in error_msg or "certificate" in error_msg:
                print(f"   ❌ SSL证书问题")
                print(f"      - 尝试移除 sslmode=require 参数")
            print(f"\n   当前connection string:")
            print(f"   {mask_database_url(DATABASE_URL)}")
            sys.exit(1)
    except Exception as e:
        print(f"❌ 连接错误: {type(e).__name__}: {e}")
        sys.exit(1)

try:
    print("📝 确保 messages 表存在（不会删除已有数据）...")
    cursor.execute(f'''
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            timestamp TEXT,
            role TEXT,
            content TEXT,
            conversation_title TEXT,
            conversation_id TEXT,
            message_id TEXT UNIQUE,
            content_ts tsvector
        )
    ''')

    cursor.execute('''
        ALTER TABLE messages
        ADD COLUMN IF NOT EXISTS content_ts tsvector
    ''')

    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS messages_message_id_key
        ON messages (message_id)
    ''')

    conn.commit()
    print("✅ 表结构已确认")

    print(f"🚀 追加导入 {len(messages_to_insert)} 条消息（重复 message_id 会跳过）...")
    processed_count = 0
    inserted_count = 0
    for start in range(0, len(messages_to_insert), BATCH_SIZE):
        batch = messages_to_insert[start:start + BATCH_SIZE]
        execute_values(cursor, '''
            INSERT INTO messages (timestamp, role, content, conversation_title, conversation_id, message_id)
            VALUES %s
            ON CONFLICT (message_id) DO NOTHING
            RETURNING id
        ''', batch, page_size=BATCH_SIZE)
        inserted_count += len(cursor.fetchall())
        conn.commit()

        processed_count += len(batch)
        print(f"   已处理 {processed_count}/{len(messages_to_insert)}，本次实际新增累计 {inserted_count}")

    print("🔍 分批生成缺失的全文搜索向量...")
    updated_vectors = 0
    while True:
        cursor.execute(f'''
            WITH rows_to_update AS (
                SELECT id
                FROM messages
                WHERE content_ts IS NULL
                ORDER BY id
                LIMIT {SEARCH_VECTOR_BATCH_SIZE}
            )
            UPDATE messages
            SET content_ts = to_tsvector('{TEXT_SEARCH_CONFIG}', COALESCE(content, ''))
            FROM rows_to_update
            WHERE messages.id = rows_to_update.id
        ''')
        batch_updated = cursor.rowcount
        conn.commit()

        if batch_updated == 0:
            break

        updated_vectors += batch_updated
        print(f"   已更新搜索向量 {updated_vectors} 条")

    print("🔍 创建全文搜索触发器和索引...")
    cursor.execute(f'''
        CREATE OR REPLACE FUNCTION update_content_ts()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.content_ts := to_tsvector('{TEXT_SEARCH_CONFIG}', COALESCE(NEW.content, ''));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    ''')

    cursor.execute(f'''
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_trigger
                WHERE tgname = 'content_ts_trigger'
                  AND tgrelid = 'messages'::regclass
            ) THEN
                CREATE TRIGGER content_ts_trigger BEFORE INSERT OR UPDATE ON messages
                FOR EACH ROW EXECUTE FUNCTION update_content_ts();
            END IF;
        END
        $$;
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS messages_content_ts_idx ON messages USING GIN(content_ts)
    ''')

    conn.commit()
    cursor.execute("SELECT COUNT(*) FROM messages")
    total_count = cursor.fetchone()[0]

    print(f"✅ 追加导入完成！")
    print(f"   📊 本次读取 {len(messages_to_insert)} 条消息")
    print(f"   ➕ 本次新增 {inserted_count} 条消息")
    print(f"   🗃️  messages 当前总数 {total_count} 条")
    print(f"   🔍 已建立全文搜索索引（tsvector + GIN）")
    print(f"   ⚙️  全文搜索配置: {TEXT_SEARCH_CONFIG}")
except Exception as e:
    if 'conn' in locals() and conn and not conn.closed:
        conn.rollback()
    print(f"❌ 导入失败，已回滚当前未提交步骤: {type(e).__name__}: {e}")
    print("   已提交的批次可能仍在库里；重新运行脚本会跳过已存在的 message_id。")
    sys.exit(1)
finally:
    if 'conn' in locals() and conn and not conn.closed:
        conn.close()
