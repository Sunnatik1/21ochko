import os
import time
import json
import datetime
from datetime import timezone
from collections import Counter, defaultdict
import requests
import telebot

# ============================================================
#   НАСТРОЙКИ ОКРУЖЕНИЯ
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PREDICTION_CHANNEL_ID = os.getenv("PREDICTION_CHANNEL_ID")
PREDICTION_DETAILED_CHANNEL_ID = os.getenv("PREDICTION_DETAILED_CHANNEL_ID")

BASE_DOMAIN = os.getenv("BASE_DOMAIN", "melbet-33493.pro")

VIRTUAL_URL = os.getenv(
    "VIRTUAL_URL",
    f"https://{BASE_DOMAIN}/service-api/LiveFeed/Get1x2_VZip?sports=146&champs=1643503&count=40&gr=1521&mode=4&country=192&partner=8&getEmpty=true&virtualSports=true&noFilterBlockEvent=true"
)
STATISTIC_URL_TEMPLATE = os.getenv(
    "STATISTIC_URL_TEMPLATE",
    f"https://{BASE_DOMAIN}/cyber-api/mainfeedlive/web/cyber/v3/statistic?country=192&fcountry=192&gameId={{game_id}}&gr=1521&lng=ru&ref=8"
)

bot = telebot.TeleBot(BOT_TOKEN, threaded=False)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": f"https://{BASE_DOMAIN}/",
}

# ============================================================
#   КОНСТАНТЫ
# ============================================================
SUITS = {0: "♠️", 1: "♣️", 2: "♦️", 3: "♥️"}

CARD_VALUES = {
    14: "A", 2: "2", 3: "3", 4: "4", 5: "5", 6: "6",
    7: "7", 8: "8", 9: "9", 10: "10", 11: "J", 12: "Q", 13: "K"
}

# ============================================================
#   ХРАНИЛИЩА
# ============================================================
active_games = {}
game_history = {}

current_prediction = {
    "message_id": None,
    "detailed_message_id": None,
    "trigger_game_num": None,
    "predicted_value": None,
    "predicted_symbol": None,
    "predicted_suit_code": None,
    "predicted_exact_card": None,
    "target_recipient": None,
    "confidence": 50,
    "target_game_num": None,
    "dogen_level": 1,
    "is_active": False,
    "target_p1_cards": [],
    "target_p2_cards": []
}

# ============================================================
#   ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def normalize_game_num(num):
    try:
        num = int(num)
    except (ValueError, TypeError):
        return 0
        
    while num > 1440:
        num -= 1440
    while num < 1:
        num += 1440
    return num

def extract_game_number(game_data):
    tn_val = game_data.get("DI") or game_data.get("TN")
    if not tn_val or not str(tn_val).isdigit():
        sc = game_data.get("SC", {})
        tn_val = sc.get("DI") or sc.get("CP")

    if tn_val is not None:
        try:
            return int(tn_val)
        except (ValueError, TypeError):
            pass
    return 0

def get_card_symbol(card_value, suit_code):
    val_str = CARD_VALUES.get(card_value, "?")
    suit_str = SUITS.get(suit_code, "?")
    return f"{val_str}{suit_str}"

def parse_cards_detail(cards_str):
    try:
        cards = cards_str if isinstance(cards_str, list) else json.loads(cards_str)
        symbols, values, full_cards = [], [], []
        for c in cards:
            cv, cs = c.get("CV", 0), c.get("CS", 0)
            symbols.append(get_card_symbol(cv, cs))
            values.append(cv)
            full_cards.append((cv, cs))
        return symbols, values, full_cards
    except Exception:
        return [], [], []

# ============================================================
#   🧠 ПРОДВИНУТЫЙ ПРЕДИКТИВНЫЙ ДВИЖОК
# ============================================================

def get_base_prediction(first_card_value):
    """Базовая стратегия матрицы триггеров"""
    if first_card_value == 6:
        return 13, "K (Король)"
    elif first_card_value in [10, 7, 9]:
        return 12, "Q (Дама)"
    elif first_card_value == 8:
        return 11, "J (Валет)"
    else:
        return 1, "A (Туз)"

