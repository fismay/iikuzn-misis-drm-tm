"""Local demonstration of recommendation artifacts."""
import argparse
import html
import json
import sys
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components
from app.graph import render_graph
from datetime import datetime

parser = argparse.ArgumentParser()
parser.add_argument('--artifact-dir', required=True)
args = parser.parse_args(sys.argv[1:])
root = Path(args.artifact_dir)
try:
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    bundle = json.loads((root / manifest['bundle']).read_text(encoding='utf-8'))
    metrics = json.loads((root / manifest['metrics']).read_text(encoding='utf-8'))
    cases = json.loads((root / manifest['cases']).read_text(encoding='utf-8'))
except (OSError, KeyError, ValueError) as exc:
    st.error(f'Не удалось прочитать артефакты: {exc}. Сначала запустите pipeline.')
    st.stop()
st.title('Рекомендации в личном кабинете')
st.warning('СИНТЕТИЧЕСКИЕ ДАННЫЕ — проверка работы прототипа' if manifest['data_mode'] == 'synthetic' else 'Реальные данные: локальный просмотр')
st.caption(f"Дата отсечения: {manifest['as_of']} · Окно: {manifest['horizon_days']} дней")
labels = [f'Клиент {i+1} · {case["segment"]}' for i, case in enumerate(cases)]
if not labels:
    st.info('Нет клиентов для демонстрации'); st.stop()
case = cases[labels.index(st.selectbox('Выберите клиента', labels))]
products = {p['product_id']: p['name'] for p in bundle['products']}
history = sorted({t['product_id'] for t in bundle['transactions'] if t['customer_id'] == case['customer_id'] and datetime.fromisoformat(t['purchased_at'].replace('Z', '+00:00')) < datetime.fromisoformat(manifest['as_of'].replace('Z', '+00:00'))})
st.subheader('История покупок')
st.write(', '.join(products.get(x, x) for x in history) or 'Покупок до даты отсечения нет')
st.subheader(f"Рекомендации: {len(case['recommendations'])} из 3")
for r in case['recommendations']:
    st.markdown(f"**{html.escape(str(r['product_name']))}**")
    st.write(r['reason_text'], f"Подтверждений: {r['support_count']}", '· Резервная выдача' if r['is_fallback'] else '')
st.subheader('Почему выбраны эти товары')
components.html(render_graph(case, products), height=max(170, 50 + 100 * len(case['recommendations'])), scrolling=False)
st.subheader('Результаты бэктеста')
st.dataframe(metrics.get('rows', []), hide_index=True)
for warning in list(dict.fromkeys(bundle['metadata'].get('warnings', []) + metrics.get('warnings', []) + case.get('warnings', []))):
    st.caption(str(warning))
