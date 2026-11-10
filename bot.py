import os
import time
import requests
import urllib3
import ssl
from datetime import datetime, timezone, timedelta

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context

# ============================================================
#                        НАСТРОЙКИ
# ============================================================
TOKEN = os.environ.get("TBANK_TOKEN")
TICKER = "TPAY"

# ЖЕЛЕЗНО ЖДЕМ 03:47 ПО НОВОСИБИРСКУ
TARGET_HOUR = 3
TARGET_MINUTE = 48

ACCOUNT_ID = 2014611372
QUANTITY_TO_BUY = 4
USE_MARGIN = True
NSK_TIMEZONE = timezone(timedelta(hours=7))
BASE_URL = "https://invest-public-api.tbank.ru/rest"

# ============================================================
#                        HTTP-СЛОЙ
# ============================================================
def get_headers():
    return {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

def safe_request(method, endpoint, data=None):
    url = f"{BASE_URL}/tinkoff.public.invest.api.contract.v1.{endpoint}"
    try:
        if method == 'GET':
            resp = requests.get(url, headers=get_headers(), timeout=10, verify=False)
        else:
            resp = requests.post(url, headers=get_headers(), json=data, timeout=10, verify=False)
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"Ошибка запроса: {e}")
        return None

# ============================================================
#                 ПОЛУЧЕНИЕ ДАННЫХ
# ============================================================
def get_figi(ticker):
    payload = {"query": ticker, "instrumentStatus": "INSTRUMENT_STATUS_BASE"}
    data = safe_request('POST', 'InstrumentsService/FindInstrument', payload)
    if data:
        for inst in data.get("instruments", []):
            if inst.get("classCode") == "TQBR":
                return inst.get("figi")
    return None

def get_current_price(figi):
    payload = {"figi": figi, "depth": 1}
    data = safe_request('POST', 'MarketDataService/GetOrderBook', payload)
    if data:
        ob = data.get("orderbook", {})
        if ob.get("bids"):
            p = ob["bids"][0].get("price", {})
            return float(p.get("units", 0)) + float(p.get("nano", 0)) / 1e9
        if ob.get("asks"):
            p = ob["asks"][0].get("price", {})
            return float(p.get("units", 0)) + float(p.get("nano", 0)) / 1e9
    return None

def get_last_price(figi):
    payload = {"instrumentId": [figi]}
    data = safe_request('POST', 'MarketDataService/GetLastPrices', payload)
    if data:
        last_prices = data.get("lastPrices", [])
        if last_prices:
            price_data = last_prices[0].get("price", {})
            return float(price_data.get("units", 0)) + float(price_data.get("nano", 0)) / 1e9
    return None

def get_portfolio_balance(account_id):
    payload = {"account_id": account_id}
    data = safe_request('POST', 'OperationsService/GetPortfolio', payload)
    if data:
        currencies = data.get("totalAmountCurrencies", {})
        if currencies:
            return float(currencies.get("units", 0)) + float(currencies.get("nano", 0)) / 1e9
    return 0.0

# ============================================================
#                   ВЫСТАВЛЕНИЕ ЗАЯВКИ
# ============================================================
def place_market_order(figi, account_id, direction, quantity, confirm_margin=False):
    direction_value = 1 if direction.lower() == "buy" else 2
    payload = {
        "figi": figi,
        "account_id": account_id,
        "direction": direction_value,
        "order_type": "ORDER_TYPE_MARKET",
        "quantity": str(quantity),
        "confirmMarginTrade": confirm_margin
    }
    data = safe_request('POST', 'OrdersService/PostOrder', payload)
    if data:
        status = data.get("executionReportStatus", "НЕТ СТАТУСА")
        print(f"ОТВЕТ БРОКЕРА: Статус заявки = {status}")
        if status in ["EXECUTION_REPORT_STATUS_REJECTED", "EXECUTION_REPORT_STATUS_CANCELLED"]:
            print(f"Заявка ОТКЛОНЕНА. Причина: {data.get('message', 'неизвестна')}")
            return False
        print(f"Рыночная заявка на {direction} {quantity} паев {TICKER} отправлена.")
        return True
    return False

# ============================================================
#               ОЖИДАНИЕ ВРЕМЕНИ
# ============================================================
def wait_until_target():
    now_nsk = datetime.now(NSK_TIMEZONE)
    target_today = now_nsk.replace(hour=TARGET_HOUR, minute=TARGET_MINUTE, second=0, microsecond=0)
    if now_nsk >= target_today:
        target = target_today + timedelta(days=1)
    else:
        target = target_today

    wait_seconds = (target - now_nsk).total_seconds()
    print(f"Ожидание до {target.strftime('%Y-%m-%d %H:%M:%S')} (НСК). Осталось {int(wait_seconds)} сек.")

    while True:
        now = datetime.now(NSK_TIMEZONE)
        if now >= target:
            print("Целевое время достигнуто. ПОКУПАЕМ!")
            break
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            break
        time.sleep(min(remaining, 60))

# ============================================================
#                      ОСНОВНАЯ ЛОГИКА
# ============================================================
def main():
    print(f"=== БОТ ДЛЯ ПОКУПКИ {TICKER} ===")
    if not TOKEN:
        print("ОШИБКА: Нет токена TBANK_TOKEN")
        return

    # 1. Сначала ждем 03:47
    wait_until_target()

    # 2. Как только время наступило - покупаем
    figi = get_figi(TICKER)
    if not figi:
        print("Ошибка: FIGI не найден")
        return

    print("Пытаемся получить цену из стакана...")
    price = get_current_price(figi)
    if price is None:
        print("Стакан пуст. Берем цену последней сделки...")
        price = get_last_price(figi)

    if price is None:
        print("ВНИМАНИЕ: Цены нет вообще. Все равно шлем рыночный ордер!")
        price = 0.0
    else:
        print(f"Текущая цена: {price:.4f} RUB")

    balance = get_portfolio_balance(ACCOUNT_ID)
    print(f"Баланс: {balance:.2f} RUB")

    print(f"Отправка заявки: {'с маржой' if USE_MARGIN else 'без маржи'}.")
    success = place_market_order(figi, ACCOUNT_ID, "buy", QUANTITY_TO_BUY, confirm_margin=USE_MARGIN)

    if success:
        print("Бот завершил работу.")
    else:
        print("Бот завершил работу с ошибкой.")

if __name__ == "__main__":
    main()
