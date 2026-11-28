"""Build reproducible artifacts for the recommendation demo.

Two modes:
- ``--synthetic`` / ``--data-dir``: project-internal backtest and demo artifacts
  (protocol in docs/evaluation/protocol.md);
- ``--workbook`` + ``--official``: organizers' historical protocol
  (docs/evaluation/organizers_protocol.md), including the optional learned ranker.
"""
import argparse
import json
import subprocess
from pathlib import Path

# Эвристический режим: лучший наблюдаемый результат на внутренних окнах
# (протокол подбора — docs/model/learned_ranker.md). Renewal-буст требует
# product_group в bundle; без него работает прежняя формула.
BEST_CONFIG = {"min_support": 1, "popularity_exponent": 0.2, "recency_weight": 1,
               "recency_days": 120, "renewal_weight": 1, "renewal_tau": 26}


def _official_mode(args):
    from reco_mvp.evaluation.organizers import (
        OfficialDataset, TEST_START, baseline_copurchase, baseline_popularity,
        load_official_workbook, score_recommendations)
    from reco_mvp.model_helper import bundle_from_official
    from reco_mvp.recommender import Recommender
    # артефакты кладём в <output>/official, чтобы Streamlit читал их из корня демо
    out = Path(args.output) / 'official'
    out.mkdir(parents=True, exist_ok=True)
    sales_rows, client_rows = load_official_workbook(args.workbook)
    item_names, item_groups = {}, {}
    for row in sales_rows:
        item_names.setdefault(row["item_id"], row["item_name"])
        item_groups[row["item_id"]] = row["product_group"]
    all_items = sorted({row["item_id"] for row in sales_rows})
    feed_maps = None
    if args.feed:
        from reco_mvp.enrichment import match_sales_to_feed, parse_feed
        offers, categories = parse_feed(args.feed)
        feed_maps = match_sales_to_feed(offers, categories, sales_rows)
    official = OfficialDataset(sales_rows, client_rows)
    result = {"split": {"train_end": official.train_end, "test_start": official.test_start},
              "cohort_sizes": {"A": len(official.group_a), "B": len(official.group_b)},
              "feed_available": bool(feed_maps), "methods": {}}
    for name, fn in [("baseline_popularity", baseline_popularity(official)),
                     ("baseline_copurchase", baseline_copurchase(official))]:
        result["methods"][name] = score_recommendations(official, fn)
    bundle = bundle_from_official(official, item_names, item_groups)
    model = Recommender(dict(BEST_CONFIG)).fit(bundle, as_of=TEST_START + "T00:00:00Z")
    result["methods"]["heuristic"] = score_recommendations(
        official, lambda cid: [x["product_id"] for x in
                               model.recommend(cid, k=3, strategy="hybrid")["recommendations"]])
    if not args.skip_ltr:
        from reco_mvp.recommender.ltr import LtrAgent, INTERNAL_TRAIN_WINDOWS
        internal = [OfficialDataset(sales_rows, client_rows, train_end=te,
                                    test_start=ts, test_end=te2)
                    for te, ts, te2 in INTERNAL_TRAIN_WINDOWS]
        kwargs = {"item_names": item_names, "item_groups": item_groups,
                  "all_items": all_items, "feed_maps": feed_maps}
        agent = LtrAgent(official, **kwargs)
        agent.train(internal, **kwargs)
        agent.save(str(out / "ranker.pkl"))
        result["methods"]["ltr"] = score_recommendations(
            official, lambda cid: agent.recommend(cid, k=3))
        result["ltr_meta"] = agent.meta
    result["disclosures"] = [
        "Каталог и вендорные признаки — текущий снимок фида (может содержать информацию "
        "из будущего); цены не используются.",
        "Ранжировщик обучен только на окнах строго до официального отсечения; официальный "
        "тестовый период не участвовал в подборе модели и гиперпараметров.",
        "Метрики — наблюдение на истории, а не измеренный рост выручки.",
    ]
    (out / "official_metrics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Готово: {out}/official_metrics.json")
    for name, res in result["methods"].items():
        a, b = res["A"]["all"], res["B"]["all"]
        print(f"{name:22s} A P@3 {a['precision_at_3']:.4f} fill {a['fill_rate']:.3f} | "
              f"B P@3 {b['precision_at_3']:.4f} fill {b['fill_rate']:.3f}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--synthetic', action='store_true')
    mode.add_argument('--data-dir')
    mode.add_argument('--workbook', help='книга организаторов .xlsx (листы Продажи/Клиенты)')
    p.add_argument('--mapping')
    p.add_argument('--as-of')
    p.add_argument('--horizon-days', type=int, default=30)
    p.add_argument('--output', required=True)
    p.add_argument('--official', action='store_true',
                   help='официальный протокол организаторов (только с --workbook)')
    p.add_argument('--feed', help='Mindbox.yml.gz для обогащения связями по каталогу')
    p.add_argument('--skip-ltr', action='store_true',
                   help='не обучать ранжировщик (только baseline и эвристика)')
    args = p.parse_args()
    if args.official:
        if not args.workbook:
            p.error('--official доступен только с --workbook')
        _official_mode(args)
        return
    if args.workbook:
        p.error('для книги организаторов укажите --official')
    if not args.synthetic and not args.as_of:
        p.error('--as-of обязателен для реальных данных')
    if args.horizon_days <= 0:
        p.error('--horizon-days должен быть положительным')
    from reco_mvp.data import prepare_dataset
    from reco_mvp.evaluation import evaluate
    from reco_mvp.recommender import Recommender
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    as_of = args.as_of or '2026-06-01T00:00:00Z'
    bundle = prepare_dataset(args.data_dir, str(out / 'data'), synthetic=args.synthetic, mapping_path=args.mapping)
    evaluate(bundle, as_of=as_of, horizon_days=args.horizon_days, output_dir=str(out / 'evaluation'), recommender_config=BEST_CONFIG)
    model = Recommender(BEST_CONFIG).fit(bundle, as_of=as_of)
    cases = []
    for customer in bundle['customers']:
        response = model.recommend(customer['customer_id'], k=3, strategy='hybrid')
        cases.append(response)
    cases.append(model.recommend('__demo_new_customer__', segment='B2B', k=3, strategy='hybrid'))
    (out / 'demo_cases.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding='utf-8')
    sha = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=False).stdout.strip() or None
    manifest = {'schema_version': '1.0', 'data_mode': bundle['data_mode'], 'as_of': as_of, 'horizon_days': args.horizon_days, 'seed': 42 if args.synthetic else None, 'recommender_config': BEST_CONFIG, 'git_sha': sha, 'bundle': 'data/bundle.json', 'metrics': 'evaluation/metrics.json', 'cases': 'demo_cases.json'}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Готово: {out}')


if __name__ == '__main__':
    main()
