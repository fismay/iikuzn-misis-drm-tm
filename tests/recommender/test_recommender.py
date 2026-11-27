"""Contract, filtering, graph evidence and cutoff regression checks."""
from copy import deepcopy
import json
import math
import unittest
from reco_mvp.recommender import Recommender
CUTOFF = '2026-06-01T00:00:00Z'

def bundle():
    customers = [{'customer_id': cid, 'segment': seg, 'known_since': None} for cid, seg in [('query', 'B2B'), ('u1', 'B2B'), ('u2', 'B2B'), ('u3', 'B2C'), ('u4', 'unknown')]]
    products = [{'product_id': pid, 'name': f'Товар {pid}', 'category': None, 'eligible_segments': None, 'active': True, 'available_from': None} for pid in ('A', 'B', 'C', 'D', 'E', 'F')]
    tx = []
    histories = {'query': 'A', 'u1': 'ABC', 'u2': 'ABC', 'u3': 'DE', 'u4': 'D'}
    for cid, pids in histories.items():
        for pid in pids:
            tx.append({'transaction_id': str(len(tx)), 'order_id': None, 'customer_id': cid, 'product_id': pid, 'purchased_at': '2026-05-01T00:00:00Z', 'quantity': 1.0})
    return {'schema_version': '1.0', 'data_mode': 'synthetic', 'customers': customers, 'products': products, 'transactions': tx, 'metadata': {'observation_end': '2026-07-01T00:00:00Z', 'warnings': [], 'segment_source': 'synthetic', 'catalog_history_available': False}}

def fit(data=None, **config):
    return Recommender(config).fit(data or bundle(), as_of=CUTOFF)

def ids(response):
    return [r['product_id'] for r in response['recommendations']]