def calculate_advanced_prediction(trigger_val, trigger_suit, history):
    """
    МАКСИМИЗАЦИЯ ТОЧНОСТИ:
    1. Марковский анализ переходов (преимущество исторической цепочки)
    2. Поиск масти методом возврата к среднему
    3. Волновой расчёт адресата (P1 vs P2)
    """
    base_val, base_sym = get_base_prediction(trigger_val)
    recent_games = list(history.values())[-35:] # Анализ 35 последних игр

    if len(recent_games) < 10:
        exact_card = f"{CARD_VALUES.get(base_val, base_val)}{SUITS.get(trigger_suit, '')}"
        return base_val, base_sym, trigger_suit, exact_card, "👤 Игрок (P1)", 65

    # --- 1. ОПРЕДЕЛЕНИЕ ОПТИМАЛЬНОГО ЗНАЧЕНИЯ (Марковский переход) ---
    transitions = defaultdict(list)
    ordered_nums = sorted(history.keys())
    for i in range(len(ordered_nums) - 3):
        g_curr = history[ordered_nums[i]]
        g_target = history[ordered_nums[i+3]]
        
        t_card = g_curr.get("player_first_card")
        target_all_cards = g_target.get("player_values", []) + g_target.get("dealer_values", [])
        if t_card is not None:
            transitions[t_card].extend(target_all_cards)

    # Если по цепочке исторически преобладает другое значение — корректируем
    final_val = base_val
    if transitions[trigger_val]:
        most_common_historical = Counter(transitions[trigger_val]).most_common(1)[0][0]
        if most_common_historical in [1, 11, 12, 13]:
            final_val = most_common_historical
            base_sym = f"{CARD_VALUES.get(final_val)} ({'Туз' if final_val==1 else 'Валет' if final_val==11 else 'Дама' if final_val==12 else 'Король'})"

    # --- 2. РАСЧЕТ ТОЧНОЙ МАСТИ (Задержавшаяся масть) ---
    suit_counts = Counter()
    for g in recent_games:
        for cv, cs in g.get("player_full_cards", []) + g.get("dealer_full_cards", []):
            if cv == final_val or (final_val == 1 and cv in [1, 14]):
                suit_counts[cs] += 1

    # Ищем масть с наименьшей частотой за последние игры для эффекта компенсации
    all_possible_suits = [0, 1, 2, 3]
    missing_suits = [s for s in all_possible_suits if s not in suit_counts]
    
    if missing_suits:
        best_suit = missing_suits[0]
    else:
        # Берем самую редкую из всех
        best_suit = suit_counts.most_common()[-1][0]

    exact_card_str = f"{CARD_VALUES.get(final_val, final_val)}{SUITS.get(best_suit, '')}"

    # --- 3. АДРЕСАТ P1/P2 И РАСЧЕТ ПРОЦЕНТА УВЕРЕННОСТИ ---
    p1_streak = 0
    p2_streak = 0
    for g in recent_games:
        p1_has = any((cv == final_val or (final_val == 1 and cv in [1, 14])) for cv, _ in g.get("player_full_cards", []))
        p2_has = any((cv == final_val or (final_val == 1 and cv in [1, 14])) for cv, _ in g.get("dealer_full_cards", []))
        if p1_has: p1_streak += 1
        if p2_has: p2_streak += 1

    total_hits = p1_streak + p2_streak
    if total_hits > 0:
        p1_prob = (p1_streak / total_hits) * 100
    else:
        p1_prob = 50.0

    if p1_prob >= 50.0:
        recipient = "👤 Игрок (P1)"
        confidence = int(min(max(p1_prob + 15, 65), 96))
    else:
        recipient = "🎩 Дилер (P2)"
        confidence = int(min(max((100 - p1_prob) + 15, 65), 96))

    return final_val, base_sym, best_suit, exact_card_str, recipient, confidence

# ============================================================
#   ОТПРАВКА И ОБНОВЛЕНИЕ В TELEGRAM
# ============================================================

def render_prediction_text(is_detailed=False, status_str="⏳ Ожидаем карты..."):
    target_num = current_prediction["target_game_num"]
    symbol = current_prediction["predicted_symbol"]
    dogen = current_prediction["dogen_level"]
    
    p1_c = " ".join(current_prediction["target_p1_cards"]) if current_prediction["target_p1_cards"] else "..."
    p2_c = " ".join(current_prediction["target_p2_cards"]) if current_prediction["target_p2_cards"] else "..."

    if not is_detailed:
        return (
            f"🎯 Игра №{target_num}\n"
            f"Значение: {symbol}\n"
            f"💰 Догон: {dogen}\n"
            f"📊 Результат: {status_str}"
        )
    else:
        exact_card = current_prediction["predicted_exact_card"]
        recipient = current_prediction["target_recipient"]
        confidence = current_prediction["confidence"]
        
        msg = f"🎯 Игра №{target_num}\nЗначение: {symbol}\n"
        if exact_card:
            msg += f"🃏 Точная карта: {exact_card}\n"
        msg += (
            f"Кому: {recipient} ({confidence}%)\n"
            f"🃏 P1: {p1_c} | P2: {p2_c}\n"
            f"💰 Догон: {dogen}\n"
            f"📊 Результат: {status_str}"
        )
        return msg

