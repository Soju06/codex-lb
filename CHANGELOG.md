# Changelog

## [1.22.0](https://github.com/Soju06/codex-lb/compare/v1.21.0...v1.22.0) (2026-07-24)


### Features

* **accounts:** add reset-credit controls and auto redeem ([#1358](https://github.com/Soju06/codex-lb/issues/1358)) ([1c985d0](https://github.com/Soju06/codex-lb/commit/1c985d0ff734d4b00c25624f3d98fc9575769266))
* **conversation:** implementation on conversation grouping on Opencode + Codex ([#1453](https://github.com/Soju06/codex-lb/issues/1453)) ([be363d3](https://github.com/Soju06/codex-lb/commit/be363d3a46af245c973d07b33f777852f368ad07))
* **dashboard:** group advanced navigation and settings behind progressive disclosure ([#1339](https://github.com/Soju06/codex-lb/issues/1339)) ([0c2283e](https://github.com/Soju06/codex-lb/commit/0c2283eee0d732350d0704c2f91f33d082849b3b))
* **helm:** make ExternalSecret remote refs configurable ([#1455](https://github.com/Soju06/codex-lb/issues/1455)) ([1c91cb7](https://github.com/Soju06/codex-lb/commit/1c91cb7c2c6f009e59212a3a7aeefaf5002ce380))
* **helm:** support Gateway API rule filters ([#1459](https://github.com/Soju06/codex-lb/issues/1459)) ([acd50d1](https://github.com/Soju06/codex-lb/commit/acd50d1dd97fc459415f4588d7f5c5abc739b925))
* **model-sources:** report upstream generation timings ([#1319](https://github.com/Soju06/codex-lb/issues/1319)) ([1ebd195](https://github.com/Soju06/codex-lb/commit/1ebd195daed487959cb1d23cec04dbce081e2a89))
* **proxy:** add stale-anchor diagnostics ([#1225](https://github.com/Soju06/codex-lb/issues/1225)) ([4720e0e](https://github.com/Soju06/codex-lb/commit/4720e0ecf1800237539fa7b9113873544e8898e2))
* **settings:** manage data retention from the dashboard ([#1364](https://github.com/Soju06/codex-lb/issues/1364)) ([cacc6c3](https://github.com/Soju06/codex-lb/commit/cacc6c367cda9ee91bf9eba71fb04cd9907e6400))
* **ui:** add Korean dashboard locale ([#1375](https://github.com/Soju06/codex-lb/issues/1375)) ([ccaa4aa](https://github.com/Soju06/codex-lb/commit/ccaa4aa35aa46e2eb7fe439ae5e96fb3e3fdacff))
* **ui:** header brand back to /dashboard on click ([#1452](https://github.com/Soju06/codex-lb/issues/1452)) ([dca802d](https://github.com/Soju06/codex-lb/commit/dca802dcca724d753cd46849883bccb99105af79))


### Bug Fixes

* **api-keys:** reject duplicate limit rules on create ([#1312](https://github.com/Soju06/codex-lb/issues/1312)) ([31f7aac](https://github.com/Soju06/codex-lb/commit/31f7aac61bc55768bbb3ab1bbf819b21b7f5d368))
* **api:** give thread-goal GET/POST distinct operationIds ([#1398](https://github.com/Soju06/codex-lb/issues/1398)) ([3b97766](https://github.com/Soju06/codex-lb/commit/3b97766972ef48e70eedf1a61db6fd4477d22502))
* **auth:** require trusted-proxy identity consensus ([#1380](https://github.com/Soju06/codex-lb/issues/1380)) ([b3a5c2a](https://github.com/Soju06/codex-lb/commit/b3a5c2a911447e6d8207189f67269d9452f8b5d9))
* **balancer:** decorrelate round-robin tie-breaking across replicas ([#1327](https://github.com/Soju06/codex-lb/issues/1327)) ([fd529b2](https://github.com/Soju06/codex-lb/commit/fd529b21269328c88a3e8291858cccb1c27d93ff))
* **balancer:** recover probing accounts ([#1376](https://github.com/Soju06/codex-lb/issues/1376)) ([fe93776](https://github.com/Soju06/codex-lb/commit/fe93776c39efb63a6f0901937198ed2896f9677d))
* **balancer:** reject implausible rate-limit resets ([#1374](https://github.com/Soju06/codex-lb/issues/1374)) ([aea1e6f](https://github.com/Soju06/codex-lb/commit/aea1e6f58e3961dbca6c2380d5473cf2a965dbb6))
* **ci:** run ruff from the locked environment ([#1465](https://github.com/Soju06/codex-lb/issues/1465)) ([d52521a](https://github.com/Soju06/codex-lb/commit/d52521a98480a0e203243563a516576714113738))
* **cli:** reject out-of-range server port before startup ([#1400](https://github.com/Soju06/codex-lb/issues/1400)) ([ec7aadb](https://github.com/Soju06/codex-lb/commit/ec7aadb3867aacee1927b096f04c49213624b307))
* **compact:** preserve historical side-effect anchors ([f76469f](https://github.com/Soju06/codex-lb/commit/f76469f1d64b13cafea3f825f2ece605defcd120))
* **config:** reject empty trusted proxy CIDRs ([#1379](https://github.com/Soju06/codex-lb/issues/1379)) ([e5b101f](https://github.com/Soju06/codex-lb/commit/e5b101f20299d02637b9a66866c2e5bfc61edb03))
* **dashboard:** keep overview visible when request logs fail ([#1397](https://github.com/Soju06/codex-lb/issues/1397)) ([603d181](https://github.com/Soju06/codex-lb/commit/603d1813a45e28bbeadc0247b033df8fe563173e))
* **db:** pin asyncpg sessions to UTC ([#1352](https://github.com/Soju06/codex-lb/issues/1352)) ([977b991](https://github.com/Soju06/codex-lb/commit/977b991b69f7e21ae2768e5be30e1bd127b0f6a2))
* **db:** reject empty migration database URL ([#1399](https://github.com/Soju06/codex-lb/issues/1399)) ([f527dee](https://github.com/Soju06/codex-lb/commit/f527dee729cc66513009a515f1ed387d95c382c8))
* **docker:** preserve runtime readability for owner-only build files ([#1373](https://github.com/Soju06/codex-lb/issues/1373)) ([cf5db08](https://github.com/Soju06/codex-lb/commit/cf5db08042ed66724593dfcf5be66740b2c43e79))
* **models:** complete required Codex catalog fields ([#1299](https://github.com/Soju06/codex-lb/issues/1299)) ([a97bcc2](https://github.com/Soju06/codex-lb/commit/a97bcc26bc7a0a34a331f2f9bb9ae13d5602fd08))
* **oauth:** persist dashboard OAuth flow state for multi-replica ([#1329](https://github.com/Soju06/codex-lb/issues/1329)) ([8e2ee2d](https://github.com/Soju06/codex-lb/commit/8e2ee2d390dae4b4afd5dd61775fd7c8d29cc795))
* **observability:** anchor TTFT to the attempt, expose queue wait as a dashboard trend ([#1333](https://github.com/Soju06/codex-lb/issues/1333)) ([f79f093](https://github.com/Soju06/codex-lb/commit/f79f0935b6224b892467d26fea2705d737315724))
* **observability:** improve TTFT and TPS accuracy ([#1325](https://github.com/Soju06/codex-lb/issues/1325)) ([bc8d0fa](https://github.com/Soju06/codex-lb/commit/bc8d0fa335d9c8fea4c611b932c2677ce1146236))
* **proxy-responses:** normalize Lite reasoning context ([#1431](https://github.com/Soju06/codex-lb/issues/1431)) ([c93e803](https://github.com/Soju06/codex-lb/commit/c93e803dc390b7aa29e606785e73f03c4b52de2c))
* **proxy:** bound raw HTTP request ingress ([#1440](https://github.com/Soju06/codex-lb/issues/1440)) ([453c1e6](https://github.com/Soju06/codex-lb/commit/453c1e6d83f1e94bcdcff4c6014721b3632c89bd))
* **proxy:** dedup and cap oversized response.create dumps ([#1366](https://github.com/Soju06/codex-lb/issues/1366)) ([d0f5b08](https://github.com/Soju06/codex-lb/commit/d0f5b084c4a40eb8fddf838730fa43c4bbad245e))
* **proxy:** forward Codex standalone web search ([#1232](https://github.com/Soju06/codex-lb/issues/1232)) ([667cdab](https://github.com/Soju06/codex-lb/commit/667cdab568aa684279dca031171ffb291680ea50))
* **proxy:** harden compact continuity recovery ([c1c3f31](https://github.com/Soju06/codex-lb/commit/c1c3f31d47ff7241d226efbea88ed8d6550a8fe1))
* **proxy:** ignore API-key-enforced service tier a model never advertises ([#1438](https://github.com/Soju06/codex-lb/issues/1438)) ([e04226b](https://github.com/Soju06/codex-lb/commit/e04226b4324fed4bf3f78514977292071356f51f))
* **proxy:** preserve encrypted compaction item ids ([#1316](https://github.com/Soju06/codex-lb/issues/1316)) ([9b79dba](https://github.com/Soju06/codex-lb/commit/9b79dba596c559319fb67fd06f640e52b9fc5149))
* **proxy:** preserve HTTP bridge model-transition forks ([#1356](https://github.com/Soju06/codex-lb/issues/1356)) ([153bbc5](https://github.com/Soju06/codex-lb/commit/153bbc50504632b44986d3a47c73acd3ee7a9062))
* **proxy:** purge stale durable bridge rows on startup ([#1310](https://github.com/Soju06/codex-lb/issues/1310)) ([9b40f74](https://github.com/Soju06/codex-lb/commit/9b40f746262b895c7f2b94d3afc17266f75b76ce))
* **proxy:** record early downstream cancellations ([#1324](https://github.com/Soju06/codex-lb/issues/1324)) ([1089ab5](https://github.com/Soju06/codex-lb/commit/1089ab5dae24a507b1c512301180997b37cc280e))
* **proxy:** recover from host network changes ([#1234](https://github.com/Soju06/codex-lb/issues/1234)) ([c2814b6](https://github.com/Soju06/codex-lb/commit/c2814b673a0ac742038775d3f91a3c4f9ab99bf6))
* **proxy:** recover idle Codex Desktop bridge ([#1439](https://github.com/Soju06/codex-lb/issues/1439)) ([b49f25a](https://github.com/Soju06/codex-lb/commit/b49f25a76f7b15823eddceb1b18a51df61d0c7fc))
* **proxy:** recover sequenced Codex prewarms ([#1359](https://github.com/Soju06/codex-lb/issues/1359)) ([c9dac81](https://github.com/Soju06/codex-lb/commit/c9dac811665a1eabc4c286f02c140f89a645f72a))
* **proxy:** recover stale response-create gates ([#1296](https://github.com/Soju06/codex-lb/issues/1296)) ([fb5a573](https://github.com/Soju06/codex-lb/commit/fb5a573ccad0cd364a53e2c5c6ef52558a618d45))
* **proxy:** recover verified responses after owner loss ([#1437](https://github.com/Soju06/codex-lb/issues/1437)) ([e6270f3](https://github.com/Soju06/codex-lb/commit/e6270f3ff6140d1f2c09bbd7820487f398de6fd2))
* **proxy:** reject unsupported multi-worker-per-instance for shared per-account caps ([#1328](https://github.com/Soju06/codex-lb/issues/1328)) ([2e10405](https://github.com/Soju06/codex-lb/commit/2e10405acb31ee67f6da73b6f0b8ec4d86e1a1d2))
* **proxy:** release pending websocket stream leases on disconnect ([#1282](https://github.com/Soju06/codex-lb/issues/1282)) ([f06173e](https://github.com/Soju06/codex-lb/commit/f06173e779b17a7e1fed5b14a24db867fd059caf)), closes [#1281](https://github.com/Soju06/codex-lb/issues/1281)
* **proxy:** retry server_is_overloaded responses ([#1315](https://github.com/Soju06/codex-lb/issues/1315)) ([93383a7](https://github.com/Soju06/codex-lb/commit/93383a725a14cab21aae414963763b8ab6e18cb0))
* **proxy:** retry stale per-account model rejections ([#1321](https://github.com/Soju06/codex-lb/issues/1321)) ([edc486a](https://github.com/Soju06/codex-lb/commit/edc486ab2b6b4cf1938eb0dba5f176c86df9eaa6))
* **proxy:** retry upstream model capacity errors ([#1385](https://github.com/Soju06/codex-lb/issues/1385)) ([63bfcdc](https://github.com/Soju06/codex-lb/commit/63bfcdc8c04c90d18f497062960bb4349173b957))
* **proxy:** route Spark using fresh per-account quota evidence ([#1248](https://github.com/Soju06/codex-lb/issues/1248)) ([1471475](https://github.com/Soju06/codex-lb/commit/14714750092f2d73158f3ba533c446d20885aa88))
* **proxy:** serialize shared-session rate-limit reads ([#1432](https://github.com/Soju06/codex-lb/issues/1432)) ([539c4a2](https://github.com/Soju06/codex-lb/commit/539c4a2a37e77aa57ce9d2d3348cd030acfca140))
* **proxy:** settle API-key reservation before budget-exhausted compact preflight raises ([#1332](https://github.com/Soju06/codex-lb/issues/1332)) ([fe625bf](https://github.com/Soju06/codex-lb/commit/fe625bfb78d8040c664ba2153e1e48275f327be3))
* **proxy:** slim inline images nested in historical tool-call outputs ([#1344](https://github.com/Soju06/codex-lb/issues/1344)) ([a8578d7](https://github.com/Soju06/codex-lb/commit/a8578d780e67b8ce115e8eb1893176a05c67ea79))
* **proxy:** spill bare session affinity under account caps ([#1382](https://github.com/Soju06/codex-lb/issues/1382)) ([5d55191](https://github.com/Soju06/codex-lb/commit/5d5519105362447b40fa529c2ae8131d52720214))
* **reports:** reject inverted date ranges ([#1396](https://github.com/Soju06/codex-lb/issues/1396)) ([f3c6ff9](https://github.com/Soju06/codex-lb/commit/f3c6ff97d5f81bac4813f91e63f298fd0c859395))
* **security:** enforce trusted proxy locality ([#1381](https://github.com/Soju06/codex-lb/issues/1381)) ([d9709cc](https://github.com/Soju06/codex-lb/commit/d9709cc64063f0488f53cfa8ad71801bca82d8b0))
* **security:** harden trusted Forwarded chain resolution ([#1377](https://github.com/Soju06/codex-lb/issues/1377)) ([21313b7](https://github.com/Soju06/codex-lb/commit/21313b74dc6bbb2d711cc4f9ca1a30aec0a5267e))
* **security:** inspect repeated session proxy headers ([#1378](https://github.com/Soju06/codex-lb/issues/1378)) ([dae97cd](https://github.com/Soju06/codex-lb/commit/dae97cde9eb09d8798574cf92b295f23ca9b5eb2))
* **shutdown:** drain audit and fleet tasks ([#1443](https://github.com/Soju06/codex-lb/issues/1443)) ([ec36ef6](https://github.com/Soju06/codex-lb/commit/ec36ef60a617684bc78f68b7f462877557fa8e9f))
* **streaming:** preserve terminal no-replay boundaries ([#1389](https://github.com/Soju06/codex-lb/issues/1389)) ([a4a2215](https://github.com/Soju06/codex-lb/commit/a4a22154c2eba756fb5d4c1426a84f71f81aa95e))
* **uploads:** bound multipart request resources ([#1447](https://github.com/Soju06/codex-lb/issues/1447)) ([6ff5af6](https://github.com/Soju06/codex-lb/commit/6ff5af654af6a43a38ab40a79c7d4568e53003ac))
* **usage:** make weekly-primary remap tiebreak data-aware ([#1391](https://github.com/Soju06/codex-lb/issues/1391)) ([71f31e6](https://github.com/Soju06/codex-lb/commit/71f31e67a09129b0b581a4e2bf829d3b6c165014))
* **usage:** price gpt-5.6 personality models ([#1275](https://github.com/Soju06/codex-lb/issues/1275)) ([bcdc384](https://github.com/Soju06/codex-lb/commit/bcdc3842edfae7931c874e03663c5eab0649ddeb))
* **useragent:** fixing the unexpected category on useragent group ([#1458](https://github.com/Soju06/codex-lb/issues/1458)) ([69b397f](https://github.com/Soju06/codex-lb/commit/69b397f77ecded9a721e9dfd6dc0630fefffc602))


### Performance Improvements

* **automations:** bound run-history query work ([#1451](https://github.com/Soju06/codex-lb/issues/1451)) ([4fbe591](https://github.com/Soju06/codex-lb/commit/4fbe59182ce050b98691ca69fa8d25b16239e829))
* **db:** optimize dashboard hot-path indexes ([#1386](https://github.com/Soju06/codex-lb/issues/1386)) ([1422be6](https://github.com/Soju06/codex-lb/commit/1422be6717987721619bece793e309b95cffd709))
* **proxy:** cache upstream-route resolution per account ([#1283](https://github.com/Soju06/codex-lb/issues/1283)) ([a52c458](https://github.com/Soju06/codex-lb/commit/a52c458b5d8a0f7e33cff4e7b422ae25436331a7))
* **usage:** persist account snapshots transactionally ([#1448](https://github.com/Soju06/codex-lb/issues/1448)) ([10f5357](https://github.com/Soju06/codex-lb/commit/10f5357d1ba2e994825b33e21fe030948012d12e))
* **usage:** scope refresh work to selected account ([#1446](https://github.com/Soju06/codex-lb/issues/1446)) ([eebb757](https://github.com/Soju06/codex-lb/commit/eebb7578beba462f25ef787e703d0b1291c959ec))


### Documentation

* add yshishenya and alchemistkiv as contributors ([#1387](https://github.com/Soju06/codex-lb/issues/1387)) ([3e6ca6e](https://github.com/Soju06/codex-lb/commit/3e6ca6e40e7c3acca6ce19196c7b678d65fe58cc))
* **contributing:** codify simplicity principles and merge gates ([#1336](https://github.com/Soju06/codex-lb/issues/1336)) ([8374f4f](https://github.com/Soju06/codex-lb/commit/8374f4fc3fe2678fceb0dea5686e897764eae044))
* **openspec:** archive accurate-stream-speed-metrics ([#1334](https://github.com/Soju06/codex-lb/issues/1334)) ([43aa394](https://github.com/Soju06/codex-lb/commit/43aa39425c6b9b72978d471f8aea47c46ce070a4))
* **openspec:** archive the settings-reduction changes ([#1368](https://github.com/Soju06/codex-lb/issues/1368)) ([a619afc](https://github.com/Soju06/codex-lb/commit/a619afcb7b998e5a1fc772808593ca4c8e7c7ba4))
* **openspec:** archive the simplicity-effort changes ([#1347](https://github.com/Soju06/codex-lb/issues/1347)) ([8fe0848](https://github.com/Soju06/codex-lb/commit/8fe0848e23368bc58f4dc7be0e3347bac64beb99))
* **readme:** restore the features grid and client logo table ([#1346](https://github.com/Soju06/codex-lb/issues/1346)) ([e47deea](https://github.com/Soju06/codex-lb/commit/e47deeac1c0eff6f73602ff241713885d7ae6fe7))
* **reference:** add generated settings reference with drift guards ([#1369](https://github.com/Soju06/codex-lb/issues/1369)) ([fdf267e](https://github.com/Soju06/codex-lb/commit/fdf267e78f630271ab8d2fe74345832a3ddc06c2))
* **replica:** refresh operations context ([#1445](https://github.com/Soju06/codex-lb/issues/1445)) ([4a1df0a](https://github.com/Soju06/codex-lb/commit/4a1df0a9b3cb2be0d7c7a994a4647352b601108a))
* **site:** add mkdocs documentation site and slim README to quick-start scope ([#1337](https://github.com/Soju06/codex-lb/issues/1337)) ([0705f9f](https://github.com/Soju06/codex-lb/commit/0705f9fd8c747ef3cf4777651d6c2ed29b0f9b45))

## [1.21.0](https://github.com/Soju06/codex-lb/compare/v1.20.1...v1.21.0) (2026-07-15)


### Features

* **accounts:** test bound proxy pools ([#1124](https://github.com/Soju06/codex-lb/issues/1124)) ([5b830e6](https://github.com/Soju06/codex-lb/commit/5b830e66f3a637b3a05a2073b889f068b1997488))
* add openai-compatible model sources ([#1129](https://github.com/Soju06/codex-lb/issues/1129)) ([d345916](https://github.com/Soju06/codex-lb/commit/d34591639ffea2760751fa908d210a60698819a9))
* **automations:** scheduled cycles, grouped runs, and run details UI ([#438](https://github.com/Soju06/codex-lb/issues/438)) ([100f6d5](https://github.com/Soju06/codex-lb/commit/100f6d503e6df021b4f00cc0994f2c7b51008363))
* **balancer:** adapt selection and refresh to absent short-window quotas ([#1267](https://github.com/Soju06/codex-lb/issues/1267)) ([9c1486e](https://github.com/Soju06/codex-lb/commit/9c1486e757348d6ef7a1686d321b4b1e2862a848))
* **ci:** mint a dedicated GitHub App token for codex label sync ([#1302](https://github.com/Soju06/codex-lb/issues/1302)) ([5470a50](https://github.com/Soju06/codex-lb/commit/5470a5006b01ff988747fc367375a1cff563f7bd))
* **dashboard:** hide expired primary windows instead of freezing them ([#1301](https://github.com/Soju06/codex-lb/issues/1301)) ([165aa91](https://github.com/Soju06/codex-lb/commit/165aa912b7baaa63ba59badf5dffa40178b388ca))
* **dashboard:** show request log speed metrics ([#1210](https://github.com/Soju06/codex-lb/issues/1210)) ([3de4dd9](https://github.com/Soju06/codex-lb/commit/3de4dd9352981235de34774cfc628440f5e572d1))
* expose upstream transport observability ([#1096](https://github.com/Soju06/codex-lb/issues/1096)) ([cd01609](https://github.com/Soju06/codex-lb/commit/cd01609b3d85a3661d059820d12552edf1679d77))
* **fleet:** add refresh endpoint ([#1128](https://github.com/Soju06/codex-lb/issues/1128)) ([1d22d6e](https://github.com/Soju06/codex-lb/commit/1d22d6ebd3d81b2340ff85a5c2630fe817c1fd74))
* **frontend:** restore Simplified Chinese i18n ([#995](https://github.com/Soju06/codex-lb/issues/995)) ([4ac2d5c](https://github.com/Soju06/codex-lb/commit/4ac2d5c0212d99382614afdccc1ac4596b0712b1)), closes [#671](https://github.com/Soju06/codex-lb/issues/671) [#780](https://github.com/Soju06/codex-lb/issues/780)
* **images:** add route observability ([#1123](https://github.com/Soju06/codex-lb/issues/1123)) ([8015bb5](https://github.com/Soju06/codex-lb/commit/8015bb5f5ffd9b6a7b5d46f27d807be564af7c4e))
* **metrics:** expose account inflight lease gauge ([#1148](https://github.com/Soju06/codex-lb/issues/1148)) ([23a78bc](https://github.com/Soju06/codex-lb/commit/23a78bc730a0f8ace158316144ac502910166e04))
* **models:** add GPT-5.6 bootstrap catalog with upstream-verified metadata ([#1176](https://github.com/Soju06/codex-lb/issues/1176)) ([64bb2b8](https://github.com/Soju06/codex-lb/commit/64bb2b89e7219feba781a845cd0cd2df8c80de44))
* **models:** replicate leader-refreshed model registry via persisted snapshots ([#1262](https://github.com/Soju06/codex-lb/issues/1262)) ([fa3906b](https://github.com/Soju06/codex-lb/commit/fa3906bc9654282a98bc4c33422c19bef67ba921))
* **planner:** derive phase planning from observed short windows ([#1268](https://github.com/Soju06/codex-lb/issues/1268)) ([7fdbdce](https://github.com/Soju06/codex-lb/commit/7fdbdce33ca7c6732653ae7de44d436376081716))
* **proxy:** add Fast Mode prohibition setting ([#1190](https://github.com/Soju06/codex-lb/issues/1190)) ([cab7ef6](https://github.com/Soju06/codex-lb/commit/cab7ef6f2dc38b36564f563a1fad5d751ea06868))
* **proxy:** add TTFT phase observability ([bdd571f](https://github.com/Soju06/codex-lb/commit/bdd571f01052bdaaf59366e6759d8b2d075169a6))
* **proxy:** configure account concurrency caps ([#1214](https://github.com/Soju06/codex-lb/issues/1214)) ([88807e3](https://github.com/Soju06/codex-lb/commit/88807e3c6c67cacc107ac6ebb6a3255da3c730f3))
* **proxy:** partition account concurrency caps across live replicas ([#1258](https://github.com/Soju06/codex-lb/issues/1258)) ([d4f2544](https://github.com/Soju06/codex-lb/commit/d4f254434c0a60a78a0a6b0fd987646252708243))
* **proxy:** repair account-bound egress routing ([#875](https://github.com/Soju06/codex-lb/issues/875)) ([1bee4ec](https://github.com/Soju06/codex-lb/commit/1bee4ec32413b41a46ea54e9be7eb723b3e3d5e6))
* **replica-ops:** document replica topology contract and land startup guardrails ([#1264](https://github.com/Soju06/codex-lb/issues/1264)) ([e9392f3](https://github.com/Soju06/codex-lb/commit/e9392f3ff9cdf13002fbf7400292aaea3f75d557))
* **request-logs:** record client IP ([#985](https://github.com/Soju06/codex-lb/issues/985)) ([18d006f](https://github.com/Soju06/codex-lb/commit/18d006f6b957d1386ac7cf083e6b889817f81df0))
* **request-ui:** add showing elapsed time to request details ([#1103](https://github.com/Soju06/codex-lb/issues/1103)) ([b1d675d](https://github.com/Soju06/codex-lb/commit/b1d675d2eca2998a248cdd471000a7aaab1591fd))
* **reset-credits:** add banked rate-limit reset credits ([#1053](https://github.com/Soju06/codex-lb/issues/1053)) ([ef1d61b](https://github.com/Soju06/codex-lb/commit/ef1d61bd5b4f4107a6192bcaeca156d60f590fe3))
* **retention:** opt-in pruning for request logs and usage history ([#1240](https://github.com/Soju06/codex-lb/issues/1240)) ([637c7c9](https://github.com/Soju06/codex-lb/commit/637c7c929db6ecbd0674ab8d1b96d81a6a95ff85))
* smart HTTP→upstream transport routing (sticky vs single-shot) ([#1093](https://github.com/Soju06/codex-lb/issues/1093)) ([2e124df](https://github.com/Soju06/codex-lb/commit/2e124df8fb230fec7528d473fdd71bfd2e7e6388))
* **usage-api:** expose configurable usage details ([#832](https://github.com/Soju06/codex-lb/issues/832)) ([244c438](https://github.com/Soju06/codex-lb/commit/244c438c2c1d5fd9338da93ada971e20f7612028))
* **usage:** ingest live rate-limit snapshots from proxied traffic ([#1273](https://github.com/Soju06/codex-lb/issues/1273)) ([75e7c2b](https://github.com/Soju06/codex-lb/commit/75e7c2ba8cd9ff751bf269645b5a35bbfc6f60fe))
* **usage:** support Codex usage reset credits ([#1105](https://github.com/Soju06/codex-lb/issues/1105)) ([12b9096](https://github.com/Soju06/codex-lb/commit/12b9096196d9361b857b3dc7cfe81b5964de44a8))
* **warmup:** add staggered idle limit prestart ([#905](https://github.com/Soju06/codex-lb/issues/905)) ([39cf659](https://github.com/Soju06/codex-lb/commit/39cf659009fbb43cc7567d71bb94fa230a5169ff))


### Bug Fixes

* **account-ui:** expand account list on tall viewports ([#1195](https://github.com/Soju06/codex-lb/issues/1195)) ([d5cef8b](https://github.com/Soju06/codex-lb/commit/d5cef8b5b4f07ea9e5139ffe56c6732a2a30b626))
* **account-ui:** fix overflow on account list page ([#1149](https://github.com/Soju06/codex-lb/issues/1149)) ([fb3698d](https://github.com/Soju06/codex-lb/commit/fb3698d4c2f23fde555d52d0be78a3f8eb26a81a))
* **accounts:** serialize cross-replica token refresh with per-account DB claims ([#1254](https://github.com/Soju06/codex-lb/issues/1254)) ([b5f0541](https://github.com/Soju06/codex-lb/commit/b5f0541a8241434f976c70222d63eab4ce9b77b9))
* **accounts:** sync free-to-paid plan upgrades ([#1217](https://github.com/Soju06/codex-lb/issues/1217)) ([57ebc3d](https://github.com/Soju06/codex-lb/commit/57ebc3dc5dd725066d9fb33e5b11556f47bffa17))
* **automation:** fall back to github.token when label sync hits rate limits ([#1272](https://github.com/Soju06/codex-lb/issues/1272)) ([3ce5ba6](https://github.com/Soju06/codex-lb/commit/3ce5ba698a62a1b6eb11b7b688575ff844e06dfb))
* **balancer:** persist rate-limit cooldowns so peer replicas honor them ([#1261](https://github.com/Soju06/codex-lb/issues/1261)) ([991be81](https://github.com/Soju06/codex-lb/commit/991be817c3895840a20a770151eb862822b73f88))
* **caching:** extend cross-replica cache invalidation bus to routing, selection, and settings caches ([#1255](https://github.com/Soju06/codex-lb/issues/1255)) ([b7bf87c](https://github.com/Soju06/codex-lb/commit/b7bf87cf212db78d07e67dca3e43f2af3170b584))
* **ci:** ignore stale and resolved Codex inline findings ([#981](https://github.com/Soju06/codex-lb/issues/981)) ([384e9b5](https://github.com/Soju06/codex-lb/commit/384e9b5aae16dbf59f28eb94ddec3ad640db0f34))
* **ci:** ignore superseded same-head check suites ([#1170](https://github.com/Soju06/codex-lb/issues/1170)) ([b0f5ea8](https://github.com/Soju06/codex-lb/commit/b0f5ea8b03a6b7685d281d74350a22377e05d72e))
* **ci:** keep manual rerun attempts authoritative ([#1216](https://github.com/Soju06/codex-lb/issues/1216)) ([87b590a](https://github.com/Soju06/codex-lb/commit/87b590a54a3746767072502a2695b0c278d2f5a4))
* **dashboard-auth:** harden local session ttl ([#1137](https://github.com/Soju06/codex-lb/issues/1137)) ([6fe3b3f](https://github.com/Soju06/codex-lb/commit/6fe3b3f1a4df80e68bc4eb383edf3df2663bdcbb))
* **dashboard:** correct weekly credits pace display ([#955](https://github.com/Soju06/codex-lb/issues/955)) ([30b3631](https://github.com/Soju06/codex-lb/commit/30b36317e6332b587a3a9202b5f0ed8248bf1018))
* **db:** merge post-batch migration heads ([#1116](https://github.com/Soju06/codex-lb/issues/1116)) ([a2290f2](https://github.com/Soju06/codex-lb/commit/a2290f28aae15b1478b9b56a1e7b6bb7c7411f7e))
* **db:** optimize dashboard query hot paths ([#1107](https://github.com/Soju06/codex-lb/issues/1107)) ([1d3f6cc](https://github.com/Soju06/codex-lb/commit/1d3f6ccd3bdc0dfa65af12c2b3e4e1f811e7cea0))
* **db:** prevent SQLite FD retention in background refresh tasks ([#1127](https://github.com/Soju06/codex-lb/issues/1127)) ([b37b7b3](https://github.com/Soju06/codex-lb/commit/b37b7b3d58c9f4ea20414b8e8a76bd453013cde3))
* **db:** serialize startup migrations across replicas ([#1256](https://github.com/Soju06/codex-lb/issues/1256)) ([0a7f354](https://github.com/Soju06/codex-lb/commit/0a7f354d376c0f4c8f93a2902730578e00f889d7))
* **deploy:** repair broken multi-replica deployment artifacts ([#1263](https://github.com/Soju06/codex-lb/issues/1263)) ([2821929](https://github.com/Soju06/codex-lb/commit/28219296f63be34304d0090126bc97afaa768f45))
* **frontend:** persist dashboard account list sort ([#1115](https://github.com/Soju06/codex-lb/issues/1115)) ([04dd331](https://github.com/Soju06/codex-lb/commit/04dd33187120c4aa1c8b4f3f9c3b4f908c4341da)), closes [#1035](https://github.com/Soju06/codex-lb/issues/1035)
* **http-bridge:** clean stale inflight session futures ([#1044](https://github.com/Soju06/codex-lb/issues/1044)) ([ea1109d](https://github.com/Soju06/codex-lb/commit/ea1109da4c3e70d1cedfc69b11475f9819213921))
* list Codex shell-only models in backend catalog ([#1138](https://github.com/Soju06/codex-lb/issues/1138)) ([5316df1](https://github.com/Soju06/codex-lb/commit/5316df16ec15dd1e6a701c9da701a31cd8cd08a9))
* **model-sources:** default Codex catalog context and capability-gated tool filtering ([#1152](https://github.com/Soju06/codex-lb/issues/1152)) ([d02417c](https://github.com/Soju06/codex-lb/commit/d02417c2d193d30c2b08833a7cadd203d9338c42))
* **models:** aggregate service tiers across accounts ([#1106](https://github.com/Soju06/codex-lb/issues/1106)) ([a1831d9](https://github.com/Soju06/codex-lb/commit/a1831d957ffdd8a8e1df9a32da63db356bad7d20))
* **models:** hide unsupported API models ([#887](https://github.com/Soju06/codex-lb/issues/887)) ([c5714ca](https://github.com/Soju06/codex-lb/commit/c5714ca90acabb6fc54d24d1da52cd749cf4e269))
* **models:** retain hidden metadata across partial refreshes ([#1221](https://github.com/Soju06/codex-lb/issues/1221)) ([cf87239](https://github.com/Soju06/codex-lb/commit/cf87239e8870f225c53637901890f529c19b5493))
* **models:** serve the Codex catalog on /v1/models for Codex clients ([#1163](https://github.com/Soju06/codex-lb/issues/1163)) ([a83d6d6](https://github.com/Soju06/codex-lb/commit/a83d6d6f4fc9ca967db63042220ce2f2dfbd73b6))
* **oauth:** isolate reauth by Team seat identity ([#1224](https://github.com/Soju06/codex-lb/issues/1224)) ([64b9f7b](https://github.com/Soju06/codex-lb/commit/64b9f7bfe692b71f170c59b177872ef0554bea90))
* **proxy:** accept large websocket response.create and fail fast with 400 ([#1236](https://github.com/Soju06/codex-lb/issues/1236)) ([02f1784](https://github.com/Soju06/codex-lb/commit/02f1784c53c53e54d2d82ad6e6ce257d3b02df13))
* **proxy:** avoid shielded-future log on startup probe timeout ([#1076](https://github.com/Soju06/codex-lb/issues/1076)) ([21c03ec](https://github.com/Soju06/codex-lb/commit/21c03ec5942032310a87d92af2539475b98755e7)), closes [#976](https://github.com/Soju06/codex-lb/issues/976)
* **proxy:** clarify account stream cap overload ([#1122](https://github.com/Soju06/codex-lb/issues/1122)) ([3cc0db4](https://github.com/Soju06/codex-lb/commit/3cc0db44c6155a9d4b1ac65a0ee20425cb6834e7))
* **proxy:** fallback v1 usage limits to upstream quotas ([#1151](https://github.com/Soju06/codex-lb/issues/1151)) ([cfe6dd6](https://github.com/Soju06/codex-lb/commit/cfe6dd6676809ac866c90f3b2358a876953d6eef))
* **proxy:** fence bridge lease writes, evict fenced-out replicas, purge orphaned ring state ([#1259](https://github.com/Soju06/codex-lb/issues/1259)) ([53f7b46](https://github.com/Soju06/codex-lb/commit/53f7b4633978f6a8b1085e2a9a5215fa91c7e62b))
* **proxy:** harden native Responses Lite continuity ([#1164](https://github.com/Soju06/codex-lb/issues/1164)) ([ffd529b](https://github.com/Soju06/codex-lb/commit/ffd529bbdec1de933f8bbbfcb5b7bc8d3e9d453c))
* **proxy:** hide blank reasoning summary comments ([#1154](https://github.com/Soju06/codex-lb/issues/1154)) ([b62e03d](https://github.com/Soju06/codex-lb/commit/b62e03d0b36d554492d20b2a5745d79e80a06826))
* **proxy:** isolate unanchored parallel bridge requests ([#1169](https://github.com/Soju06/codex-lb/issues/1169)) ([cea5240](https://github.com/Soju06/codex-lb/commit/cea5240a015afdc02552246ce58ee6ba2113842b))
* **proxy:** keep native capacity waits alive ([#1218](https://github.com/Soju06/codex-lb/issues/1218)) ([0b0d43d](https://github.com/Soju06/codex-lb/commit/0b0d43d8296c1d66ede84bd8493a53a1b7fb57ae))
* **proxy:** normalize non-native upstream requests to Codex CLI fingerprint ([#1089](https://github.com/Soju06/codex-lb/issues/1089)) ([8616e48](https://github.com/Soju06/codex-lb/commit/8616e48116ecb56947554d0e888bfeebd4ab0295))
* **proxy:** normalize selected installation metadata ([#1204](https://github.com/Soju06/codex-lb/issues/1204)) ([2d52ffd](https://github.com/Soju06/codex-lb/commit/2d52ffd51214e878740f299f4dcf32920ea6101c))
* **proxy:** oauth blocked when bindings lack route ([#1064](https://github.com/Soju06/codex-lb/issues/1064)) ([1f6b5d9](https://github.com/Soju06/codex-lb/commit/1f6b5d940081376a68b9fb1cdad75acffcae9bb8))
* **proxy:** omit synthesized tools on owner-forward and source egress ([#1203](https://github.com/Soju06/codex-lb/issues/1203)) ([e94e077](https://github.com/Soju06/codex-lb/commit/e94e077bb12e25e8376ff452b69165eb5de7c1d4))
* **proxy:** preserve admitted bridge waiters on upstream close ([#1191](https://github.com/Soju06/codex-lb/issues/1191)) ([03b7778](https://github.com/Soju06/codex-lb/commit/03b777813a03be11f1a0290e4230fbbaf35534f2))
* **proxy:** preserve all non-message system and developer input items ([#1172](https://github.com/Soju06/codex-lb/issues/1172)) ([af6b515](https://github.com/Soju06/codex-lb/commit/af6b515b46e6745ed0d57d044bdc34ed8bce0e0c))
* **proxy:** preserve model_messages in catalog response ([#1178](https://github.com/Soju06/codex-lb/issues/1178)) ([3ccdc32](https://github.com/Soju06/codex-lb/commit/3ccdc327d61fc31123a205f31496e83eae310218))
* **proxy:** preserve multi-agent v2 passthrough ([#1189](https://github.com/Soju06/codex-lb/issues/1189)) ([9bbc5cd](https://github.com/Soju06/codex-lb/commit/9bbc5cd7398fe89c5d567f792cef65988b8f96a6))
* **proxy:** preserve Responses Lite additional tools ([#1161](https://github.com/Soju06/codex-lb/issues/1161)) ([f746a8d](https://github.com/Soju06/codex-lb/commit/f746a8d7f3caed1fe4c2d94a6183dd8bceeff401))
* **proxy:** preserve responses turn-state fidelity ([#1193](https://github.com/Soju06/codex-lb/issues/1193)) ([88540b6](https://github.com/Soju06/codex-lb/commit/88540b6ab89b1f9544a2be22c7dcd9190ba2e1cd))
* **proxy:** preserve websocket incomplete reasons ([#1219](https://github.com/Soju06/codex-lb/issues/1219)) ([b962b35](https://github.com/Soju06/codex-lb/commit/b962b3593167d8d137f13063feaeac77f4744db3))
* **proxy:** preserve websocket reconnect affinity ([#1220](https://github.com/Soju06/codex-lb/issues/1220)) ([ca10437](https://github.com/Soju06/codex-lb/commit/ca1043779ddcc91f8493d1c62dabc16dcc3be103))
* **proxy:** prevent sequenced websocket replay ([#1223](https://github.com/Soju06/codex-lb/issues/1223)) ([4f891a6](https://github.com/Soju06/codex-lb/commit/4f891a6af0093780e517804c4dd89b3bddb80be8))
* **proxy:** queue bridged requests through response-create gate contention ([#1266](https://github.com/Soju06/codex-lb/issues/1266)) ([87fae43](https://github.com/Soju06/codex-lb/commit/87fae4301583799b1ee72b46f312a2aec95ee8bb))
* **proxy:** reselect only safe websocket turns ([#1207](https://github.com/Soju06/codex-lb/issues/1207)) ([4c3d109](https://github.com/Soju06/codex-lb/commit/4c3d109107e5d6e323b953fae971efdc55890264))
* **proxy:** reserve stream capacity for recovery ([#1188](https://github.com/Soju06/codex-lb/issues/1188)) ([bf0345f](https://github.com/Soju06/codex-lb/commit/bf0345f519a4c5fcf6236fe400500c5f25d441c4))
* **proxy:** route by complete account capability catalogs ([#1205](https://github.com/Soju06/codex-lb/issues/1205)) ([f8815b8](https://github.com/Soju06/codex-lb/commit/f8815b8d30f7ab3816d88f73d88cab7c337556bc))
* **proxy:** send complete Codex identity headers ([#1197](https://github.com/Soju06/codex-lb/issues/1197)) ([d57307b](https://github.com/Soju06/codex-lb/commit/d57307be9ef773224c3e934561f71530614cfaf9))
* **proxy:** set parallel_tool_calls to false for compaction requests ([#1054](https://github.com/Soju06/codex-lb/issues/1054)) ([f212300](https://github.com/Soju06/codex-lb/commit/f212300dbe60492accdb09115a6a64ba87646c86))
* **proxy:** stop local rate-limit selection retry loops ([#1121](https://github.com/Soju06/codex-lb/issues/1121)) ([9de9003](https://github.com/Soju06/codex-lb/commit/9de9003d9f763ec9be45c35e30b0d2881dff4eed))
* **proxy:** stop synthesizing top-level tools for upstream Codex requests ([#1200](https://github.com/Soju06/codex-lb/issues/1200)) ([fc57c9c](https://github.com/Soju06/codex-lb/commit/fc57c9cae992ecc0690a0d7a3dcc17bd0abadd0c)), closes [#1184](https://github.com/Soju06/codex-lb/issues/1184)
* **proxy:** strip internal responses lite header ([#1099](https://github.com/Soju06/codex-lb/issues/1099)) ([6d6be73](https://github.com/Soju06/codex-lb/commit/6d6be73b3abfa1afc1c1667583c3a2d007154ef0))
* **proxy:** strip unsafe HTTP headers before owner-bridge forward ([#1036](https://github.com/Soju06/codex-lb/issues/1036)) ([6d0e62f](https://github.com/Soju06/codex-lb/commit/6d0e62ff6e5f95d5b10d92d32ef57d93ab556f4f))
* **proxy:** support Codex image edit routes ([#1160](https://github.com/Soju06/codex-lb/issues/1160)) ([1bfc5ce](https://github.com/Soju06/codex-lb/commit/1bfc5ce0ea81ce7d34f84da31e1171ebc15f5455))
* **proxy:** synthesize interrupted custom tool call outputs ([#1175](https://github.com/Soju06/codex-lb/issues/1175)) ([264dd03](https://github.com/Soju06/codex-lb/commit/264dd03740ac205b005b1e90ced0a21a11d5f7b3))
* **proxy:** wait on local account cap pressure ([#1155](https://github.com/Soju06/codex-lb/issues/1155)) ([689c6d8](https://github.com/Soju06/codex-lb/commit/689c6d83ae2a166b98cde64ad134e829f179126a))
* **quota-planner:** atomic warmup budget claims ([#1260](https://github.com/Soju06/codex-lb/issues/1260)) ([a8e12f8](https://github.com/Soju06/codex-lb/commit/a8e12f8e34222547a9a0c6e8dc51327a0f7b1f38))
* **quota-planner:** normalize datetimes before database use ([#1043](https://github.com/Soju06/codex-lb/issues/1043)) ([9e3819a](https://github.com/Soju06/codex-lb/commit/9e3819a717b9a0ec0a75a7013abeacfeb62031e3))
* **quota-planner:** normalize decision datetimes for persistence ([#1034](https://github.com/Soju06/codex-lb/issues/1034)) ([9f5ae7c](https://github.com/Soju06/codex-lb/commit/9f5ae7c2d92421081c427bb3bcff07355934f30e))
* **reset-credits:** make redemption replica-safe with durable ledger and claim ([#1257](https://github.com/Soju06/codex-lb/issues/1257)) ([01188e7](https://github.com/Soju06/codex-lb/commit/01188e7c08f009dfb882ce66a0147b7710e33a63))
* **retry:** honor minute/hour and compound retry-after hints ([#1052](https://github.com/Soju06/codex-lb/issues/1052)) ([b19914a](https://github.com/Soju06/codex-lb/commit/b19914a777349d2f5ae3d711aaaf4bb54d793124))
* **scheduling:** harden scheduler leader election for multi-replica safety ([#1253](https://github.com/Soju06/codex-lb/issues/1253)) ([13d3a32](https://github.com/Soju06/codex-lb/commit/13d3a3216e4c5afb64ae3e022c849a0ef5592d9b))
* **ui:** make routing strategy guide clickable cards ([#1229](https://github.com/Soju06/codex-lb/issues/1229)) ([534d953](https://github.com/Soju06/codex-lb/commit/534d9533511ba7c69a6782596b71cd7023e58531))
* **ui:** stabilize account layout and API key dialog ([#1062](https://github.com/Soju06/codex-lb/issues/1062)) ([9e4f766](https://github.com/Soju06/codex-lb/commit/9e4f766f9e6524bd450b7d320e557d9ac57bcf7e))
* **usage:** sync paid-plan upgrades on workspace-less accounts ([#1098](https://github.com/Soju06/codex-lb/issues/1098)) ([65556cb](https://github.com/Soju06/codex-lb/commit/65556cb593425b40e41fefab01167cc6d7b918bf))
* **warmup:** separate idle threshold, fix jitter, redesign warm-up UI ([#1230](https://github.com/Soju06/codex-lb/issues/1230)) ([1c63550](https://github.com/Soju06/codex-lb/commit/1c6355011eac6c6b74687ec11ae0e6c43efe648c))
* **websocket:** avoid fresh retry for tool-output deltas ([#1042](https://github.com/Soju06/codex-lb/issues/1042)) ([a7ec8d2](https://github.com/Soju06/codex-lb/commit/a7ec8d269f39055be5a1dfcf5887667f9d9426ef))
* **zod:** add the zod schema with new enum introduced at [#875](https://github.com/Soju06/codex-lb/issues/875) ([#1102](https://github.com/Soju06/codex-lb/issues/1102)) ([cde6242](https://github.com/Soju06/codex-lb/commit/cde6242e92104af19d90a55de492e25ff85ff5ef))


### Performance Improvements

* **accounts:** serve request-usage summaries from persistent rollup ([#1238](https://github.com/Soju06/codex-lb/issues/1238)) ([f8d4bea](https://github.com/Soju06/codex-lb/commit/f8d4beab1436eacc632462748c21fc57d75f050c))
* **clients:** keep upstream connections and DNS across interactive turns ([#1245](https://github.com/Soju06/codex-lb/issues/1245)) ([08e7a90](https://github.com/Soju06/codex-lb/commit/08e7a9042ed0e380ee4ca59f54a8976da8f2010b))
* **dashboard:** compress responses, cache assets, lazy-load charts ([#1243](https://github.com/Soju06/codex-lb/issues/1243)) ([c01abca](https://github.com/Soju06/codex-lb/commit/c01abcaf0066586cadc8a37e3d846a02c7354a8c))
* **frontend:** code-split dashboard routes ([#1278](https://github.com/Soju06/codex-lb/issues/1278)) ([3e4479d](https://github.com/Soju06/codex-lb/commit/3e4479d94cb3e2b76e33e15b901640ead487c00d))
* **frontend:** self-host JetBrains Mono, drop Google Fonts ([#1279](https://github.com/Soju06/codex-lb/issues/1279)) ([acfb295](https://github.com/Soju06/codex-lb/commit/acfb295e739775b3135dd59f94c0b2376f7df9b4))
* **proxy:** cut pre-upstream DB round trips on interactive turns ([#1241](https://github.com/Soju06/codex-lb/issues/1241)) ([b62797e](https://github.com/Soju06/codex-lb/commit/b62797ec10280e703111a8409be23c183a39fb9d))
* **proxy:** detach stream-end persistence from the response path ([#1269](https://github.com/Soju06/codex-lb/issues/1269)) ([0b43631](https://github.com/Soju06/codex-lb/commit/0b43631630af649f3121ae9c6ed6904447d5c97e))
* **proxy:** make SSE event framing linear instead of quadratic ([#1242](https://github.com/Soju06/codex-lb/issues/1242)) ([ed9d4d5](https://github.com/Soju06/codex-lb/commit/ed9d4d50d06ffd3d46b7f92d4743b6442e4bf9c8))
* **proxy:** parse each SSE event once per layer, skip no-op reserialization ([#1270](https://github.com/Soju06/codex-lb/issues/1270)) ([f3cc5b1](https://github.com/Soju06/codex-lb/commit/f3cc5b12147a2925d71bd5dcad9658ba7c7f818b))
* **request-logs:** cache the listing total per filter signature ([#1277](https://github.com/Soju06/codex-lb/issues/1277)) ([27b982b](https://github.com/Soju06/codex-lb/commit/27b982bce2def6da653ef0dbb8376f34810b8555))
* **request-logs:** skip-scan unfiltered filter-option facets ([#1239](https://github.com/Soju06/codex-lb/issues/1239)) ([7c2cd90](https://github.com/Soju06/codex-lb/commit/7c2cd9073155e524dc1f331898a06f4e42c3d4eb))
* **usage:** aggregate the summary window in SQL, drop dead log refresh ([#1244](https://github.com/Soju06/codex-lb/issues/1244)) ([22544be](https://github.com/Soju06/codex-lb/commit/22544beb3cee2fcf5ddfbd31b5a80cdf38a05a5f))
* **usage:** avoid repeated history cache scans ([#902](https://github.com/Soju06/codex-lb/issues/902)) ([1cf319c](https://github.com/Soju06/codex-lb/commit/1cf319c9196cc3879fa5f5a48317437db6d2408c))


### Documentation

* **codex:** clarify built-in image generation eligibility ([#1181](https://github.com/Soju06/codex-lb/issues/1181)) ([0dafbb4](https://github.com/Soju06/codex-lb/commit/0dafbb42572d51fad3e93173226cc40f5f49c8d2))
* explain routing strategy tradeoffs ([#1119](https://github.com/Soju06/codex-lb/issues/1119)) ([4fb3d41](https://github.com/Soju06/codex-lb/commit/4fb3d410aad56b3fe3b58589e50c53a66e680531))
* **openspec:** archive absent-window-display and label-sync-app-token ([#1303](https://github.com/Soju06/codex-lb/issues/1303)) ([9cb28bb](https://github.com/Soju06/codex-lb/commit/9cb28bb8a78011f047a622445a404dcc9627f607))
* **openspec:** archive adaptive-window and bridge-gate changes ([#1271](https://github.com/Soju06/codex-lb/issues/1271)) ([2f56dca](https://github.com/Soju06/codex-lb/commit/2f56dca2c0b5d281c6e2e8d5a852d273f3bd1e7f))
* **openspec:** archive the live-rate-limit-ingestion change ([#1300](https://github.com/Soju06/codex-lb/issues/1300)) ([36279c5](https://github.com/Soju06/codex-lb/commit/36279c59b490d8e71f597fa421feea441fa6c232))

## [1.20.1](https://github.com/Soju06/codex-lb/compare/v1.20.0...v1.20.1) (2026-06-18)


### Bug Fixes

* **release:** guard stable artifact publishing ([#1049](https://github.com/Soju06/codex-lb/issues/1049)) ([30aa30e](https://github.com/Soju06/codex-lb/commit/30aa30e45f8e3ed5a0542c2973f4d9da6e65c97c))


### Performance Improvements

* **dashboard:** split request log page count ([#1038](https://github.com/Soju06/codex-lb/issues/1038)) ([ea6dc8b](https://github.com/Soju06/codex-lb/commit/ea6dc8bf98236c0bf48f7510a9f9c88adf4bc7e4))

## [1.20.0](https://github.com/Soju06/codex-lb/compare/v1.19.0...v1.20.0) (2026-06-16)


### Features

* **acc-del:** cascade delete request log option  ([#823](https://github.com/Soju06/codex-lb/issues/823)) ([3fac360](https://github.com/Soju06/codex-lb/commit/3fac360b5415f414522cd68dd80a47e7210af5ec))
* **accounts:** add account list sort controls ([#897](https://github.com/Soju06/codex-lb/issues/897)) ([0e413e6](https://github.com/Soju06/codex-lb/commit/0e413e6929261c2ad34c1f060d6d21e70ad4425e))
* **accounts:** add dashboard action for account force-probe ([#895](https://github.com/Soju06/codex-lb/issues/895)) ([72222c5](https://github.com/Soju06/codex-lb/commit/72222c5a815ea0dbc3e67133adae2f5af324f95e))
* **accounts:** export OpenCode auth json ([#757](https://github.com/Soju06/codex-lb/issues/757)) ([530c97f](https://github.com/Soju06/codex-lb/commit/530c97f080093db186cdad92cd30939f64836b77))
* **accounts:** surface email duplicate pairs ([#829](https://github.com/Soju06/codex-lb/issues/829)) ([ab754ff](https://github.com/Soju06/codex-lb/commit/ab754ff0f7e115fe453ea58b108bf69f936afca3)), closes [#787](https://github.com/Soju06/codex-lb/issues/787)
* add reports page with cost/token charts and CSV export ([#854](https://github.com/Soju06/codex-lb/issues/854)) ([f5fcfab](https://github.com/Soju06/codex-lb/commit/f5fcfabe2f849a711e2a70eb12a3ce4acca21de4))
* **api-keys:** add key overview usage stats ([#900](https://github.com/Soju06/codex-lb/issues/900)) ([ed8caab](https://github.com/Soju06/codex-lb/commit/ed8caabec424e6b5b5164b3a2b7df180f55f0547))
* **api:** add app version response header ([#796](https://github.com/Soju06/codex-lb/issues/796)) ([a84d6f4](https://github.com/Soju06/codex-lb/commit/a84d6f4364751ef897ff779b514430615032a856))
* **auth:** proactively refresh stale active accounts ([#928](https://github.com/Soju06/codex-lb/issues/928)) ([163326a](https://github.com/Soju06/codex-lb/commit/163326a3d099e4542e91cca1ca1150c1503f4605))
* **cli:** add Codex session retag command ([#763](https://github.com/Soju06/codex-lb/issues/763)) ([7b67aef](https://github.com/Soju06/codex-lb/commit/7b67aef953d5f01f186720010f8a3eb7456d97b1))
* **config:** flexible location of DATA_DIR by env-variable ([#837](https://github.com/Soju06/codex-lb/issues/837)) ([3787dd3](https://github.com/Soju06/codex-lb/commit/3787dd37e8cdced3ced4cb3e05bd4d0c06d027a4))
* **dashboard-auth:** add read-only guest access ([#703](https://github.com/Soju06/codex-lb/issues/703)) ([1f02ec8](https://github.com/Soju06/codex-lb/commit/1f02ec80314b788352faec2268f4bb93128fa20a))
* **dashboard-ui:** Multiple dashboard changes ([#973](https://github.com/Soju06/codex-lb/issues/973)) ([fc00649](https://github.com/Soju06/codex-lb/commit/fc006492107309a2bbb6a6051539a7e8ecccd488))
* **dashboard:** support weekly pace working days ([#901](https://github.com/Soju06/codex-lb/issues/901)) ([7abbcf8](https://github.com/Soju06/codex-lb/commit/7abbcf8bdf7222bedf19cea4c78566678083db61))
* **frontend:** add upstream proxy controls ([#912](https://github.com/Soju06/codex-lb/issues/912)) ([f1d4826](https://github.com/Soju06/codex-lb/commit/f1d4826579406a960a6355b45f1b8ab05d21e32f))
* **proxy:** add account-bound Codex upstream routing ([#878](https://github.com/Soju06/codex-lb/issues/878)) ([0c01b19](https://github.com/Soju06/codex-lb/commit/0c01b197668613d3e14000a7bbdb1ed93cfd7cc5))
* **proxy:** add SOCKS4/SOCKS5 outbound proxy support via env vars ([#1008](https://github.com/Soju06/codex-lb/issues/1008)) ([c8fcc65](https://github.com/Soju06/codex-lb/commit/c8fcc65a140adf5fdcabd548881b8bd7eb673b64))
* **proxy:** add upstream websocket proxy support ([#758](https://github.com/Soju06/codex-lb/issues/758)) ([4532646](https://github.com/Soju06/codex-lb/commit/4532646e1de3f48339b37bd3024b35f99ab956ce)), closes [#407](https://github.com/Soju06/codex-lb/issues/407)
* **proxy:** restore opportunistic traffic class on main ([#716](https://github.com/Soju06/codex-lb/issues/716)) ([52445aa](https://github.com/Soju06/codex-lb/commit/52445aa70b700e08875c70dadcac18bdb92bdb04))
* **quota:** add reset-confirmed limit warm-up ([#786](https://github.com/Soju06/codex-lb/issues/786)) ([bfc7d6c](https://github.com/Soju06/codex-lb/commit/bfc7d6cd4138047dcc8e691a25019f19286a838f))
* **report:** add window comparison, cleanup openspec and ui fixes ([776dfaa](https://github.com/Soju06/codex-lb/commit/776dfaa30a065ea7a9c4959a5c6b115c236e735d))
* **routing:** add relative availability strategy ([#765](https://github.com/Soju06/codex-lb/issues/765)) ([7227e16](https://github.com/Soju06/codex-lb/commit/7227e169848ffb8580cc0e00f1bc0a9c4260c44f))
* show update indicator for stale runtime version ([#805](https://github.com/Soju06/codex-lb/issues/805)) ([76488a5](https://github.com/Soju06/codex-lb/commit/76488a5e30d120ab54ed063c9a352819437b1733))
* **ui:** log the User-Agent and store it in database ([#882](https://github.com/Soju06/codex-lb/issues/882)) ([1a8e112](https://github.com/Soju06/codex-lb/commit/1a8e11256eae2cbf83587621c453b1f2996ec509))
* **ui:** polish proxy and account dashboard UX ([#937](https://github.com/Soju06/codex-lb/issues/937)) ([522cae5](https://github.com/Soju06/codex-lb/commit/522cae5e4d16e5b1f5abb69de9afb9d21b1500ed))


### Bug Fixes

* **acc:** create `monthly` window for the `free` account due to the policy change ([#909](https://github.com/Soju06/codex-lb/issues/909)) ([50b9add](https://github.com/Soju06/codex-lb/commit/50b9add6dd5425fb74c5fed65cbcda145ca67fcc))
* **accounts:** avoid SQLite window plan for usage trends ([#861](https://github.com/Soju06/codex-lb/issues/861)) ([50b393f](https://github.com/Soju06/codex-lb/commit/50b393f9e987ecef2601382e41ff7c480914497e))
* **accounts:** dedupe request usage rows by request id ([#904](https://github.com/Soju06/codex-lb/issues/904)) ([86e2350](https://github.com/Soju06/codex-lb/commit/86e2350d21fb0b39f3d5f225e46abede49a89e28))
* **accounts:** key imported credentials by workspace ([#865](https://github.com/Soju06/codex-lb/issues/865)) ([47b0c36](https://github.com/Soju06/codex-lb/commit/47b0c3624faf9c40211cbf52957d91ddce4bb5cb))
* **accounts:** mark invalidated credentials for reauth ([#925](https://github.com/Soju06/codex-lb/issues/925)) ([39fb653](https://github.com/Soju06/codex-lb/commit/39fb653c0e779aef30a2ecbfa6608ea4d797a789))
* **accounts:** preserve shared workspace account slots ([#974](https://github.com/Soju06/codex-lb/issues/974)) ([dd4436f](https://github.com/Soju06/codex-lb/commit/dd4436f7dc619894e9a38e6c8f4a0a2510230e7b))
* **api:** tolerate null model filters and truncation ([#886](https://github.com/Soju06/codex-lb/issues/886)) ([cc636d7](https://github.com/Soju06/codex-lb/commit/cc636d7e61bef87bee7f2e8ee988cb8e903fe0c0)), closes [#885](https://github.com/Soju06/codex-lb/issues/885) [#856](https://github.com/Soju06/codex-lb/issues/856)
* **copy:** add clipboard fallback for non-secure contexts ([#843](https://github.com/Soju06/codex-lb/issues/843)) ([58387f0](https://github.com/Soju06/codex-lb/commit/58387f0e5fa0781624f1b355af5df207e9e92297))
* **docker:** pin Postgres upgrade helper digest ([#945](https://github.com/Soju06/codex-lb/issues/945)) ([66a6220](https://github.com/Soju06/codex-lb/commit/66a62204d021fae3d568337daba4c0e685f1a5d0))
* **frontend:** disable browser translation on dashboard ([#908](https://github.com/Soju06/codex-lb/issues/908)) ([a0aea6d](https://github.com/Soju06/codex-lb/commit/a0aea6df9c410185903a021fbef28aa8b40794ea)), closes [#906](https://github.com/Soju06/codex-lb/issues/906)
* **frontend:** guard React DOM against translation mutations ([#929](https://github.com/Soju06/codex-lb/issues/929)) ([ea9f99c](https://github.com/Soju06/codex-lb/commit/ea9f99c6fb253995349fc5a3bee687a179b8b30f))
* **model-registry:** populate bootstrap model metadata ([#750](https://github.com/Soju06/codex-lb/issues/750)) ([5e77320](https://github.com/Soju06/codex-lb/commit/5e77320be78676d6f25a39a7ececaa771d923bb2)), closes [#528](https://github.com/Soju06/codex-lb/issues/528)
* normalize responses instruction messages ([#950](https://github.com/Soju06/codex-lb/issues/950)) ([603c794](https://github.com/Soju06/codex-lb/commit/603c794d74547bd122b8dd43668bcc4637480b89))
* **proxy-responses:** harden concurrent responses routing ([#868](https://github.com/Soju06/codex-lb/issues/868)) ([cb5951d](https://github.com/Soju06/codex-lb/commit/cb5951df0f18f94eac783d57d6f0bac9734c1c7d))
* **proxy:** avoid unsafe websocket previous-response replay ([#802](https://github.com/Soju06/codex-lb/issues/802)) ([b58e724](https://github.com/Soju06/codex-lb/commit/b58e7249a80f04300febbe93362f6230c72fcd16))
* **proxy:** bridge codex compaction triggers ([#977](https://github.com/Soju06/codex-lb/issues/977)) ([bf1ded2](https://github.com/Soju06/codex-lb/commit/bf1ded2e1f994a79cfc05c640ba911ee4e4ffa9c))
* **proxy:** bypass HTTP bridge for input images ([#903](https://github.com/Soju06/codex-lb/issues/903)) ([bcd63c8](https://github.com/Soju06/codex-lb/commit/bcd63c8272b066f0a6ec7d624f07c33b45e26a18))
* **proxy:** classify request budget exhaustion as timeout ([#820](https://github.com/Soju06/codex-lb/issues/820)) ([978797a](https://github.com/Soju06/codex-lb/commit/978797a82a8931f95695d837be2d7cc3cf073703))
* **proxy:** classify stale Codex websocket anchors ([#801](https://github.com/Soju06/codex-lb/issues/801)) ([93ce129](https://github.com/Soju06/codex-lb/commit/93ce1299a73dab1d2f5231bf4d39fb99d2c135ff))
* **proxy:** clear stale HTTP bridge retry errors ([#815](https://github.com/Soju06/codex-lb/issues/815)) ([5aa2162](https://github.com/Soju06/codex-lb/commit/5aa21624ff4528ac9d1cfe17dca37fde9ae2de94))
* **proxy:** extend HTTP Responses stream budget ([#825](https://github.com/Soju06/codex-lb/issues/825)) ([3993c9c](https://github.com/Soju06/codex-lb/commit/3993c9cede74b9ff3450821ae67777885e5b1c13))
* **proxy:** give HTTP bridge long-turn budget ([#819](https://github.com/Soju06/codex-lb/issues/819)) ([ff02923](https://github.com/Soju06/codex-lb/commit/ff029236ae605a481b0d138392a9f88f85de7afc))
* **proxy:** harden responses bridge stale cleanup ([#931](https://github.com/Soju06/codex-lb/issues/931)) ([c90d62b](https://github.com/Soju06/codex-lb/commit/c90d62b72af6bb1bda61650a6b624a4236a4c324))
* **proxy:** improve Cursor chat-completions compatibility ([#833](https://github.com/Soju06/codex-lb/issues/833)) ([a1cb7e0](https://github.com/Soju06/codex-lb/commit/a1cb7e098321287d82046f8f149e16ff0ec05729))
* **proxy:** keep local usage snapshots advisory ([#1030](https://github.com/Soju06/codex-lb/issues/1030)) ([ceb671a](https://github.com/Soju06/codex-lb/commit/ceb671a872de66e2c3513c0cdeb6fea6ccb88d57))
* **proxy:** keep streams alive while account capacity recovers ([#1000](https://github.com/Soju06/codex-lb/issues/1000)) ([8d8061b](https://github.com/Soju06/codex-lb/commit/8d8061bae897863603fd6876d164811a796a5310))
* **proxy:** parse multiline Codex websocket errors ([#810](https://github.com/Soju06/codex-lb/issues/810)) ([1f089f2](https://github.com/Soju06/codex-lb/commit/1f089f2991640847bc8c0c9471f84195aaf5b69f))
* **proxy:** preserve codex image generation tools ([#930](https://github.com/Soju06/codex-lb/issues/930)) ([fb8800f](https://github.com/Soju06/codex-lb/commit/fb8800f463f153bc4861bdb91adcaf7edf24bf6c)), closes [#839](https://github.com/Soju06/codex-lb/issues/839)
* **proxy:** recover websocket terminal auth failures ([#809](https://github.com/Soju06/codex-lb/issues/809)) ([098100f](https://github.com/Soju06/codex-lb/commit/098100f7d84548ab9a2e88b8c4bcc1d9004a6743))
* **proxy:** repair request failure metadata migration ([#870](https://github.com/Soju06/codex-lb/issues/870)) ([0e584fa](https://github.com/Soju06/codex-lb/commit/0e584fa81c0b7e89ac57ba8cc9d5b13b02bd6d54))
* **quota:** keep credit-backed accounts usable ([#764](https://github.com/Soju06/codex-lb/issues/764)) ([6da403a](https://github.com/Soju06/codex-lb/commit/6da403a6195d42943393229cb0c18ba8ba96d277))
* **release:** require validation for canonical beta PRs ([#1032](https://github.com/Soju06/codex-lb/issues/1032)) ([2a64002](https://github.com/Soju06/codex-lb/commit/2a64002b429478273610de00a4ca10f9f5e3d743))
* **repo:** remove Codex sandbox-breaking symlink ([#942](https://github.com/Soju06/codex-lb/issues/942)) ([1b03833](https://github.com/Soju06/codex-lb/commit/1b038334f811af67df54389f2c42345b5391caaa))
* **report-ui:** fix language and onHover Tooltip alignment ([#961](https://github.com/Soju06/codex-lb/issues/961)) ([094d47b](https://github.com/Soju06/codex-lb/commit/094d47be5982dbcde48a48a8723076b5423d6786))
* **report:** add ui changes on report ([776dfaa](https://github.com/Soju06/codex-lb/commit/776dfaa30a065ea7a9c4959a5c6b115c236e735d))
* **report:** multiple fix for report ui ([776dfaa](https://github.com/Soju06/codex-lb/commit/776dfaa30a065ea7a9c4959a5c6b115c236e735d))
* **report:** Multiple fixes and enhances on report ([#990](https://github.com/Soju06/codex-lb/issues/990)) ([776dfaa](https://github.com/Soju06/codex-lb/commit/776dfaa30a065ea7a9c4959a5c6b115c236e735d))
* **security:** harden CodeQL alert surfaces ([#935](https://github.com/Soju06/codex-lb/issues/935)) ([2992b86](https://github.com/Soju06/codex-lb/commit/2992b86d52a1b5fc13cc238ed684b6ef8b816044))
* **sticky-sessions:** chunk delete_entries to avoid sqlite bind overflow ([#828](https://github.com/Soju06/codex-lb/issues/828)) ([20158f9](https://github.com/Soju06/codex-lb/commit/20158f9e5e5e01454f7c9e162f280e01a0b4f27c))


### Performance Improvements

* **dashboard:** load projections in background ([#800](https://github.com/Soju06/codex-lb/issues/800)) ([5c40be8](https://github.com/Soju06/codex-lb/commit/5c40be83dc128754df3c5711d5a5d45f364b9e80))
* **dashboard:** speed up SQLite overview usage reads ([#866](https://github.com/Soju06/codex-lb/issues/866)) ([7b1d208](https://github.com/Soju06/codex-lb/commit/7b1d208ff3f53bf62472c040699e3bd805f6f8da))
* **db:** add raw window usage latest index ([#797](https://github.com/Soju06/codex-lb/issues/797)) ([93be0cd](https://github.com/Soju06/codex-lb/commit/93be0cdc5bbf6f9afedb969d9ff81b7038d41dbf))
* **usage:** avoid SQLite window latest usage lookup ([#862](https://github.com/Soju06/codex-lb/issues/862)) ([65c5f4f](https://github.com/Soju06/codex-lb/commit/65c5f4f56e2f10ca035dbc8a584fedfe77a32f47))


### Documentation

* add plastictaste as a contributor for code, test, and doc ([#1003](https://github.com/Soju06/codex-lb/issues/1003)) ([013b98f](https://github.com/Soju06/codex-lb/commit/013b98fa2d184998f880bc7020eb6ee47fb2911e))
* add PR readiness trapdoors to AGENTS ([#838](https://github.com/Soju06/codex-lb/issues/838)) ([9b0f5c1](https://github.com/Soju06/codex-lb/commit/9b0f5c1e9d5cc17dae6865c199f6527bd38c527a))
* **images:** clarify single-image request contract ([#891](https://github.com/Soju06/codex-lb/issues/891)) ([65b5e72](https://github.com/Soju06/codex-lb/commit/65b5e729be52bb79e0f70fe2cdda8a37d324ac3e))
* **proxy:** explain fast mode service tiers ([#910](https://github.com/Soju06/codex-lb/issues/910)) ([769a12c](https://github.com/Soju06/codex-lb/commit/769a12ca126df42cd7aa086709fbb965e27fa690)), closes [#291](https://github.com/Soju06/codex-lb/issues/291)
* **readme:** clarify plan-dependent model availability ([#893](https://github.com/Soju06/codex-lb/issues/893)) ([5c114a0](https://github.com/Soju06/codex-lb/commit/5c114a08b1fa47c3be1522a43ec7fff9cd861dc9)), closes [#375](https://github.com/Soju06/codex-lb/issues/375) [#219](https://github.com/Soju06/codex-lb/issues/219)
* **readme:** lowercase codex provider name to match Codex 2026-05-23 update ([#798](https://github.com/Soju06/codex-lb/issues/798)) ([a185479](https://github.com/Soju06/codex-lb/commit/a185479f9b080a6c00c503e74ed6947a9e4b5dbe)), closes [#783](https://github.com/Soju06/codex-lb/issues/783)
* **readme:** update all-contributors emoji key link ([#830](https://github.com/Soju06/codex-lb/issues/830)) ([88eeba9](https://github.com/Soju06/codex-lb/commit/88eeba910363ef2b01da6433360479ca282930d6))

## [1.19.0](https://github.com/Soju06/codex-lb/compare/v1.18.2...v1.19.0) (2026-05-25)


### Features

* **accounts:** add export action with audit and no-store safeguards ([#412](https://github.com/Soju06/codex-lb/issues/412)) ([c03e310](https://github.com/Soju06/codex-lb/commit/c03e31023bc0481696f1e85a0d373eaa086ef531))
* **accounts:** add operator-controlled account aliases ([#759](https://github.com/Soju06/codex-lb/issues/759)) ([781e259](https://github.com/Soju06/codex-lb/commit/781e2598b44356c90fc9e5e9c0780a87b59db202))
* **api-ui:** add account cost distribution for API ([#734](https://github.com/Soju06/codex-lb/issues/734)) ([d0a6737](https://github.com/Soju06/codex-lb/commit/d0a6737fb6016a627b23d12810e4a89d7b062ac3))
* **api-ui:** add account pool window usage remaining bar ([#785](https://github.com/Soju06/codex-lb/issues/785)) ([8eee9e2](https://github.com/Soju06/codex-lb/commit/8eee9e2c00e3739d1108f3d98cc4bf075d71900e))
* **api:** add codex /model support for allowed models ([#607](https://github.com/Soju06/codex-lb/issues/607)) ([15874aa](https://github.com/Soju06/codex-lb/commit/15874aa80795486c0bb0a33489a417c8b5613f51))
* **dashboard:** account burn projection card ([#752](https://github.com/Soju06/codex-lb/issues/752)) ([c48a20a](https://github.com/Soju06/codex-lb/commit/c48a20a566aa74213ec909bcc3e798bf835cbeef))
* **dashboard:** present hourly/weekly credits as raw remaining/total ([#612](https://github.com/Soju06/codex-lb/issues/612)) ([b6b2f8b](https://github.com/Soju06/codex-lb/commit/b6b2f8b0b64dbef8b66459e21b9406bdc24a8e94))
* **frontend:** add GitHub link to status bar ([#508](https://github.com/Soju06/codex-lb/issues/508)) ([8577edc](https://github.com/Soju06/codex-lb/commit/8577edc9a2eaeeabc736c2dcc6f3597e2da6e08f))
* **release:** add PR-driven beta release channel ([#732](https://github.com/Soju06/codex-lb/issues/732)) ([72b2962](https://github.com/Soju06/codex-lb/commit/72b2962b1a45f9b4796f4fc11f3f9db69cbceaf0))
* **request-log:** detail cost breakdown for each request ([#694](https://github.com/Soju06/codex-lb/issues/694)) ([cb05d0e](https://github.com/Soju06/codex-lb/commit/cb05d0eca7908b0be6a82ba0ad736382351a6608))
* **ui:** multiple dashboard ui adjustments ([#776](https://github.com/Soju06/codex-lb/issues/776)) ([c933b52](https://github.com/Soju06/codex-lb/commit/c933b527ea285a543fdbc19a94936b8de66eebc4))


### Bug Fixes

* **accounts:** hide zero-capacity primary quota rows ([#770](https://github.com/Soju06/codex-lb/issues/770)) ([8920274](https://github.com/Soju06/codex-lb/commit/8920274add84917a63bf18fbbfb475038ffa778a))
* **accounts:** own DB session in detached token-refresh task ([#774](https://github.com/Soju06/codex-lb/issues/774)) ([3bdc9de](https://github.com/Soju06/codex-lb/commit/3bdc9dea0b524ad0e46a89d1afc727712825eb0b))
* **archive:** stream gzip writes asynchronously ([#725](https://github.com/Soju06/codex-lb/issues/725)) ([67917ca](https://github.com/Soju06/codex-lb/commit/67917ca310775114ac1a6c6f87befa5487d6e857))
* **archive:** throttle backpressure warnings ([#718](https://github.com/Soju06/codex-lb/issues/718)) ([feb0def](https://github.com/Soju06/codex-lb/commit/feb0def4fd69a77ff832bd9ffad97852d8628b45))
* **ci:** harden Codex label sync token writes ([#740](https://github.com/Soju06/codex-lb/issues/740)) ([c40837d](https://github.com/Soju06/codex-lb/commit/c40837dcfeaae8ad22fd73176f8e4f03c0bb1379))
* **ci:** restore main and enforce merge-head gates ([#715](https://github.com/Soju06/codex-lb/issues/715)) ([b061ea5](https://github.com/Soju06/codex-lb/commit/b061ea5d25c503df8d8961d8b0f92d301ee5ef71))
* **ci:** tolerate transient Codex label read failures ([#769](https://github.com/Soju06/codex-lb/issues/769)) ([8fe58fe](https://github.com/Soju06/codex-lb/commit/8fe58fe53f6c8c40196043e9a772e2fe78da9f4c))
* **codex desktop:** restore backend responses compatibility ([#756](https://github.com/Soju06/codex-lb/issues/756)) ([fe591b7](https://github.com/Soju06/codex-lb/commit/fe591b727c9a06aadb5689e1dc9c52550c85b29e))
* Codex websocket pre-created keepalives ([#727](https://github.com/Soju06/codex-lb/issues/727)) ([f52167d](https://github.com/Soju06/codex-lb/commit/f52167dd6b8e7dee3f40b84e118f4c412a8c2ec9))
* **codex_version:** fall back to npm registry when GitHub is rate-limited ([#744](https://github.com/Soju06/codex-lb/issues/744)) ([7d790ba](https://github.com/Soju06/codex-lb/commit/7d790bae893b62ff7216c1c3c0f4f11e17015d4f)), closes [#664](https://github.com/Soju06/codex-lb/issues/664)
* **codex:** accept OpenAI-style backend responses requests ([#755](https://github.com/Soju06/codex-lb/issues/755)) ([838386c](https://github.com/Soju06/codex-lb/commit/838386cc0c66c6c0d5ae42e3bd1d20b8681cc313))
* **limit-warmup:** refresh opt-in state before warm-up ([#778](https://github.com/Soju06/codex-lb/issues/778)) ([a96c487](https://github.com/Soju06/codex-lb/commit/a96c4876359aa811592e76537cc92125e45c5a3a))
* **oauth:** isolate concurrent browser flows ([#753](https://github.com/Soju06/codex-lb/issues/753)) ([409a83e](https://github.com/Soju06/codex-lb/commit/409a83ef5a9c3026a7549c4a63cef4daae1df5cb))
* **openai:** preserve json mode instruction messages ([#731](https://github.com/Soju06/codex-lb/issues/731)) ([b48ed67](https://github.com/Soju06/codex-lb/commit/b48ed67bef119e5dc0929df20188630b3a0cc5b5)), closes [#730](https://github.com/Soju06/codex-lb/issues/730)
* **proxy:** accept /backend-api/codex/v1/&lt;rest&gt; as alias for /backend-api/codex/&lt;rest&gt; ([#610](https://github.com/Soju06/codex-lb/issues/610)) ([0aaaa80](https://github.com/Soju06/codex-lb/commit/0aaaa80d69078634dc9b5fe471da2a2d02e3004f))
* **proxy:** add HTTP bridge keepalive backstop and prewarm timeout ([#736](https://github.com/Soju06/codex-lb/issues/736)) ([28c2430](https://github.com/Soju06/codex-lb/commit/28c243078f7b10d8c08f01f3c6f3bd02f229d056))
* **proxy:** align Codex websocket error parsing ([#789](https://github.com/Soju06/codex-lb/issues/789)) ([714315f](https://github.com/Soju06/codex-lb/commit/714315f4d443dddf1256d7872a123c639316978a))
* **proxy:** allow larger compressed responses bodies ([#772](https://github.com/Soju06/codex-lb/issues/772)) ([67795a1](https://github.com/Soju06/codex-lb/commit/67795a1ae8e5420070502bee5ad029182471bef5))
* **proxy:** bound HTTP bridge startup waits ([#723](https://github.com/Soju06/codex-lb/issues/723)) ([48e7ccf](https://github.com/Soju06/codex-lb/commit/48e7ccf924260255d912d0b9e637b3be37537c8c))
* **proxy:** fail over compact after invalidated token ([#777](https://github.com/Soju06/codex-lb/issues/777)) ([afd23d2](https://github.com/Soju06/codex-lb/commit/afd23d229901d39c99f15bc0bd686b6c7df73ce2))
* **proxy:** fail over reset-prone upstream stalls ([#771](https://github.com/Soju06/codex-lb/issues/771)) ([13dcf74](https://github.com/Soju06/codex-lb/commit/13dcf7434f65ab0ba4a8c1f1136d05b602f4cb01))
* **proxy:** fail over websocket connect timeouts ([#726](https://github.com/Soju06/codex-lb/issues/726)) ([a8b44f7](https://github.com/Soju06/codex-lb/commit/a8b44f77d8ef954e983095ff47fbff21c96e3e8c))
* **proxy:** inline external image URLs in HTTP bridge path ([#794](https://github.com/Soju06/codex-lb/issues/794)) ([5ff6679](https://github.com/Soju06/codex-lb/commit/5ff6679e3b6fc1801080a95cc62986a78d48b7be))
* **proxy:** make Codex Spark quota gating plan-aware ([#751](https://github.com/Soju06/codex-lb/issues/751)) ([a476ecd](https://github.com/Soju06/codex-lb/commit/a476ecd866e9e1f8507be645146aceafec9eb082))
* **proxy:** mask codex chatgpt previous-response websocket errors ([#775](https://github.com/Soju06/codex-lb/issues/775)) ([d39350f](https://github.com/Soju06/codex-lb/commit/d39350f323cfcc024f3c2e125a5d3c813772a933))
* **proxy:** mask websocket prepare continuity errors ([#717](https://github.com/Soju06/codex-lb/issues/717)) ([a0a290b](https://github.com/Soju06/codex-lb/commit/a0a290b0f9c9e2b82dad10a9e6812e84d507cea0))
* **proxy:** recover stale websocket previous response anchors ([#724](https://github.com/Soju06/codex-lb/issues/724)) ([48f083e](https://github.com/Soju06/codex-lb/commit/48f083ef1ffb74be867636abd9fc737e5086692b))
* **proxy:** replay pre-visible websocket drops ([#729](https://github.com/Soju06/codex-lb/issues/729)) ([4471b9a](https://github.com/Soju06/codex-lb/commit/4471b9af4254505b7aa46eeb2097049f215ff6c7))
* **proxy:** report backend context window in v1 models ([#722](https://github.com/Soju06/codex-lb/issues/722)) ([ebabd31](https://github.com/Soju06/codex-lb/commit/ebabd312e23ab62b7a718da332457989d6311e12))
* **proxy:** respect stream_idle_timeout_seconds in HTTP bridge keepalive backstop ([#793](https://github.com/Soju06/codex-lb/issues/793)) ([ccdf99f](https://github.com/Soju06/codex-lb/commit/ccdf99fd4b3695b7f9933af9886b0f9e5d252477))
* **settings:** include all updatable fields in audit changed_fields ([#719](https://github.com/Soju06/codex-lb/issues/719)) ([def95bb](https://github.com/Soju06/codex-lb/commit/def95bb8dc085e83f869a6919a054a4a7502f11a))
* **status:** reconcile background account recovery after resets ([#754](https://github.com/Soju06/codex-lb/issues/754)) ([4b9ada8](https://github.com/Soju06/codex-lb/commit/4b9ada8e398bb4cc50471e2fd3c6186b367b96ed)), closes [#479](https://github.com/Soju06/codex-lb/issues/479)
* **usage:** ignore stale usage after account reset ([#760](https://github.com/Soju06/codex-lb/issues/760)) ([d739ebf](https://github.com/Soju06/codex-lb/commit/d739ebf142b59a06b013f14cb558f004e207939d))


### Performance Improvements

* **dashboard:** memoize per-account depletion EWMA state ([#749](https://github.com/Soju06/codex-lb/issues/749)) ([2abe7a9](https://github.com/Soju06/codex-lb/commit/2abe7a98c7ac95fcc9348b09858dc1f0b7a524b2))


### Documentation

* add Lotfree618 as a contributor for code, test, and 2 more ([#739](https://github.com/Soju06/codex-lb/issues/739)) ([66764f4](https://github.com/Soju06/codex-lb/commit/66764f44aa81e378e0c43fe5e549de766d550d1a))
* add usage reset troubleshooting FAQ ([#710](https://github.com/Soju06/codex-lb/issues/710)) ([b6c35f6](https://github.com/Soju06/codex-lb/commit/b6c35f6cfa6a35f56b93718305ca1755773aac61))
* backfill missing contributors ([#741](https://github.com/Soju06/codex-lb/issues/741)) ([505a208](https://github.com/Soju06/codex-lb/commit/505a2081f5a1a3e7a195888a454d46a822546b80))

## [1.18.2](https://github.com/Soju06/codex-lb/compare/v1.18.1...v1.18.2) (2026-05-19)


### Bug Fixes

* **api-keys:** map enforced service_tier auto/default to outbound omission ([#611](https://github.com/Soju06/codex-lb/issues/611)) ([9a64e23](https://github.com/Soju06/codex-lb/commit/9a64e238f185bfee48069029de9a25b3413ad4d8))
* **api-keys:** size usage reservations from request budget ([#706](https://github.com/Soju06/codex-lb/issues/706)) ([ea72eea](https://github.com/Soju06/codex-lb/commit/ea72eeaac9b8512ba2684f5617c5e6fa04a39fc1))
* **dashboard:** derive quota status from usage windows ([#686](https://github.com/Soju06/codex-lb/issues/686)) ([c463def](https://github.com/Soju06/codex-lb/commit/c463deffecafd745c96a5a9a3fe053e1e6b9f9bf))
* **dashboard:** make weekly credit pace backend-driven ([#692](https://github.com/Soju06/codex-lb/issues/692)) ([313783c](https://github.com/Soju06/codex-lb/commit/313783c17294ded64b4a0fc320384da25308bf3b))
* **db:** recover stale reservations and serialize sqlite writers ([#667](https://github.com/Soju06/codex-lb/issues/667)) ([6635628](https://github.com/Soju06/codex-lb/commit/663562892396d53affc2f2ddb12790d30487e970))
* **model-refresh:** refresh HTTP client on transport errors ([#547](https://github.com/Soju06/codex-lb/issues/547)) ([2087df0](https://github.com/Soju06/codex-lb/commit/2087df09984bf773c4ee1d5dff4ac976162dab52))
* **models:** report full context in v1 metadata ([#681](https://github.com/Soju06/codex-lb/issues/681)) ([c3c977d](https://github.com/Soju06/codex-lb/commit/c3c977d9fdfd5e2fc2a9b7f1903622fd8de46c89))
* **proxy:** cap selector "Try again in" hint at 300s ([#690](https://github.com/Soju06/codex-lb/issues/690)) ([cf09bd6](https://github.com/Soju06/codex-lb/commit/cf09bd6d4ad1bfad91b458681f491ec36fef9772)), closes [#676](https://github.com/Soju06/codex-lb/issues/676) [#678](https://github.com/Soju06/codex-lb/issues/678)
* **proxy:** eliminate /v1 responses cancel/retry stream contamination ([#696](https://github.com/Soju06/codex-lb/issues/696)) ([c9da497](https://github.com/Soju06/codex-lb/commit/c9da4974c9b10217e83f7dc0a81f5207337c7c58))
* **proxy:** harden long Codex websocket turns ([#674](https://github.com/Soju06/codex-lb/issues/674)) ([12bec0f](https://github.com/Soju06/codex-lb/commit/12bec0f37f853342d3fa6eab1624bccd1c20d961))
* **proxy:** include sticky thread in budget-pressure guard ([#514](https://github.com/Soju06/codex-lb/issues/514)) ([1e2c6d6](https://github.com/Soju06/codex-lb/commit/1e2c6d6fb49bb74bae2707f7afb5de6514c95a73))
* **proxy:** keep idle timeout classification after scheduler jitter ([#693](https://github.com/Soju06/codex-lb/issues/693)) ([17e8abc](https://github.com/Soju06/codex-lb/commit/17e8abc0eca7cb823eb3c7f7e9687b45dca920a8))
* **proxy:** mask partial previous-response stream errors ([#689](https://github.com/Soju06/codex-lb/issues/689)) ([a4a138c](https://github.com/Soju06/codex-lb/commit/a4a138cd3003ad891212760f4ffe63bab798e9f7))
* **proxy:** persist request logs outside cancellation ([#688](https://github.com/Soju06/codex-lb/issues/688)) ([6e8fa56](https://github.com/Soju06/codex-lb/commit/6e8fa56fe07967d74b7e84b7757238425a989e5f))
* **proxy:** repair interrupted Codex response continuity ([#698](https://github.com/Soju06/codex-lb/issues/698)) ([682447f](https://github.com/Soju06/codex-lb/commit/682447f2981f608bd94e19dce3c58a464c564568))
* **proxy:** trim durable full-resend continuations ([#687](https://github.com/Soju06/codex-lb/issues/687)) ([dfc38c0](https://github.com/Soju06/codex-lb/commit/dfc38c0d35398012086e2bac7c92c27a13ce8441))
* **security:** remediate Docker code scanning alerts ([#699](https://github.com/Soju06/codex-lb/issues/699)) ([de24c2e](https://github.com/Soju06/codex-lb/commit/de24c2ea374b3b398792dd2e61f2cb7efcab233d))


### Documentation

* add AnobleSCM as a contributor for code, and test ([#695](https://github.com/Soju06/codex-lb/issues/695)) ([718931e](https://github.com/Soju06/codex-lb/commit/718931e7d036cd2ab57cec9a9d6507bb5231c3d8))
* add jgorostegui as contributor ([#697](https://github.com/Soju06/codex-lb/issues/697)) ([d9d5a6e](https://github.com/Soju06/codex-lb/commit/d9d5a6e8d8bf1067ec8db4e98aeb2855e5a75de5))
* add linusmixson as contributor ([#705](https://github.com/Soju06/codex-lb/issues/705)) ([f3ad60a](https://github.com/Soju06/codex-lb/commit/f3ad60aa4933ffde83096bc7c4da7b224cb4ce84))

## [1.18.1](https://github.com/Soju06/codex-lb/compare/v1.18.0...v1.18.1) (2026-05-17)


### Bug Fixes

* **db,firewall:** validate pooled connections + raise IP cache TTL ([#679](https://github.com/Soju06/codex-lb/issues/679)) ([f46a5de](https://github.com/Soju06/codex-lb/commit/f46a5de6afed70553381d76532b477ad9fdb2ecd))
* **db,firewall:** validate pooled connections + raise IP cache TTL ([#679](https://github.com/Soju06/codex-lb/issues/679)) ([f46a5de](https://github.com/Soju06/codex-lb/commit/f46a5de6afed70553381d76532b477ad9fdb2ecd)), closes [#672](https://github.com/Soju06/codex-lb/issues/672)
* **proxy:** proxy Codex control-plane protocol ([#596](https://github.com/Soju06/codex-lb/issues/596)) ([1ca7d2e](https://github.com/Soju06/codex-lb/commit/1ca7d2e8a20375f0062866de6296792954ff37a4))
* **proxy:** replay HTTP bridge quota failures ([#634](https://github.com/Soju06/codex-lb/issues/634)) ([ad34477](https://github.com/Soju06/codex-lb/commit/ad3447776e8312f8bd1967dfaa659b8961916882))
* **proxy:** suppress duplicate side-effect tool calls ([#586](https://github.com/Soju06/codex-lb/issues/586)) ([bc5d1bd](https://github.com/Soju06/codex-lb/commit/bc5d1bd0a97cbcc2a292ec4f615185409322327d))


### Documentation

* add balakumardev as a contributor for ideas ([#468](https://github.com/Soju06/codex-lb/issues/468)) ([1c75087](https://github.com/Soju06/codex-lb/commit/1c750879c16f2714a25055af93ba6fec60f15d6c))

## [1.18.0](https://github.com/Soju06/codex-lb/compare/v1.17.0...v1.18.0) (2026-05-17)


### Features

* **accounts:** expose weekly token pace data ([#655](https://github.com/Soju06/codex-lb/issues/655)) ([9bd5bc3](https://github.com/Soju06/codex-lb/commit/9bd5bc3b73803935fff437437b0a038002177265))
* **acc:** preserve metrics when the account is deleted ([#640](https://github.com/Soju06/codex-lb/issues/640)) ([4aee3aa](https://github.com/Soju06/codex-lb/commit/4aee3aaa26f32bd2c6ac7206afefc70637bc4ea3))
* **dashboard:** show weekly token pace card ([#656](https://github.com/Soju06/codex-lb/issues/656)) ([998e2f0](https://github.com/Soju06/codex-lb/commit/998e2f058c2218a69377ce054199d76d4e181107))
* **observability:** add conversation archive backend ([#653](https://github.com/Soju06/codex-lb/issues/653)) ([1473269](https://github.com/Soju06/codex-lb/commit/147326907c840f7641bbd353807388743dd6a74b))
* **observability:** add conversation archive viewer ([#654](https://github.com/Soju06/codex-lb/issues/654)) ([06ceac7](https://github.com/Soju06/codex-lb/commit/06ceac750b2e68d5b9d7cb0080f87a7cffcaf5cc))


### Bug Fixes

* **api:** add back the account selection on api creation ([#594](https://github.com/Soju06/codex-lb/issues/594)) ([f9b62bf](https://github.com/Soju06/codex-lb/commit/f9b62bfdee061a2b1011e804041f631cee1804ec))
* **auth:** deactivate account on refresh-time token_expired ([#600](https://github.com/Soju06/codex-lb/issues/600)) ([7b0e0ff](https://github.com/Soju06/codex-lb/commit/7b0e0ff9a6b28020e87dbe23c17c1cc8fb498298))
* **chat-completions:** drop unknown message-object keys during coercion ([#637](https://github.com/Soju06/codex-lb/issues/637)) ([d314d00](https://github.com/Soju06/codex-lb/commit/d314d00ccfd60245e8792b8282766e94742a3cb8))
* **dashboard-auth:** reject passwords longer than bcrypt's 72-byte limit ([#598](https://github.com/Soju06/codex-lb/issues/598)) ([d9b0493](https://github.com/Soju06/codex-lb/commit/d9b0493879a4e2059f70838c4334eda47c900dd2))
* **frontend/auth:** cap dashboard password at 72 UTF-8 bytes ([#615](https://github.com/Soju06/codex-lb/issues/615)) ([#647](https://github.com/Soju06/codex-lb/issues/647)) ([7ba02b9](https://github.com/Soju06/codex-lb/commit/7ba02b97740ab642708b46732bc542ef5d01f2c2))
* **oauth:** start device polling on oauth start ([#635](https://github.com/Soju06/codex-lb/issues/635)) ([94fa0bc](https://github.com/Soju06/codex-lb/commit/94fa0bc3f615679157a82e151b372370842dc23a))
* **proxy:** classify upstream overloaded_error as retryable transient ([#601](https://github.com/Soju06/codex-lb/issues/601)) ([8ef0833](https://github.com/Soju06/codex-lb/commit/8ef0833c58a8a8964f5b1d42914bc83494320d52))
* **proxy:** make /v1/responses streams parseable by the OpenAI SDK ([#639](https://github.com/Soju06/codex-lb/issues/639)) ([febf4b6](https://github.com/Soju06/codex-lb/commit/febf4b63156598084cafd209ed88534cffd90ad6))
* **proxy:** mask single previous response misses ([#516](https://github.com/Soju06/codex-lb/issues/516)) ([67cc11f](https://github.com/Soju06/codex-lb/commit/67cc11f831a1e39fc1a91a709b2168e37016e9b5))
* **proxy:** pre-validate strict function tool schemas ([#658](https://github.com/Soju06/codex-lb/issues/658)) ([0998cac](https://github.com/Soju06/codex-lb/commit/0998cacd25f9e057a790155c07b2d121a697d46e))
* **proxy:** support Pro Lite account routing ([#558](https://github.com/Soju06/codex-lb/issues/558)) ([a737c7e](https://github.com/Soju06/codex-lb/commit/a737c7e66e9464ef81d64d445b440019f8e90762))
* **proxy:** trim anchored http bridge replay inputs ([#652](https://github.com/Soju06/codex-lb/issues/652)) ([3d682a6](https://github.com/Soju06/codex-lb/commit/3d682a6dd0d2a2b99a8ed9a164e81403fcb8bce4))
* **proxy:** trim replayed websocket tool inputs ([#651](https://github.com/Soju06/codex-lb/issues/651)) ([0ea6293](https://github.com/Soju06/codex-lb/commit/0ea6293ea60282880cd39e46e291f0eafdeb16c0))
* **proxy:** trim websocket codex full-replay continuations ([#650](https://github.com/Soju06/codex-lb/issues/650)) ([e503b06](https://github.com/Soju06/codex-lb/commit/e503b068bea04eec70a5db9d5d0c3ddd40fab7f9))
* remove unsupported `type` field from issue forms ([#638](https://github.com/Soju06/codex-lb/issues/638)) ([a1ef739](https://github.com/Soju06/codex-lb/commit/a1ef73925391523752e9260b7f230eabe0743b64))


### Performance Improvements

* add index to db ([#613](https://github.com/Soju06/codex-lb/issues/613)) ([852be82](https://github.com/Soju06/codex-lb/commit/852be82861794893c9429d5add1a1b1250155f1c))


### Documentation

* add aruis as a contributor for code, test, and bug ([#646](https://github.com/Soju06/codex-lb/issues/646)) ([ba1948b](https://github.com/Soju06/codex-lb/commit/ba1948be1085290897785d63acca66d2e1560e00))
* add issue forms, PR template, and community health files ([#614](https://github.com/Soju06/codex-lb/issues/614)) ([d1d6360](https://github.com/Soju06/codex-lb/commit/d1d6360d2c88159f87bbc4ac2ec7b43e10fa29e8))
* add JeffKandt as a contributor for test, and review ([#606](https://github.com/Soju06/codex-lb/issues/606)) ([28332ad](https://github.com/Soju06/codex-lb/commit/28332adf9114233af286c38caacc7089458dfc4c))
* add jumski, mgwals, ozpool, klaascommerce as contributors ([#641](https://github.com/Soju06/codex-lb/issues/641)) ([ff2f237](https://github.com/Soju06/codex-lb/commit/ff2f237e759d358a69327e8508e80883a5cddcaa))
* add Komzpa as a contributor for code, and bug ([#603](https://github.com/Soju06/codex-lb/issues/603)) ([8a0cfdb](https://github.com/Soju06/codex-lb/commit/8a0cfdb326a699415e88280ba0582f3fb40ed324))
* **contributing:** codify merge gates, collaborator rules, and bus-factor clause ([#642](https://github.com/Soju06/codex-lb/issues/642)) ([91b39fd](https://github.com/Soju06/codex-lb/commit/91b39fdb897bd9a88a45a983b6e28e636e8524af))

## [1.17.0](https://github.com/Soju06/codex-lb/compare/v1.16.0...v1.17.0) (2026-05-12)


### Features

* **dash:** sort the pie in decending order for better graphing. ([#580](https://github.com/Soju06/codex-lb/issues/580)) ([3a60855](https://github.com/Soju06/codex-lb/commit/3a60855f31d86c28e5a0ab7990cead61da4f0e52))


### Bug Fixes

* **accounts:** recover quota status from usage refresh ([#559](https://github.com/Soju06/codex-lb/issues/559)) ([ee747d3](https://github.com/Soju06/codex-lb/commit/ee747d373b50386461ed41ec6dc463cb14912a57))
* **chat:** resolve parallel tool_call argument duplication via output_index routing ([#543](https://github.com/Soju06/codex-lb/issues/543)) ([6b1cb47](https://github.com/Soju06/codex-lb/commit/6b1cb47e3e39a9825b25282b0f8ec2911743786a))
* **proxy:** emit text deltas for final response output ([#504](https://github.com/Soju06/codex-lb/issues/504)) ([b9c2474](https://github.com/Soju06/codex-lb/commit/b9c24748eb983a002c007fe5fbd8dbfc48135efa))
* **proxy:** expose drain status for graceful deploys ([#564](https://github.com/Soju06/codex-lb/issues/564)) ([a2fca41](https://github.com/Soju06/codex-lb/commit/a2fca41791bd62862a439b7562b7b69deefa615d))
* **proxy:** handle model fetch timeouts ([#541](https://github.com/Soju06/codex-lb/issues/541)) ([d4520d7](https://github.com/Soju06/codex-lb/commit/d4520d7ad7d15d3676b1ba46a91be149f7fe2854))
* **proxy:** inject SSE keepalive comment frames to prevent client stream hangs [Codex getting stuck issue] ([#524](https://github.com/Soju06/codex-lb/issues/524)) ([66302c3](https://github.com/Soju06/codex-lb/commit/66302c3efe026813fb4bd48c5754fc3b7926dfbd))
* **proxy:** narrow budget-safe gate to primary usage ([#561](https://github.com/Soju06/codex-lb/issues/561)) ([3ed7834](https://github.com/Soju06/codex-lb/commit/3ed78347e462c99f9b6950534b395e4850ae6e1b))
* **proxy:** preserve websocket stream error statuses ([#585](https://github.com/Soju06/codex-lb/issues/585)) ([8530f89](https://github.com/Soju06/codex-lb/commit/8530f89374e9c648f5fb91deaa9adaf244c3535c))
* there is no primary quota for free account, which cause the pie chart wrong on 5h ([#579](https://github.com/Soju06/codex-lb/issues/579)) ([97b1de4](https://github.com/Soju06/codex-lb/commit/97b1de44e437bc9ed7a5405ffc42474d18ac71bb))
* **upstream:** drop top_p because majority of gpt-5 don't support it, same as temperature ([#538](https://github.com/Soju06/codex-lb/issues/538)) ([c7cc61e](https://github.com/Soju06/codex-lb/commit/c7cc61e4a88467517912c901e483f09d0130e1bb))


### Documentation

* add e1ektr0 as a contributor for code, and test ([#581](https://github.com/Soju06/codex-lb/issues/581)) ([26372cc](https://github.com/Soju06/codex-lb/commit/26372cc273a45c07b84234799ae0da147e71c683))

## [1.16.0](https://github.com/Soju06/codex-lb/compare/v1.15.0...v1.16.0) (2026-05-10)


### Features

* **accounts:** split compact quota row display ([#562](https://github.com/Soju06/codex-lb/issues/562)) ([9581fe7](https://github.com/Soju06/codex-lb/commit/9581fe7c65f025780943486757a0c8020d7b7917))
* add API key filter for dashboard request logs ([#497](https://github.com/Soju06/codex-lb/issues/497)) ([43cbdac](https://github.com/Soju06/codex-lb/commit/43cbdac318c3b84944f010c1bc8421b81a4cf605))
* **auth:** make dashboard session lifetime configurable ([#465](https://github.com/Soju06/codex-lb/issues/465)) ([386e0e9](https://github.com/Soju06/codex-lb/commit/386e0e93ca0bfb5d086e2a260c8e491c226f4f0a))
* **proxy:** add /backend-api/files upload protocol ([#515](https://github.com/Soju06/codex-lb/issues/515)) ([7ecb77a](https://github.com/Soju06/codex-lb/commit/7ecb77ae854591e1e6c4e50e785573c74b7ca68e))
* **proxy:** add OpenAI-compatible /v1/images API (gpt-image-2 via image_generation tool) ([#498](https://github.com/Soju06/codex-lb/issues/498)) ([359743d](https://github.com/Soju06/codex-lb/commit/359743d086e45b0b4ca14502d8d3fbfea628b939))
* **proxy:** make upstream response.create max bytes configurable via env var ([#476](https://github.com/Soju06/codex-lb/issues/476)) ([753c040](https://github.com/Soju06/codex-lb/commit/753c040dbd77634f6b281cbe4e1f1f6992fee1cf))


### Bug Fixes

* **api-limit:** Add fallback for api limit reset ([#475](https://github.com/Soju06/codex-lb/issues/475)) ([61386dc](https://github.com/Soju06/codex-lb/commit/61386dcc5d24147a46f30201fdb7d2879c9e8b9d))
* **auth:** preserve existing session expiry through TOTP and tighten hour input ([#511](https://github.com/Soju06/codex-lb/issues/511)) ([4b10807](https://github.com/Soju06/codex-lb/commit/4b1080730aa4bfd3ef9fd35b843ac0743bb6f8ae))
* **chat-completions:** normalize provider thinking aliases ([#424](https://github.com/Soju06/codex-lb/issues/424)) ([4419771](https://github.com/Soju06/codex-lb/commit/4419771c0c7791b20a899c07a65c74879c56f978))
* **db:** size background pool for burst traffic ([#563](https://github.com/Soju06/codex-lb/issues/563)) ([1e397e8](https://github.com/Soju06/codex-lb/commit/1e397e806de92a8eb7f8fbb9326ffdebdd75e6ea))
* **oauth:** make manual callback idempotent ([#481](https://github.com/Soju06/codex-lb/issues/481)) ([c72b68a](https://github.com/Soju06/codex-lb/commit/c72b68a74c9010a34e9503557f7fe027d6cfb922))
* **openspec:** satisfy strict purpose validation ([#552](https://github.com/Soju06/codex-lb/issues/552)) ([1d88236](https://github.com/Soju06/codex-lb/commit/1d882362bf9813488f1b2d9f40f32d67d491b453))
* **proxy:** reject `input_image.file_id` / `sediment://` with 400 `unsupported_input_image_format`, plus HTTP responses bridge hardening (close-code 1000 fail-fast classifier, payload-size HTTP transport auto-fallback, per-request bridge bypass for oversized payloads). The original inline-rewrite from #571 was narrowed in [#574](https://github.com/Soju06/codex-lb/pull/574) after upstream verification showed `input_image.file_id` is not an accepted shape on the Responses API. ([#571](https://github.com/Soju06/codex-lb/pull/571), [#574](https://github.com/Soju06/codex-lb/pull/574))
* **proxy:** load balancer filter ([#485](https://github.com/Soju06/codex-lb/issues/485)) ([b7b150d](https://github.com/Soju06/codex-lb/commit/b7b150d7bc91a375e65483dc896652d19d4595df))
* **proxy:** map unsupported reasoning effort 'minimal' to a supported value ([#494](https://github.com/Soju06/codex-lb/issues/494)) ([5278f84](https://github.com/Soju06/codex-lb/commit/5278f847c2dce72d7118761e152dc17f213b9854))
* **proxy:** pre-validate strict JSON schemas to surface invalid_json_schema ([#491](https://github.com/Soju06/codex-lb/issues/491)) ([#495](https://github.com/Soju06/codex-lb/issues/495)) ([ecc1bca](https://github.com/Soju06/codex-lb/commit/ecc1bcae17ad916684419a15ee440d475d21947d))
* **proxy:** retry transient stream timeouts ([#551](https://github.com/Soju06/codex-lb/issues/551)) ([77944c9](https://github.com/Soju06/codex-lb/commit/77944c93b61c516f205681e1b09bbba38c46f88e))
* **proxy:** return api-key limits from v1 usage ([#501](https://github.com/Soju06/codex-lb/issues/501)) ([694ec18](https://github.com/Soju06/codex-lb/commit/694ec180881cbd89c949e68d93e77fc4c9465a2d))
* **proxy:** use `DEFAULT_HOME_DIR` for oversized `response.create` dumps so non-container deploys (notably macOS `uv tool` / LaunchAgent installs) can write the debug dump path. Resolves [#556](https://github.com/Soju06/codex-lb/issues/556). ([#575](https://github.com/Soju06/codex-lb/pull/575))
* **proxy:** revert `slim oversized response.create history` from #560. The history-slimming approach conflicted with prompt-cache affinity, mis-trained the assistant on its own prior speech via the omission notice, and could break the WebSocket `previous_response_id` continuity. The portable dump-path slice from #560 was re-landed in #575; the broader design discussion is tracked in [#568](https://github.com/Soju06/codex-lb/issues/568). ([#569](https://github.com/Soju06/codex-lb/pull/569))
* **types:** clear existing ty diagnostics ([#517](https://github.com/Soju06/codex-lb/issues/517)) ([0cd5d4e](https://github.com/Soju06/codex-lb/commit/0cd5d4ebe49e4507e4aaa937c940f198e77ce7b0))


### Documentation

* add Komzpa as a contributor for code ([#531](https://github.com/Soju06/codex-lb/issues/531)) ([5bf5d94](https://github.com/Soju06/codex-lb/commit/5bf5d944fc200833cec0f3b82391c3a3333396cb))
* add mikabytes as a contributor for code, doc, and test ([#509](https://github.com/Soju06/codex-lb/issues/509)) ([22cc5f8](https://github.com/Soju06/codex-lb/commit/22cc5f8ef1e416ef6b374f484469edf4e5f24c0b))
* add rio-jeong as a contributor for code, bug, and test ([#492](https://github.com/Soju06/codex-lb/issues/492)) ([f1e2d90](https://github.com/Soju06/codex-lb/commit/f1e2d906f270a402a2c881885c51ae84fdc06fee))
* add stemirkhan as a contributor for bug ([#505](https://github.com/Soju06/codex-lb/issues/505)) ([7170b13](https://github.com/Soju06/codex-lb/commit/7170b1368070e0e9b5954d8a4da2c513f72f3442))
* add stemirkhan as a contributor for code, doc, and test ([#503](https://github.com/Soju06/codex-lb/issues/503)) ([dbda5c7](https://github.com/Soju06/codex-lb/commit/dbda5c74a188399190272b1a9a4c108a57678930))
* add tobwen as a contributor for code, test, and bug ([#489](https://github.com/Soju06/codex-lb/issues/489)) ([1ac1fe2](https://github.com/Soju06/codex-lb/commit/1ac1fe2f5771d6549e1e6c15a7add2ef38ac5912))

## [1.15.0](https://github.com/Soju06/codex-lb/compare/v1.14.1...v1.15.0) (2026-04-24)


### Features

* **proxy:** add GPT-5.5 and GPT-5.5 Pro model support ([#477](https://github.com/Soju06/codex-lb/issues/477)) ([9c2cd97](https://github.com/Soju06/codex-lb/commit/9c2cd972687ec717b53308b154ad1c0044391a87))


### Bug Fixes

* **proxy:** inject session-level previous_response_id to enable input trimming for all clients ([#456](https://github.com/Soju06/codex-lb/issues/456)) ([637fa85](https://github.com/Soju06/codex-lb/commit/637fa85e6aadc4ef363e379d5a3acb2a5bbbf900))
* **proxy:** prevent admission semaphore leak and raise concurrency limits ([#466](https://github.com/Soju06/codex-lb/issues/466)) ([015f669](https://github.com/Soju06/codex-lb/commit/015f669e44826ac4373f9410ba78d596b97995ae))

## [1.14.1](https://github.com/Soju06/codex-lb/compare/v1.14.0...v1.14.1) (2026-04-22)


### Bug Fixes

* **bootstrap:** log first-run token at WARNING, not INFO ([#459](https://github.com/Soju06/codex-lb/issues/459)) ([179cb4a](https://github.com/Soju06/codex-lb/commit/179cb4a825831d91cbd5d5b22b3805c212b44536))
* **proxy:** harden continuity recovery, safe WS replay, and shutdown/restart bridge lifecycle ([#415](https://github.com/Soju06/codex-lb/issues/415)) ([4fccca1](https://github.com/Soju06/codex-lb/commit/4fccca1e994397a13c885d1a98a24988527df43e))


### Documentation

* add stemirkhan as a contributor for code, and test ([#452](https://github.com/Soju06/codex-lb/issues/452)) ([86bf3cd](https://github.com/Soju06/codex-lb/commit/86bf3cd8f9c1814de9268084a9306cd99f8a5937))

## [1.14.0](https://github.com/Soju06/codex-lb/compare/v1.13.1...v1.14.0) (2026-04-21)


### Features

* **api-keys:** show assigned account availability in picker ([#422](https://github.com/Soju06/codex-lb/issues/422)) ([81804ab](https://github.com/Soju06/codex-lb/commit/81804ab8b6e372da78018e220984dfcb5c0a7bbf))
* **dashboard:** show account plan in request logs table ([#425](https://github.com/Soju06/codex-lb/issues/425)) ([dbf4775](https://github.com/Soju06/codex-lb/commit/dbf4775ec7042ee72c1f8932b1a52079aab1c854))


### Bug Fixes

* **api-keys:** reuse shared copy button for created keys ([#432](https://github.com/Soju06/codex-lb/issues/432)) ([b59f1c8](https://github.com/Soju06/codex-lb/commit/b59f1c8f1585746440860f319eae0621166de371))
* **proxy:** prefer budget-safe routing and support image-generation compatibility ("code":"invalid_request_error","param":"tools") ([#421](https://github.com/Soju06/codex-lb/issues/421)) ([e632d94](https://github.com/Soju06/codex-lb/commit/e632d9476ed12df2d9c0d5986eab80b420835ff8))
* **proxy:** prevent context blowup by trimming input on client-supplied previous_response_id ([#448](https://github.com/Soju06/codex-lb/issues/448)) ([d80fc0c](https://github.com/Soju06/codex-lb/commit/d80fc0c68cdce70a299588daa8ad04cd82f9bfa0))

## [1.13.1](https://github.com/Soju06/codex-lb/compare/v1.13.0...v1.13.1) (2026-04-16)


### Bug Fixes

* **auth:** accept API keys on /api/codex/usage ([#417](https://github.com/Soju06/codex-lb/issues/417)) ([d75981d](https://github.com/Soju06/codex-lb/commit/d75981dde5a33098e674431847e29205322aa31d))
* replace reject-fast admission with wait-then-reject and tune HA defaults ([#413](https://github.com/Soju06/codex-lb/issues/413)) ([8d6d7c0](https://github.com/Soju06/codex-lb/commit/8d6d7c0c358fdc4b2a7cc83d94c3f2f7f413fbdf))

## [1.13.0](https://github.com/Soju06/codex-lb/compare/v1.12.0...v1.13.0) (2026-04-14)


### Features

* **auth:** add dashboard proxy auth modes ([#366](https://github.com/Soju06/codex-lb/issues/366)) ([ed4a754](https://github.com/Soju06/codex-lb/commit/ed4a7546b57b6987b62d7188f4af013d6f4d598b))
* auto-generate bootstrap token and enable sticky/reset defaults ([#377](https://github.com/Soju06/codex-lb/issues/377)) ([79e5f13](https://github.com/Soju06/codex-lb/commit/79e5f13dd22b5a47f85e3508a44a4b1ce7dd72b9))
* **ui:** UI adjustments on dashboards ([#379](https://github.com/Soju06/codex-lb/issues/379)) ([0f80ca2](https://github.com/Soju06/codex-lb/commit/0f80ca2db4857fe97f79f5c9cf2e6abe9d88b61d))


### Bug Fixes

* **auth:** allow explicit unauthenticated proxy client CIDRs ([#399](https://github.com/Soju06/codex-lb/issues/399)) ([1c27c7a](https://github.com/Soju06/codex-lb/commit/1c27c7af4738fb8454b93df2eb77cf6d82a6a4b8))
* **auth:** harden dashboard auth modes (Codex review follow-up) ([#384](https://github.com/Soju06/codex-lb/issues/384)) ([d106a71](https://github.com/Soju06/codex-lb/commit/d106a7137364060ea869dc0cd47862333db7f4b7))
* **http-bridge:** propagate bridged Spark model errors as HTTP 400 ([#388](https://github.com/Soju06/codex-lb/issues/388)) ([7b2998c](https://github.com/Soju06/codex-lb/commit/7b2998cd99b4837a09047c5218364da98ca6655a))
* **proxy:** harden admission control and usage refresh ([#372](https://github.com/Soju06/codex-lb/issues/372)) ([8698c0f](https://github.com/Soju06/codex-lb/commit/8698c0fd11deedb2d10e6d506c8b9ee80931b2b7))
* **proxy:** hide bridge topology behind owner handoff ([#363](https://github.com/Soju06/codex-lb/issues/363)) ([d10ea17](https://github.com/Soju06/codex-lb/commit/d10ea172b47f51480a5d4fd255a7f1ec2cbdccda))
* **proxy:** preserve previous_response_id on bridge recovery to prevent context blowup ([#397](https://github.com/Soju06/codex-lb/issues/397)) ([85802e6](https://github.com/Soju06/codex-lb/commit/85802e64bdde414b576aac2c299d3a075f8d603b))
* **proxy:** websocket connect-phase failover + deterministic failover integration tests ([#396](https://github.com/Soju06/codex-lb/issues/396)) ([20ddb3b](https://github.com/Soju06/codex-lb/commit/20ddb3b490e91648b354c7d773dccaf348ed92b7))
* **ui:** some append fix for [#379](https://github.com/Soju06/codex-lb/issues/379) ([#386](https://github.com/Soju06/codex-lb/issues/386)) ([9cf7be7](https://github.com/Soju06/codex-lb/commit/9cf7be7f1d5aefcb821914ccee54f10248b8d343))


### Documentation

* add aruis as a contributor for doc ([#382](https://github.com/Soju06/codex-lb/issues/382)) ([1b5c216](https://github.com/Soju06/codex-lb/commit/1b5c216f9d4a9e35f9ee8f5d43fa567968640eb4))
* add balakumardev and ihazgithub as contributors for code and test ([a9e7e89](https://github.com/Soju06/codex-lb/commit/a9e7e894121102c80d911d0a27f066be3564a626))
* add huzky-v as a contributor for code, and test ([#393](https://github.com/Soju06/codex-lb/issues/393)) ([f6b0134](https://github.com/Soju06/codex-lb/commit/f6b01341816ce853fb35ee0eb80f290e0359711d))
* add Kazet111 as a contributor for code, and test ([#403](https://github.com/Soju06/codex-lb/issues/403)) ([6df46c5](https://github.com/Soju06/codex-lb/commit/6df46c54e1932657363948a57bca706fae9a37ad))
* add OverHash as a contributor for code, and test ([#394](https://github.com/Soju06/codex-lb/issues/394)) ([38ffedb](https://github.com/Soju06/codex-lb/commit/38ffedb9ca75355c9ca99603e94a4435265dffaa))
* add SHAREN as a contributor for code, and test ([#381](https://github.com/Soju06/codex-lb/issues/381)) ([cf65c04](https://github.com/Soju06/codex-lb/commit/cf65c04ceb5e493f1d8a407ea72753510eb1a4b3))
* **api-keys:** clarify local-only behavior when auth is disabled ([#374](https://github.com/Soju06/codex-lb/issues/374)) ([54e9aa9](https://github.com/Soju06/codex-lb/commit/54e9aa90fa888c48f487092ec5e2e1a6cc1fdce2))

## [1.12.0](https://github.com/Soju06/codex-lb/compare/v1.11.0...v1.12.0) (2026-04-08)


### Features

* add accounts as pools for api to use ([#338](https://github.com/Soju06/codex-lb/issues/338)) ([659f7dc](https://github.com/Soju06/codex-lb/commit/659f7dcdb7156c6f384053d4734394da69ca0886))
* **config:** add model_context_window_overrides setting ([#340](https://github.com/Soju06/codex-lb/issues/340)) ([04da855](https://github.com/Soju06/codex-lb/commit/04da8553f764646bfcd52d087ea0a20a91c3995a))
* enable import-without-overwrite by default ([#362](https://github.com/Soju06/codex-lb/issues/362)) ([af9af6d](https://github.com/Soju06/codex-lb/commit/af9af6db3893e691842a8af43892adda4f9e9ccf))


### Bug Fixes

* **dashboard:** clarify donut usage breakdown ([#344](https://github.com/Soju06/codex-lb/issues/344)) ([87af885](https://github.com/Soju06/codex-lb/commit/87af8852c5d2e8bd3fdfe9d6e207745be7408c9c))
* **dashboard:** restore capacity-based usage donut totals ([#336](https://github.com/Soju06/codex-lb/issues/336)) ([1bcdcaa](https://github.com/Soju06/codex-lb/commit/1bcdcaacc1a51d3ce4f794b479383f6a9fe1158a))


### Documentation

* add comprehensive docstrings to select_account in logic.py ([#350](https://github.com/Soju06/codex-lb/issues/350)) ([36a4e7c](https://github.com/Soju06/codex-lb/commit/36a4e7cbd70fdb95d772d16aeded35ec1ae9a80d))
* add Daeroni as a contributor for doc ([#356](https://github.com/Soju06/codex-lb/issues/356)) ([15c4e54](https://github.com/Soju06/codex-lb/commit/15c4e54087089092478aaafe4bbfb6390fac0d84))
* add embogomolov as a contributor for code, and test ([#361](https://github.com/Soju06/codex-lb/issues/361)) ([d82cdf4](https://github.com/Soju06/codex-lb/commit/d82cdf4cdc8fd42ea5dfc3b43455ad857ab5421e))
* add Felix201209 as a contributor for code ([#360](https://github.com/Soju06/codex-lb/issues/360)) ([5e8cf21](https://github.com/Soju06/codex-lb/commit/5e8cf214f8e8ce8c516e15f7f3545cab6807aa7c))
* add Felix201209 as a contributor for doc ([#357](https://github.com/Soju06/codex-lb/issues/357)) ([6a7b8b2](https://github.com/Soju06/codex-lb/commit/6a7b8b27af6cc23b3f1a19802cc7b377489b2f49))

## [1.11.0](https://github.com/Soju06/codex-lb/compare/v1.10.1...v1.11.0) (2026-04-06)


### Features

* **accounts:** add refreshable browser OAuth link ([#316](https://github.com/Soju06/codex-lb/issues/316)) ([aeaf106](https://github.com/Soju06/codex-lb/commit/aeaf106a507b3b82ff305184ffae114faecf74f6))
* **dashboard:** add selectable overview timeframes ([#319](https://github.com/Soju06/codex-lb/issues/319)) ([d8d812f](https://github.com/Soju06/codex-lb/commit/d8d812f57f1463d8512dd6ac659f446e76bc5f94))
* deterministic failover & soft drain ([#328](https://github.com/Soju06/codex-lb/issues/328)) ([fc77c76](https://github.com/Soju06/codex-lb/commit/fc77c7604af6ed4d621fd4e7a8435507e0f3e21e))
* **v1-usage:** add credit-based Codex override windows ([#304](https://github.com/Soju06/codex-lb/issues/304)) ([6c3c556](https://github.com/Soju06/codex-lb/commit/6c3c5564c530d0670995577882038a00f5b46f8b))


### Bug Fixes

* **api:** for /backend-api/codex/model, return it in codex format ([#331](https://github.com/Soju06/codex-lb/issues/331)) ([c141a8a](https://github.com/Soju06/codex-lb/commit/c141a8ac963ebe25ed8e82ed7b9ab3057e4c083a))
* avoid Windows startup crash in memory monitor and add manual reg… ([#329](https://github.com/Soju06/codex-lb/issues/329)) ([5c2d26e](https://github.com/Soju06/codex-lb/commit/5c2d26e8f11abf5bdaed13aed7904f097cc18e3f))
* **dashboard:** show remaining totals in usage donuts ([#303](https://github.com/Soju06/codex-lb/issues/303)) ([7827941](https://github.com/Soju06/codex-lb/commit/78279417c1557753a93001a6586997fb204fe18d))
* **helm:** disable service links and use fully qualified image names ([#321](https://github.com/Soju06/codex-lb/issues/321)) ([c54edee](https://github.com/Soju06/codex-lb/commit/c54edeefa00b4271f6f80270462bb8ddcade5e92))
* **helm:** one-click external database setup improvements ([#322](https://github.com/Soju06/codex-lb/issues/322)) ([4c3c945](https://github.com/Soju06/codex-lb/commit/4c3c9453a48aaced5e023447446da00d6843c7cf))


### Documentation

* add Daltonganger as a contributor for bug ([#332](https://github.com/Soju06/codex-lb/issues/332)) ([1c8a7e5](https://github.com/Soju06/codex-lb/commit/1c8a7e5633b55dadeb8ccb2ae3791a23787b3a9f))
* add L1st3r as a contributor for bug ([#335](https://github.com/Soju06/codex-lb/issues/335)) ([05a77d8](https://github.com/Soju06/codex-lb/commit/05a77d857ec90b101feee675a1dfb20f556b0188))
* add mhughdo as a contributor for code, and test ([#333](https://github.com/Soju06/codex-lb/issues/333)) ([0fc01f6](https://github.com/Soju06/codex-lb/commit/0fc01f676fe826f6228140c529e75ca1e31076c2))
* add salwinh as a contributor for code, and test ([#334](https://github.com/Soju06/codex-lb/issues/334)) ([7fed142](https://github.com/Soju06/codex-lb/commit/7fed14284a0c6025cf615856b6ca123b2d8cf463))

## [1.10.1](https://github.com/Soju06/codex-lb/compare/v1.10.0...v1.10.1) (2026-04-03)


### Bug Fixes

* **ci:** lowercase Trivy image-ref and bump all actions to latest ([3b94de4](https://github.com/Soju06/codex-lb/commit/3b94de4457a93b2ff220a33ea9b7a164c02e0b37))
* **ci:** use exact tag v8.0.0 for setup-uv (no v8 major tag exists) ([c657c91](https://github.com/Soju06/codex-lb/commit/c657c91bf26b4d99bb783e7e4f3b4268d0a4028f))


### Documentation

* add L1st3r as a contributor for code, and test ([#318](https://github.com/Soju06/codex-lb/issues/318)) ([d0ff5a7](https://github.com/Soju06/codex-lb/commit/d0ff5a71212132f64ecf4e3b594059a7d648f45a))
* external DB secrets guide, ServiceMonitor alternatives, production deployment guide ([#315](https://github.com/Soju06/codex-lb/issues/315)) ([8d558f6](https://github.com/Soju06/codex-lb/commit/8d558f6a9b3beafcbca36c92ba694f099c9ca115))

## [1.10.0](https://github.com/Soju06/codex-lb/compare/v1.9.0...v1.10.0) (2026-04-02)


### Features

* **helm:** expose all caching subsystems in chart values ([cd39073](https://github.com/Soju06/codex-lb/commit/cd39073c4f2b9f086a00bf84c9cd80af27cc194a))


### Bug Fixes

* **ci:** lowercase GHCR owner in Helm OCI push ([03c14f6](https://github.com/Soju06/codex-lb/commit/03c14f61e132c81f483dd21f977e7f0dd32be083))
* **helm:** harden defaults for multi-replica and streaming deployments ([70a348e](https://github.com/Soju06/codex-lb/commit/70a348e80bc6f46ec616e3ff497f056277049156))
* **helm:** improve cache locality and align backpressure with capacity ([6c17201](https://github.com/Soju06/codex-lb/commit/6c1720189416da41a5c7c979ec8b523f0218c46a))


### Documentation

* **helm:** replace local-path install with OCI registry commands ([55ddeb7](https://github.com/Soju06/codex-lb/commit/55ddeb7300d6a1780ec748b3e1d940613333ab69))

## [1.9.0](https://github.com/Soju06/codex-lb/compare/v1.8.3...v1.9.0) (2026-04-02)


### Features

* add a "API" page to see details of the API keys ([#269](https://github.com/Soju06/codex-lb/issues/269)) ([938c734](https://github.com/Soju06/codex-lb/commit/938c7344b2cfc62ecbc7519abf60b04f9ddf9fcd))
* add stickysession selection box to select multiple sessions too be deleted ([#286](https://github.com/Soju06/codex-lb/issues/286)) ([c64b860](https://github.com/Soju06/codex-lb/commit/c64b8604afcf3afcdac040fed823a51b95cb4955))
* **api-keys:** add per-key enforced service tier ([#288](https://github.com/Soju06/codex-lb/issues/288)) ([cc851a5](https://github.com/Soju06/codex-lb/commit/cc851a5eedf8375f4df7e2a909d28b48023f08c4))
* **api-keys:** add self-service /v1/usage endpoint ([#295](https://github.com/Soju06/codex-lb/issues/295)) ([652f600](https://github.com/Soju06/codex-lb/commit/652f60080109ea1ac25f4a0d2bc5124f9587ca08))
* **balancer:** add capacity-weighted routing for tier-aware load distribution ([#297](https://github.com/Soju06/codex-lb/issues/297)) ([fa8eab4](https://github.com/Soju06/codex-lb/commit/fa8eab4eb6844e9b737d705327ea6b926cc49419))


### Bug Fixes

* **balancer:** trust usage data over stale runtime_reset for early quota resets ([#289](https://github.com/Soju06/codex-lb/issues/289)) ([a269b37](https://github.com/Soju06/codex-lb/commit/a269b3769a6a115921e3d54f9b32b535f9bb2f2b))
* **chat:** prevent duplicated tool-call arguments in chat completions ([#287](https://github.com/Soju06/codex-lb/issues/287)) ([41ceb4f](https://github.com/Soju06/codex-lb/commit/41ceb4f24d07cacfff9f8b21dad50c4458414278))
* **deploy:** restore Docker auto-migration, cache/rate-limiter fixes, Helm/K8s CI/CD ([#274](https://github.com/Soju06/codex-lb/issues/274)) ([16391ae](https://github.com/Soju06/codex-lb/commit/16391aec7c76096fb20218e353731d44a9cbc4f8))
* **docker:** resolve distroless ARM64 build by detecting arch-specific lib paths ([b21d4bd](https://github.com/Soju06/codex-lb/commit/b21d4bd498714aac3ab785c361008a3f2238b688))
* prevent sticky session thrashing when all accounts exceed budget threshold ([#279](https://github.com/Soju06/codex-lb/issues/279)) ([502db37](https://github.com/Soju06/codex-lb/commit/502db371232d6fc905985c140b0b80d96472aaea))
* **proxy:** resolve k8s-era TC regressions ([#290](https://github.com/Soju06/codex-lb/issues/290)) ([020784a](https://github.com/Soju06/codex-lb/commit/020784a38b731381e05e4c8fef7505525c60fd84))
* **tests:** stabilize proxy retry logging assertions ([0f86737](https://github.com/Soju06/codex-lb/commit/0f867376df870516551416b3df650adedd85ed05))


### Performance Improvements

* **usage:** replace DISTINCT ON with lateral join in latest_by_account ([#277](https://github.com/Soju06/codex-lb/issues/277)) ([8be87a6](https://github.com/Soju06/codex-lb/commit/8be87a64f1576f770b11de171f947b68e74420b3))


### Documentation

* add Daltonganger as a contributor for code, and test ([#298](https://github.com/Soju06/codex-lb/issues/298)) ([7f17d72](https://github.com/Soju06/codex-lb/commit/7f17d72ecfd26aa20877c4d6ec37f71417e48897))

## [1.8.3](https://github.com/Soju06/codex-lb/compare/v1.8.2...v1.8.3) (2026-03-30)


### Bug Fixes

* **proxy:** complete cache-locality fix for prompt cache hit rate restoration ([#273](https://github.com/Soju06/codex-lb/issues/273)) ([aa971fa](https://github.com/Soju06/codex-lb/commit/aa971fa96c6789f079aa98c67205e1263f3c7598))

## [1.8.2](https://github.com/Soju06/codex-lb/compare/v1.8.1...v1.8.2) (2026-03-26)


### Bug Fixes

* **api-keys:** normalize timezone-aware expirations before persistence ([#249](https://github.com/Soju06/codex-lb/issues/249)) ([abf96f8](https://github.com/Soju06/codex-lb/commit/abf96f85a265cf3d45eed7f47ecfb10de6979b01))
* graph do not render when primary = [], even secondary have data ([#253](https://github.com/Soju06/codex-lb/issues/253)) ([98434c4](https://github.com/Soju06/codex-lb/commit/98434c491698747c5c0dbb69f2f4c471affdd86a))
* **middleware:** handle disconnects and body read failures ([#263](https://github.com/Soju06/codex-lb/issues/263)) ([8188c31](https://github.com/Soju06/codex-lb/commit/8188c31110b7e284a97d83777728ed54b7e83593))


### Documentation

* add huzky-v as a contributor for question, and maintenance ([#257](https://github.com/Soju06/codex-lb/issues/257)) ([337db69](https://github.com/Soju06/codex-lb/commit/337db69b7a138f43cae4dd857dd08196d06e4cff))
* add yigitkonur as a contributor for bug, and code ([#258](https://github.com/Soju06/codex-lb/issues/258)) ([a5ffdf3](https://github.com/Soju06/codex-lb/commit/a5ffdf307f161672f74bd44e6ccbd286bbbe8faa))

## [1.8.1](https://github.com/Soju06/codex-lb/compare/v1.8.0...v1.8.1) (2026-03-22)


### Documentation

* add ink-splatters as a contributor for code, and bug ([#247](https://github.com/Soju06/codex-lb/issues/247)) ([eb968b9](https://github.com/Soju06/codex-lb/commit/eb968b9d53b8fdd856f36d07714c93b4eb7dd61f))

## [1.8.0](https://github.com/Soju06/codex-lb/compare/v1.7.0...v1.8.0) (2026-03-21)


### Features

* **proxy:** split service tier logging and pricing ([#238](https://github.com/Soju06/codex-lb/issues/238)) ([04c9304](https://github.com/Soju06/codex-lb/commit/04c93044aa061051d0ea404795078e44b6241360))


### Bug Fixes

* fail closed when HTTP bridge loses previous_response continuity ([#239](https://github.com/Soju06/codex-lb/issues/239)) ([a87e0ca](https://github.com/Soju06/codex-lb/commit/a87e0ca342981263d33668d97eac5cdc9c86842b))
* improve native Codex websocket parity ([#242](https://github.com/Soju06/codex-lb/issues/242)) ([fb0e759](https://github.com/Soju06/codex-lb/commit/fb0e7595f46984d26c97a761dd339af4ade83223))
* **proxy:** support desktop Codex originators ([#240](https://github.com/Soju06/codex-lb/issues/240)) ([ac38bd1](https://github.com/Soju06/codex-lb/commit/ac38bd186dd4eb51947ad9b7e83ecb6addd6ca99))
* tighten dashboard database indexes ([#241](https://github.com/Soju06/codex-lb/issues/241)) ([f2469a2](https://github.com/Soju06/codex-lb/commit/f2469a2b8102dd1efe7f4948ee1e82d461f30e93))

## [1.7.0](https://github.com/Soju06/codex-lb/compare/v1.6.3...v1.7.0) (2026-03-20)


### Features

* add GPT-5.4 mini pricing ([#234](https://github.com/Soju06/codex-lb/issues/234)) ([3236119](https://github.com/Soju06/codex-lb/commit/323611940387057cc70e474219240c225b40d33b))


### Bug Fixes

* bridge backend HTTP responses through websocket sessions ([#236](https://github.com/Soju06/codex-lb/issues/236)) ([2723d97](https://github.com/Soju06/codex-lb/commit/2723d9720af184cd875de8ca3d5ed8d89171c30c))

## [1.6.3](https://github.com/Soju06/codex-lb/compare/v1.6.2...v1.6.3) (2026-03-19)


### Bug Fixes

* preserve v1 responses session continuity over HTTP ([#232](https://github.com/Soju06/codex-lb/issues/232)) ([7ba5b75](https://github.com/Soju06/codex-lb/commit/7ba5b751f90e619bb396afa1ed650d837bba9308))

## [1.6.2](https://github.com/Soju06/codex-lb/compare/v1.6.1...v1.6.2) (2026-03-19)


### Bug Fixes

* **proxy:** restore token cache affinity routing ([#228](https://github.com/Soju06/codex-lb/issues/228)) ([ab8f820](https://github.com/Soju06/codex-lb/commit/ab8f820f2e8adbfb0c1f9ebc43c17acd4333441c))

## [1.6.1](https://github.com/Soju06/codex-lb/compare/v1.6.0...v1.6.1) (2026-03-18)


### Bug Fixes

* clarify account quota labels and dashboard masking ([#215](https://github.com/Soju06/codex-lb/issues/215)) ([ec00fa8](https://github.com/Soju06/codex-lb/commit/ec00fa84071976a5b6484bb819975dbd1ff5d4f2))
* **dashboard:** cap primary donut remaining by secondary absolute credits ([#222](https://github.com/Soju06/codex-lb/issues/222)) ([d0e286a](https://github.com/Soju06/codex-lb/commit/d0e286af931e1d7bbe7c62583857c34ae611b57d))
* **proxy:** add transient 500 retry with same-account affinity and failover ([#225](https://github.com/Soju06/codex-lb/issues/225)) ([c1ed531](https://github.com/Soju06/codex-lb/commit/c1ed531a3d58003e00ca5dff562bc761ef93fc48))
* **proxy:** preserve sticky sessions during temporary account unavailability ([#226](https://github.com/Soju06/codex-lb/issues/226)) ([68b3bc0](https://github.com/Soju06/codex-lb/commit/68b3bc08a24fbb5914776a689996950ce29f502f))


### Documentation

* add minpeter as a contributor for code, and test ([#223](https://github.com/Soju06/codex-lb/issues/223)) ([3b2c1d4](https://github.com/Soju06/codex-lb/commit/3b2c1d406d2aaff5e9b941d89169dfad8f5e4002))

## [1.6.0](https://github.com/Soju06/codex-lb/compare/v1.5.3...v1.6.0) (2026-03-18)


### Features

* **proxy:** improve token cache affinity for codex and v1/responses endpoints ([#220](https://github.com/Soju06/codex-lb/issues/220)) ([dfc3aa7](https://github.com/Soju06/codex-lb/commit/dfc3aa714e0ec8ae4b6443abc262795875926320))


### Bug Fixes

* move the trend back to secondary instead of primary for free accounts ([#190](https://github.com/Soju06/codex-lb/issues/190)) ([944ea93](https://github.com/Soju06/codex-lb/commit/944ea93db600b004e1ff8df29397e47114af65b9))
* the account page select param is not respected ([#198](https://github.com/Soju06/codex-lb/issues/198)) ([6036184](https://github.com/Soju06/codex-lb/commit/6036184af2696dadc157bc6590bcc9e95d183177))

## [1.5.3](https://github.com/Soju06/codex-lb/compare/v1.5.2...v1.5.3) (2026-03-13)


### Bug Fixes

* **proxy:** match Codex CLI header fingerprint for transcribe upstream requests ([#199](https://github.com/Soju06/codex-lb/issues/199)) ([2a89631](https://github.com/Soju06/codex-lb/commit/2a8963143515da25bf718ede913fac14dbd918ee))


### Documentation

* add huzky-v as a contributor for code, and bug ([#201](https://github.com/Soju06/codex-lb/issues/201)) ([d1410c6](https://github.com/Soju06/codex-lb/commit/d1410c60a99e8b36c2464412c0e1b5db50f01914))

## [1.5.2](https://github.com/Soju06/codex-lb/compare/v1.5.1...v1.5.2) (2026-03-13)


### Bug Fixes

* **proxy:** close stream immediately after terminal SSE events ([#196](https://github.com/Soju06/codex-lb/issues/196)) ([dcf1ae3](https://github.com/Soju06/codex-lb/commit/dcf1ae3675346d75b571a29644c2722f776dc436))

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
