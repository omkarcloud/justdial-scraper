"""Cache TTL per /justdial/* endpoint (cache.py, keyed on the validated
params). Justdial is a business directory: listings change by the day,
reviews by the hour on busy pages, but the page-1 result set of a search
is shuffled on every upstream call, so a search's docid order must be
pinned for as long as its pages are served.
"""
from datetime import timedelta

# The search index (page-1 rows + the ordered docid list) and every page
# sliced from it share one TTL so page 3 is cut from the same order page 1
# showed; listings move slowly within a day.
SEARCH_CACHE = timedelta(hours=12)
# One row per docid: the parsed business page (details + phone slice it).
COMPANY_CACHE = timedelta(hours=24)
# A free listing's "Show Number" allocation is a virtual number that
# Justdial keeps for weeks.
PHONE_CACHE = timedelta(days=7)
# Reviews arrive daily on busy listings; a page is cheap to refetch.
REVIEWS_CACHE = timedelta(hours=6)
# Galleries grow slowly (owner uploads, review photos).
PHOTOS_CACHE = timedelta(hours=24)
# Category autosuggest: the category tree is static, popularity drifts.
SUGGEST_CACHE = timedelta(days=7)
