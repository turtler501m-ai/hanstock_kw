#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/../.."

.venv/bin/python << 'PYEOF'
import sqlite3

conn = sqlite3.connect('.runtime/signals.db')
cursor = conn.cursor()

print('='*60)
print("📊 시그널 모니터링 (채널별)")
print('='*60)

for ch, label in [('goldmoon', 'GoldMoon'), ('chart_leader', '차트리더'), ('jurin_6', '주린스쿨')]:
    print()
    print('='*40)
    print(f'  📢 {label}')
    print('='*40)

    # 진입 신호
    cursor.execute("""
        SELECT symbol, message_date FROM signals 
        WHERE channel_key = ? AND direction IN ('long','short')
        ORDER BY message_date DESC LIMIT 3
    """, (ch,))
    entries = cursor.fetchall()

    # 청산 신호
    cursor.execute("""
        SELECT symbol, message_date FROM signals 
        WHERE channel_key = ? AND direction = 'exit'
        ORDER BY message_date DESC LIMIT 3
    """, (ch,))
    exits = cursor.fetchall()

    if entries:
        print('  📗 진입 시그널')
        for r in entries:
            print(f'    • {r[0]} | {r[1][11:19]}')
    else:
        print('  📗 진입: 없음')

    if exits:
        print('  📙 청산 시그널')
        for r in exits:
            print(f'    • {r[0]} | {r[1][11:19]}')
    else:
        print('  📙 청산: 없음')

conn.close()
PYEOF
