def calc_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)

def calc_sma(prices: list[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0
    return sum(prices[-period:]) / period

def calc_ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    ema = [values[0]]
    for value in values[1:]:
        ema.append((value * alpha) + (ema[-1] * (1 - alpha)))
    return ema

def calc_macd(prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    if len(prices) < slow + signal:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0, "bull_cross": False, "bear_cross": False}
    fast_ema = calc_ema_series(prices, fast)
    slow_ema = calc_ema_series(prices, slow)
    macd_line = [fast_ema[i] - slow_ema[i] for i in range(len(prices))]
    signal_line = calc_ema_series(macd_line, signal)
    macd_now, macd_prev = macd_line[-1], macd_line[-2]
    sig_now, sig_prev = signal_line[-1], signal_line[-2]
    return {
        "macd": round(macd_now, 4),
        "signal": round(sig_now, 4),
        "hist": round(macd_now - sig_now, 4),
        "bull_cross": macd_prev <= sig_prev and macd_now > sig_now,
        "bear_cross": macd_prev >= sig_prev and macd_now < sig_now,
    }

def calc_bollinger(prices: list[float], period: int = 20) -> tuple:
    if len(prices) < period:
        price = prices[-1] if prices else 0
        return price, price, price
    window = prices[-period:]
    mid = sum(window) / period
    std = (sum((x - mid) ** 2 for x in window) / period) ** 0.5
    return round(mid - 2 * std), round(mid), round(mid + 2 * std)


def calc_rsi_series(prices: list[float], period: int = 14) -> list[float]:
    """prices 리스트의 각 봉에 대한 RSI 값 목록을 반환한다.
    반환 길이 = len(prices) - period
    """
    if len(prices) <= period:
        return []
    result = []
    for i in range(period, len(prices)):
        result.append(calc_rsi(prices[:i + 1], period))
    return result


def calc_rsi_divergence(prices: list[float], period: int = 40) -> dict:
    """RSI 하락 다이버전스 감지 (로스캐머런 두 번째 매매법).

    최근 period봉을 전반부/후반부로 나눠서:
    - 가격 고점: 후반부 > 전반부 (더 높음)
    - RSI 고점: 후반부 < 전반부 (더 낮음)
    두 조건이 모두 성립하면 bearish=True.

    반환:
        {
            "bearish": bool,
            "price_high1": float,  # 전반부 가격 고점
            "price_high2": float,  # 후반부 가격 고점
            "rsi_high1": float,    # 전반부 RSI 고점
            "rsi_high2": float,    # 후반부 RSI 고점
        }
    """
    needed = period + 14  # RSI 계산에 최소 14봉 워밍업 필요
    empty = {"bearish": False, "price_high1": 0.0, "price_high2": 0.0,
             "rsi_high1": 0.0, "rsi_high2": 0.0}
    if len(prices) < needed:
        return empty

    recent_prices = prices[-period:]
    rsi_series = calc_rsi_series(prices, 14)
    recent_rsi = rsi_series[-period:]

    if len(recent_rsi) < period:
        return empty

    half = period // 2
    price_high1 = max(recent_prices[:half])
    price_high2 = max(recent_prices[half:])
    rsi_high1 = max(recent_rsi[:half])
    rsi_high2 = max(recent_rsi[half:])

    bearish = (
        price_high2 > price_high1   # 가격 고점 상승
        and rsi_high2 < rsi_high1   # RSI 고점 하락
        and rsi_high2 > 45          # 과매도 구간 아님 (완전 붕괴는 제외)
    )
    return {
        "bearish": bearish,
        "price_high1": price_high1,
        "price_high2": price_high2,
        "rsi_high1": rsi_high1,
        "rsi_high2": rsi_high2,
    }
