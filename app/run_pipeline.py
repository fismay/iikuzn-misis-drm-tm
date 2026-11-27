"""Build reproducible artifacts for the recommendation demo."""
import argparse
import json
import subprocess
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--synthetic', action='store_true')
    mode.add_argument('--data-dir')
    p.add_argument('--mapping')
    p.add_argument('--as-of')
    p.add_argument('--horizon-days', type=int, default=30)
    p.add_argument('--output', required=True)
    args = p.parse_args()
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
    evaluate(bundle, as_of=as_of, horizon_days=args.horizon_days, output_dir=str(out / 'evaluation'))
    model = Recommender().fit(bundle, as_of=as_of)
    cases = []
    for customer in bundle['customers']:
        response = model.recommend(customer['customer_id'], k=3, strategy='hybrid')
        cases.append(response)
    cases.append(model.recommend('__demo_new_customer__', segment='B2B', k=3, strategy='hybrid'))
    (out / 'demo_cases.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding='utf-8')
    sha = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True, text=True, check=False).stdout.strip() or None
    manifest = {'schema_version': '1.0', 'data_mode': bundle['data_mode'], 'as_of': as_of, 'horizon_days': args.horizon_days, 'seed': 42 if args.synthetic else None, 'git_sha': sha, 'bundle': 'data/bundle.json', 'metrics': 'evaluation/metrics.json', 'cases': 'demo_cases.json'}
    (out / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Готово: {out}')

if __name__ == '__main__':
    main()
