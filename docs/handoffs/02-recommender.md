# Contributor 2 — передача рекомендателя

## База и статус

Bootstrap: `2f2dbe47b82b0860e0eb43ecfd8dbecc138e19d5` (проверен комментарий git archive
в переданном ZIP). Реализованы только файлы зоны участника 2. Отдельная история Git
не создавалась: архив не содержит .git. Commit, push и PR из этой среды не
выполнены: `git ls-remote` завершается `Could not resolve host: github.com`, gh
не установлен. При доступном Git публиковать только `feature/02-recommender`, PR в main;
не сливать PR самостоятельно. Доставочный пакет содержит patch и скрипт публикации,
который создаёт отдельный настоящий clone и проверяет происхождение bootstrap.

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

В доступном Python 3.12.16 нет pytest, а загрузка зависимостей недоступна из-за
сети. Поэтому тесты написаны как unittest.TestCase: их также собирает pytest,
и не требуется менять согласованные зависимости.

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

В обычной установленной среде:

```bash
python -m pytest tests/recommender -q
```

Эта команда и весь pytest tests здесь **не выполнялись**. Для локального запуска
без установленного пакета используйте PYTHONPATH=src и Python 3.11+.

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
evaluation/app не заявляется. Пакет проверен относительно указанного bootstrap,
а не актуальных удалённых веток; после восстановления сети проверить origin/main,
выполнить общий integration smoke в отдельном checkout и сообщить фактический SHA.
Очень широкие истории клиентов требуют профилирования квадратичного построения
пар. Запросов на изменение чужих файлов и контракта нет.
