"""The /justdial/* endpoint functions (route_glue's route_table calls them
with the schema's loaded kwargs).

Caching (cache.cached_call, cache_config TTLs):

  * companies/search keeps ONE search index per (query, city, area, sort,
    filters) — page 1's rows plus the ordered list of up to 100 docids the
    site hands out with page 1. Every later page is sliced from that order
    (10 docids -> the listing API), so page 3 never reshuffles under the
    caller even though the site shuffles its page 1 on every call.
  * companies/details and companies/phone slice ONE cached page parse
    (justdial.company keyed on the docid): the phone endpoint reads the
    number printed on the page and only calls "Show Number" (callallocate)
    for a free listing that prints none.
  * reviews / photos / suggest cache their own responses.
"""
import math

from cache import cached_call
from justdial import cache_config as ttl
from justdial import parsers, refs
from justdial.fetch import (fetch_call_allocate, fetch_detail, fetch_listing_api, fetch_photos,
                            fetch_ratings_api, fetch_results, fetch_reviews_page, fetch_suggest,
                            resolve_short_link)

PAGE_SIZE = 10          # the site's own page size (listing API, reviews)
MAX_RESULTS = 100       # the site hands out at most 100 docids per search


# ---- categories/suggest -------------------------------------------------------

def _suggest(query, city):
    results = parsers.parse_suggest(fetch_suggest(query, city), city)
    return {"query": query, "city": city, "count": len(results), "results": results}


def categories_suggest(**data):
    return cached_call("justdial.suggest", data, _suggest, ttl.SUGGEST_CACHE)


# ---- companies/search ---------------------------------------------------------

def _filters(sort, top_rated, open_now):
    """The listing API's attribute_values list for the chosen sort / filters."""
    attributes = []
    if sort:
        attributes.append({"e": "100", "v": [sort]})
    if top_rated:
        attributes.append({"e": "6", "v": ["HR"]})
    if open_now:
        attributes.append({"e": "10", "v": ["OPEN"]})
    return attributes


def _search_index(text, ncatid, city, area, sort, top_rated, open_now):
    """Page 1 + the docid order. Plain searches come from the server-rendered
    data route (every exit answers it); a sort or filter only exists in the
    listing API, so those go there from page 1 on."""
    attributes = _filters(sort, top_rated, open_now)
    if ncatid and not text:
        # a bare category id (nct-…): the id alone drives the listing, but
        # the site echoes the `search` slug in its titles, so learn the
        # category's name first (one cheap page, cached with the index)
        text = parsers.parse_search(fetch_results(city, "category", ncatid))["query"] or "category"
    # the `search` path segment: "<text>[-in-<Area>]"
    search = refs.search_slug(text, area)
    if not attributes:
        return parsers.parse_search(fetch_results(city, search, ncatid))
    if not ncatid:
        # a sorted / filtered free-text search still needs the site's own
        # resolution of the term (category id when it matches one)
        page = parsers.parse_search(fetch_results(city, search))
        ncatid = (page["category"] or {}).get("id")
        text = page["query"] or text
    page = parsers.parse_listing_api(
        fetch_listing_api(city, refs.slug(text or ""), ncatid, area=area, page=1, attributes=attributes), city)
    page["area"] = area
    return page


