"""Keyless bibliographic adapters ported from sourcefidelity-api (MIT).

Providers: OpenAlex (identity and OA locations), Internet Archive (full text
and lending classification), Wayback (moved or dead cited pages), Lantern
(Media History Digital Library) and BnF Gallica. Each adapter returns identity,
access URLs and a classification; none of them is evidence until the fetched
text passes the page classifier.
"""
