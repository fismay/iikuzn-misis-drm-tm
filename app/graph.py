"""Render a small, escaped SVG graph from the actual recommendation explanations."""
from html import escape


def render_graph(case, products):
    recs = case.get('recommendations', [])
    if not recs:
        return '<p>Нет связей для отображения.</p>'
    height = max(160, 40 + 100 * len(recs))
    chunks = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 {height}" role="img" aria-label="Граф причин рекомендаций">',
              '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/></marker></defs>',
              '<style>text{font: 14px sans-serif;fill:#10223a}.sub{font-size:12px;fill:#475569}</style>']
    def text(value, limit=36):
        s = str(value)
        return escape(s if len(s) <= limit else s[:limit-1] + '…', quote=True)
    for i, rec in enumerate(recs):
        y = 20 + i * 100
        source_ids = rec.get('source_product_ids', [])
        source = ', '.join(str(products.get(pid, pid)) for pid in source_ids) if source_ids else ('Популярность сегмента' if rec['reason_type'] == 'segment_popularity' else 'Общая популярность')
        prefix = 'Покупка: ' if source_ids else 'Источник: '
        chunks.extend([f'<rect x="10" y="{y}" width="350" height="65" rx="12" fill="#e8f0ff"/>',
                       f'<text x="24" y="{y+27}">{text(prefix+source)}</text>',
                       f'<text x="24" y="{y+48}" class="sub">{text(rec["reason_type"])}</text>',
                       f'<line x1="365" y1="{y+32}" x2="485" y2="{y+32}" stroke="#64748b" stroke-width="2" marker-end="url(#arrow)"/>',
                       f'<rect x="490" y="{y}" width="390" height="65" rx="12" fill="#e4f7ed"/>',
                       f'<text x="504" y="{y+27}">{text(rec["product_name"], 43)}</text>',
                       f'<text x="504" y="{y+48}" class="sub">Подтверждений: {int(rec["support_count"])}' + (' · резервная выдача' if rec.get('is_fallback') else '') + '</text>'])
    chunks.append('</svg>')
    return ''.join(chunks)
