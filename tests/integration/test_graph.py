from app.graph import render_graph


def test_graph_uses_actual_source_and_escapes_names():
    case = {'recommendations': [{'reason_type': 'co_purchase', 'source_product_ids': ['a'], 'product_name': '<script>x</script>', 'support_count': 3, 'is_fallback': False}]}
    svg = render_graph(case, {'a': 'Купленный товар'})
    assert 'Купленный товар' in svg
    assert '&lt;script&gt;' in svg
    assert '<script>' not in svg
    assert 'Подтверждений: 3' in svg


def test_empty_graph():
    assert 'Нет связей' in render_graph({'recommendations': []}, {})
