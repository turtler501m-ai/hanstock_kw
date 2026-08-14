import sqlite3
import json
import pandas as pd
from datetime import datetime, timedelta
from src.config import config
from src.utils.logger import logger

def verify_ai_performance():
    db_path = config.trade_db_path
    conn = sqlite3.connect(db_path)
    
    # decision_logs 와 trades 테이블 조인하여 AI 예측 성능 분석
    query = """
    SELECT 
        d.ts as decision_ts,
        d.symbol,
        d.name,
        d.action,
        d.price as predicted_price,
        d.ai_metadata,
        t.ts as execution_ts,
        t.filled_price,
        t.order_status
    FROM decision_logs d
    LEFT JOIN trades t ON d.symbol = t.symbol AND ABS(strftime('%s', t.ts) - strftime('%s', d.ts)) < 3600
    WHERE d.ai_metadata IS NOT NULL AND d.ai_metadata != '{}'
    ORDER BY d.ts DESC
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if df.empty:
        print("분석할 AI 결정 로그가 없습니다.")
        return

    print(f"\n=== AI 성능 검증 리포트 ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ===")
    print(f"총 분석 건수: {len(df)}건")
    
    # AI 메타데이터 파싱
    df['ml_score'] = df['ai_metadata'].apply(lambda x: json.loads(x).get('ml_score'))
    df['model_version'] = df['ai_metadata'].apply(lambda x: json.loads(x).get('model_version'))
    
    # 성공 여부 판단 (단순하게 매수 후 체결 여부 및 이후 가격 흐름 - 여기선 체결 여부 우선)
    executed = df[df['order_status'].isin(['filled', 'simulated', 'submitted'])]
    print(f"실행(체결) 건수: {len(executed)}건 (실행률: {len(executed)/len(df)*100:.1f}%)")
    
    # 신뢰도 구간별 통계
    if 'ml_score' in df.columns:
        df['confidence_group'] = pd.cut(df['ml_score'].fillna(0), bins=[0, 0.4, 0.6, 0.8, 1.0], labels=['Low', 'Mid', 'High', 'Very High'])
        summary = df.groupby('confidence_group', observed=False).size()
        print("\n[신뢰도 구간별 결정 분포]")
        print(summary)

    print("\n[최근 AI 결정 상세]")
    print(df[['decision_ts', 'symbol', 'name', 'ml_score', 'order_status']].head(10).to_string(index=False))

if __name__ == "__main__":
    verify_ai_performance()
