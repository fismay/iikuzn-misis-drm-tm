# Передача интеграции

Пайплайн: `python -m app.run_pipeline --synthetic --output artifacts/demo`. Интерфейс: `python -m streamlit run app/streamlit_app.py -- --artifact-dir artifacts/demo`. Порядок сборки и реальный режим описаны в docs/integration/ASSEMBLY.md.

Проверка выполнена в отдельном checkout с main 2f2dbe47b82b0860e0eb43ecfd8dbecc138e19d5, моделью e23b125ef8f0076d5f7fdf3430ff6e26742cf2d0 и оценкой 427b9e75d8cd16a685cde28ef20df5ac576368ad. Синтетический pipeline завершился успешно. Реальные данные не проверены. Итоговый main требует повторной проверки после слияния зависимостей.

Повторная проверка: 37 тестов прошли в интеграционном checkout; синтетический pipeline завершился. Интерфейс и SVG-граф визуально проверены в браузере на синтетическом клиенте. До слияния PR модели и оценки main остаётся неполным; реальный набор не проверен.
