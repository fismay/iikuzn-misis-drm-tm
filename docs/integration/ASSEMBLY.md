# Сборка и запуск

Для чистой копии:

```sh
git clone https://github.com/fismay/iikuzn-misis-drm-tm.git
cd iikuzn-misis-drm-tm
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e ".[ranker]"   # extra нужен только для обучаемого ранжировщика
python -m pytest tests -q
python -m app.run_pipeline --synthetic --output artifacts/demo
python -m streamlit run app/streamlit_app.py -- --artifact-dir artifacts/demo
```

Для реальных файлов: `python -m app.run_pipeline --data-dir data/raw --mapping data/local_mapping.json --as-of 2026-06-01T00:00:00Z --horizon-days 30 --output artifacts/real`. Дату необходимо выбрать по фактическому периоду данных. Файлы и результаты с идентификаторами клиентов не загружать в Git. Если отсутствует модуль — проверьте установку зависимостей и пакета. Если окно не покрыто данными — выберите более раннюю дату.

Книга организаторов (листы «Продажи»/«Клиенты») для официального протокола читается напрямую режимом `--official`, без предварительного преобразования:

```sh
curl -o data/raw/Mindbox.yml.gz https://store.softline.ru/feeds/Mindbox.yml.gz
python -m app.run_pipeline --workbook data/raw/source.xlsx --official \
  --feed data/raw/Mindbox.yml.gz --output artifacts/demo
```

Команда воспроизводит baseline'ы организаторов, считает эвристику и обучаемый ранжировщик (требуется extra `ranker`; без него — `--skip-ltr`), пишет `artifacts/demo/official/official_metrics.json` и `ranker.pkl`. Streamlit подхватывает таблицу автоматически. Подробности — в [docs/evaluation/organizers_protocol.md](../evaluation/organizers_protocol.md).

Синтетический и внутренний реальныx-режимы (`--synthetic`, `--data-dir`) остаются на внутреннем протоколе — он отличается от протокола организаторов и не подменяет его.
