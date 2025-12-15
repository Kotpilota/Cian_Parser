"""
Парсер ЖК Бристоль (CIAN)
Возвращает JSON с данными о ЖК и квартирах
"""

import re
import json
import time
import logging
from datetime import datetime

from pydantic import BaseModel, computed_field
from playwright.sync_api import sync_playwright, Browser, Page


# ============ CONFIG ============

class Config:
    JK_URL = "https://zhk-bristol-i.cian.ru/"
    HEADLESS = True
    TIMEOUT = 60000
    LOOP_ENABLED = False
    LOOP_INTERVAL = 3600

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


# ============ LOGGING ============

LOG_FILE = "parser.log"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
logger.addHandler(_handler)


# ============ EXCEPTIONS ============

class ParserError(Exception):
    """Базовая ошибка парсера"""


class NewObjectIdNotFound(ParserError):
    """newobject_id не найден на странице"""


class NoFlatsFound(ParserError):
    """Квартиры не найдены"""


# ============ MODELS ============

class Flat(BaseModel):
    """Модель квартиры"""
    id: str
    url: str
    rooms: int  # 0 = студия
    area: float
    floor: int
    floors_total: int
    price: int
    address: str | None = None
    year_built: int | None = None
    house_status: str | None = None
    images: list[str] = []

    @computed_field
    @property
    def price_per_m2(self) -> int:
        """Цена за квадратный метр"""
        return int(self.price / self.area) if self.area > 0 else 0


class JK(BaseModel):
    """Модель ЖК"""
    id: str
    name: str
    url: str
    status: str | None = None
    address: str | None = None
    developer: str | None = None
    price_min: int | None = None
    price_max: int | None = None
    price_per_m2_min: int | None = None
    price_per_m2_max: int | None = None
    building_class: str | None = None
    floors: str | None = None
    buildings_count: int | None = None
    building_type: str | None = None
    ceiling_height: float | None = None
    finishing: str | None = None
    parking: str | None = None
    year_built: int | None = None


class ParseResult(BaseModel):
    """Результат парсинга"""
    jk: JK
    flats: list[Flat]
    flats_count: int
    parsed_at: datetime


# ============ HELPERS ============

def decode_url(url: str) -> str:
    """Декодирование URL из JSON"""
    return url.replace('\\u002F', '/')


def parse_rooms(text: str) -> int:
    """Извлечение количества комнат из текста"""
    if "студия" in text.lower():
        return 0
    match = re.search(r'(\d+)', text)
    return int(match.group(1)) if match else 0


def parse_area(text: str) -> float:
    """Извлечение площади из текста"""
    match = re.search(r'([\d,]+)', text)
    return float(match.group(1).replace(',', '.')) if match else 0.0


def parse_building_from_house_name(house_name: str) -> str | None:
    """Извлечение корпуса из house_name: 'Шекспира, 1к1 (510к2)' -> '1к1'"""
    if not house_name or ',' not in house_name:
        return None
    building_part = house_name.split(',', 1)[1].strip()
    building = re.sub(r'\s*\([^)]+\)\s*$', '', building_part).strip()
    return building or None


def normalize_status(status: str | None) -> str | None:
    """Нормализация статуса дома"""
    if not status:
        return None
    status_lower = status.lower().strip()
    if "сдан" in status_lower:
        return "Сдан"
    if "строит" in status_lower:
        return "Строится"
    return status


def safe_int(value: str, default: int = 0) -> int:
    """Безопасное преобразование в int"""
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def safe_float(value: str, default: float = 0.0) -> float:
    """Безопасное преобразование в float"""
    try:
        return float(value.replace(',', '.').replace(' м', ''))
    except (ValueError, TypeError, AttributeError):
        return default


# ============ PARSER ============

