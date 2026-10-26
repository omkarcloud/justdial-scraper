"""The 6 Justdial endpoints. Every path is served with and without the
`/justdial` prefix, so code generated against the hosted API on RapidAPI
(paths like /companies/details) runs unchanged against this server.

Params are validated by the marshmallow schemas in justdial/schemas.py (ONE
param per input: `company` takes a business ID, a justdial.com business link
or a DT short link; `query` takes a category name, free text, a category ID
or a justdial.com category link); unknown params are rejected."""
import json
from urllib.parse import urlencode

from bottle import request, response, route

from schema_fields import load_query
from scraper_errors import BadRequest, NotFound
from justdial import api, schemas


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The request query as unicode strings (bottle 0.12's .get() hands back
    latin-1 decoded bytes, so getunicode is mandatory)."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def _page_link(path, params, page):
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "")}
    query["page"] = page
    scheme, host = request.urlparts.scheme, request.urlparts.netloc
    return f"{scheme}://{host}{path}?{urlencode(query)}"


def paginate(result, path, params):
    """Lift the `pagination` block into the flat shape the hosted API
    returns: count / per_page / current_page / total_pages / next / previous."""
    pagination = result.pop("pagination", None) or {}
    page = int(pagination.get("page") or 1)
    total_pages = pagination.get("total_pages")
    has_next = pagination.get("has_next")
    if has_next is None:
        has_next = bool(total_pages) and page < total_pages
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": _page_link(path, params, page + 1 if has_next else None),
        "previous": _page_link(path, params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def call(label, schema_cls, impl, paginated=False):
    """Validate, run, map errors: bad params -> 400, missing entity -> 404,
    transport / blocks -> 500."""
    raw = query_dict()
    data, error = load_query(schema_cls, raw)
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"justdial rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Exception as e:                 # retries exhausted / blocked
        return json_response({"error": f"justdial {label} failed: {e}"}, 500)
    if paginated:
        result = paginate(result, request.path, raw)
    return json_response(result)


ENDPOINTS = [
    ("/companies/details", schemas.CompanySchema, api.company_details, False),
    ("/categories/suggest", schemas.CategoriesSuggestSchema, api.categories_suggest, False),
    ("/companies/search", schemas.CompaniesSearchSchema, api.companies_search, True),
    ("/companies/phone", schemas.CompanySchema, api.company_phone, False),
    ("/companies/reviews", schemas.CompanyReviewsSchema, api.company_reviews, True),
    ("/companies/photos", schemas.CompanySchema, api.company_photos, False),
]


def mount(path, schema_cls, impl, paginated):
    """Serve a handler at /path and /justdial/path."""
    label = path.strip("/")

    def handler():
        return call(label, schema_cls, impl, paginated)

    handler.__name__ = "justdial_" + label.replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/justdial" + path, method="GET")(handler)


for _path, _schema, _impl, _paginated in ENDPOINTS:
    mount(_path, _schema, _impl, _paginated)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
