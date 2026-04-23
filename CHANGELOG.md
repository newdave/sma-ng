# Changelog

## [1.6.15](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.14...sma-ng-v1.6.15) (2026-04-23)


### Bug Fixes

* set default RENDER_GID to 992 for render group ([6e3d192](https://github.com/newdave/sma-ng/commit/6e3d192b605f800d402029782de6ec987d174117))

## [1.6.14](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.13...sma-ng-v1.6.14) (2026-04-23)


### Features

* add deploy:config-audit task and ini_audit helper ([f6ff54f](https://github.com/newdave/sma-ng/commit/f6ff54f1300179440cf8f5580c2bd877be994af5))


### Bug Fixes

* normalize wildcard cluster hostnames in /status response; add directory webhook test ([d55399f](https://github.com/newdave/sma-ng/commit/d55399fdc2cf22d880812073562076b686f4592b))
* restore corrupted deploy task files and add ShellCheck suppressions ([70b03e9](https://github.com/newdave/sma-ng/commit/70b03e9a6265d26effa28cb070b244a94ca2a260))


### Documentation

* add ShellCheck and Markdown linting rules to AGENTS.md and CLAUDE.md ([2ca1456](https://github.com/newdave/sma-ng/commit/2ca1456162408ad3d473dce2fcddf29b7c57bd22))

## [1.6.13](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.12...sma-ng-v1.6.13) (2026-04-22)


### Bug Fixes

* derive docker cluster hostnames from host ([4689e33](https://github.com/newdave/sma-ng/commit/4689e333d8a4ca178ea9b71bc3c9d1a5c313d4ce))

## [1.6.12](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.11...sma-ng-v1.6.12) (2026-04-22)


### Bug Fixes

* rewrite docker shebangs for container venv ([9a3b646](https://github.com/newdave/sma-ng/commit/9a3b6462a7d9730743ebf95892f3ed0b5401778a))

## [1.6.11](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.10...sma-ng-v1.6.11) (2026-04-22)


### Bug Fixes

* use shared ssh helper in deploy docker-upgrade health check ([f69d6f3](https://github.com/newdave/sma-ng/commit/f69d6f3fe24f5404b8ed2d6e01e8486b54272041))

## [1.6.10](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.9...sma-ng-v1.6.10) (2026-04-22)


### Features

* add docker deploy tasks for bundled postgres management ([32d80f6](https://github.com/newdave/sma-ng/commit/32d80f6b0398772711924d7e4248879792757847))


### Bug Fixes

* docker stuff ([286252e](https://github.com/newdave/sma-ng/commit/286252e2504e904db92d6c73839b09e78120ca48))

## [1.6.9](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.8...sma-ng-v1.6.9) (2026-04-22)


### Features

* add analyzer config and openvino backend scaffold ([c7624c9](https://github.com/newdave/sma-ng/commit/c7624c92d395409c8cd9ece2b6c3b581db7de435))
* apply analyzer recommendations in media planning ([6bac61a](https://github.com/newdave/sma-ng/commit/6bac61ad19c6585030b521d8c1a727a8b2c84963))

## [1.6.8](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.7...sma-ng-v1.6.8) (2026-04-22)


### Bug Fixes

* refresh docker runtime configuration ([10f4669](https://github.com/newdave/sma-ng/commit/10f466997b99c22cacc6a6a0231c57a6cee22b47))

## [1.6.7](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.6...sma-ng-v1.6.7) (2026-04-22)


### Bug Fixes

* honor rewritten paths for config routing ([88de91b](https://github.com/newdave/sma-ng/commit/88de91b4247d9cb956a391cfface7971246fa050))
* improve daemon status handling under load ([70c92e5](https://github.com/newdave/sma-ng/commit/70c92e586b9d23d21a988dcffacb7af6792eac3f))


### Documentation

* clarify logical commit grouping rules ([07bda0a](https://github.com/newdave/sma-ng/commit/07bda0a8d867a7152332682bdfc6905b4d69f042))

## [1.6.6](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.5...sma-ng-v1.6.6) (2026-04-22)


### Bug Fixes

* expose full DRI topology for Intel SR-IOV guests ([3755897](https://github.com/newdave/sma-ng/commit/3755897fb348620b19770a1cea61e862790e5c1b))


### Documentation

* document Intel SR-IOV docker requirements ([2ad65d4](https://github.com/newdave/sma-ng/commit/2ad65d47bad36f3099fd12f3fc7736bc115c3c62))

## [1.6.5](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.4...sma-ng-v1.6.5) (2026-04-22)


### Bug Fixes

* align docker compose env handling ([1f0167a](https://github.com/newdave/sma-ng/commit/1f0167a253447bd471df756bbb63dbd541b9c9e2))


### Documentation

* add Codex /errors command mirror ([e239184](https://github.com/newdave/sma-ng/commit/e239184f6bf63500364445bdbe9ec02c1bed3750))
* update docker compose quickstart env setup ([9fe5c3e](https://github.com/newdave/sma-ng/commit/9fe5c3ed15beb155e31735e5dac9a310fa68484d))

## [1.6.4](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.3...sma-ng-v1.6.4) (2026-04-22)


### Bug Fixes

* defer path rewrites to job-creation time in handler ([f681f03](https://github.com/newdave/sma-ng/commit/f681f031d4d81dc2c3f995549c86375ee3353639))
* require SMA_DAEMON_DB_URL for non-pg profiles via :? syntax ([2390cb5](https://github.com/newdave/sma-ng/commit/2390cb52b26e64803ded599a3d80b006a3f3a297))

## [1.6.3](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.2...sma-ng-v1.6.3) (2026-04-22)


### Bug Fixes

* wire sma-pgsql internal hostname for -pg compose profiles ([d32e845](https://github.com/newdave/sma-ng/commit/d32e845c3b36460bf5150f7dfdeaafeb9249df30))

## [1.6.2](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.1...sma-ng-v1.6.2) (2026-04-22)


### Bug Fixes

* remove :? from POSTGRES_PASSWORD fallback in docker-compose.yml ([58a8a55](https://github.com/newdave/sma-ng/commit/58a8a55b252dbbcdf4b540da010408ece4d4b932))

## [1.6.1](https://github.com/newdave/sma-ng/compare/sma-ng-v1.6.0...sma-ng-v1.6.1) (2026-04-22)


### Bug Fixes

* _append_env returns 0 when value is empty under set -e ([c8bcac4](https://github.com/newdave/sma-ng/commit/c8bcac4550047dbfe126718f5fbf2f7f2d76345b))

## [1.6.0](https://github.com/newdave/sma-ng/compare/sma-ng-v1.5.2...sma-ng-v1.6.0) (2026-04-22)


### Features

* add SMA_DAEMON_DB_* component env vars for PostgreSQL connection ([a585f2d](https://github.com/newdave/sma-ng/commit/a585f2da331069c301e701e4c50721ad1ea38cbb))

## [1.5.2](https://github.com/newdave/sma-ng/compare/sma-ng-v1.5.1...sma-ng-v1.5.2) (2026-04-22)


### Bug Fixes

* stop overriding SMA_DAEMON_DB_URL in *-pg compose services and docker-upgrade ([97b353c](https://github.com/newdave/sma-ng/commit/97b353c11b041bad6edb6e6a3028eee7d74564bb))

## [1.5.1](https://github.com/newdave/sma-ng/compare/sma-ng-v1.5.0...sma-ng-v1.5.1) (2026-04-22)


### Bug Fixes

* apply path_rewrites inside get_config_for_path and get_args_for_path ([d1a34a6](https://github.com/newdave/sma-ng/commit/d1a34a6aafa527cc84b64b48ca46fa040835d71e))

## [1.5.0](https://github.com/newdave/sma-ng/compare/sma-ng-v1.4.3...sma-ng-v1.5.0) (2026-04-22)


### Features

* add deploy:ghcr-login mise task for GHCR image access ([e2671cb](https://github.com/newdave/sma-ng/commit/e2671cb9608041e5e44e54412b0d6018d793bb33))

## [1.4.3](https://github.com/newdave/sma-ng/compare/sma-ng-v1.4.2...sma-ng-v1.4.3) (2026-04-22)


### Bug Fixes

* construct and inject SMA_DAEMON_DB_URL from PG vars in deploy:docker-upgrade ([c3e86d5](https://github.com/newdave/sma-ng/commit/c3e86d51ca75d9ec5bb73f4df10e1ece5b3a92f4))

## [1.4.2](https://github.com/newdave/sma-ng/compare/sma-ng-v1.4.1...sma-ng-v1.4.2) (2026-04-22)


### Bug Fixes

* remove :? validation from non-pg SMA_DAEMON_DB_URL in docker-compose.yml ([4c0867c](https://github.com/newdave/sma-ng/commit/4c0867c351c0bd554cb74194f75e5211e49e0152))

## [1.4.1](https://github.com/newdave/sma-ng/compare/sma-ng-v1.4.0...sma-ng-v1.4.1) (2026-04-22)


### Bug Fixes

* inject POSTGRES_PASSWORD into docker compose commands in deploy:docker-upgrade ([acf6e83](https://github.com/newdave/sma-ng/commit/acf6e83b034229f3336fd6dc665da2b6b6726836))

## [1.4.0](https://github.com/newdave/sma-ng/compare/sma-ng-v1.3.5...sma-ng-v1.4.0) (2026-04-22)


### Features

* add deploy:docker-upgrade mise task for Docker Compose installations ([881cca7](https://github.com/newdave/sma-ng/commit/881cca7da26a5989e460833bdf0e9aa1e18f25ef))

## [1.3.5](https://github.com/newdave/sma-ng/compare/sma-ng-v1.3.4...sma-ng-v1.3.5) (2026-04-21)


### Bug Fixes

* update intel QSV/VAAPI render group GID to 109 ([e1a4321](https://github.com/newdave/sma-ng/commit/e1a43212cf7651d69feeed339eba3e5ce800c264))

## [1.3.4](https://github.com/newdave/sma-ng/compare/sma-ng-v1.3.3...sma-ng-v1.3.4) (2026-04-21)


### Documentation

* fix SMA_CONFIG path and document RENDER_GID in daemon.env.sample ([9004728](https://github.com/newdave/sma-ng/commit/90047282660a1c85aabbeb0d75265204518c7b46))

## [1.3.3](https://github.com/newdave/sma-ng/compare/sma-ng-v1.3.2...sma-ng-v1.3.3) (2026-04-21)


### Bug Fixes

* expose /dev/dri/renderD128 device node directly instead of bind-mounting /dev/dri ([721feb3](https://github.com/newdave/sma-ng/commit/721feb379f4e6da4f8ec4c466e69f87265caa4c3))

## [1.3.2](https://github.com/newdave/sma-ng/compare/sma-ng-v1.3.1...sma-ng-v1.3.2) (2026-04-21)


### Bug Fixes

* rename sforcedefault to force_subtitle_defaults in mediaprocessor ([d3ca2e4](https://github.com/newdave/sma-ng/commit/d3ca2e40d1208fd9d0f27102d7ef57bdf33397c9))
* set LIBVA_DRIVER_NAME=iHD conditionally on Intel GPU only ([a8d419b](https://github.com/newdave/sma-ng/commit/a8d419b55c5612bcaec2f998a36845ec6db14a31))

## [1.3.1](https://github.com/newdave/sma-ng/compare/sma-ng-v1.3.0...sma-ng-v1.3.1) (2026-04-21)


### Bug Fixes

* require SMA_DAEMON_DB_URL on non-pg profiles; fix SMA_DAEMON_CONFIG path ([b138479](https://github.com/newdave/sma-ng/commit/b13847923d8f2d74a22aeb71daa0125b6e3d98e0))

## [1.3.0](https://github.com/newdave/sma-ng/compare/sma-ng-v1.2.2...sma-ng-v1.3.0) (2026-04-21)


### Features

* add enabled flag to Universal Audio config section ([2241f2e](https://github.com/newdave/sma-ng/commit/2241f2e5d212ba5f25a49994d9d2e835a7bf0f39))


### Bug Fixes

* correct Docker sma user creation, config path, and GPU auto-detection ([37f0d55](https://github.com/newdave/sma-ng/commit/37f0d55f10a4f6adab060be49dfd3e42ed4355e4))
* tag Docker images as latest on push to main ([4ba0e46](https://github.com/newdave/sma-ng/commit/4ba0e462ff6b43b9d6dc9e2044996557c584005e))
* use GHCR image in docker-compose and remove broken postgres port binding ([f467539](https://github.com/newdave/sma-ng/commit/f467539a8040fb0f7cf3071c835cb48d824f630b))


### Documentation

* update documentation for script extraction and Docker fixes ([e5854bf](https://github.com/newdave/sma-ng/commit/e5854bf0454b6de8bf1b5f856b819cab8e82c20a))

## [1.2.2](https://github.com/newdave/sma-ng/compare/sma-ng-v1.2.1...sma-ng-v1.2.2) (2026-04-21)


### Documentation

* codify workflow and commit discipline ([9671215](https://github.com/newdave/sma-ng/commit/96712153d7430606f4f1cb650cb8a4f445ea4ca0))

## [1.2.1](https://github.com/newdave/sma-ng/compare/sma-ng-v1.2.0...sma-ng-v1.2.1) (2026-04-21)


### Documentation

* add daemon error retrieval command ([64aac08](https://github.com/newdave/sma-ng/commit/64aac08299e64d67a27c10154ad496a4dd67e199))

## [1.2.0](https://github.com/newdave/sma-ng/compare/sma-ng-v1.1.4...sma-ng-v1.2.0) (2026-04-21)


### Features

* add path search to jobs list ([d97e46c](https://github.com/newdave/sma-ng/commit/d97e46c4e13e5153fcf20556408edf26643922a7))
* split docker compose profiles by encoder and database ([09b3a2a](https://github.com/newdave/sma-ng/commit/09b3a2afc893af25b53289e07951001c713a04d4))


### Bug Fixes

* harden daemon progress reporting and log discovery ([fe5f211](https://github.com/newdave/sma-ng/commit/fe5f21191129dfc77845dec0d4d5ee90b0f4f480))
* recycle original before restoreFromOutput overwrites input path ([07add3d](https://github.com/newdave/sma-ng/commit/07add3ddc6f19c33f380113db9368305b32763c2))


### Documentation

* add deployment architecture and onboarding guides ([ab82b91](https://github.com/newdave/sma-ng/commit/ab82b91b340b20af6458ae3660215569c0aad62c))

## [1.1.4](https://github.com/newdave/sma-ng/compare/sma-ng-v1.1.3...sma-ng-v1.1.4) (2026-04-20)


### Bug Fixes

* correct qtfs cleanup and recycle bin helper naming ([70b73b8](https://github.com/newdave/sma-ng/commit/70b73b84225fda0218aa6d4d153d75462c996404))
* harden deploy setup and qsv diagnostics ([0626e51](https://github.com/newdave/sma-ng/commit/0626e5192ccad04ed01c954f5562dfcd0c02079e))


### Documentation

* sync Codex and Claude repo guidance ([920b2b0](https://github.com/newdave/sma-ng/commit/920b2b05f9bcdb045f06dd657b33eac64f54824f))

## [1.1.3](https://github.com/newdave/sma-ng/compare/sma-ng-v1.1.2...sma-ng-v1.1.3) (2026-04-19)


### Bug Fixes

* suppress SC2016 for intentional jq variable refs in sma-webhook.sh ([1d941bc](https://github.com/newdave/sma-ng/commit/1d941bc596d01741456f002d0b2f9d3d8e210e5d))

## [1.1.2](https://github.com/newdave/sma-ng/compare/sma-ng-v1.1.1...sma-ng-v1.1.2) (2026-04-19)


### Bug Fixes

* resolve Pyright errors in test_mediaprocessor and test_handler import ([492629b](https://github.com/newdave/sma-ng/commit/492629b867d3428b151ca349d34573052c3e67c4))

## [1.1.1](https://github.com/newdave/sma-ng/compare/sma-ng-v1.1.0...sma-ng-v1.1.1) (2026-04-19)


### Documentation

* replace &lt;repo&gt; placeholder with real GitHub URL ([a2d412e](https://github.com/newdave/sma-ng/commit/a2d412e43ef6f6405f22cf310ae1335b6752adc4))

## [1.1.0](https://github.com/newdave/sma-ng/compare/sma-ng-v1.0.1...sma-ng-v1.1.0) (2026-04-19)


### Features

* upgrade FFmpeg from 8.0 to 8.1 ([93e4995](https://github.com/newdave/sma-ng/commit/93e4995616ce00971eb15d3fa7ea67949d61cf54))

## [1.0.1](https://github.com/newdave/sma-ng/compare/sma-ng-v1.0.0...sma-ng-v1.0.1) (2026-04-19)


### Bug Fixes

* make Intel QSV/oneVPL conditional on amd64 for arm64 support ([5e04cd0](https://github.com/newdave/sma-ng/commit/5e04cd0a592b29138375ff9e4021a054f54e0120))

## [1.0.0](https://github.com/newdave/sma-ng/compare/sma-ng-v0.3.0...sma-ng-v1.0.0) (2026-04-19)


### ⚠ BREAKING CHANGES

* remove SQLite backend; PostgreSQL is now required
* drop SQLite backend; require PostgreSQL
* --db flag and SQLite fallback removed; SMA_DAEMON_DB_URL or daemon.json db_url is now required to start the daemon.

### Features

* add admin dashboard page with database management actions ([93e04f3](https://github.com/newdave/sma-ng/commit/93e04f3cfa03dd85b8eab1b0888a5bc6e6e8b3ab))
* add i915 GPU tuning and plexmatch sidecar scripts ([6e55738](https://github.com/newdave/sma-ng/commit/6e55738ab6d65910160528d0dbbb1b3b3ef7290a))
* add job context propagation to daemon logging (option 2) ([be2c262](https://github.com/newdave/sma-ng/commit/be2c26200ab3c0b26bf600ec33f734d2862d1247))
* add JSON structured logging to daemon file handlers (option 3) ([35dc461](https://github.com/newdave/sma-ng/commit/35dc4619a2c6d6711318e9df60d5e22b92b9cb36))
* add log viewer to dashboard ([6b1d564](https://github.com/newdave/sma-ng/commit/6b1d56416f1ee0b6e6c97990f5d0b231c3e32a62))
* add recycle-bin cleaner thread to daemon ([fb85a4a](https://github.com/newdave/sma-ng/commit/fb85a4a739100796062c10a11434a75d97c82fce))
* add rename task to Makefile and mise.toml ([c3da5f6](https://github.com/newdave/sma-ng/commit/c3da5f6f01388a7998c619e4bebc468fa993fc79))
* add rename_via_arr to mediamanager; wire force-rename in manual.py ([9a7afd4](https://github.com/newdave/sma-ng/commit/9a7afd4f5b7f760a0e337e0dd5311b07c63ef641))
* add rename.py CLI and RenameProcessor for standalone media file renaming ([e121a92](https://github.com/newdave/sma-ng/commit/e121a925a8071baaa26f81c6ce39ac521b1c97c6))
* add startup smoke test for config validation ([613b24d](https://github.com/newdave/sma-ng/commit/613b24d8d93b59903591f9aefd01a6ab181cdce3))
* air-date episode fallback and date-based naming for episode 0 ([4ec3249](https://github.com/newdave/sma-ng/commit/4ec324945725abb384c581743ef3afac07f37e71))
* API key auth, native arr webhooks, and trigger script updates ([69a2f27](https://github.com/newdave/sma-ng/commit/69a2f27d03ed0a970e4162428bb8e86b9f7d419d))
* drop SQLite backend; require PostgreSQL ([04a7d7b](https://github.com/newdave/sma-ng/commit/04a7d7b1687a43485af70a02be7cdb739a79a082))
* drop SQLite backend; require PostgreSQL ([d87d4e5](https://github.com/newdave/sma-ng/commit/d87d4e5e5bd0f242ba0bcb106d853be13c5e6efc))
* multi-profile config deployment with per-service credential stamping ([d224218](https://github.com/newdave/sma-ng/commit/d2242182d6f6b146f7a15b3362f526d185e97aa3))
* naming template improvements ([93dde33](https://github.com/newdave/sma-ng/commit/93dde33896375fea64f378b9acb123f3cdd4c67e))
* native Sonarr/Radarr webhook endpoints ([e0e26fd](https://github.com/newdave/sma-ng/commit/e0e26fd339929a61af874eaf1769fafb5db2527b))
* overhaul Docker image and compose for multi-arch, Intel QSV, GPU group resolution ([b0b4faf](https://github.com/newdave/sma-ng/commit/b0b4faf01b127d998af47f6ab7bbb60235f4082c))
* remove SQLite backend; PostgreSQL is now required ([6ef6a53](https://github.com/newdave/sma-ng/commit/6ef6a531bd3f8a281f4bceadb4bfc6de5033874b))
* tiered bitrate encoding profiles and codec refinements ([6faf936](https://github.com/newdave/sma-ng/commit/6faf936dfe9c8ffc0434cc0de0ee916b6c49b5b3))
* update naming templates and defaults in sample configs ([c48e2b9](https://github.com/newdave/sma-ng/commit/c48e2b99e391c1102c70c5f3183d5da1fe83b75a))


### Bug Fixes

* array-safe auth headers and HTTP error handling in trigger scripts ([0c2570f](https://github.com/newdave/sma-ng/commit/0c2570ff8a863dd8a2579768b9250941d5c7b252))
* collect all -vf parts into a single comma-joined filter chain ([0e7d3da](https://github.com/newdave/sma-ng/commit/0e7d3da5d13a36d9c3fbb0c991aa5d37c88b2585))
* demote noisy per-job worker log lines from INFO to DEBUG ([e5842f7](https://github.com/newdave/sma-ng/commit/e5842f72206d3b5da857a8b1d821e03b4a2c0947))
* pass disable_existing_loggers=False to fileConfig to preserve existing handlers ([a3aa96c](https://github.com/newdave/sma-ng/commit/a3aa96c408f8fbf5a83c1de954b575f652b7bf50))
* pretty-render Output Data log entries; inject job_id into config log handlers ([6e69bac](https://github.com/newdave/sma-ng/commit/6e69baceaca249c6d04a5ea0de60470bead06fbc))
* remove self-approval from auto-merge job ([f8cf207](https://github.com/newdave/sma-ng/commit/f8cf2073232c6c5f0b20ab206cf94638ff27dc72))
* remove TimeoutStopSec=infinity from systemd unit ([6f094c2](https://github.com/newdave/sma-ng/commit/6f094c2aae55e00a407212a8cb99e61e461d9d1f))
* resolve CI failures and release.yml YAML error ([7eef8cf](https://github.com/newdave/sma-ng/commit/7eef8cfedfc3980f3949048632db8f229049b9b2))
* set TimeoutStopSec=10 in systemd unit ([4979a19](https://github.com/newdave/sma-ng/commit/4979a19a4b36a5a6566e9facf022b6ad7bf8de2a))
* sync daemon log path in logging config on write; add custom hooks template ([9d1aa37](https://github.com/newdave/sma-ng/commit/9d1aa37a502fabb0ca4420e1c25ac3cd297c8cf3))
* use SIGKILL in deploy:restart to bypass graceful drain ([5613dc0](https://github.com/newdave/sma-ng/commit/5613dc038231cf0848560f55518fe40da19bd425))
* use venv python shebang in daemon.py and update.py ([2bffcd6](https://github.com/newdave/sma-ng/commit/2bffcd64f21b0e12a12e75919d8523ba83806834))


### Documentation

* add /logs endpoints, log viewer, and sync API reference ([4b5f80f](https://github.com/newdave/sma-ng/commit/4b5f80f21562a66a0431b4b133ea49779e92dd64))
* add git commit workflow rules to CLAUDE.md ([22f8665](https://github.com/newdave/sma-ng/commit/22f866586adda0b9d5e2777743c8f5a7068281b5))
* split documentation into focused pages; update README and CLAUDE.md ([7ceafe4](https://github.com/newdave/sma-ng/commit/7ceafe4526df7b28e92d8a5afd67c6128f6f65ac))
* sync docs, sample configs, and update tests ([aa3408b](https://github.com/newdave/sma-ng/commit/aa3408b91ed3fe7135d8b19d2997ec93a0895c98))
* update for PostgreSQL requirement, new features, and hardware accel ([8f0052d](https://github.com/newdave/sma-ng/commit/8f0052dbf072c1d867312fcd2e61af6a4084bc5a))

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
