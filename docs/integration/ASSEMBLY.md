# Сборка и запуск

Для чистой копии:

```sh
git clone https://github.com/fismay/iikuzn-misis-drm-tm.git
cd iikuzn-misis-drm-tm
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install -e .
python -m pytest tests -q
python -m app.run_pipeline --synthetic --output artifacts/demo
python -m streamlit run app/streamlit_app.py -- --artifact-dir artifacts/demo
```

Для реальных файлов: `python -m app.run_pipeline --data-dir data/raw --mapping data/local_mapping.json --as-of 2026-06-01T00:00:00Z --horizon-days 30 --output artifacts/real`. Дату необходимо выбрать по фактическому периоду данных. Файлы и результаты с идентификаторами клиентов не загружать в Git. Если отсутствует модуль — проверьте установку зависимостей и пакета. Если окно не покрыто данными — выберите более раннюю дату.

Книга организаторов с несколькими листами требует предварительного преобразования; этот запуск не воспроизводит их протокол оценки напрямую.
