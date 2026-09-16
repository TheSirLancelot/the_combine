"""The hand-built web front end.

Streamlit got this project from nothing to useful and its limits are all in one
place: the phone. Its layout is desktop-first, its tables scroll sideways off a
small screen, and every interaction is a full script re-run. This package is the
same pipeline behind a small Starlette app that renders real HTML, so a page is
a page and a phone gets a page built for a phone.

Nothing here computes anything. Every number still comes from `combine.pipeline`
and the two front ends are two views of one answer, which is the only way they
can be trusted to agree.
"""
