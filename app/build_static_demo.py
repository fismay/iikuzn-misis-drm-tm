"""Publish a static, read-only demo from synthetic recommendation artifacts."""
import json
from pathlib import Path


root = Path(__file__).resolve().parent.parent
artifacts = root / 'artifacts' / 'demo'
bundle = json.loads((artifacts / 'data' / 'bundle.json').read_text())
cases = json.loads((artifacts / 'demo_cases.json').read_text())
official = json.loads((root / 'app' / 'official_metrics_public.json').read_text())
names = {p['product_id']: p['name'] for p in bundle['products']}
transactions = bundle['transactions']
for case in cases:
    case['history'] = [names[t['product_id']] for t in transactions if t['customer_id'] == case['customer_id'] and t['purchased_at'] < case['as_of']]
payload = json.dumps({'cases': cases, 'official': official}, ensure_ascii=False).replace('</', '<\\/')
html = '''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>МИСИС · рекомендации для клиента</title>
<style>
:root{font-family:Inter,system-ui,Arial,sans-serif;color:#172033;background:#f4f6fb}*{box-sizing:border-box}body{margin:0}header{background:#14204a;color:#fff;padding:24px max(24px,calc((100vw - 1120px)/2))}header small{color:#a7c6ff;letter-spacing:.12em;text-transform:uppercase}h1{margin:7px 0;font-size:32px}header p{color:#d7e1fa;margin:5px 0}main{max-width:1120px;margin:28px auto;padding:0 24px}.notice{background:#fff3d2;border:1px solid #f0d37f;border-radius:12px;padding:13px 17px;margin-bottom:24px}.grid{display:grid;grid-template-columns:300px 1fr;gap:24px}.box{background:white;border:1px solid #e1e6f0;border-radius:16px;padding:22px;box-shadow:0 4px 22px #12214a0a}h2{font-size:20px;margin:0 0 17px}h3{font-size:16px;margin:0 0 7px}label{font-size:13px;font-weight:700;color:#45506b}select{width:100%;padding:12px;margin:8px 0 20px;border:1px solid #b9c4dc;border-radius:9px;background:#fff;font-size:15px}.pill{display:inline-block;background:#e7edff;color:#344ea5;padding:5px 9px;border-radius:99px;font-size:12px;font-weight:700;margin:3px}.card{border:1px solid #dfe6f4;border-radius:12px;padding:16px;margin:10px 0;background:#fbfcff}.rank{background:#294ab3;color:white;border-radius:50%;width:28px;height:28px;display:inline-grid;place-items:center;margin-right:8px}.reason{color:#546078;font-size:14px;margin:7px 0 0 36px}.metricgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric{background:#f4f7ff;border-radius:12px;padding:16px}.metric strong{font-size:22px;display:block;color:#1b3d9e}.metric span{font-size:12px;color:#526078}.muted{color:#65718a;font-size:13px}.link{color:#315dc5}footer{padding:30px 0;color:#65718a;font-size:13px}@media(max-width:750px){.grid{grid-template-columns:1fr}.metricgrid{grid-template-columns:1fr 1fr}h1{font-size:26px}}
</style>
<header><small>МИСИС · DRM TM · хакатон</small><h1>Рекомендации в личном кабинете</h1><p>Три товара для следующей покупки с объяснением выбора</p></header>
<main><div class="notice"><strong>Синтетические данные.</strong> Клиенты и покупки в интерактивном примере сгенерированы для проверки сценария. Метрики ниже взяты из отдельного официального бэктеста и не означают прирост продаж.</div>
<div class="grid"><aside class="box"><h2>Сценарий клиента</h2><label for="client">Выберите клиента</label><select id="client"></select><div id="profile"></div><hr style="border:0;border-top:1px solid #e5e9f1;margin:20px 0"><h3>История покупок</h3><div id="history" class="muted"></div></aside><section class="box"><h2>Топ-3 рекомендаций</h2><div id="recommendations"></div></section></div>
<section class="box" style="margin-top:24px"><h2>Результаты официального бэктеста</h2><p class="muted">Precision@3 на отложенном периоде. Группа A: 337 клиентов; группа B: 172 клиента. Fill Rate модели: 100%.</p><div class="metricgrid" id="metrics"></div><p class="muted">Обучение и подбор модели проводились на более ранних данных; тестовый период использован для финальной оценки. Сравнение с базовым методом совместных покупок. <a class="link" href="https://github.com/fismay/iikuzn-misis-drm-tm">Код и методика</a>.</p></section>
<footer>Демонстрационный стенд показывает сохранённые результаты алгоритма на синтетических данных. Для расчёта новых рекомендаций и воспроизведения бэктеста используйте код проекта.</footer></main>
<script id="data" type="application/json">PAYLOAD</script><script>
const data=JSON.parse(document.getElementById('data').textContent), cases=data.cases, select=document.getElementById('client');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
cases.forEach((c,i)=>{const o=document.createElement('option');o.value=i;o.textContent=`Клиент ${i+1} · ${c.segment}`;select.append(o)});
function render(){const c=cases[+select.value||0];document.getElementById('profile').innerHTML=`<span class="pill">${esc(c.segment)}</span><span class="pill">${esc(c.strategy)}</span><p class="muted">Дата отсечения: ${esc(c.as_of.slice(0,10))}</p>`;document.getElementById('history').innerHTML=[...new Set(c.history)].map(x=>`<span class="pill">${esc(x)}</span>`).join('')||'Нет покупок до даты отсечения';document.getElementById('recommendations').innerHTML=c.recommendations.map((r,i)=>`<article class="card"><h3><span class="rank">${i+1}</span>${esc(r.product_name)}</h3><p class="reason">${esc(r.reason_text)}</p><p class="reason">Подтверждений: ${esc(r.support_count)}${r.is_fallback?' · резервная выдача':''}</p></article>`).join('')||'<p>Нет подходящих товаров</p>'}
select.addEventListener('change',render);render();
const m=data.official.methods;document.getElementById('metrics').innerHTML=['A','B'].flatMap(g=>[['LTR',m.ltr],['Совместные покупки',m.baseline_copurchase]].map(([name,src])=>`<div class="metric"><strong>${(src[g].all.precision_at_3*100).toFixed(2)}%</strong><span>${esc(name)} · группа ${g}</span></div>`)).join('');
</script></html>'''.replace('PAYLOAD', payload)
out = root / 'docs' / 'index.html'
out.write_text(html, encoding='utf-8')
print(out)
