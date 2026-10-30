"""Presentación de consultas históricas autorizadas del reporte."""
from flask import render_template, request, url_for

from app.services import sales_history_service


def page(kind='all', key=None):
    view = sales_history_service.detail(kind, key, request.args.to_dict())
    filters = {name: view[name] for name in ('tab', 'start', 'end', 'q')}
    # Fixed local destination: never accept a caller-provided return URL.
    view['back'] = url_for('pages.report_sales', **filters)
    route = 'pages.report_sales_history' if kind == 'all' else 'pages.report_sales_history_detail'
    route_args = {} if kind == 'all' else {'kind': kind, 'key': key}
    view['prev'] = url_for(route, **route_args, **filters, page=view['page'] - 1) if view['page'] > 1 else None
    view['next'] = url_for(route, **route_args, **filters, page=view['page'] + 1) if view['page'] < view['pages'] else None
    return render_template('reports/history.html', view=view, reference_mode=False, pro_shell=True,
                           movements_live=True)
