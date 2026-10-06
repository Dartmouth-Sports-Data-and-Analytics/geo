"""Hand-confirmed facts about how each school's athletics site behaves; edit these tables, not the scraping code."""

# Fallback slug spellings, tried after the reference file's own slug.
SPORT_SLUG_ALIASES = {
    # 'mrow' is tried at every school; it only costs an extra 404.
    "mens-rowing": ["mens-heavyweight-rowing", "mens-crew", "mrow"],
    # Men's lightweight rowing uses a different slug at each school.
    "rowing": ["mens-lightweight-rowing", "mlr", "lightweight-rowing"],
    "womens-rowing": ["womens-heavyweight-rowing", "womens-crew"],
    "sailing": ["coed-sailing", "csail", "mens-sailing"],
}


# year Y = the Y-(Y+1) academic year for every sport; spring sports use bare-year URL Y+1, and the stored year always equals the requested year.
SPRING_SEASON_SPORTS = {
    "baseball", "softball", "mens-lacrosse", "womens-lacrosse",
    "mens-rowing", "rowing", "womens-rowing", "womens-lightweight-rowing",
}


# Sports whose bare /roster/{year} URL can silently serve wrong content, so dash-year URLs are tried first.
SKIP_BARE_YEAR_SPORTS = {
    # Rowing: Harvard's bare-year trap; dash-year is native elsewhere.
    "mens-rowing",
    "rowing",
    "womens-rowing",
    "womens-lightweight-rowing",
    "sailing",
    "track-and-field",
    # Same trap as sailing.
    "womens-sailing",
    # Identical names across all years at Harvard.
    "mens-golf",
    # Site-side trap confirmed not to be caused by request pacing.
    "field-hockey",
    "football",
    "mens-soccer",
    "equestrian",
    "womens-rugby",

    # Identical rosters across years at Cornell, Harvard, Penn, Yale; harmless where bare-year already works.
    "mens-basketball",
    "womens-basketball",
    "mens-ice-hockey",
    "womens-ice-hockey",
    "mens-squash",
    "womens-squash",
    "mens-swimming-and-diving",
    "womens-swimming-and-diving",
    "mens-tennis",
    "womens-tennis",
    "mens-track-and-field",
    "womens-track-and-field",
    "womens-golf",
    "skiing",
}


# Per-school override: these pages only return correct history via the bare-year URL, so it is tried first.
SCHOOL_PREFERS_BARE_YEAR = {
    ("Harvard", "football"),
    ("Harvard", "field-hockey"),
    ("Harvard", "mens-soccer"),
    ("Penn", "football"),
    ("Penn", "field-hockey"),
    ("Penn", "mens-soccer"),
    ("Yale", "football"),
    ("Yale", "field-hockey"),
    ("Yale", "mens-soccer"),
}


# Dash-year first up to the given year, bare-year after (the site switched URL schemes; each scheme serves the current team for the other era).
SCHOOL_PREFERS_DASH_YEAR = {
    ("Cornell", "mens-cross-country"): 2023,
    ("Cornell", "womens-cross-country"): 2023,
    ("Yale", "womens-volleyball"): 2021,
}


# For these teams the site names each season's page differently, and the wrong form returns a stale or default roster instead of an error.
# Each list reads "through this season, use only this URL form" ("dash" = 2020-21, "bare" = 2020). Found with probe_year.py or the site's season list.
SCHOOL_FORM_THROUGH = {
    ("Yale", "mens-soccer"): [(2020, "dash")],
    ("Harvard", "mens-rowing"): [(2026, "dash")],
    # Penn men's tennis: 2016-17 is labeled with the plain year 2016, and every season after it is a dash label.
    ("Penn", "mens-tennis"): [(2016, "bare"), (2026, "dash")],
    # Yale: the other form returns the site's default page for these seasons (checked 2020-21 to 2023-24).
    # Yale football 2022-23: the plain-year page keeps 2021-22's class labels (a stale copy); the dash page is the real roster.
    ("Yale", "football"): [(2021, "bare"), (2022, "dash"), (2026, "bare")],
    ("Yale", "mens-cross-country"): [(2023, "bare")],
    ("Yale", "mens-track-and-field"): [(2023, "dash")],
    # Yale volleyball: plain years, then 2020-21 and 2021-22 as dash seasons, then plain years again.
    ("Yale", "womens-volleyball"): [(2019, "bare"), (2021, "dash"), (2026, "bare")],
    ("Yale", "volleyball"): [(2019, "bare"), (2021, "dash"), (2026, "bare")],
}


# Seasons with no fetchable roster page, so they are never requested.
KNOWN_MISSING_SEASONS = {
    # The team page is blank on the site (the players have individual pages), so infer_seasons.py rebuilds this season from 2022 and 2024.
    ("Yale", "mens-golf", 2023),
    ("Harvard", "mens-rowing", 2019),  # the site serves the current roster for this season, so there is no real page
}

# First varsity year per program; earlier years are never fetched and are dropped from disk.
PROGRAM_FIRST_YEAR = {
    ("Princeton", "womens-rugby"): 2022,
    ("Brown", "mens-golf"): 2026,
    ("Brown", "womens-golf"): 2026,
    ("Brown", "mens-squash"): 2026,
    ("Brown", "womens-squash"): 2026,
}


# Escape hatch for pages that return the same roster for every year; currently empty.
CURRENT_SEASON_ONLY = set()


# Combos confirmed by hand as unavailable, skipped before any request; the reason string is for humans only.
KNOWN_UNAVAILABLE = {
    ("Cornell", "skiing"): "confirmed club sport, not varsity",
    ("Cornell", "womens-rugby"): "confirmed club sport, not varsity",
    ("Cornell", "sailing"): "no real separate program behind this slug — see womens-sailing",
    ("Cornell", "womens-golf"): "confirmed not sponsored as a varsity team",

    # Only Harvard and Princeton field women's lightweight rowing.
    ("Brown", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",
    ("Columbia", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",
    ("Cornell", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",
    ("Dartmouth", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",
    ("Penn", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",
    ("Yale", "womens-lightweight-rowing"): "confirmed no women's lightweight rowing program — see womens-rowing",

    # Cornell and Penn have one men's rowing team, already under mens-rowing / mens-crew.
    ("Cornell", "rowing"): "no separate lightweight page — their one men's rowing team is already under mens-rowing",
    ("Penn", "rowing"): "no separate lightweight page — their one men's rowing team is already under mens-crew",
}

# Yale's mens-rowing/mens-crew are swapped vs. other schools, so an override is the only slug tried.
SCHOOL_SLUG_OVERRIDES = {
    ("Yale", "mens-rowing"): "mens-crew",
    ("Yale", "rowing"): "mens-rowing",
}


# Confirmed combined (coed) pages: only the neutral entry is scraped.
KNOWN_COMBINED_PAGE = {
    ("Columbia", "cross-country"),
    ("Columbia", "track-and-field"),
    ("Harvard", "cross-country"),
    ("Harvard", "track-and-field"),
}


# Slugs never to request as-is; currently empty.
BARE_SLUG_NEVER_REAL = set()


# Bare 'rowing' is men's lightweight (its own team), so it is exempt from the neutral-entry cleanup below.
NEUTRAL_ENTRY_CLEANUP_EXEMPT = {"rowing"}
