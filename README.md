# CIAN Bristol Parser

Парсер для сбора данных о квартирах ЖК «Бристоль» с сайта CIAN.

## Возможности

- Парсинг информации о ЖК (адрес, застройщик, цены, класс, этажность)
- Сбор данных о квартирах от застройщика (площадь, этаж, цена, фото)
- Экспорт в JSON
- Режим цикличного запуска

## Требования

- Python 3.11+
- Playwright (Chromium)

## Установка

### Windows

```powershell
# Клонирование
git clone <repository-url>
cd Cian

# Виртуальное окружение
python -m venv .venv
.venv\Scripts\activate

# Зависимости
pip install -r requirements.txt
playwright install chromium
```

### Linux / macOS

```bash
# Клонирование
git clone <>
cd Cian_Parser

# Виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate

# Зависимости
pip install -r requirements.txt
playwright install chromium

# Linux: системные зависимости
playwright install-deps chromium
```

## Запуск

```bash
# Однократный запуск
python bristol_parser.py

# Сохранение в файл
python bristol_parser.py > result.json
```

### Циклический режим

В `bristol_parser.py`:

```python
class Config:
    LOOP_ENABLED = True
    LOOP_INTERVAL = 3600  # секунды
```

## Конфигурация

| Параметр | Значение | Описание |
|----------|----------|----------|
| `JK_URL` | `https://zhk-bristol-i.cian.ru/` | URL страницы ЖК |
| `HEADLESS` | `True` | Без UI |
| `TIMEOUT` | `60000` | Таймаут (мс) |
| `LOOP_ENABLED` | `False` | Цикличный режим |
| `LOOP_INTERVAL` | `3600` | Интервал (сек) |

## Структура результата

```json
{
  "jk": {
    "id": "2531395",
    "name": "ЖК «Бристоль»",
    "status": "Сдан",
    "address": "ул. Шекспира",
    "developer": "СЗ СК Ключ",
    "price_min": 12782958,
    "price_max": 18002766,
    "building_class": "Комфорт"
  },
  "flats": [
    {
      "id": "287452441",
      "rooms": 2,
      "area": 53.8,
      "floor": 3,
      "floors_total": 4,
      "price": 14500000,
      "price_per_m2": 269516,
      "address": "ул. Шекспира, 1к2",
      "house_status": "Сдан"
    }
  ],
  "flats_count": 42,
  "parsed_at": "2024-12-15T12:00:00"
}
```

## Использование как модуль

```python
from bristol_parser import BristolParser

parser = BristolParser()
result = parser.parse()

print(f"ЖК: {result.jk.name}")
print(f"Квартир: {result.flats_count}")

for flat in result.flats:
    print(f"{flat.rooms}-комн., {flat.area} м2, {flat.price:,} руб.")
```

## Лицензия

MIT
