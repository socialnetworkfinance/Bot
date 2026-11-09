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

# Время покупки (по Новосибирску, UTC+7).
TARGET_HOUR = 14
TARGET_MINUTE = 0

# ------------------------------------------------------------
#                 ВЫБОР БРОКЕРСКОГО СЧЕТА
# ------------------------------------------------------------
ACCOUNT_ID = 2014611372

# ------------------------------------------------------------
#               КОЛИЧЕСТВО ПОКУПАЕМЫХ ПАЁВ
# ------------------------------------------------------------
# Сколько паёв TPAY купить. Если None - по балансу.
QUANTITY_TO_BUY = 4

# Использовать ли маржу (заёмные средства брокера):
USE_MARGIN = True

# Часовой пояс Новосибирска
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
    """Универсальная обёртка над REST API Т-Инвестиций."""
    url = f"{BASE_URL}/tinkoff.public.invest.api.contract.v1.{endpoint}"
    try:
        if method == 'GET':
            resp = requests.get(url, headers=get_headers(), timeout=10, verify=False)
        else:
            resp = requests.post(url, headers=get_headers(), json=data, timeout=10, verify=False)

        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return None
        else:
            print(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"Ошибка запроса: {e}")
        return None


# ============================================================
#                 ПОЛУЧЕНИЕ ДАННЫХ О СЧЁТЕ
# ============================================================
def get_accounts():
    if ACCOUNT_ID is not None:
        print(f"Используется счет из настроек: {ACCOUNT_ID}")
        return ACCOUNT_ID

    data = safe_request('POST', 'UsersService/GetAccounts', {})
    if data:
        accounts = data.get("accounts", [])
        if accounts:
            first_id = accounts[0].get("id")
            print(f"ACCOUNT_ID не задан. Используется первый счет: {first_id}")
            return first_id
    return None


def get_figi(ticker):
    payload = {
        "query": ticker,
        "instrumentStatus": "INSTRUMENT_STATUS_BASE"
    }
    data = safe_request('POST', 'InstrumentsService/FindInstrument', payload)
    if not data:
        return None

    instruments = data.get("instruments", [])
    for inst in instruments:
        if (inst.get("classCode") == "TQBR"
                and inst.get("instrumentType") == "INSTRUMENT_TYPE_ETF"):
            return inst.get("figi")
    for inst in instruments:
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
        total = data.get("totalAmountPortfolio", {})
        if total:
            return float(total.get("units", 0)) + float(total.get("nano", 0)) / 1e9
        for pos in data.get("positions", []):
            if pos.get("instrument_type") == "currency" and pos.get("currency") == "rub":
                q = pos.get("quantity", {})
                return float(q.get("units", 0)) + float(q.get("nano", 0)) / 1e9
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
            print(f"❌ Заявка ОТКЛОНЕНА брокером. Причина: {data.get('message', 'неизвестна')}")
            return False
        print(f"Рыночная заявка на {direction} {quantity} паёв {TICKER} отправлена.")
        return True
    else:
        print(f"❌ Не удалось выставить заявку (ошибка API).")
        return False


# ============================================================
#                      ОСНОВНАЯ ЛОГИКА
# ============================================================
def main():
    print("=" * 60)
    print(f" БОТ ДЛЯ ПОКУПКИ {TICKER}")
    print("=" * 60)

    if not TOKEN:
        print("ОШИБКА: переменная окружения TBANK_TOKEN не задана.")
        return

    account_id = get_accounts()
    if not account_id:
        print("Не удалось получить account_id. Проверьте токен.")
        return
    print(f"ID счёта: {account_id}")

    figi = get_figi(TICKER)
    if not figi:
        print(f"Не удалось найти FIGI для {TICKER}. Проверьте тикер/токен.")
        return
    print(f"FIGI {TICKER}: {figi}")

    print("Начинаем выполнение...")

    # --------------------------------------------------------
    #   ПОЛУЧЕНИЕ ЦЕНЫ (МЯГКАЯ ПРОВЕРКА)
    # --------------------------------------------------------
    print("Пытаемся получить цену из стакана...")
    price = get_current_price(figi)

    if price is None:
        print("Стакан пуст. Пробуем получить цену последней сделки...")
        price = get_last_price(figi)

    if price is None:
        print("⚠️ ВНИМАНИЕ: Не удалось получить текущую цену (стакан и последняя сделка недоступны).")
        print("Но так как вы просили, бот всё равно отправит рыночную заявку!")
        price = 0.0  # Заглушка, чтобы не было ошибки при расчете
    else:
        print(f"Текущая цена {TICKER}: {price:.4f} RUB")

    balance = get_portfolio_balance(account_id)
    print(f"Баланс RUB: {balance:.2f}")

    # --------------------------------------------------------
    #   ОПРЕДЕЛЕНИЕ КОЛИЧЕСТВА ПОКУПАЕМЫХ ПАЁВ
    # --------------------------------------------------------
    if QUANTITY_TO_BUY is not None and QUANTITY_TO_BUY > 0:
        quantity = int(QUANTITY_TO_BUY)
        print(f"Количество паёв (из QUANTITY_TO_BUY): {quantity}")
        
        # Считаем требуемые средства только если знаем цену
        if price > 0:
            required_money = quantity * price
            print(f"Требуется средств: {required_money:.2f} RUB "
                  f"(баланс: {balance:.2f} RUB).")

            if required_money > balance and not USE_MARGIN:
                print("Недостаточно средств. USE_MARGIN=False, покупка отменена.")
                return
            if required_money > balance and USE_MARGIN:
                print(f"Внимание: сделка уйдёт в маржу на "
                      f"{required_money - balance:.2f} RUB.")
        else:
            print("Проверка требуемых средств пропущена (цена неизвестна).")
    else:
        # Если QUANTITY_TO_BUY не задан, а цену мы не знаем — рассчитать нельзя
        if price <= 0:
            print("Нельзя рассчитать количество по балансу, так как цена неизвестна.")
            return
            
        if balance <= 0:
            print("Недостаточно средств.")
            return
        quantity = int((balance * 0.95) // price)
        if quantity <= 0:
            print(f"Баланса {balance:.2f} RUB не хватает на 1 пай.")
            return
        print(f"QUANTITY_TO_BUY=None → количество рассчитано по балансу: {quantity} паёв.")

    # --------------------------------------------------------
    #   ОТПРАВКА ЗАЯВКИ
    # --------------------------------------------------------
    print(f"Отправка заявки: {'с маржой' if USE_MARGIN else 'без маржи'}.")
    success = place_market_order(
        figi, account_id, "buy", quantity,
        confirm_margin=USE_MARGIN
    )

    if success:
        print("Заявка на покупку исполнена (или отправлена).")
    else:
        print("Заявка не была выставлена. Проверьте вручную.")


if __name__ == "__main__":
    main()