def send_new_prediction(trigger_num, symbol, exact_card, recipient, confidence, target_num):
    current_prediction["target_p1_cards"] = []
    current_prediction["target_p2_cards"] = []
    
    current_prediction["trigger_game_num"] = trigger_num
    current_prediction["target_game_num"] = target_num
    current_prediction["predicted_symbol"] = symbol
    current_prediction["predicted_exact_card"] = exact_card
    current_prediction["target_recipient"] = recipient
    current_prediction["confidence"] = confidence
    current_prediction["is_active"] = True

    if PREDICTION_CHANNEL_ID:
        try:
            sent_general = bot.send_message(PREDICTION_CHANNEL_ID, render_prediction_text(is_detailed=False))
            current_prediction["message_id"] = sent_general.message_id
            print(f"🎯 Общий прогноз отправлен на №{target_num} ({symbol})")
        except Exception as e:
            print(f"❌ Ошибка отправки общего прогноза: {e}")
    
    if PREDICTION_DETAILED_CHANNEL_ID:
        try:
            sent_detailed = bot.send_message(PREDICTION_DETAILED_CHANNEL_ID, render_prediction_text(is_detailed=True))
            current_prediction["detailed_message_id"] = sent_detailed.message_id
            print(f"📊 Детальный прогноз отправлен на №{target_num}")
        except Exception as e:
            print(f"❌ Ошибка отправки детального прогноза: {e}")

def update_live_prediction_cards(p1_cards, p2_cards):
    if not current_prediction.get("is_active"):
        return

    if current_prediction["target_p1_cards"] == p1_cards and current_prediction["target_p2_cards"] == p2_cards:
        return

    current_prediction["target_p1_cards"] = p1_cards
    current_prediction["target_p2_cards"] = p2_cards

    if current_prediction.get("message_id") and PREDICTION_CHANNEL_ID:
        try:
            bot.edit_message_text(
                chat_id=PREDICTION_CHANNEL_ID,
                message_id=current_prediction["message_id"],
                text=render_prediction_text(is_detailed=False)
            )
        except Exception:
            pass

    if current_prediction.get("detailed_message_id") and PREDICTION_DETAILED_CHANNEL_ID:
        try:
            bot.edit_message_text(
                chat_id=PREDICTION_DETAILED_CHANNEL_ID,
                message_id=current_prediction["detailed_message_id"],
                text=render_prediction_text(is_detailed=True)
            )
        except Exception:
            pass

def check_prediction_for_game(player_values, dealer_values, predicted_val):
    if not predicted_val:
        return False
    all_values = player_values + dealer_values
    for val in all_values:
        if predicted_val == 1 and val in [1, 14]:
            return True
        if val == predicted_val:
            return True
    return False

def check_detailed_prediction_for_game(p1_full, p2_full, predicted_val, predicted_suit, target_recipient):
    if not predicted_val:
        return False, False

    check_cards = p1_full if "P1" in (target_recipient or "") else p2_full
    val_hit = False
    exact_hit = False

    for cv, _ in check_cards:
        if (predicted_val == 1 and cv in [1, 14]) or (cv == predicted_val):
            val_hit = True
            break

    if predicted_suit is not None:
        for cv, cs in (p1_full + p2_full):
            if ((predicted_val == 1 and cv in [1, 14]) or (cv == predicted_val)) and cs == predicted_suit:
                exact_hit = True
                break

    return val_hit, exact_hit

def finalize_prediction(status_code, exact_hit=False):
    if not current_prediction.get("is_active"):
        return

    target_num = current_prediction["target_game_num"]
    res_str = {0: "✅0️⃣", 1: "✅1️⃣", 2: "✅2️⃣"}.get(status_code, "❌")
    if exact_hit and status_code >= 0:
        res_str += " 🎯 (ТОЧНАЯ КАРТА!)"

    if current_prediction.get("message_id") and PREDICTION_CHANNEL_ID:
        try:
            bot.edit_message_text(
                chat_id=PREDICTION_CHANNEL_ID,
                message_id=current_prediction["message_id"],
                text=render_prediction_text(is_detailed=False, status_str=res_str)
            )
        except Exception as e:
            print(f"❌ Ошибка финализации общего прогноза: {e}")

    if current_prediction.get("detailed_message_id") and PREDICTION_DETAILED_CHANNEL_ID:
        try:
            bot.edit_message_text(
                chat_id=PREDICTION_DETAILED_CHANNEL_ID,
                message_id=current_prediction["detailed_message_id"],
                text=render_prediction_text(is_detailed=True, status_str=res_str)
            )
        except Exception as e:
            print(f"❌ Ошибка финализации детального прогноза: {e}")

    if status_code >= 0:
        current_prediction["dogen_level"] = 1
    else:
        current_prediction["dogen_level"] *= 2

    current_prediction["is_active"] = False
    current_prediction["message_id"] = None
    current_prediction["detailed_message_id"] = None

