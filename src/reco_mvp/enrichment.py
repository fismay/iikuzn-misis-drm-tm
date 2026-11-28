"""Обогащение по фиду каталога store.softline.ru (YML) — только для связей.

Фид (``Mindbox.yml.gz``, ~220 МБ в распакованном виде) читается потоково.
Сопоставление артикулов продаж — по ``<vendorCode>`` и по артикулу в ``<url>``
(нормализация: верхний регистр, только буквы/цифры), как инструктирует лист
«Каталог». Товары вне фида получают вендора по узнаваемым токенам названия.

Цены из фида сознательно не используются: они не нужны в задаче и не участвуют
в оценке. Фид — текущий снимок каталога; это потенциальный источник «будущего»
в признаках, что явно указано в docs/model/learned_ranker.md.
"""
from __future__ import annotations

import gzip
import re
import unicodedata
from collections import defaultdict
from xml.etree.ElementTree import iterparse


def parse_feed(gz_path: str) -> tuple[list[dict], dict[str, tuple[str | None, str]]]:
    """Потоково читает YML-фид. Возвращает (offers, categories)."""
    offers: list[dict] = []
    categories: dict[str, tuple[str | None, str]] = {}
    with gzip.open(gz_path, "rb") as fh:
        for _, el in iterparse(fh, events=("end",)):
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "category":
                categories[el.get("id")] = (el.get("parentId"), (el.text or "").strip())
            elif tag == "offer":
                offer = {"id": el.get("id"), "available": el.get("available")}
                for field in ("vendorCode", "name", "vendor", "url", "categoryId"):
                    child = el.find(field)
                    offer[field] = (child.text or "").strip() \
                        if child is not None and child.text else None
                offers.append(offer)
                el.clear()
    return offers, categories


def _norm(value: str | None) -> str:
    return re.sub(r"[^0-9A-ZА-Я]", "", (value or "").upper())


def match_sales_to_feed(offers, categories, sales_rows) -> dict[str, dict]:
    """item_id -> {vendor, cats(имена категории с предками)} или пустой словарь."""
    by_vendor_code: dict[str, dict] = {}
    by_url: dict[str, dict] = {}
    for offer in offers:
        if offer.get("vendorCode"):
            by_vendor_code.setdefault(_norm(offer["vendorCode"]), offer)
        url = offer.get("url") or ""
        tail = re.search(r"/([^/]+)/\d+/?$", url.rstrip("/"))
        if tail:
            by_url.setdefault(_norm(tail.group(1)), offer)

    def category_chain(category_id):
        names = set()
        seen = 0
        while category_id and category_id in categories and seen < 12:
            parent, name = categories[category_id]
            if name:
                names.add(name.lower())
            category_id, seen = parent, seen + 1
        return names

    vendor_names = sorted({(o.get("vendor") or "").strip().lower() for o in offers if o.get("vendor")})
    stop = {"ооо", "оао", "ранее", "группа", "лаборатория", "корпорация", "инк", "кмпани"}
    vend_tokens = []
    for vendor in vendor_names:
        toks = frozenset(t for t in re.findall(r"[а-яa-z0-9]+", vendor)
                         if len(t) > 2 and t not in stop)
        if toks:
            vend_tokens.append(toks)
    token_uniqueness: dict[str, int] = defaultdict(int)
    for toks in vend_tokens:
        for t in toks:
            token_uniqueness[t] += 1

    result: dict[str, dict] = {}
    wanted: dict[str, str] = {}
    for row in sales_rows:
        wanted.setdefault(row["item_id"], str(row.get("sku") or ""))
    for item_id, sku in wanted.items():
        offer = None
        if not item_id.startswith("N-") and sku:
            offer = by_vendor_code.get(_norm(sku)) or by_url.get(_norm(sku))
        vendor = None
        cats: set[str] = set()
        if offer:
            vendor = (offer.get("vendor") or "").strip().lower() or None
            cats = category_chain(offer.get("categoryId"))
        result[item_id] = {"vendor": vendor, "cats": cats}
    # name-based vendor fallback for items absent from the feed
    name_by_item: dict[str, str] = {}
    for row in sales_rows:
        name_by_item.setdefault(row["item_id"], str(row.get("item_name") or ""))
    for item_id, info in result.items():
        if info["vendor"]:
            continue
        text = unicodedata.normalize("NFKC", name_by_item.get(item_id, "")).lower()
        best = None
        for toks in vend_tokens:
            if all(t in text for t in toks) and any(token_uniqueness[t] == 1 for t in toks):
                if best is None or len(toks) > len(best):
                    best = toks
        if best:
            info["vendor"] = " ".join(sorted(best))
    return result
