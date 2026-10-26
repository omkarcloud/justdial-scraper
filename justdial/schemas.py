"""Marshmallow request schemas for every /justdial/* route.

ONE param per input (tripadvisor QueryOrIdField convention, never a sibling
`url`/`id` pair):

    company   a Justdial business id (022PXX22.XX22.140804130028.J6U9, dots
              or dashes), any justdial.com business link (…_BZDET) or a
              DT-… short link (justdial.com/DT-43RCW5QC, jsdl.in/DT-…)
    query     a category name ("Dentists"), free text ("bademiya"), a bare
              category id (nct-10156331) or a justdial.com category link
              (which carries the city, the area and the category id)
    city      any Indian city Justdial lists ("Mumbai", "Bengaluru", "New
              Delhi"); required unless `query` is a category link

Sort / filter values map to the site's own listing-API tokens (research
2026-10-24: attribute_values e=100 sort, e=6 "HR" rated 3.5+, e=10 "OPEN").
"""
from marshmallow import ValidationError, validates_schema

from schema_fields import BaseSchema, ChoiceField, Flag, PageField, QueryField, RefField, StrippedString
from justdial import refs

MAX_SEARCH_PAGE = 10      # the site serves at most 100 results per search, 10 per page
MAX_REVIEWS_PAGE = 1000   # 10 per page; a busy listing has a few thousand

SEARCH_SORTS = {"relevance": None, "rating": "Rating", "popular": "Popular"}
REVIEW_SORTS = {"relevant": "po", "latest": "re", "high_to_low": "hl"}


class CompanyRefField(RefField):
    resolver = staticmethod(refs.resolve_company)


class QueryRefField(RefField):
    resolver = staticmethod(refs.resolve_query)


class CityField(StrippedString):
    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        if not kwargs["required"]:
            kwargs.setdefault("load_default", None)
        kwargs.setdefault("metadata", {"description": "Indian city as Justdial names it (Mumbai, "
                                                      "Bengaluru, New Delhi, Pune); required unless "
                                                      "query is a category link"})
        super().__init__(**kwargs)


class CategoriesSuggestSchema(BaseSchema):
    query = QueryField(max_length=100, metadata={
        "description": "start of a category name or business name, as typed in Justdial's search box"})
    city = CityField(required=True, metadata={
        "description": "Indian city the suggestions are scoped to (Mumbai, Delhi, Bengaluru, …)"})


class CompaniesSearchSchema(BaseSchema):
    query = QueryRefField(metadata={
        "description": "category name (Dentists), free text (bademiya), a category id "
                       "(nct-10156331) or a justdial.com category link"})
    city = CityField()
    area = StrippedString(required=False, load_default=None, metadata={
        "description": "locality inside the city as Justdial spells it (Andheri West, Koramangala)"})
    page = PageField(max_page=MAX_SEARCH_PAGE, metadata={
        "description": "1-10; Justdial serves at most 100 results per search, 10 per page"})
    sort = ChoiceField(SEARCH_SORTS, load_default=None, metadata={
        "description": "relevance (Justdial's own order, default), rating or popular"})
    top_rated = Flag(metadata={"description": "true = only businesses rated 3.5 and above"})
    open_now = Flag(metadata={"description": "true = only businesses open right now (IST)"})

    @validates_schema
    def _city_required(self, data, **kwargs):
        query = data.get("query") or {}
        if not (data.get("city") or query.get("city")):
            raise ValidationError("city is required unless query is a justdial.com category link.", "city")


class CompanySchema(BaseSchema):
    company = CompanyRefField(metadata={
        "description": "Justdial business id (022PXX22.XX22.140804130028.J6U9), a justdial.com "
                       "business link (…_BZDET) or a DT-… short link"})


class CompanyReviewsSchema(CompanySchema):
    page = PageField(max_page=MAX_REVIEWS_PAGE, metadata={"description": "1-based, 10 reviews per page"})
    sort = ChoiceField(REVIEW_SORTS, load_default="po", metadata={
        "description": "relevant (default), latest or high_to_low"})
