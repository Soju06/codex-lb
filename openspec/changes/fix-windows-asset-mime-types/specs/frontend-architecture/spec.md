## MODIFIED Requirements

### Requirement: Dashboard serving is compressed, cache-correct, and chart-lazy

Dashboard API and static-asset responses MUST be served gzip-compressed when the client accepts it, while proxy paths MUST NOT pass through a compressing wrapper. Content-hashed assets under `/assets/` MUST be served with immutable year-long `Cache-Control`; `index.html` MUST remain `no-cache`. Chart vendor code MUST NOT load before first paint: it MUST live in an async-only chunk that is neither statically imported by the entry chunk nor modulepreloaded. Static assets MUST be served with their correct web MIME types (`.js`/`.mjs` as `text/javascript`, `.css` as `text/css`, `.svg` as `image/svg+xml`, `.json` as `application/json`, `.woff`/`.woff2` as `font/woff`/`font/woff2`, `.html` as `text/html`) regardless of the host operating system's `mimetypes` registry state, so strict browser MIME checking never rejects dashboard module scripts.

#### Scenario: Assets are compressed and immutable

- **WHEN** a browser requests a hashed asset under `/assets/` with `Accept-Encoding: gzip`
- **THEN** the response is gzip-encoded
- **AND** carries `Cache-Control: public, max-age=31536000, immutable`

#### Scenario: index.html stays fresh across deploys

- **WHEN** the SPA shell is requested
- **THEN** the response carries `Cache-Control: no-cache`

#### Scenario: Proxy streaming paths are never compressed by the dashboard wrapper

- **WHEN** a request targets a proxy path (`/backend-api/*`, `/v1/*`)
- **THEN** the dashboard gzip middleware passes it through untouched

#### Scenario: Ranged asset requests bypass compression

- **WHEN** an asset request carries a `Range` header
- **THEN** the response is served uncompressed with a valid 206 `Content-Range` over unencoded bytes

#### Scenario: Chart vendor code loads lazily

- **WHEN** the built dashboard entry page loads
- **THEN** the recharts chunk is not statically imported by the entry chunk and not modulepreloaded
- **AND** charts render correctly once their async chunk loads

#### Scenario: Module scripts survive a poisoned OS MIME registry

- **GIVEN** the host operating system maps `.js` to `text/plain` in its `mimetypes` sources (e.g. Windows `HKCR` registry entries)
- **WHEN** a browser requests a hashed `.js` asset under `/assets/`
- **THEN** the response `Content-Type` is `text/javascript`
- **AND** the dashboard SPA boots instead of failing strict module MIME checking
