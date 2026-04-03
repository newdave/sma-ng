# Changelog

## [0.3.0](https://github.com/newdave/sma-ng/compare/sma-ng-v0.2.0...sma-ng-v0.3.0) (2026-04-03)


### Features

* add /reload, /restart endpoints and SIGHUP graceful restart ([b939b12](https://github.com/newdave/sma-ng/commit/b939b1261de4fbdd1609ee756b79fb166cc2fce3))
* add configurable QSV look-ahead, B-frames, ref-frames; add OpenAPI spec and CI validation ([3881bc3](https://github.com/newdave/sma-ng/commit/3881bc3b6e7a5d4c02ce416a7e865d233254dd78))
* add job priority queue ordering via dashboard ([e19b59d](https://github.com/newdave/sma-ng/commit/e19b59d0fc924bb705edc13536bf47133c7f0c41))
* remote restart/shutdown via PostgreSQL pending_command ([f785b3a](https://github.com/newdave/sma-ng/commit/f785b3ab24979e8c20fc21e86140159997d0a6e5))
* show cluster node status on dashboard via PostgreSQL ([3c3cc65](https://github.com/newdave/sma-ng/commit/3c3cc65e3566dee5c1cd5bc28a7f9fd60c81cb61))
* skip .mp4 files in filesystem scanner; add per-path enabled flag ([9a29bd1](https://github.com/newdave/sma-ng/commit/9a29bd16962c1e6b12a09eed30db73624192b690))
* update sma-webhook.sh to cover all daemon endpoints ([742bed4](https://github.com/newdave/sma-ng/commit/742bed43ac7c7f1e354c230b7ebc36e8ab5f72ad))


### Bug Fixes

* add version tag trigger to docker workflow ([8732843](https://github.com/newdave/sma-ng/commit/8732843cad2d638147fe4226414cf1af3eaf5144))
* flush response buffer before triggering shutdown/restart ([790ab9d](https://github.com/newdave/sma-ng/commit/790ab9d65e7f2c4c39917657015262bd6a6987c4))
* update QSV defaults and clarify restart message ([8534d17](https://github.com/newdave/sma-ng/commit/8534d171794ab0503ae82178e4007ed05f1dc287))

## [0.2.0](https://github.com/newdave/sma-ng/compare/sma-ng-v0.1.2...sma-ng-v0.2.0) (2026-04-01)


### Features

* add /dashboard endpoint; redirect / to /dashboard ([a6305db](https://github.com/newdave/sma-ng/commit/a6305db70d00809f20d2a0a80f20bd326b3db706))
* multi-worker concurrency, graceful shutdown, dynamic Docker config ([81a904c](https://github.com/newdave/sma-ng/commit/81a904c56a2ee78aea5e195ac8ce22a383ddfddc))


### Bug Fixes

* abort conversion early when output directory cannot be created ([6d0309f](https://github.com/newdave/sma-ng/commit/6d0309f37f47e490440c77eee266ef28a3d20897))
* check for existing UID/GID before creating user and group ([e9d8666](https://github.com/newdave/sma-ng/commit/e9d86661ea57cf68096c2780c2837eea1e1b53db))
* enforce max-workers=1 to prevent hardware encoder contention ([721032e](https://github.com/newdave/sma-ng/commit/721032e298b9c5da76b94bef0d8b59931038d55a))
* groupadd -f to tolerate GID 1000 already existing in ubuntu base ([94d1ccc](https://github.com/newdave/sma-ng/commit/94d1ccc96dc063fb1d987e3c83ed07e766289e1c))
* handle missing default subtitle language in setDefaultSubtitleStream ([e79f0f1](https://github.com/newdave/sma-ng/commit/e79f0f152a957af5a294995918aee826d388a371))
* optimize github actions workflows ([fe9c9df](https://github.com/newdave/sma-ng/commit/fe9c9df6c4c0d95a00cc8c9f0f1bdf6a31343afc))
* remove --db-url CLI flag to prevent credentials appearing in ps output ([7afdcb9](https://github.com/newdave/sma-ng/commit/7afdcb987ce8fc0e4fbbe4f9ff8223e689c07169))
* remove existing UID before useradd to avoid duplicate-UID conflict ([3ca5bdb](https://github.com/newdave/sma-ng/commit/3ca5bdb75cbc7cb4997198560351ef7a954745ef))
* remove logging to config/sma.log ([863307c](https://github.com/newdave/sma-ng/commit/863307c3a13eb86e35ae3e5cb74347f132a0b59f))
* simplify user creation — ensure UID/GID exist, skip if already present ([02e8229](https://github.com/newdave/sma-ng/commit/02e8229a82a2b34fe65124716af653b978591508))
* specify docker/Dockerfile path in docker workflow ([d4db6a3](https://github.com/newdave/sma-ng/commit/d4db6a350c1e25f63132c07abffcd6134d74b8ec))
* suppress software encoder params (-x265-params, -x264-params) for HW codecs ([32fa74a](https://github.com/newdave/sma-ng/commit/32fa74aab53ce1475254b2872ee10021dc33e22b))
* UID and GID resolution ([8b108a9](https://github.com/newdave/sma-ng/commit/8b108a98763c8a15b2fd5b176a07b2418f09f542))
* validate profile values for QSV codecs to prevent invalid options ([fa95206](https://github.com/newdave/sma-ng/commit/fa95206036bbf36386056d03cb38b9637c5ec18b))


### Documentation

* add docstrings to converter base classes, ffmpeg.py, and __init__.py ([75decfd](https://github.com/newdave/sma-ng/commit/75decfdf93c9a9cf2112b4982a2084e5121a012c))
* add docstrings to MediaProcessor (mediaprocessor.py) ([13db344](https://github.com/newdave/sma-ng/commit/13db344a3805dfe822871335dd69b736ceb84a2c))
* add docstrings to Metadata class (metadata.py) ([5f8dfd9](https://github.com/newdave/sma-ng/commit/5f8dfd93d202224ac63fa27a33e5d835955acb16))
* add docstrings to readsettings, manual, postprocess, lang, plex, log, update, extensions ([caca227](https://github.com/newdave/sma-ng/commit/caca2277c2a5f4164e0dfb8950a10349df21d491))
* add remaining docstrings and mark documentation plan complete ([316416a](https://github.com/newdave/sma-ng/commit/316416a8dea1fcd921b977986b83544960f1698a))
* mark Phase 7 complete in codebase refactor plan ([d5a2d1d](https://github.com/newdave/sma-ng/commit/d5a2d1ddac7834c238dfb44e0226171c753a81fd))
* mark refactor plan tasks 1-10 complete; task 11 (SubtitleProcessor) still pending ([77100d0](https://github.com/newdave/sma-ng/commit/77100d03e2e22525af9a467beb642e39735720ab))
* overhaul README and add AGENTS.md for Codex guidance ([a7dea87](https://github.com/newdave/sma-ng/commit/a7dea87c69839a1c3bdc178c96d532b303b8f926))
* remove completed codebase refactor plan ([dd28501](https://github.com/newdave/sma-ng/commit/dd28501d95ce72b1b753a7293ab7e761e278552b))
* update CLAUDE.md for multi-worker concurrency, shutdown, and release flow ([d8a8b9e](https://github.com/newdave/sma-ng/commit/d8a8b9ee52de37ced01d2f66051cbc168ff78b8f))
* update for triggers/ refactor, /dashboard endpoint, and config path changes ([da9395c](https://github.com/newdave/sma-ng/commit/da9395cc9d4169079093c791828d78b5df881ecc))
