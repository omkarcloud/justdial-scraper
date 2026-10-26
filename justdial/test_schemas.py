"""Offline tests for the /justdial/* request schemas.

    python -m pytest justdial/test_schemas.py -q
"""
import os
import sys

import pytest
from marshmallow import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from justdial import schemas  # noqa: E402

MAO = "022PXX22.XX22.140804130028.J6U9"


def test_company_schema_accepts_id_link_and_short_link():
    assert schemas.CompanySchema().load({"company": "022pxx22-xx22-140804130028-j6u9"}) == {"company": MAO}
    assert schemas.CompanySchema().load(
        {"company": "https://www.justdial.com/Mumbai/Mao/022PXX22-XX22-140804130028-J6U9_BZDET"}) == {"company": MAO}
    assert schemas.CompanySchema().load({"company": "https://jsdl.in/DT-43RCW5QC"}) == {"company": "DT-43RCW5QC"}


@pytest.mark.parametrize("value", ["", "mao family", "https://example.com/x_BZDET",
                                   "https://www.justdial.com/Mumbai/Restaurants/nct-10408936"])
def test_company_schema_rejects(value):
    with pytest.raises(ValidationError) as err:
        schemas.CompanySchema().load({"company": value})
    assert "company" in err.value.messages


def test_company_schema_rejects_sibling_params():
    with pytest.raises(ValidationError):
        schemas.CompanySchema().load({"company": MAO, "url": "https://www.justdial.com/x"})


def test_reviews_schema():
    loaded = schemas.CompanyReviewsSchema().load({"company": MAO})
    assert loaded == {"company": MAO, "page": 1, "sort": "po"}
    loaded = schemas.CompanyReviewsSchema().load({"company": MAO, "page": "3", "sort": "Latest"})
    assert loaded["page"] == 3 and loaded["sort"] == "re"
    assert schemas.CompanyReviewsSchema().load({"company": MAO, "sort": "high_to_low"})["sort"] == "hl"
    with pytest.raises(ValidationError) as err:
        schemas.CompanyReviewsSchema().load({"company": MAO, "sort": "newest"})
    assert "sort" in err.value.messages
    with pytest.raises(ValidationError):
        schemas.CompanyReviewsSchema().load({"company": MAO, "page": "0"})


def test_search_schema_text_and_city():
    loaded = schemas.CompaniesSearchSchema().load({"query": " Dentists ", "city": "Pune"})
    assert loaded == {"query": {"text": "Dentists", "ncatid": None, "city": None, "area": None},
                      "city": "Pune", "area": None, "page": 1, "sort": None, "top_rated": None, "open_now": None}
    loaded = schemas.CompaniesSearchSchema().load(
        {"query": "nct-10156331", "city": "Delhi", "area": "Saket", "page": "2", "sort": "Rating",
         "top_rated": "yes", "open_now": "0"})
    assert loaded["query"]["ncatid"] == "10156331" and loaded["area"] == "Saket"
    assert loaded["page"] == 2 and loaded["sort"] == "Rating"
    assert loaded["top_rated"] is True and loaded["open_now"] is False
    assert schemas.CompaniesSearchSchema().load({"query": "x", "city": "Pune", "sort": "popular"})["sort"] == "Popular"
    assert schemas.CompaniesSearchSchema().load({"query": "x", "city": "Pune", "sort": "relevance"})["sort"] is None


def test_search_schema_link_carries_city():
    loaded = schemas.CompaniesSearchSchema().load(
        {"query": "https://www.justdial.com/Mumbai/Restaurants-in-Andheri-West/nct-10408936"})
    assert loaded["query"] == {"text": "Restaurants", "ncatid": "10408936", "city": "Mumbai", "area": "Andheri West"}
    assert loaded["city"] is None


def test_search_schema_rejects():
    with pytest.raises(ValidationError) as err:
        schemas.CompaniesSearchSchema().load({"query": "Dentists"})
    assert "city" in err.value.messages
    with pytest.raises(ValidationError):
        schemas.CompaniesSearchSchema().load({"city": "Pune"})
    with pytest.raises(ValidationError) as err:
        schemas.CompaniesSearchSchema().load({"query": "x", "city": "Pune", "page": "11"})
    assert "page" in err.value.messages
    with pytest.raises(ValidationError) as err:
        schemas.CompaniesSearchSchema().load({"query": "x", "city": "Pune", "sort": "distance"})
    assert "sort" in err.value.messages
    with pytest.raises(ValidationError) as err:
        schemas.CompaniesSearchSchema().load({"query": "x", "city": "Pune", "open_now": "maybe"})
    assert "open_now" in err.value.messages
    with pytest.raises(ValidationError):
        schemas.CompaniesSearchSchema().load({"query": "https://example.com/Mumbai/Restaurants/nct-1", "city": "Mumbai"})
    with pytest.raises(ValidationError):
        schemas.CompaniesSearchSchema().load({"query": "x", "city": "Pune", "url": "y"})


def test_suggest_schema():
    assert schemas.CategoriesSuggestSchema().load({"query": "dent", "city": " Delhi "}) == {"query": "dent", "city": "Delhi"}
    with pytest.raises(ValidationError) as err:
        schemas.CategoriesSuggestSchema().load({"query": "dent"})
    assert "city" in err.value.messages
    with pytest.raises(ValidationError):
        schemas.CategoriesSuggestSchema().load({"city": "Delhi"})