def process_prediction_check(game_num, p1_values, p2_values, p1_full, p2_full):
    if not current_prediction.get("is_active"):
        return
        
    target_num = current_prediction["target_game_num"]
    plus_1_num = normalize_game_num(target_num + 1)
    plus_2_num = normalize_game_num(target_num + 2)
    pred_val = current_prediction.get("predicted_value")
    pred_suit = current_prediction.get("predicted_suit_code")
    recipient = current_prediction.get("target_recipient")
    
    is_hit = check_prediction_for_game(p1_values, p2_values, pred_val)
    is_detailed_hit, exact_hit = check_detailed_prediction_for_game(
        p1_full, p2_full, pred_val, pred_suit, recipient
    )

    if game_num == target_num:
        if is_hit:
            finalize_prediction(0, exact_hit if is_detailed_hit else False)
    elif game_num == plus_1_num:
        if is_hit:
            finalize_prediction(1, exact_hit if is_detailed_hit else False)
    elif game_num == plus_2_num:
        if is_hit:
            finalize_prediction(2, exact_hit if is_detailed_hit else False)
        else:
            finalize_prediction(-1, False)

def process_new_prediction(game_num, first_card_value, first_card_suit):
    if not first_card_value or game_num == 0:
        return

    if current_prediction.get("is_active"):
        return

    # Вычисление улучшенного прогноза
    pred_val, pred_sym, best_suit, exact_card_str, recipient, confidence = calculate_advanced_prediction(
        first_card_value, first_card_suit, game_history
    )

    # 🛡️ ФИЛЬТР ВЫСОКОЙ ТОЧНОСТИ #1 (Пропуск низковероятных сигналов)
    if confidence < 65:
        print(f"⚠️ Прогноз пропущен: низкий уровень уверенности ({confidence}%).")
        return

    # 🛡️ ФИЛЬТР ВЫСОКОЙ ТОЧНОСТИ #2 (Фильтр перегрева)
    if game_history:
        last_game = list(game_history.values())[-1]
        last_cards = last_game.get("player_values", []) + last_game.get("dealer_values", [])
        if pred_val in last_cards or (pred_val == 1 and 14 in last_cards):
            print(f"⚠️ Прогноз {pred_sym} пропущен: карта выпадала в прошлой игре.")
            return

    target_num = normalize_game_num(game_num + 3)

    current_prediction["predicted_value"] = pred_val
    current_prediction["predicted_suit_code"] = best_suit
    
    send_new_prediction(game_num, pred_sym, exact_card_str, recipient, confidence, target_num)

# ============================================================
#   ОСНОВНОЙ ЦИКЛ ОБРАБОТКИ
# ============================================================

def get_active_games_info(session):
    try:
        resp = session.get(VIRTUAL_URL, headers=HEADERS, timeout=10)
        data = resp.json()
        
        raw_games = data.get("Value", []) or data.get("games", [])
        if isinstance(raw_games, dict):
            raw_games = [raw_games]

        result = []
        for idx, g in enumerate(raw_games):
            game_id = g.get("I") or g.get("id")
            if not game_id:
                continue

            sc = g.get("SC", {}) or g.get("scores", {})
            is_finished = (sc.get("CPS") == "Игра завершена") or (sc.get("currentPeriodName") == "Игра завершена")

            result.append({
                "id": game_id,
                "index": idx,
                "is_finished": is_finished,
                "raw_data": g
            })
            
        return result
    except Exception as e:
        print(f"❌ Ошибка получения списка игр: {e}")
        return []

