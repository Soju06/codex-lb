# Changelog

## [1.5.1](https://github.com/Soju06/codex-lb/compare/v1.5.0...v1.5.1) (2026-03-13)


### Bug Fixes

* **proxy:** raise timeout defaults and remove getattr anti-pattern ([#193](https://github.com/Soju06/codex-lb/issues/193)) ([77dbc8a](https://github.com/Soju06/codex-lb/commit/77dbc8a123c5ef3db122923d3a80d3e5b5e86ce2))

## [1.5.0](https://github.com/Soju06/codex-lb/compare/v1.4.1...v1.5.0) (2026-03-13)


### Features

* **frontend:** add privacy email blur toggle ([#180](https://github.com/Soju06/codex-lb/issues/180)) ([356edcb](https://github.com/Soju06/codex-lb/commit/356edcbb7f0624e71a10035315b71577c02e73d3))
* **proxy:** add upstream websocket transport control ([#189](https://github.com/Soju06/codex-lb/issues/189)) ([fb6b6cf](https://github.com/Soju06/codex-lb/commit/fb6b6cf616319fc4b72b0200e31499c84cb5c34a))
* **responses:** add websocket transport and request log tracing ([#169](https://github.com/Soju06/codex-lb/issues/169)) ([ceb1746](https://github.com/Soju06/codex-lb/commit/ceb17465d12186e19bff4e9ea984e482dd109f8b))


### Bug Fixes

* **proxy:** decouple stream duration from proxy request budget ([#187](https://github.com/Soju06/codex-lb/issues/187)) ([aa65e97](https://github.com/Soju06/codex-lb/commit/aa65e97d6f9f2c5014e4d032a7d81b3e8af8d618))
* **proxy:** preserve dedicated responses compact contract ([#175](https://github.com/Soju06/codex-lb/issues/175)) ([7442743](https://github.com/Soju06/codex-lb/commit/7442743662c9a6889507d339adebf0388d9761e6))
* **ui:** the label color in the trend does not show on dark mode ([#188](https://github.com/Soju06/codex-lb/issues/188)) ([8e62c4a](https://github.com/Soju06/codex-lb/commit/8e62c4ad724005df414cb7fa06becda00da8e807))


### Documentation

* add flokosti96 as a contributor for code, and test ([#192](https://github.com/Soju06/codex-lb/issues/192)) ([c2b105a](https://github.com/Soju06/codex-lb/commit/c2b105a3e545838e6b791692782c49f767e77647))

## [1.4.1](https://github.com/Soju06/codex-lb/compare/v1.4.0...v1.4.1) (2026-03-12)


### Bug Fixes

* **db:** fail fast on startup schema drift ([#174](https://github.com/Soju06/codex-lb/issues/174)) ([b7086b9](https://github.com/Soju06/codex-lb/commit/b7086b9f79f63d99d103ba6bf952f97b20137bb4))
* **proxy:** add sticky session controls and cleanup ([#176](https://github.com/Soju06/codex-lb/issues/176)) ([1116b3f](https://github.com/Soju06/codex-lb/commit/1116b3f73c54161b55e99dbd66cba1a189d67197))
* **proxy:** canonicalize additional quota routing ([#182](https://github.com/Soju06/codex-lb/issues/182)) ([b33264f](https://github.com/Soju06/codex-lb/commit/b33264f8d44f8619d8ba0fcbf763f064390ec1e3))


### Documentation

* add defin85 as a contributor for bug, and test ([#184](https://github.com/Soju06/codex-lb/issues/184)) ([ecad9e4](https://github.com/Soju06/codex-lb/commit/ecad9e4ae3c0346b9f5dad5fb59f00146f5aa2d9))

## [1.4.0](https://github.com/Soju06/codex-lb/compare/v1.3.2...v1.4.0) (2026-03-11)


### Features

* **proxy:** bound request latency across proxy paths ([#178](https://github.com/Soju06/codex-lb/issues/178)) ([3ca7124](https://github.com/Soju06/codex-lb/commit/3ca71249b20971f0f9d3ab86fe45d8d5bbf2ccaa))


### Bug Fixes

* **proxy:** route gated models by additional usage ([#173](https://github.com/Soju06/codex-lb/issues/173)) ([73bf90c](https://github.com/Soju06/codex-lb/commit/73bf90cc477628e780a95c5e22c09406f3d7c62d))

## [1.3.2](https://github.com/Soju06/codex-lb/compare/v1.3.1...v1.3.2) (2026-03-10)


### Bug Fixes

* **db:** add migration to normalize postgresql enum value casing ([#170](https://github.com/Soju06/codex-lb/issues/170)) ([e597fd6](https://github.com/Soju06/codex-lb/commit/e597fd6af983481acfdbe489bbd73bb39a2d6b7c))

## [1.3.1](https://github.com/Soju06/codex-lb/compare/v1.3.0...v1.3.1) (2026-03-10)


### Bug Fixes

* **proxy:** avoid refresh blocking and dedupe stale refreshes ([#162](https://github.com/Soju06/codex-lb/issues/162)) ([3b2fbd5](https://github.com/Soju06/codex-lb/commit/3b2fbd526711dee3eb09a60321a8972fe33baefd))
* **proxy:** decouple usage refresh from request selection ([#155](https://github.com/Soju06/codex-lb/issues/155)) ([dddd961](https://github.com/Soju06/codex-lb/commit/dddd961555727fa529b16750bc65eea49e6bbef8))
* safe line rendering, additional quotas relocation, and screenshot updates ([#166](https://github.com/Soju06/codex-lb/issues/166)) ([a1c788d](https://github.com/Soju06/codex-lb/commit/a1c788d612860c23eafe75a75d5ebdba5dc3ef52))


### Documentation

* add defin85 as a contributor for code ([#168](https://github.com/Soju06/codex-lb/issues/168)) ([703a2c9](https://github.com/Soju06/codex-lb/commit/703a2c92fb97fa408f057c8152dca805177d9fa1))

## [1.3.0](https://github.com/Soju06/codex-lb/compare/v1.2.0...v1.3.0) (2026-03-10)


### Features

* additional rate limits (Spark quotas), EWMA depletion indicator, and quotas UI ([#151](https://github.com/Soju06/codex-lb/issues/151)) ([13cc1ce](https://github.com/Soju06/codex-lb/commit/13cc1cee7ac19c032e9ffbdef820d02b4e400573))
* **db:** optimize SQLite startup and query paths ([#145](https://github.com/Soju06/codex-lb/issues/145)) ([316e9b6](https://github.com/Soju06/codex-lb/commit/316e9b69ee250d4b1af84eb360d297f7e99b932d))
* **proxy:** add upstream request tracing ([#144](https://github.com/Soju06/codex-lb/issues/144)) ([c530d24](https://github.com/Soju06/codex-lb/commit/c530d248dd268abb0466ddba55abbc8176c99dbb))


### Bug Fixes

* **proxy:** add request logging to compact and transcribe paths ([#153](https://github.com/Soju06/codex-lb/issues/153)) ([368853a](https://github.com/Soju06/codex-lb/commit/368853a87efaede5cd8ae826fb67f6dd7c5fc8f6))
* **proxy:** align compact retry account header after refresh ([#150](https://github.com/Soju06/codex-lb/issues/150)) ([b7aaef0](https://github.com/Soju06/codex-lb/commit/b7aaef03901fcf618a1dcded2aa6b19ef4c863bd))
* **proxy:** match Codex CLI compact timeout defaults ([#160](https://github.com/Soju06/codex-lb/issues/160)) ([799791c](https://github.com/Soju06/codex-lb/commit/799791cd4bb52211bfd442aa9334a845a4d65014))
* **proxy:** preserve v1 prompt cache affinity ([#161](https://github.com/Soju06/codex-lb/issues/161)) ([855c92e](https://github.com/Soju06/codex-lb/commit/855c92e03810c5adf9cf476325e41df22991a37a))
* **proxy:** scope codex session routing affinity ([#143](https://github.com/Soju06/codex-lb/issues/143)) ([28411b2](https://github.com/Soju06/codex-lb/commit/28411b2ef8a913eb92f13146cb7882921904045d))
* **proxy:** skip error backoff for transient upstream 5xx errors ([#152](https://github.com/Soju06/codex-lb/issues/152)) ([9819c0b](https://github.com/Soju06/codex-lb/commit/9819c0babb3796659ed86b62d673a8172cf185d7))


### Documentation

* add aaiyer as a contributor for bug, code, and test ([#149](https://github.com/Soju06/codex-lb/issues/149)) ([270d152](https://github.com/Soju06/codex-lb/commit/270d152fb017b1d8df1a732c19afca29b128c57b))
* **agents:** remove invalid deployment topology ([165d221](https://github.com/Soju06/codex-lb/commit/165d2216ddcacda237180c3c8dd81bff80225d14))
* **readme:** update opencode provider setup ([064efd9](https://github.com/Soju06/codex-lb/commit/064efd905b118e69b23a59eea2214c0c716f5083))

## [1.2.0](https://github.com/Soju06/codex-lb/compare/v1.1.1...v1.2.0) (2026-03-08)


### Features

* add manual OAuth callback URL paste for remote server deployments ([#136](https://github.com/Soju06/codex-lb/issues/136)) ([7651336](https://github.com/Soju06/codex-lb/commit/7651336a4ab867e06784f6b307666e5488dab259))
* enforce model/effort per API key and add real usage+cost visibility in settings; fixes; layout ([#135](https://github.com/Soju06/codex-lb/issues/135)) ([f014136](https://github.com/Soju06/codex-lb/commit/f014136fc9cf3c63cf6a1567c7f7f0967fb9af7a))
* **proxy:** support service_tier forwarding ([#137](https://github.com/Soju06/codex-lb/issues/137)) ([8bde95a](https://github.com/Soju06/codex-lb/commit/8bde95a33445149a4310a71f10d494d1c62bf7fc))


### Bug Fixes

* **app-header:** apply desktop nav pill classes to NavLink ([#133](https://github.com/Soju06/codex-lb/issues/133)) ([c6b801e](https://github.com/Soju06/codex-lb/commit/c6b801e3e5c8ce90326f6c145c8914d1f036fe0e))
* **proxy:** finalize v1 responses non-stream reservations ([#146](https://github.com/Soju06/codex-lb/issues/146)) ([a8ebe6c](https://github.com/Soju06/codex-lb/commit/a8ebe6cd6612417d90750b9c72d0046875bc1f1d))
* **proxy:** preserve v1 response reasoning output ([#138](https://github.com/Soju06/codex-lb/issues/138)) ([0327279](https://github.com/Soju06/codex-lb/commit/032727968628610617b72925d7c76f68c9c8ef67))
* **usage:** avoid deactivating accounts on usage 403 ([#147](https://github.com/Soju06/codex-lb/issues/147)) ([fec1256](https://github.com/Soju06/codex-lb/commit/fec1256010ffb0b7318e9eef933345b0fcd6023a))


### Documentation

* add mws-weekend-projects as a contributor for code, and test ([#141](https://github.com/Soju06/codex-lb/issues/141)) ([7cbb181](https://github.com/Soju06/codex-lb/commit/7cbb181da441ec38251b9d370fe5c1d6050cd921))
* add quangdo126 as a contributor for code, and test ([#142](https://github.com/Soju06/codex-lb/issues/142)) ([b44f63d](https://github.com/Soju06/codex-lb/commit/b44f63d16b984ad7c420607aa65711f16c63bb21))
* add xCatalitY as a contributor for code, and test ([#139](https://github.com/Soju06/codex-lb/issues/139)) ([c68231b](https://github.com/Soju06/codex-lb/commit/c68231bdfbd5ed5ebef7ed394981318505f8969b))

## [1.1.1](https://github.com/Soju06/codex-lb/compare/v1.1.0...v1.1.1) (2026-03-03)


### Bug Fixes

* **responses:** strip unsupported safety_identifier before upstream ([#130](https://github.com/Soju06/codex-lb/issues/130)) ([528e7fd](https://github.com/Soju06/codex-lb/commit/528e7fd85152f8e6f39c5551b5ae085e90935356))

## [1.1.0](https://github.com/Soju06/codex-lb/compare/v1.0.4...v1.1.0) (2026-03-02)


### Features

* **codex-review:** add re-review loop with convergence termination ([a4e0832](https://github.com/Soju06/codex-lb/commit/a4e08326ebe8e5431d9a012e4608e75811add0c6))
* **db:** adopt timestamp alembic revisions with auto remap ([#123](https://github.com/Soju06/codex-lb/issues/123)) ([57e840c](https://github.com/Soju06/codex-lb/commit/57e840c37e9327726ddf9fc5acad10a0e12b670e))
* migrate firewall module and React dashboard page ([#84](https://github.com/Soju06/codex-lb/issues/84)) ([a35348a](https://github.com/Soju06/codex-lb/commit/a35348a0e5b1b40c573aa24aaf866b7e74dd4042))
* **proxy:** add transcription compatibility routes ([#111](https://github.com/Soju06/codex-lb/issues/111)) ([0b591df](https://github.com/Soju06/codex-lb/commit/0b591df57989b74004a345cb2ced630b8241b9f2))


### Bug Fixes

* **app-routing:** add routing strategy setting and fix true round-robin runtime rotation ([#100](https://github.com/Soju06/codex-lb/issues/100)) ([df4cceb](https://github.com/Soju06/codex-lb/commit/df4cceb695e20d629d2b2655e547ccff4df87fae))
* **oauth-ui:** start device polling immediately after device start ([#108](https://github.com/Soju06/codex-lb/issues/108)) ([faf3535](https://github.com/Soju06/codex-lb/commit/faf3535de528b3cd45ce5544540becf44c72ff37))
* **responses:** strip unsupported prompt params before upstream ([#128](https://github.com/Soju06/codex-lb/issues/128)) ([0f50c6f](https://github.com/Soju06/codex-lb/commit/0f50c6f11d5739b5e66badec45d50391f69c2760))
* **round-robin:** harden runtime locking and per-app balancer state ([#112](https://github.com/Soju06/codex-lb/issues/112)) ([7e5df87](https://github.com/Soju06/codex-lb/commit/7e5df8799598d4ef22efc1ff87ac40aaf258725d))


### Documentation

* add DOCaCola as a contributor for bug, test, and doc ([#106](https://github.com/Soju06/codex-lb/issues/106)) ([8fdab9f](https://github.com/Soju06/codex-lb/commit/8fdab9ff301038d1d4a9c6822ad1f66db1cfd498))
* add ink-splatters as a contributor for doc ([#122](https://github.com/Soju06/codex-lb/issues/122)) ([2607cb9](https://github.com/Soju06/codex-lb/commit/2607cb90beb8bd7c0e201b9d32af271e8e9cdc98))
* add joeblack2k as a contributor for code, bug, and test ([#109](https://github.com/Soju06/codex-lb/issues/109)) ([6dfb74a](https://github.com/Soju06/codex-lb/commit/6dfb74a6cde036f341056b25f91f249ebfa02f16))
* add pcy06 as a contributor for code, and test ([#121](https://github.com/Soju06/codex-lb/issues/121)) ([4290fb0](https://github.com/Soju06/codex-lb/commit/4290fb0eb85a8d1102819e4194a02a0bc6c1200f))
* fix codex defaults / add migration note ([#120](https://github.com/Soju06/codex-lb/issues/120)) ([6bfab1c](https://github.com/Soju06/codex-lb/commit/6bfab1c2bc8b2701b2a36f867bdb6975aaf56ac9))
* **git-workflow:** update PR title guidelines and workflow steps ([d88ab86](https://github.com/Soju06/codex-lb/commit/d88ab86e3a655c0d928cc35b275f7a5c1d0bf2dc))
* **git-workflow:** update pushing guidelines for forked PRs ([ef29f71](https://github.com/Soju06/codex-lb/commit/ef29f712ec00358977f10a64e5a4f6a1db3bceff))

## [1.0.4](https://github.com/Soju06/codex-lb/compare/v1.0.3...v1.0.4) (2026-02-20)


### Bug Fixes

* handle free-plan quota quirks (weekly-only windows, stale plan type after upgrade) ([#71](https://github.com/Soju06/codex-lb/issues/71)) ([c5f6ea8](https://github.com/Soju06/codex-lb/commit/c5f6ea8eabe7cbfb81f0f75bac46d398b46bb9d2))
* **proxy:** align message coercion and response mapping with OpenAI API spec ([#87](https://github.com/Soju06/codex-lb/issues/87)) ([d9fee7a](https://github.com/Soju06/codex-lb/commit/d9fee7a2a283c52438a18d9692ed20a7be69623c))
* **proxy:** OpenCode compatibility and better usage ([#86](https://github.com/Soju06/codex-lb/issues/86)) ([c243630](https://github.com/Soju06/codex-lb/commit/c2436307ac59d199aa48b1b1a29c98be6bc9debd))
* support non-overwrite import for same account across multiple team plans ([#72](https://github.com/Soju06/codex-lb/issues/72)) ([82e7cc7](https://github.com/Soju06/codex-lb/commit/82e7cc750a35fe5b200ade2ca210051dfee140ae))


### Documentation

* add azkore as a contributor for code, bug, and test ([#90](https://github.com/Soju06/codex-lb/issues/90)) ([5c3cbb7](https://github.com/Soju06/codex-lb/commit/5c3cbb77c19e2e792784cf1d459507fc8225b003))
* add hhsw2015 as a contributor for bug ([#91](https://github.com/Soju06/codex-lb/issues/91)) ([3262d50](https://github.com/Soju06/codex-lb/commit/3262d5083d43460e684b2acd09a2504bf4501b21))
* add JordxnBN as a contributor for code, bug, and test ([#92](https://github.com/Soju06/codex-lb/issues/92)) ([537b3cf](https://github.com/Soju06/codex-lb/commit/537b3cf9feb85d538202a6b4fd68b81b1a5b800c))

## [1.0.3](https://github.com/Soju06/codex-lb/compare/v1.0.2...v1.0.3) (2026-02-18)


### Bug Fixes

* **proxy:** expose models regardless of supported_in_api ([#82](https://github.com/Soju06/codex-lb/issues/82)) ([aac71d9](https://github.com/Soju06/codex-lb/commit/aac71d9d29632e7d1cc290d980b5b7f178f0dcc3))

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