class RecommenderTests(unittest.TestCase):

    def test_graph_support_ranking_sources_and_contract(self):
        result = fit().recommend('query', strategy='graph_only')
        assert ids(result) == ['B', 'C']
        assert result.keys() == {'schema_version', 'customer_id', 'segment', 'strategy', 'as_of', 'data_mode', 'recommendations', 'warnings'}
        for rank, rec in enumerate(result['recommendations'], 1):
            assert rec.keys() == {'product_id', 'product_name', 'rank', 'score', 'reason_type', 'reason_text', 'source_product_ids', 'support_count', 'is_fallback'}
            assert rec['rank'] == rank
            assert rec['source_product_ids'] == ['A']
            assert rec['support_count'] == 2
            self.assertAlmostEqual(rec['score'], 2 / math.sqrt(3 * 2))
            assert rec['reason_type'] == 'co_purchase'
            assert rec['is_fallback'] is False
            assert '2 клиентов' in rec['reason_text']
        json.dumps(result, allow_nan=False)

    def test_future_transactions_do_not_change_recommendations(self):
        for strategy in ['popular', 'graph_only', 'hybrid']:
            with self.subTest(strategy=strategy):
                original = bundle()
                changed = deepcopy(original)
                for i in range(40):
                    cid = f'future{i}'
                    changed['customers'].append({'customer_id': cid, 'segment': 'B2B', 'known_since': CUTOFF})
                    for pid in ('A', 'F'):
                        changed['transactions'].append({'transaction_id': f'future{i}{pid}', 'order_id': None, 'customer_id': cid, 'product_id': pid, 'quantity': 1.0, 'purchased_at': CUTOFF if i == 0 else '2026-06-02T00:00:00Z'})
                assert fit(original).recommend('query', strategy=strategy) == fit(changed).recommend('query', strategy=strategy)
                model = fit(changed)
                assert 'future0' not in model._segments
                assert 'F' not in model._buyers['all']
                assert 'F' not in model._history.get('future0', ())

    def test_all_strategies_filter_purchases_and_catalog_even_in_fallback(self):
        for strategy in ['popular', 'graph_only', 'hybrid']:
            with self.subTest(strategy=strategy):
                data = bundle()
                data['products'][1]['active'] = False
                data['products'][2]['eligible_segments'] = ['B2C']
                data['products'][3]['available_from'] = '2026-06-02T00:00:00Z'
                result = fit(data).recommend('query', strategy=strategy, k=10)
                assert not set(ids(result)) & {'A', 'B', 'C', 'D', 'F'}
                assert ids(result) == ([] if strategy == 'graph_only' else ['E'])

    def test_support_is_union_not_sum_and_repeat_rows_do_not_inflate_it(self):
        data = bundle()
        data['transactions'].append({'transaction_id': 'queryB', 'order_id': 'another', 'customer_id': 'query', 'product_id': 'B', 'purchased_at': '2026-05-02T00:00:00Z', 'quantity': 9})
        duplicate = deepcopy(data['transactions'][1])
        duplicate['transaction_id'] = 'repeat'
        data['transactions'].append(duplicate)
        result = fit(data).recommend('query', strategy='graph_only')
        assert ids(result) == ['C']
        rec = result['recommendations'][0]
        assert rec['source_product_ids'] == ['A', 'B']
        assert rec['support_count'] == 2
        self.assertAlmostEqual(rec['score'], 2 / math.sqrt(3 * 2))

    def test_cold_start_and_popularity_fallback_flags(self):
        model = fit()
        assert ids(model.recommend('new', strategy='graph_only')) == []
        popular = model.recommend('new', segment='B2B', strategy='popular', k=4)
        assert ids(popular) == ['A', 'B', 'C', 'D']
        assert [r['is_fallback'] for r in popular['recommendations']] == [False, False, False, True]
        hybrid = model.recommend('new', segment='B2B', strategy='hybrid')
        assert all((r['is_fallback'] for r in hybrid['recommendations']))
        assert all((not r['source_product_ids'] for r in hybrid['recommendations']))
        assert hybrid['recommendations'][0]['support_count'] == 3

    def test_unknown_customer_cannot_receive_restricted_b2b_product(self):
        data = bundle()
        for p in data['products']:
            if p['product_id'] in {'A', 'B', 'C'}:
                p['eligible_segments'] = ['B2B']
        assert ids(fit(data).recommend('new')) == ['D', 'E']

    def test_global_graph_used_only_when_segment_pair_is_unreliable(self):
        data = bundle()
        data['customers'][2]['segment'] = 'B2C'
        model = fit(data)
        result = model.recommend('query', strategy='graph_only')
        assert ids(result) == ['B', 'C']
        assert all((r['is_fallback'] and r['support_count'] == 2 for r in result['recommendations']))
        assert any(('всем сегментам' in w for w in result['warnings']))
        assert ids(fit(data, min_support=3).recommend('query', strategy='graph_only')) == []

    def test_empty_segment_catalog_shortfall_and_unknown_active_warnings(self):
        data = bundle()
        data['customers'][-1]['segment'] = ''
        data['products'][3]['active'] = None
        for p in data['products']:
            if p['product_id'] not in {'D', 'E'}:
                p['active'] = False
        result = fit(data).recommend('new', segment='')
        assert result['segment'] == 'unknown'
        assert ids(result) == ['D', 'E']
        assert any(('выдано 2 из 3' in w for w in result['warnings']))
        assert any(('active неизвестен' in w for w in result['warnings']))
        assert any(('совместимость не подтверждена' in w for w in result['warnings']))

    def test_aware_offsets_and_cutoff_boundary(self):
        data = bundle()
        data['transactions'] = [dict(r, purchased_at='2026-06-01T03:00:00+03:00') for r in data['transactions']]
        assert ids(fit(data).recommend('new')) == []
        data['transactions'][0]['purchased_at'] = '2026-06-01T02:59:59+03:00'
        data['products'][0]['available_from'] = '2026-06-01T03:00:00+03:00'
        assert ids(fit(data).recommend('new')) == ['A']

    def test_known_segment_conflict_rejected_and_ids_preserved(self):
        model = fit()
        with self.assertRaisesRegex(ValueError, 'противоречит'):
            model.recommend('query', segment='B2C')
        with self.assertRaisesRegex(ValueError, 'противоречит'):
            model.recommend('u4', segment='B2B')
        assert model.recommend('0001')['customer_id'] == '0001'
        assert model.recommend('query', k=0)['recommendations'] == []

    def test_invalid_k(self):
        for k in [-1, 1.5, True, '3', None]:
            with self.subTest(k=k):
                with self.assertRaisesRegex(ValueError, 'k должен'):
                    fit().recommend('query', k=k)

    def test_invalid_strategy_config_timestamp_and_unfitted_call(self):
        with self.assertRaisesRegex(ValueError, 'strategy'):
            fit().recommend('query', strategy='random')
        with self.assertRaisesRegex(ValueError, 'Сначала'):
            Recommender().recommend('query')
        for value in (0, -2, 1.5, True):
            with self.assertRaisesRegex(ValueError, 'min_support'):
                Recommender({'min_support': value})
        with self.assertRaisesRegex(ValueError, 'часовой пояс'):
            Recommender().fit(bundle(), as_of='2026-06-01')

    def test_determinism_input_isolation_and_refit_replaces_state(self):
        data = bundle()
        model = fit(data)
        expected = model.recommend('query')
        shuffled = deepcopy(data)
        for field in ('customers', 'products', 'transactions'):
            shuffled[field].reverse()
        assert expected == fit(shuffled).recommend('query')
        data['products'][1]['name'] = 'mutated'
        data['products'][1]['eligible_segments'] = []
        result = model.recommend('query')
        result['recommendations'][0]['source_product_ids'].clear()
        assert model.recommend('query') == expected
        empty = bundle()
        empty['transactions'] = []
        model.fit(empty, as_of=CUTOFF)
        assert ids(model.recommend('query')) == []

    def test_shared_synthetic_smoke(self):
        from reco_mvp.data import make_synthetic_bundle
        data = make_synthetic_bundle()
        model = fit(data)
        for strategy in ('popular', 'graph_only', 'hybrid'):
            for cid in [c['customer_id'] for c in data['customers']] + ['unseen']:
                response = model.recommend(cid, strategy=strategy)
                assert len(set(ids(response))) == len(ids(response)) <= 3
                assert not set(ids(response)) & model._history.get(cid, frozenset())
                assert not {'P06', 'P14', 'P15'} & set(ids(response))
                for row in response['recommendations']:
                    assert set(row['source_product_ids']) <= model._history.get(cid, frozenset())
                json.dumps(response, allow_nan=False)
if __name__ == '__main__':
    unittest.main()
