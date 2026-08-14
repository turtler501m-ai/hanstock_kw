import time
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api.kis_api import KIStockAPI
from src.config import config


def mask_account(account: str) -> str:
    if len(account) < 6:
        return "*" * len(account)
    return f"{account[:4]}****{account[-2:]}"


def main() -> None:
    account = config.kistock_account.strip()
    cano = account[:8]
    product_code = account[8:] if len(account) > 8 else "01"
    base_url = (
        "https://openapi.koreainvestment.com:9443"
        if config.trading_env == "real"
        else "https://openapivts.koreainvestment.com:29443"
    )

    print(f"env={config.trading_env}")
    print(f"base_url={base_url}")
    print(f"account={mask_account(account)} len={len(account)} digits={account.isdigit()}")
    print(f"CANO={mask_account(cano)} ACNT_PRDT_CD={product_code}")
    print(f"app_key_len={len(config.kistock_app_key)} app_secret_len={len(config.kistock_app_secret)}")

    start = time.monotonic()
    try:
        api = KIStockAPI(notify_errors=False)
        print(f"token=ok elapsed={time.monotonic() - start:.2f}s")

        balance_start = time.monotonic()
        data = api.get_balance()
        print(
            "balance=ok "
            f"elapsed={time.monotonic() - balance_start:.2f}s "
            f"rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}"
        )
        print(f"holdings={len(data.get('output1', []))} summary={len(data.get('output2', []))}")
    except Exception as exc:
        print(f"balance=error type={type(exc).__name__} elapsed={time.monotonic() - start:.2f}s msg={exc}")


if __name__ == "__main__":
    main()