def main():
    global active_games, game_history
    print("🚀 Бот запущен (Продвинутый модуль прогнозирования активирован)...")
    session = requests.Session()

    while True:
        try:
            games_info = get_active_games_info(session)
            if not games_info:
                time.sleep(3)
                continue

            current_game_ids = set(g["id"] for g in games_info if g["id"])

            for g_info in games_info:
                game_id = g_info["id"]
                if not game_id:
                    continue

                if game_id not in active_games:
                    game_num = extract_game_number(g_info["raw_data"])
                    announcement_text = f"⏳ Ожидание игры #N{game_num}\n (ID: {game_id})"
                    
                    msg_id = None
                    if CHANNEL_ID:
                        try:
                            sent = bot.send_message(CHANNEL_ID, announcement_text)
                            msg_id = sent.message_id
                        except Exception as e:
                            print(f"⚠️ Ошибка отправки анонса #N{game_num}: {e}")

                    active_games[game_id] = {
                        "message_id": msg_id,
                        "game_num": game_num,
                        "last_state": announcement_text,
                        "is_finished": False
                    }

                slot = active_games[game_id]
                game_num = slot["game_num"]

                stat_url = STATISTIC_URL_TEMPLATE.format(game_id=game_id)
                resp = session.get(stat_url, headers=HEADERS, timeout=5)
                if resp.status_code == 204 or not resp.text.strip():
                    continue

                data = resp.json()
                score_detail = data.get("fullScoreDetail", {})
                p1_score = score_detail.get("scoreOpp1", 0)
                p2_score = score_detail.get("scoreOpp2", 0)
                total_points = p1_score + p2_score
                status = data.get("currentPeriodName", "")

                stat = data.get("statistic", {}).get("main", {})
                p1_cards, p1_values, p1_full = parse_cards_detail(stat.get("P1", "[]"))
                p2_cards, p2_values, p2_full = parse_cards_detail(stat.get("P2", "[]"))

                is_finished = (status == "Игра завершена")

                # LIVE-трансляция выпавших карт в целевую игру
                if current_prediction.get("is_active") and game_num == current_prediction["target_game_num"]:
                    update_live_prediction_cards(p1_cards, p2_cards)

                # Завершение игры и расчет
                if is_finished and game_num not in game_history:
                    first_card_value = p1_values[0] if p1_values else None
                    first_card_suit = p1_full[0][1] if p1_full else None

                    game_history[game_num] = {
                        "player_first_card": first_card_value,
                        "player_values": p1_values,
                        "dealer_values": p2_values,
                        "player_full_cards": p1_full,
                        "dealer_full_cards": p2_full
                    }

                    if len(game_history) > 60:
                        oldest = min(game_history.keys())
                        del game_history[oldest]

                    print(f"📝 Игра #{game_num} завершена | Карта: {first_card_value} масть {first_card_suit}")

                    process_prediction_check(game_num, p1_values, p2_values, p1_full, p2_full)
                    
                    if not current_prediction.get("is_active"):
                        process_new_prediction(game_num, first_card_value, first_card_suit)

                # Трансляция хода текущей игры
                current_state = f"{p1_score}_{p2_score}_{'_'.join(p1_cards)}_{'_'.join(p2_cards)}_{is_finished}"

                if current_state != slot["last_state"] and (p1_cards or p2_cards):
                    cards_p1 = " ".join(p1_cards) if p1_cards else "?"
                    cards_p2 = " ".join(p2_cards) if p2_cards else "?"

                    if not is_finished:
                        game_info = f"#N{game_num}. {p1_score}({cards_p1}) - {p2_score}({cards_p2}) #T{total_points}\n (ID: {game_id})"
                    else:
                        p1_win = (p1_score <= 21 and p1_score > p2_score) or (p2_score > 21 and p1_score <= 21)
                        p2_win = (p2_score <= 21 and p2_score > p1_score) or (p1_score > 21 and p1_score <= 21)
                        draw = (p1_score == p2_score) or (p1_score > 21 and p2_score > 21)

                        res_p1 = "✅" if p1_win else ("🔰" if draw else "")
                        res_p2 = "✅" if p2_win else ("🔰" if draw else "")
                        game_info = f"#N{game_num}. {res_p1}{p1_score}({cards_p1}) - {res_p2}{p2_score}({cards_p2}) #T{total_points}\n (ID: {game_id})"

                    try:
                        if slot["message_id"] is None and CHANNEL_ID:
                            sent = bot.send_message(CHANNEL_ID, game_info)
                            slot["message_id"] = sent.message_id
                        elif CHANNEL_ID and slot["message_id"]:
                            bot.edit_message_text(chat_id=CHANNEL_ID, message_id=slot["message_id"], text=game_info)
                    except Exception as e:
                        print(f"⚠️ Ошибка редактирования: {e}")

                    slot["last_state"] = current_state
                    if is_finished:
                        slot["is_finished"] = True

            finished_to_remove = [
                gid for gid, data in active_games.items()
                if data["is_finished"] and gid not in current_game_ids
            ]
            for gid in finished_to_remove:
                del active_games[gid]

            time.sleep(3)

        except Exception as e:
            print(f"❌ Ошибка главного цикла: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
