"""Private implementation package for :mod:`app.modules.proxy.service`.

`service.py` remains the public compatibility façade for `ProxyService`.
Domain-specific implementation parts live here so the large proxy service can
be decomposed incrementally without changing external imports.
"""
