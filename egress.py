"""The one network helper the justdial module needs: the Accept-Language a
session sends. Justdial is an Indian site, so every session announces the
language list Chrome sends on a machine set up in India."""

ACCEPT_LANGUAGE_IN = "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7"


def accept_language(proxy=None, country=None):
    return ACCEPT_LANGUAGE_IN
