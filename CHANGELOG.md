# Changelog

## [1.0.2](https://github.com/Soju06/codex-lb/compare/v1.0.1...v1.0.2) (2026-02-18)


### Bug Fixes

* **proxy:** strip forwarded identity headers before upstream ([#78](https://github.com/Soju06/codex-lb/issues/78)) ([9d39486](https://github.com/Soju06/codex-lb/commit/9d394868ba8970809ed836e255bf59ece69e85fb))

## [1.0.1](https://github.com/Soju06/codex-lb/compare/v1.0.0...v1.0.1) (2026-02-18)


### Bug Fixes

* **deps:** add brotli for upstream response decompression ([#77](https://github.com/Soju06/codex-lb/issues/77)) ([52026f2](https://github.com/Soju06/codex-lb/commit/52026f28a1d54069ca9cfa30eea99aee383340e5))


### Documentation

* standardize logo sizes and alignment in README client section ([7e53625](https://github.com/Soju06/codex-lb/commit/7e536252ab10a3cc69349665d70a7fc3107a04c4))
* update README to enhance client logo visibility and improve layout ([2b9851a](https://github.com/Soju06/codex-lb/commit/2b9851afe36889e4ba5211a69d5a6dc19f80716c))

## [1.0.0](https://github.com/Soju06/codex-lb/compare/v0.6.0...v1.0.0) (2026-02-18)


### ⚠ BREAKING CHANGES

* hard-cut migration to Alembic replaces all prior schema history; legacy weeklyToken* API key fields removed; React SPA replaces Jinja dashboard; static MODEL_CATALOG replaced by dynamic upstream model registry with plan-aware routing.

### Features

* password auth, API keys, React frontend, Alembic migrations, dynamic model registry ([#68](https://github.com/Soju06/codex-lb/issues/68)) ([35eb981](https://github.com/Soju06/codex-lb/commit/35eb9817cbd81878ee0dd5ed286094ab76eb189a))


### Bug Fixes

* **proxy:** prevent API key reservation leak on stream retry and compact errors ([#74](https://github.com/Soju06/codex-lb/issues/74)) ([592d47b](https://github.com/Soju06/codex-lb/commit/592d47b3df7b0e8c830d531b5625dcccb9c3f919))

## [0.6.0](https://github.com/Soju06/codex-lb/compare/v0.5.2...v0.6.0) (2026-02-10)


### Features

* **api:** OpenAI compatibility layers for Responses support ([#56](https://github.com/Soju06/codex-lb/issues/56)) ([3e95eb1](https://github.com/Soju06/codex-lb/commit/3e95eb134fc6066c6891830d6dd62a876b4526ee))
* **dashboard:** refactor load path and usage refresh ([#59](https://github.com/Soju06/codex-lb/issues/59)) ([899de74](https://github.com/Soju06/codex-lb/commit/899de74e48c8bace2fbbac92a0f9f6b5c699d15f))
* TOTP AUTH FOR WEB PANEL ([#61](https://github.com/Soju06/codex-lb/issues/61)) ([d05df1e](https://github.com/Soju06/codex-lb/commit/d05df1e6f658f6397c2ddaf7c0297814722839f0)), closes [#62](https://github.com/Soju06/codex-lb/issues/62)


### Documentation

* add dwnmf as a contributor for code, and test ([#63](https://github.com/Soju06/codex-lb/issues/63)) ([26bd133](https://github.com/Soju06/codex-lb/commit/26bd1334e727129a0e51168e222753ce485c737e))
* **openspec:** add context docs policy ([#57](https://github.com/Soju06/codex-lb/issues/57)) ([8a491f8](https://github.com/Soju06/codex-lb/commit/8a491f88637d3b4eb28e24aa5063f495350ecca1))

## [0.5.2](https://github.com/Soju06/codex-lb/compare/v0.5.1...v0.5.2) (2026-02-04)


### Bug Fixes

* **docker:** default data dir in containers ([#52](https://github.com/Soju06/codex-lb/issues/52)) ([e065f80](https://github.com/Soju06/codex-lb/commit/e065f804a8cc1c9ddb1e1076de169c833d8640a6))

## [0.5.1](https://github.com/Soju06/codex-lb/compare/v0.5.0...v0.5.1) (2026-02-03)


### Bug Fixes

* **core:** support gzip/deflate request decompression ([#49](https://github.com/Soju06/codex-lb/issues/49)) ([1db79aa](https://github.com/Soju06/codex-lb/commit/1db79aaef8d65af4b9246fad2b0687be17daba6b))


### Documentation

* add choi138 as a contributor for code, bug, and test ([#50](https://github.com/Soju06/codex-lb/issues/50)) ([80d5aae](https://github.com/Soju06/codex-lb/commit/80d5aaefd5c61ea420fda90744e8ffda69eaecf6))

## [0.5.0](https://github.com/Soju06/codex-lb/compare/v0.4.0...v0.5.0) (2026-01-29)


### Features

* **db:** add configurable pool settings ([#44](https://github.com/Soju06/codex-lb/issues/44)) ([e2e553d](https://github.com/Soju06/codex-lb/commit/e2e553debfac1ab51c691a883b16812db6acdd9e))
* **proxy:** add v1 chat and models endpoints ([#39](https://github.com/Soju06/codex-lb/issues/39)) ([c242304](https://github.com/Soju06/codex-lb/commit/c242304304583821afebb9e2c0b2803012d4a7aa))


### Bug Fixes

* **accounts:** update upsert for duplicate email ([#35](https://github.com/Soju06/codex-lb/issues/35)) ([5f68773](https://github.com/Soju06/codex-lb/commit/5f6877342d81abca82e800dbf0b21458e78cb1d9))
* **core:** support zstd request decompression and modularize middleware ([#42](https://github.com/Soju06/codex-lb/issues/42)) ([d0eebb7](https://github.com/Soju06/codex-lb/commit/d0eebb7b9c8c16b1a1293279db42633ba75b1867))
* **proxy:** use short-lived sessions for streaming ([#38](https://github.com/Soju06/codex-lb/issues/38)) ([cb48757](https://github.com/Soju06/codex-lb/commit/cb48757bfbf66d3fb2598523d66c6b5bda44a55d))
* **usage:** coalesce refresh requests ([#36](https://github.com/Soju06/codex-lb/issues/36)) ([04d8fab](https://github.com/Soju06/codex-lb/commit/04d8fab891236e4d4b6bb46c5219730acbabd822))


### Documentation

* add hhsw2015 as a contributor for maintenance ([#43](https://github.com/Soju06/codex-lb/issues/43)) ([1651968](https://github.com/Soju06/codex-lb/commit/1651968e2c8605190fe8647c755f2ab97a7db3d3))

## [0.4.0](https://github.com/Soju06/codex-lb/compare/v0.3.1...v0.4.0) (2026-01-26)


### Features

* **proxy:** add v1 responses compatibility for OpenCode ([#28](https://github.com/Soju06/codex-lb/issues/28)) ([04d58d2](https://github.com/Soju06/codex-lb/commit/04d58d2430e4ba88f28e9e811f08b628e9a4674c))


### Bug Fixes

* **dashboard:** remove rounding in avgPerHour calculation ([#29](https://github.com/Soju06/codex-lb/issues/29)) ([b432939](https://github.com/Soju06/codex-lb/commit/b432939d6ea832d917658dfdbcb935f88f9e08a6)), closes [#26](https://github.com/Soju06/codex-lb/issues/26)


### Documentation

* add hhsw2015 as a contributor for code, and test ([#31](https://github.com/Soju06/codex-lb/issues/31)) ([a1f0e79](https://github.com/Soju06/codex-lb/commit/a1f0e796e45862e520953f60716d2b5eaab3a0d9))
* add opencode setup guide ([#32](https://github.com/Soju06/codex-lb/issues/32)) ([9330619](https://github.com/Soju06/codex-lb/commit/93306198902e558e6bce89719d7cd6b1e797ddc5))
* add pcy06 as a contributor for doc ([#34](https://github.com/Soju06/codex-lb/issues/34)) ([506b7b1](https://github.com/Soju06/codex-lb/commit/506b7b160b11b558533fafb39793870ceefd9131))

## [0.3.1](https://github.com/Soju06/codex-lb/compare/v0.3.0...v0.3.1) (2026-01-22)


### Documentation

* add Quack6765 as a contributor for design ([7a5ec08](https://github.com/Soju06/codex-lb/commit/7a5ec084b9a8d32c844127739f826a5f83bf1440))
* update .all-contributorsrc ([14ea9da](https://github.com/Soju06/codex-lb/commit/14ea9da361a978a56c4d1f7facefe789193c7b91))
* update README.md ([f283d60](https://github.com/Soju06/codex-lb/commit/f283d60ae359585cd128a965ca6fba2a14249a11))

## [0.3.0](https://github.com/Soju06/codex-lb/compare/v0.2.0...v0.3.0) (2026-01-21)


### Features

* add cached input tokens handling and update related metrics in … ([5bf6609](https://github.com/Soju06/codex-lb/commit/5bf66095b8000ffc8fbdf8d989f60171604f69d3))
* add cached input tokens handling and update related metrics in logs and usage schemas ([c965036](https://github.com/Soju06/codex-lb/commit/c9650367c1a2d14e63e3440788b7cd44b08ebd9a))
* add formatting for cached input tokens metadata in metrics display ([53feaa6](https://github.com/Soju06/codex-lb/commit/53feaa62f7c5c282508f37c3fd42d9af655c2fa9))
* add secondary usage tracking and selection logic for accounts in load balancer ([d66cf69](https://github.com/Soju06/codex-lb/commit/d66cf69b2834b42fefbbfa646d82477f9832fdda))
* add ty type checking and refactors ([41fa811](https://github.com/Soju06/codex-lb/commit/41fa8112ba9b900ffa5dbee3a39d94267e2caa75))
* **app:** add migrations and reasoning effort support ([9eae590](https://github.com/Soju06/codex-lb/commit/9eae5903a08363291e397f983a531ddf325658d7))
* implement dashboard settings for sticky threads and reset preferences ([cd04812](https://github.com/Soju06/codex-lb/commit/cd0481247f0ceffdd92173ea84773960e52a7253))


### Bug Fixes

* **app:** tune sqlite pragmas and usage UI ([a44a4fd](https://github.com/Soju06/codex-lb/commit/a44a4fd6fe5771282a12ee62a34c9be819254322))
* **app:** update effort display format in history ([0796740](https://github.com/Soju06/codex-lb/commit/0796740ab570cf476b2285a615559a9a6318082f))
* **app:** update effort display format to include parentheses ([6fbae96](https://github.com/Soju06/codex-lb/commit/6fbae960f393ff92cae0feb614ca0e811a855851))
* **dashboard:** fallback primary remaining to summary ([02b3d39](https://github.com/Soju06/codex-lb/commit/02b3d39c2b734271af7c420fc52b7e87350177e1))
* **db:** avoid leaked async connection in migration ([9aa1d03](https://github.com/Soju06/codex-lb/commit/9aa1d0395481a96a21db2d0add18ee1753f183b2))
* **db:** use returning for dml checks ([4ec7c7a](https://github.com/Soju06/codex-lb/commit/4ec7c7a6615e6e5852b0865e09184544f09ebedc))
* **ui:** style and label settings checkboxes ([722cad8](https://github.com/Soju06/codex-lb/commit/722cad851706e2784815dad4069902cc95b3f662))


### Documentation

* expand 0.2.0 changelog ([32148dc](https://github.com/Soju06/codex-lb/commit/32148dc2d195cec0dd85f61fc0a13d8cbef24e24))

## [0.2.0](https://github.com/Soju06/codex-lb/compare/v0.1.5...v0.2.0) (2026-01-19)


### Features

* add ty type checking and pre-commit hook
* add health response schema and typed context cleanup


### Bug Fixes

* normalize stored plan types (pro/team/business/enterprise/edu) so accounts no longer show as unknown
* prevent rate-limit status when usage is below 100% by using cooldown/backoff and primary-window quota checks
* surface per-account quota reset times by applying primary/secondary reset windows with fallbacks


### Refactor

* move auth/usage helpers into module boundaries and extract proxy helpers
* tighten typing across services and tests

## [0.1.5](https://github.com/Soju06/codex-lb/compare/v0.1.4...v0.1.5) (2026-01-14)


### Bug Fixes

* align rate-limit backoff and reset handling ([4d59650](https://github.com/Soju06/codex-lb/commit/4d596508e5ad13e68aa6e64f9cb32324bd38f07b))

## [0.1.4](https://github.com/Soju06/codex-lb/compare/v0.1.3...v0.1.4) (2026-01-13)


### Bug Fixes

* **db:** harden session cleanup on cancellation ([dee3916](https://github.com/Soju06/codex-lb/commit/dee3916efa83dedec1d5ad43e1e14950b8c6e4a7))

## [0.1.3](https://github.com/Soju06/codex-lb/compare/v0.1.2...v0.1.3) (2026-01-12)


### Documentation

* use absolute image URLs for PyPI ([5fa65a5](https://github.com/Soju06/codex-lb/commit/5fa65a572980f356738f49be3adf2c62fdc38466))

## [0.1.2](https://github.com/Soju06/codex-lb/compare/v0.1.1...v0.1.2) (2026-01-12)


### Bug Fixes

* sync package __version__ ([3dd97e6](https://github.com/Soju06/codex-lb/commit/3dd97e6397a8ea9d3528c166d1e729936f98f737))

## [0.1.1](https://github.com/Soju06/codex-lb/compare/v0.1.0...v0.1.1) (2026-01-12)


### Bug Fixes

* address lint warnings ([7c3cc06](https://github.com/Soju06/codex-lb/commit/7c3cc06c9a6a9a9a8895c1dd5fcc57b3c0eebdb3))
* reactivate accounts when secondary quota clears ([58a4263](https://github.com/Soju06/codex-lb/commit/58a42630d644559f96f045a96c25d0126810542e))
* skip project install in docker build ([64e9156](https://github.com/Soju06/codex-lb/commit/64e9156075c256ef48c0587ea1abb7cc092b97a5))


### Documentation

* add dashboard hero and accounts view ([3522654](https://github.com/Soju06/codex-lb/commit/3522654fe5d09adbe32895d4b24e8b00faac9dfe))

## [0.1.0](https://github.com/Soju06/codex-lb/releases/tag/v0.1.0) (2026-01-07)


### Bug Fixes

* address lint warnings ([7c3cc06](https://github.com/Soju06/codex-lb/commit/7c3cc06c9a6a9a8895c1dd5fcc57b3c0eebdb3))
* skip project install in docker build ([64e9156](https://github.com/Soju06/codex-lb/commit/64e9156075c256ef48c0587ea1abb7cc092b97a5))
