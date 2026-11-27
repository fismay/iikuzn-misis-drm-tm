# Contributor 2 — передача рекомендателя

## База и статус

Bootstrap: `2f2dbe47b82b0860e0eb43ecfd8dbecc138e19d5`. Рабочая ветка:
`feature/02-recommender`; commit реализации: `38f7ab6e7eaebe1c81d396de39d0ed8465e0a99a`.
Сеть и авторизация GitHub восстановлены; проверено право WRITE. Для продолжения
создан отдельный настоящий clone, ветка отслеживает origin/feature/02-recommender.
Bootstrap является предком HEAD; origin/main на момент проверки совпадает с ним.
Реализованы только файлы зоны участника 2. Ветка предназначена для PR в main;
PR самостоятельно не сливается. Предыдущий ZIP остаётся офлайн-снимком, актуальные
результаты проверки и дальнейшие изменения находятся в этой ветке.

## Изменённые пути

- src/reco_mvp/recommender/__init__.py
- src/reco_mvp/recommender/model.py
- tests/recommender/test_recommender.py
- docs/model/algorithm.md
- docs/handoffs/02-recommender.md

Чужие файлы и корневые зависимости не менялись. Новых зависимостей нет.

## Точный пример

```python
from reco_mvp.data import make_synthetic_bundle
from reco_mvp.recommender import Recommender

bundle = make_synthetic_bundle()
model = Recommender({"min_support": 2}).fit(bundle, as_of="2026-06-01T00:00:00Z")
response = model.recommend("C001", strategy="hybrid", k=3)
assert [r["product_id"] for r in response["recommendations"]] == ["P03", "P04", "P07"]
cold = model.recommend("UNSEEN", segment="B2C", strategy="hybrid")
```

support_count у co_purchase — уникальное объединение покупателей использованных
связей. У popularity — уникальные покупатели товара в соответствующей группе.
У popularity source_product_ids пуст. Общая связь при недостатке сегментной
поддержки помечена is_fallback и предупреждением. Hybrid сохраняет приоритет
graph → segment popularity → global popularity, не сортирует несопоставимые score.
Подробнее: docs/model/algorithm.md.

## Проверки и реальные результаты

После восстановления сети создана изолированная .venv на Python 3.12.16.
Успешно выполнены установка requirements.txt и editable-установка пакета:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -e .
.venv/bin/python -m pytest tests -q
```

Результат: **19 passed in 0.04s** — 14 тестов recommender и 5 тестов data/contracts.
Тесты recommender написаны как unittest.TestCase и собираются pytest без новых
зависимостей. Совместно установлены pandas 2.3.3, openpyxl 3.1.5,
streamlit 1.67.0 и pytest 8.4.2 в рамках согласованных диапазонов.

Выполнено из корня bootstrap checkout:

```bash
PYTHONPATH=src /opt/homebrew/bin/python3.12 -m unittest discover -s tests/recommender -v
```

Результат: **14 тестов прошли**, 0 failures/errors. Параметризованные сценарии
проверяются через subTest. Покрыты будущие покупки и точная граница cutoff,
timezone offset, уникальная поддержка нескольких источников, фильтры каждой
стратегии и fallback, неизвестные клиенты, конфликт сегмента, отсутствие
неподтверждённых связей, детерминированность, изоляция состояния и refit.
Встроенный smoke на общей синтетике проверил 62 клиента (61 из Bundle + неизвестный)
× 3 стратегии = 186 ответов. Для всех проверены отсутствие дубликатов, уже
купленного, будущих/неактивных товаров, действительность источников и JSON
с конечными числами. Это модульная проверка с data, не общий pipeline/evaluator/UI.

Для запуска только своей зоны: `python -m pytest tests/recommender -q`.
Для локального запуска unittest без установленного пакета используйте
PYTHONPATH=src и Python 3.11+.

Измерение на общей синтетике (61 клиент, 15 товаров, 158 строк транзакций):
медиана fit за 20 прогонов **0.160 ms**; 183 recommend (61×3) — **1.567 ms** суммарно,
в среднем **0.0086 ms**. Это маленький синтетический пример, не оценка production
нагрузки. Precision@3 и превосходство над baseline здесь не измерялись.

## Зависимости, ограничения и передача участникам

Участнику 3: fit поддерживает произвольный cutoff и все три стратегии, не читает
файлы. Когорты, горизонт наблюдения, будущие цели и метрики формирует evaluator.
Профиль с known_since=null не является доказательством исторического присутствия.
Участнику 4: строить граф из source_product_ids только для co_purchase; popularity
привязывать к сегменту/общей группе, показывать warnings и fallback. Для вывода
score нельзя подписывать вероятность.

Реальные данные не получены и не проверены. Сквозная сборка отсутствующих
в main evaluation/app не заявляется. Проверена собственная ветка относительно
указанного bootstrap и origin/main. Совместную проверку незамерженных веток
evaluation/app следует проводить в отдельном integration-check checkout.
Очень широкие истории клиентов требуют профилирования квадратичного построения
пар. Запросов на изменение чужих файлов и контракта нет.