class BristolParser:
    """Парсер ЖК Бристоль"""

    def __init__(self):
        self.config = Config()
        self.newobject_id: str | None = None
        self.flats_url: str | None = None

    def parse(self) -> ParseResult:
        """Основной метод — запускает парсинг и возвращает результат"""
        logger.info("Запуск парсера")

        with sync_playwright() as pw:
            browser = self._launch_browser(pw)
            try:
                page = self._create_page(browser)
                self._open_jk_page(page)

                self.newobject_id = self._extract_newobject_id(page)
                self.flats_url = self._build_flats_url()

                jk = self._parse_jk(page)
                jk_html = page.content()
                flats = self._parse_flats(page, jk, jk_html)
            finally:
                browser.close()

        result = ParseResult(
            jk=jk,
            flats=flats,
            flats_count=len(flats),
            parsed_at=datetime.now(),
        )
        logger.info(f"Парсинг завершён. Квартир: {result.flats_count}")
        return result

    # -------- Browser --------

    def _launch_browser(self, pw) -> Browser:
        """Запуск браузера"""
        return pw.chromium.launch(
            headless=self.config.HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )

    def _create_page(self, browser: Browser) -> Page:
        """Создание страницы с контекстом"""
        context = browser.new_context(
            viewport={"width": 1400, "height": 900},
            user_agent=self.config.USER_AGENT,
        )
        return context.new_page()

    def _open_jk_page(self, page: Page) -> None:
        """Открытие страницы ЖК"""
        logger.info(f"Открываю страницу: {self.config.JK_URL}")
        page.goto(self.config.JK_URL, timeout=self.config.TIMEOUT)
        page.wait_for_load_state("domcontentloaded")

    # -------- Extract IDs --------

    def _extract_newobject_id(self, page: Page) -> str:
        """Извлечение newobject_id из HTML"""
        logger.info("Ищу newobject_id")
        html = page.content()

        patterns = [
            r'newobject%5B0%5D=(\d+)',
            r'"newobject":\[(\d+)\]',
            r'newobject_id["\']?:\s*(\d+)',
            r'"id":(\d+).*?"type":"newobject"',
        ]

        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                newobject_id = match.group(1)
                logger.info(f"newobject_id: {newobject_id}")
                return newobject_id

        raise NewObjectIdNotFound("newobject_id не найден в HTML")

    def _build_flats_url(self) -> str:
        """Формирование URL страницы со всеми квартирами"""
        url = (
            f"https://www.cian.ru/cat.php"
            f"?deal_type=sale&engine_version=2&offer_type=flat"
            f"&from_developer=1&newobject%5B0%5D={self.newobject_id}"
        )
        logger.info(f"URL квартир: {url}")
        return url

    # -------- Parse JK --------

    def _parse_jk(self, page: Page) -> JK:
        """Парсинг данных о ЖК"""
        logger.info("Парсинг данных ЖК")
        html = page.content()

        jk_data = {
            "id": self.newobject_id,
            "name": "ЖК «Бристоль»",
            "url": self.config.JK_URL,
        }
        jk_data.update(self._extract_jk_fields(html))
        return JK(**jk_data)

    def _extract_jk_fields(self, html: str) -> dict:
        """Извлечение полей ЖК из HTML"""
        data = {}

        # Название
        if match := re.search(r'"displayName":"([^"]+)"', html):
            data["name"] = match.group(1).replace("\\u00ab", "«").replace("\\u00bb", "»")

        # Статус
        if match := re.search(r'"buildingStatusInfo":\{[^}]*"name":"([^"]+)"', html):
            data["status"] = normalize_status(match.group(1))

        # Адрес
        if match := re.search(r'class="street-address">([^<]+)<', html):
            data["address"] = match.group(1)

        # Застройщик
        if match := re.search(r'"builders":\[\{"[^}]*"name":"([^"]+)"', html):
            data["developer"] = match.group(1)

        # Цены от застройщика
        if match := re.search(r'"fromDeveloperMinPrice":(\d+)', html):
            data["price_min"] = int(match.group(1))
        elif match := re.search(r'"minPrice":"([\d.]+)"', html):
            data["price_min"] = safe_int(match.group(1))

        if match := re.search(r'"fromDeveloperMaxPrice":(\d+)', html):
            data["price_max"] = int(match.group(1))
        elif match := re.search(r'"maxPrice":"([\d.]+)"', html):
            data["price_max"] = safe_int(match.group(1))

        # Цена за м²
        if match := re.search(r'"minPriceForMeterFromDeveloperValue":(\d+)', html):
            data["price_per_m2_min"] = int(match.group(1))

        if match := re.search(r'"priceForMeterFromDeveloperDisplay":"[^"]*?(\d[\d\s]*\d)\s*₽', html):
            price_str = match.group(1).replace(' ', '').replace('\\u00a0', '')
            data["price_per_m2_max"] = safe_int(price_str)

        # Год сдачи
        if match := re.search(r'"completionYear":(\d{4})', html):
            data["year_built"] = int(match.group(1))

        # Класс
        if match := re.search(r'"newbuildingClass":"([^"]+)"', html):
            data["building_class"] = match.group(1)

        # Тип дома
        if match := re.search(r'"materials":\["([^"]+)"', html):
            data["building_type"] = match.group(1).capitalize()

        # Этажность
        if match := re.search(r'"floor":\{"minFloors":(\d+),"maxFloors":(\d+)\}', html):
            min_f, max_f = match.group(1), match.group(2)
            data["floors"] = f"{min_f}-{max_f}" if min_f != max_f else min_f

        # Спецификации
        if specs_match := re.search(r'"shortSpecifications":\[(.+?)\]', html):
            specs = specs_match.group(1)

            if match := re.search(r'"title":"Корпуса","value":"(\d+)"', specs):
                data["buildings_count"] = int(match.group(1))

            if match := re.search(r'"title":"Отделка","value":"([^"]+)"', specs):
                data["finishing"] = match.group(1)

            if match := re.search(r'"title":"Потолки","value":"([^"]+)"', specs):
                data["ceiling_height"] = safe_float(match.group(1))

        # Парковка
        if match := re.search(r'"parking":\[\{"[^}]*"title":"([^"]+)"', html):
            data["parking"] = match.group(1)

        return data

    # -------- Parse Flats --------

    def _parse_flats(self, page: Page, jk: JK, jk_html: str) -> list[Flat]:
        """Парсинг списка квартир"""
        logger.info("Парсинг квартир")

        layouts_flats = self._extract_flats_from_layouts(jk_html, jk)
        layouts_by_id = {f.id: f for f in layouts_flats}
        logger.info(f"Из layouts извлечено: {len(layouts_flats)} квартир")

        page.goto(self.flats_url, timeout=self.config.TIMEOUT)
        page.wait_for_load_state("networkidle")

        if "Нет подходящих объявлений" in page.content():
            logger.warning("Объявления не найдены, возвращаем layouts")
            return layouts_flats

        flats = []
        page_num = 1
        max_pages = 50

        while page_num <= max_pages:
            logger.info(f"Парсинг страницы {page_num}")
            page_flats = self._parse_flats_page(page, jk, layouts_by_id)
            flats.extend(page_flats)

            next_btn = page.query_selector('[data-name="Pagination"] [class*="next"], a[rel="next"]')
            if not next_btn or not next_btn.is_visible():
                break

            next_btn.click()
            page.wait_for_load_state("networkidle")
            page_num += 1

        logger.info(f"Детальный парсинг {len(flats)} квартир...")
        for i, flat in enumerate(flats):
            try:
                self._enrich_flat_details(page, flat, jk)
                logger.info(f"  [{i+1}/{len(flats)}] {flat.id}: {flat.address}")
            except Exception as e:
                logger.warning(f"  [{i+1}/{len(flats)}] {flat.id}: ошибка - {e}")

        logger.info(f"Всего квартир: {len(flats)}")
        return flats

    def _extract_flats_from_layouts(self, html: str, jk: JK) -> list[Flat]:
        """Извлечение квартир из layouts JSON"""
        pattern = (
            r'\{"roomCount":"([^"]+)","offerUrl":"([^"]+)","houseName":"([^"]+)",'
            r'"finishDate":"([^"]+)","totalArea":"([^"]+)","priceDisplay":"[^"]+",'
            r'"price":(\d+),[^}]*"offerId":(\d+),"layoutImageUrl":"([^"]+)"\}'
        )

        flats = []
        for match in re.findall(pattern, html):
            room_count, url, house_name, finish_date, area_str, price, offer_id, image_url = match

            building = parse_building_from_house_name(house_name)
            address = f"{jk.address}, {building}" if building else jk.address

            flat = Flat(
                id=offer_id,
                url=decode_url(url),
                rooms=parse_rooms(room_count),
                area=parse_area(area_str),
                floor=0,
                floors_total=0,
                price=int(price),
                address=address,
                year_built=jk.year_built,
                house_status=finish_date,
                images=[decode_url(image_url)] if image_url else [],
            )
            flats.append(flat)

        return flats

    def _parse_flats_page(self, page: Page, jk: JK, layouts_by_id: dict) -> list[Flat]:
        """Парсинг квартир на одной странице"""
        html = page.content()
        valid_ids = self._extract_valid_flat_ids(html, jk.id)
        logger.info(f"Квартир этого ЖК: {len(valid_ids)}")

        cards = page.query_selector_all('[data-name="LinkArea"]')
        if not cards:
            cards = page.query_selector_all('article[data-name="CardComponent"]')
        logger.info(f"Найдено карточек: {len(cards)}")

        flats = []
        for card in cards:
            if flat := self._parse_flat_card(card, jk, layouts_by_id, valid_ids):
                flats.append(flat)

        return flats

    def _extract_valid_flat_ids(self, html: str, jk_id: str) -> set:
        """ID квартир, принадлежащих этому ЖК"""
        pattern = r'"cianId":(\d+)[^}]*?"parentId":(\d+)'
        return {cian_id for cian_id, parent_id in re.findall(pattern, html) if parent_id == jk_id}

    def _parse_flat_card(self, card, jk: JK, layouts_by_id: dict, valid_ids: set) -> Flat | None:
        """Парсинг одной карточки квартиры"""
        try:
            url, flat_id = self._extract_flat_url_and_id(card)
            if not flat_id or (valid_ids and flat_id not in valid_ids):
                return None

            title_text = self._get_card_text(card)
            rooms, area, floor, floors_total, price = self._parse_card_data(card, title_text)

            if area == 0 or price == 0:
                return None

            layout = layouts_by_id.get(flat_id)
            address, house_status, images, year_built = self._merge_with_layout(
                card, title_text, layout, jk
            )

            return Flat(
                id=flat_id,
                url=url,
                rooms=rooms,
                area=area,
                floor=floor,
                floors_total=floors_total,
                price=price,
                address=address,
                year_built=year_built,
                house_status=house_status,
                images=images,
            )
        except Exception as e:
            logger.debug(f"Ошибка парсинга карточки: {e}")
            return None

    def _extract_flat_url_and_id(self, card) -> tuple[str, str]:
        """Извлечение URL и ID квартиры из карточки"""
        link = card.query_selector('a[href*="/flat/"]') or card.query_selector('a[href*="sale/flat"]')
        if not link:
            return "", ""

        url = link.get_attribute("href") or ""
        if url.startswith("/"):
            url = f"https://www.cian.ru{url}"

        match = re.search(r'/flat/(\d+)', url)
        return url, match.group(1) if match else ""

    def _get_card_text(self, card) -> str:
        """Получение текста карточки"""
        title_el = card.query_selector('[data-name="LinkArea"]') or card
        return title_el.inner_text() if title_el else ""

    def _parse_card_data(self, card, text: str) -> tuple[int, float, int, int, int]:
        """Парсинг основных данных из карточки"""
        rooms = parse_rooms(text)

        area = 0.0
        if match := re.search(r'(\d+[,.]?\d*)\s*м²', text):
            area = float(match.group(1).replace(',', '.'))

        floor = floors_total = 0
        if match := re.search(r'(\d+)\s*[/из]+\s*(\d+)\s*эт', text):
            floor, floors_total = int(match.group(1)), int(match.group(2))

        price = self._parse_price(card, text)
        return rooms, area, floor, floors_total, price

    def _parse_price(self, card, text: str) -> int:
        """Извлечение цены"""
        text = text.replace('\u00a0', ' ')
        if match := re.search(r'([\d\s]+)\s*₽', text):
            price_str = match.group(1).replace(' ', '')
            if price_str.isdigit():
                return int(price_str)

        price_el = card.query_selector('[data-mark="MainPrice"]')
        if price_el:
            price_text = price_el.inner_text().replace('\u00a0', ' ')
            if match := re.search(r'([\d\s]+)', price_text):
                price_str = match.group(1).replace(' ', '')
                if price_str.isdigit():
                    return int(price_str)
        return 0

    def _merge_with_layout(self, card, text: str, layout: Flat | None, jk: JK) -> tuple:
        """Объединение данных карточки с данными из layouts"""
        # Адрес
        address = None
        if addr_el := card.query_selector('[data-name="AddressItem"]'):
            address = addr_el.inner_text().strip()
        if not address and layout:
            address = layout.address
        if not address:
            address = jk.address

        # Статус
        house_status = None
        text_lower = text.lower()
        if "дом сдан" in text_lower:
            house_status = "Сдан"
        elif "строится" in text_lower:
            house_status = "Строится"
        if not house_status and layout:
            house_status = layout.house_status
        if not house_status:
            house_status = jk.status
        house_status = normalize_status(house_status)

        # Изображения и год
        images = layout.images if layout else []
        year_built = layout.year_built if layout and layout.year_built else jk.year_built

        return address, house_status, images, year_built

    def _enrich_flat_details(self, page: Page, flat: Flat, jk: JK) -> None:
        """Загрузка детальной страницы квартиры"""
        page.goto(flat.url, timeout=self.config.TIMEOUT)
        page.wait_for_load_state("networkidle")
        html = page.content()

        # Адрес из разных источников
        building = None
        for pattern in [
            r'<meta[^>]*name="description"[^>]*content="[^"]*ул\.\s*Шекспира,\s*(\d+к\d+)',
            r'<title>[^<]*ул\.\s*Шекспира,\s*(\d+к\d+)',
        ]:
            if match := re.search(pattern, html):
                building = match.group(1)
                break

        if not building:
            if match := re.search(r'"house":\{"id":\d+,"name":"([^"]+)"', html):
                building = parse_building_from_house_name(match.group(1))

        if building:
            flat.address = f"{jk.address}, {building}"

        # Фото
        if match := re.search(r'"photos":\[([^\]]+)\]', html):
            urls = re.findall(r'"fullUrl":\s*"([^"]+)"', match.group(1))
            if urls:
                flat.images = [decode_url(u) for u in urls]


# ============ MAIN ============

def main() -> dict:
    """Запуск парсера"""
    parser = BristolParser()
    result = parser.parse()
    return result.model_dump(mode="json")


def run_loop() -> None:
    """Запуск в цикле"""
    config = Config()
    logger.info(f"Запуск в цикле. Интервал: {config.LOOP_INTERVAL} сек")

    while True:
        try:
            data = main()
            logger.info(f"Данные получены: {data['flats_count']} квартир")
        except Exception as e:
            logger.error(f"Ошибка: {e}")

        logger.info(f"Следующий запуск через {config.LOOP_INTERVAL} сек")
        time.sleep(config.LOOP_INTERVAL)


if __name__ == "__main__":
    if Config.LOOP_ENABLED:
        run_loop()
    else:
        print(json.dumps(main(), ensure_ascii=False, indent=2))
