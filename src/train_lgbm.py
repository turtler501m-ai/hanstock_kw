import json
import os
import pickle
import sys
import sqlite3
from datetime import datetime
import pandas as pd
from pathlib import Path

# Add project root to sys.path to allow running as a script directly
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import config
from src.strategy.features import FEATURE_VERSION, MODEL_FEATURE_COLUMNS
from src.utils.logger import logger

def fetch_decision_logs():
    db_path = config.trade_db_path
    if not os.path.exists(db_path):
        logger.error("DB 파일을 찾을 수 없습니다.")
        return None
    
    conn = sqlite3.connect(db_path)
    # 실제 환경에서는 decision_logs 의 수익률을 추적하여 target(y)을 만들어야 합니다.
    df = pd.read_sql_query("SELECT * FROM decision_logs", conn)
    conn.close()
    return df

def _load_indicators(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}

def _build_training_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series] | None:
    if "label" in df.columns:
        label_column = "label"
    elif "future_return_20d" in df.columns:
        label_column = "future_return_20d"
    else:
        logger.warning("label 또는 future_return_20d 컬럼이 없어 학습할 수 없습니다.")
        return None

    rows = []
    for _, row in df.iterrows():
        indicators = _load_indicators(row.get("indicators"))
        merged = {**indicators}
        for column in MODEL_FEATURE_COLUMNS:
            if column in row and pd.notna(row[column]):
                merged[column] = row[column]
        rows.append({column: float(merged.get(column, 0.0) or 0.0) for column in MODEL_FEATURE_COLUMNS})

    x_train = pd.DataFrame(rows, columns=MODEL_FEATURE_COLUMNS)
    y_train = df[label_column].astype(float)
    return x_train, y_train

def train_model():
    df = fetch_decision_logs()
    if df is None or df.empty:
        logger.warning("학습할 데이터(decision_logs)가 부족합니다.")
        return

    frame = _build_training_frame(df)
    if frame is None:
        return
    x_train, y_train = frame

    try:
        import lightgbm as lgb
    except ImportError:
        logger.error("lightgbm이 설치되어 있지 않습니다. `pip install lightgbm` 후 다시 실행하세요.")
        return

    logger.info(f"{len(df)}건의 로그 데이터를 바탕으로 LightGBM 모델을 학습합니다...")
    if set(y_train.dropna().unique()).issubset({0.0, 1.0}):
        model = lgb.LGBMClassifier(random_state=42)
    else:
        model = lgb.LGBMRegressor(random_state=42)
    model.fit(x_train, y_train)

    models_dir = Path(".runtime") / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / f"{config.ai_model_version}.pkl"
    meta_path = models_dir / f"{config.ai_model_version}.json"
    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    meta_path.write_text(
        json.dumps(
            {
                "model_version": config.ai_model_version,
                "feature_version": FEATURE_VERSION,
                "trained_at": datetime.now().isoformat(),
                "train_rows": int(len(x_train)),
                "feature_columns": MODEL_FEATURE_COLUMNS,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(f"모델 학습 및 저장 완료 ({model_path})")

if __name__ == "__main__":
    train_model()