def _search_page(text, ncatid, city, area, sort, top_rated, open_now, page):
    """Page N (N > 1): the N-th 10-docid slice of the index, through the
    listing API. Past the end -> an empty page."""
    index = _index(text, ncatid, city, area, sort, top_rated, open_now)
    docids = index["docids"][(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    if not docids:
        return {"results": []}
    category_id = (index["category"] or {}).get("id") or ncatid
    search_term = refs.slug(index["query"] or text or "")
    data = fetch_listing_api(city, search_term, category_id, area=area, page=page,
                             docids=",".join(docids), search_id=index["search_id"],
                             attributes=_filters(sort, top_rated, open_now))
    return {"results": parsers.parse_listing_api(data, city)["results"]}


def _index(text, ncatid, city, area, sort, top_rated, open_now):
    key = {"text": text, "ncatid": ncatid, "city": city, "area": area, "sort": sort,
           "top_rated": top_rated, "open_now": open_now}
    return cached_call("justdial.search_index", key, _search_index, ttl.SEARCH_CACHE)


def companies_search(query, city=None, area=None, page=1, sort=None, top_rated=None, open_now=None):
    text, ncatid = query.get("text"), query.get("ncatid")
    city = query.get("city") or city
    area = query.get("area") or area
    top_rated, open_now = bool(top_rated), bool(open_now)
    index = _index(text, ncatid, city, area, sort, top_rated, open_now)
    docids = index["docids"]
    total = min(max(len(docids), len(index["results"])), MAX_RESULTS)
    if page == 1:
        results = index["results"]
    else:
        key = {"text": text, "ncatid": ncatid, "city": city, "area": area, "sort": sort,
               "top_rated": top_rated, "open_now": open_now, "page": page}
        results = cached_call("justdial.search_page", key, _search_page, ttl.SEARCH_CACHE)["results"]
    return {
        "query": index["query"] or text,
        "city": index["city"] or city,
        "area": index["area"] or area,
        "title": index["title"],
        "category": index["category"],
        "is_free_text": index["is_free_text"],
        "sort": sort.lower() if sort else "relevance",
        "results": results,
        "pagination": {
            "page": page,
            "items_per_page": PAGE_SIZE,
            "total_pages": max(1, math.ceil(total / PAGE_SIZE)) if total else 1,
            "total_count": total,
        },
    }


# ---- the shared business record -------------------------------------------------

def _docid(company):
    """A loaded `company` ref -> the docid (a DT short link costs one redirect)."""
    if refs.is_short_code(company):
        return cached_call("justdial.short_link", {"code": company}, resolve_short_link, ttl.COMPANY_CACHE)
    return company


def _company_record(docid):
    return parsers.parse_detail(fetch_detail(docid))


def _record(docid):
    return cached_call("justdial.company", {"docid": docid}, _company_record, ttl.COMPANY_CACHE)


def company_details(company):
    """Everything on the public business page."""
    record = dict(_record(_docid(company)))
    record.pop("phone_allocation_id", None)
    return record


# ---- companies/phone ----------------------------------------------------------------

def _phone(docid):
    record = _record(docid)
    contact = record.get("contact") or {}
    phone = contact.get("phone")
    source = "listing"
    alternates = []
    if not phone and record.get("phone_allocation_id"):
        revealed = parsers.parse_call_allocate(
            fetch_call_allocate(record["phone_allocation_id"], (record.get("category") or {}).get("id")))
        phone, alternates, source = revealed["phone"], revealed["alternates"], "show_number"
    return {
        "id": record["id"],
        "name": record["name"],
        "link": record["link"],
        "phone": phone,
        "alternate_phones": alternates,
        "whatsapp": contact.get("whatsapp"),
        "landline": contact.get("landline"),
        "toll_free": contact.get("toll_free"),
        "source": source if phone else None,
    }


def company_phone(company):
    return cached_call("justdial.phone", {"docid": _docid(company)}, _phone, ttl.PHONE_CACHE)


# ---- companies/reviews --------------------------------------------------------------

def _reviews(docid, page, sort):
    if sort in (None, "po"):
        parsed = parsers.parse_reviews_page(fetch_reviews_page(docid, page))
    else:
        parsed = parsers.parse_reviews_api(fetch_ratings_api(docid, sort, page), docid)
        record = _record(docid)
        parsed["company"] = {"id": record["id"], "name": record["name"], "rating": record["rating"],
                             "ratings_count": record["ratings_count"]}
    total = parsed["total_count"]
    reviews = parsed["reviews"]
    if total is None:
        total = len(reviews) if page == 1 else None
    pagination = {"page": page, "items_per_page": PAGE_SIZE}
    if total is None:
        pagination.update({"has_next": len(reviews) >= PAGE_SIZE, "total_pages": None, "total_count": None})
    else:
        pagination.update({"total_pages": max(1, math.ceil(total / PAGE_SIZE)), "total_count": total})
    return {
        "company": parsed["company"],
        "sort": {"po": "relevant", "re": "latest", "hl": "high_to_low"}.get(sort, "relevant"),
        "summary": parsed["summary"],
        "recent_ratings": parsed["recent_ratings"],
        "reviews": reviews,
        "pagination": pagination,
    }


def company_reviews(company, page=1, sort="po"):
    return cached_call("justdial.reviews", {"docid": _docid(company), "page": page, "sort": sort or "po"},
                       _reviews, ttl.REVIEWS_CACHE)


# ---- companies/photos ---------------------------------------------------------------

def _photos(docid):
    return parsers.parse_photos(fetch_photos(docid))


def company_photos(company):
    return cached_call("justdial.photos", {"docid": _docid(company)}, _photos, ttl.PHOTOS_CACHE)
