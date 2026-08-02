"""
investMITRA — Neon Database Setup
Run from project root: python scripts/setup_neon.py
"""

import psycopg2
import os

NEON_URL = "postgresql://neondb_owner:npg_3w8mepDlnJrE@ep-holy-flower-azlby8l7.c-3.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"

def main():
    print("Connecting to Neon...")
    conn = psycopg2.connect(NEON_URL, connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()
    print("Connected.")

    # Drop old schema if exists
    print("Dropping old schema...")
    cur.execute("DROP SCHEMA IF EXISTS investmitra CASCADE;")
    print("Done.")

    # Run full schema
    print("Running init_neon.sql...")
    sql_path = os.path.join(os.path.dirname(__file__), '..', 'infra', 'sql', 'init_neon.sql')
    with open(sql_path, encoding='utf-8') as f:
        sql = f.read()
    cur.execute(sql)
    print("Done.")

    # Verify
    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'investmitra'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    """)
    rows = cur.fetchall()
    print(f"\nTables created: {len(rows)}")
    for r in rows:
        print(f"  - {r[0]}")

    # Verify source_registry seeded
    cur.execute("SELECT COUNT(*) FROM investmitra.source_registry;")
    count = cur.fetchone()[0]
    print(f"\nSources seeded: {count}")

    conn.close()
    print("\nNeon setup complete.")

if __name__ == "__main__":
    main()
