"""🔁 جبران تبدیل‌هایی که با نرخ قدیمی انجام شده‌اند.

پیش‌تر «تبدیل برنز به نقره» ارزش‌خنثی بود: ۱۰۰ برنز (ارزش ۱۰۰) به ۱۰
نقره (ارزش ۱۰۰) تبدیل می‌شد و ارزش کل تکان نمی‌خورد. حالا تبدیل یک
«ارتقا» است و باید سود بدهد (۱۰۰ برنز ➜ ۱۲ نقره).

کاربری که *پیش از* این تغییر تبدیل کرده، آن سود را نگرفته است. این
ماژول یک بار اجرا می‌شود، تبدیل‌های گذشته را از روی دفتر تراکنش پیدا
می‌کند و تفاوت را به حساب کاربر می‌ریزد.

تضمین‌ها:
  • **یک بار**: هر تبدیل با شناسهٔ خودش علامت می‌خورد، پس اجرای دوباره
    چیزی اضافه نمی‌کند.
  • **فقط کمبود**: اگر کاربر همان موقع سود گرفته باشد، چیزی داده
    نمی‌شود؛ هرگز سکه پس گرفته نمی‌شود.
  • **اتمیک**: همه چیز داخل یک تراکنش انجام می‌گیرد.

    from economy import upgrade_migration
    upgrade_migration.run()
"""
from economy import settings, storage
from economy.coins import accounts
from economy.transactions import ledger

# کلیدی که داخل رکورد کاربر نگه می‌دارد کدام تبدیل‌ها جبران شده‌اند.
MARKER = "upgrade_compensated"


def _expected_gain(changes, config):
    """سود ارزشی که این تبدیل *باید* با نرخ امروز می‌داشت.

    ``changes`` مثل ``{"bronze": -100, "silver": 10}`` است. از روی سکهٔ
    خرج‌شده تعداد دفعات تبدیل حساب می‌شود، بعد سود هر دفعه.
    """
    values = settings.coin_values()
    bronze = int(changes.get(accounts.BRONZE, 0))
    silver = int(changes.get(accounts.SILVER, 0))
    gold = int(changes.get(accounts.GOLD, 0))

    # برنز ➜ نقره: برنز منفی، نقره مثبت.
    if bronze < 0 and silver > 0:
        cost = int(config["BronzeToSilverCost"])
        gain = int(config["BronzeToSilverGain"])
        if cost <= 0:
            return 0, None
        times = -bronze // cost
        if times <= 0:
            return 0, None
        # نقره‌ای که باید می‌گرفت، منهای نقره‌ای که گرفت.
        shortfall = (gain * times) - silver
        return max(0, shortfall), accounts.SILVER

    # نقره ➜ طلا: نقره منفی، طلا مثبت.
    if silver < 0 and gold > 0:
        cost = int(config["SilverToGoldCost"])
        gain = int(config["SilverToGoldGain"])
        if cost <= 0:
            return 0, None
        times = -silver // cost
        if times <= 0:
            return 0, None
        shortfall = (gain * times) - gold
        return max(0, shortfall), accounts.GOLD

    return 0, None


def preview():
    """بدون تغییر دادن چیزی، گزارش می‌دهد چه کسی چقدر طلبکار است."""
    config = settings.load()
    data = storage.snapshot()
    report = {}
    for key, user in data.get("users", {}).items():
        done = set(user.get(MARKER, []))
        owed = {}
        for entry in user.get("transactions", []):
            if entry.get("kind") != ledger.KIND_CONVERT:
                continue
            entry_id = entry.get("id")
            if entry_id is None or entry_id in done:
                continue
            amount, coin = _expected_gain(entry.get("changes", {}), config)
            if amount > 0 and coin:
                owed[coin] = owed.get(coin, 0) + amount
        if owed:
            report[key] = owed
    return report


def run():
    """جبران را اعمال می‌کند. خروجی: گزارش آنچه پرداخت شد."""
    config = settings.load()
    paid = {}

    with storage.transaction() as data:
        for key in list(data.get("users", {})):
            user = accounts._user(data, key)
            done = list(user.get(MARKER, []))
            done_set = set(done)
            granted = {}

            for entry in list(user.get("transactions", [])):
                if entry.get("kind") != ledger.KIND_CONVERT:
                    continue
                entry_id = entry.get("id")
                if entry_id is None or entry_id in done_set:
                    continue

                amount, coin = _expected_gain(entry.get("changes", {}),
                                              config)
                # حتی وقتی چیزی بدهکار نیستیم، تبدیل را علامت می‌زنیم تا
                # اجرای بعدی دوباره بررسی‌اش نکند.
                done_set.add(entry_id)
                done.append(entry_id)
                if amount > 0 and coin:
                    user[coin] = int(user.get(coin, 0)) + amount
                    granted[coin] = granted.get(coin, 0) + amount

            if done:
                user[MARKER] = done
            if granted:
                total = accounts._refresh_total(data, user)
                ledger.record(
                    data, key, ledger.KIND_RECEIVE, granted,
                    reference=f"upgrade_migration:{key}",
                    note="جبران تبدیل با نرخ قدیمی",
                    balance_after=accounts._snapshot_balance(user),
                    total_value=total,
                )
                paid[key] = dict(granted)
            else:
                # ارزش کل را هم تازه می‌کنیم تا هیچ‌کس با عدد کهنه نماند.
                accounts._refresh_total(data, user)

    return paid


def already_done(chat_id, user_id):
    """آیا تبدیل‌های این کاربر بررسی شده‌اند."""
    key = accounts.user_key(chat_id, user_id)
    user = storage.snapshot().get("users", {}).get(key, {})
    return bool(user.get(MARKER))
