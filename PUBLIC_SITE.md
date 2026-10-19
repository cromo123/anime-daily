# Public website operations

## What is served

The existing FastAPI/static architecture remains in place. `/` includes original
introductory text in the HTML even if the challenge API or JavaScript is unavailable.
`/how-to-play`, `/methodology`, `/faq`, and the four `/guides/...` pages explain the
actual rules. No copied MAL descriptions, invented reviews, or SEO structured data
are added. About, Privacy, Contact, and the footer link to these resources.

`/past-challenges` is a separate, server-rendered archive with 20 records per page.
`/archive` remains the unchanged player-specific JSON API for the playable calendar.
Recaps use approved stored challenges, not generated history. Only dates after the
existing public-history cutoff (inclusive) and **strictly before the current UTC-12 date** can
appear. This conservative rule waits until the day has ended everywhere before
publishing answers; a query parameter or browser timezone cannot bypass it.
Missing, legacy, invalid, fixture-contaminated, and unresolved representative data
are not published. Some pages can be sparse if stored rows fail those checks.

Recaps use existing Python comparison rules, show all 20 comparisons, link to MAL,
and disclose that metrics come from the current catalog, **not frozen historical
snapshots**. Community percentages require at least three official answers. They
use correctness recorded at the first official submission, not a re-scoring with
current catalog values; a changed metric can therefore disagree with the original
recorded accuracy. The recap discloses that difference explicitly.
Player IDs and individual answers/results are never rendered. Replay links use
`/?date=YYYY-MM-DD`; official versus practice behavior remains unchanged.

Archive listing uses two bounded SELECTs. Representative metadata uses the existing
process-local bulk graph cache, not per-entry queries. A first request can warm this
cache; it is not a new graph resolver or repeated live MAL request. The sitemap
includes at most 100 validated historical recaps; older pages remain discoverable
through pagination. No speculative `lastmod` values are emitted.

## Feedback and private administrative access

`/feedback` offers Bug and Feedback/Suggestion modes. `POST /feedback/submit` saves
reports in the additive `feedback_submissions` table using the existing SQLite /
PostgreSQL adapter. It is deliberately unrelated to player identity and game state.
It does not deliver email. Contact email is optional and private.

Safeguards: server-side field validation, 24 KiB streamed body limit, length bounds,
same-origin JSON submission checks, honeypot, safe errors, parameterized SQL, and
idempotent opaque submission IDs. Double-clicks are suppressed in the form; an
unchanged retry after network failure keeps the same ID. Submitted content is never
served as public HTML. No additional third-party scripts or services are used.

Rate limits remain **20 requests per resolved address / 10 minutes**, with a
**100-request global / 10-minute** cap. Only salted, process-local address hashes
are retained for rate limiting; raw addresses are not saved in feedback records.

The Docker command starts Uvicorn with its default proxy trust (loopback unless
`FORWARDED_ALLOW_IPS` overrides it). ACA terminates connections at ingress, so many
users can appear as one proxy peer and share the default limit. This is inferred
from the checked-in command and platform documentation; production configuration
was not accessed or changed.

Optional `ANIME_DAILY_FEEDBACK_TRUSTED_PROXIES` accepts a comma-separated list of
**explicitly verified immediate ingress IPs/CIDRs**, default empty. Only requests
whose ASGI peer is in that list may use the single rightmost `X-Forwarded-For` IP.
ACA appends that observed address; earlier values are not trusted. Invalid,
duplicate, or oversized forwarding headers fall back to the connection peer.
Wildcards and default-route CIDRs are rejected. No browser-cookie identity or
user-supplied leading IP can bypass the limits; the global cap still applies.

Before enabling this option, verify the immediate peer/trust boundary in the
deployment and ensure ASGI still receives that peer (Uvicorn proxy rewriting must
not turn an untrusted header into the apparent peer). Do not set
`FORWARDED_ALLOW_IPS=*` or guess broad private-network ranges. If the server already
resolves the client through a verified trusted-proxy configuration, use that ASGI
address and leave this new option empty. Do not parse two independent proxy chains.
No production trust list was invented here; without verified configuration, users
behind ingress can still share a limit. Restart after changing this setting.

Source: [ACA HTTP ingress headers](https://learn.microsoft.com/en-us/azure/container-apps/ingress-overview#http-headers).
This follows the ACA-specific rightmost-address contract, not arbitrary proxy/CDN
chains. A CDN in front of ACA may still cause shared limits; verify before enabling.
Limits reset on process
restart and are not distributed across replicas/workers; reevaluate protection
before scaling beyond the current single-worker container.

There is **no public admin endpoint**. Administrative access requires an authorized
shell and database credentials (or SQLite file permissions). Keep those credentials
private; do not grant general website users a shell or database account.

From that authorized environment, with its intended `DATABASE_URL` already set:

```sh
uv run python manage_feedback.py list --limit 50
uv run python manage_feedback.py list --kind bug --limit 20
uv run python manage_feedback.py show REPORT_UUID
uv run python manage_feedback.py prune --older-than-days 90
# Review the preview before intentionally applying feedback-only deletion:
uv run python manage_feedback.py prune --older-than-days 90 --apply
```

For a local SQLite file, place `--database-path PATH` **before** the subcommand.
Listing omits message/email; `show` reveals one private report. Output is escaped
JSON, not executable markup. Treat report text as untrusted and do not paste secrets
into reports. Reports are newest first. Aim to remove reports no longer needed
after 90 days using the preview/apply command; cleanup is manual, not scheduled.
The Privacy Policy discloses that reports may remain until this cleanup occurs.
For an individual deletion request, the owner can use the report UUID with a
parameterized DELETE after verifying the request. No public deletion API is added.

## Safe deployment checklist (owner action)

1. Review the full diff, content, local screenshots, and tests. No deployment was
   performed by this task. Do not run tests against a production `DATABASE_URL`.
2. Back up the target database using the existing operational process. Confirm the
   intended host without printing connection secrets.
3. In the authorized deployment environment run the existing idempotent initializer:
   `uv run python -c "from database import initialize_database; initialize_database()"`.
   This adds only `feedback_submissions` and its timestamp index. It does not migrate,
   delete, or rewrite anime, challenge, answer, result, or player records. Restart
   the application so the process-local schema cache includes the new version.
4. Run the isolated PostgreSQL checks before deployment. Native PostgreSQL 12.4
   verification now passes (see the correction record below); repeat against the
   production PostgreSQL major version in an existing safe staging/local environment
   when available. Never point automated fixtures at an existing database.
5. Deploy through the existing reviewed workflow only after owner approval. Check
   `/`, all help pages, an eligible recap, `/feedback`, `/robots.txt`, `/sitemap.xml`,
   `ads.txt`, and the unchanged daily/archive/progress APIs. Check a narrow mobile
   viewport, keyboard focus, browser console, and a full game.
6. Confirm shell/database access is restricted, read feedback privately, and perform
   retention cleanup periodically. No new paid service, email delivery, scheduler,
   infrastructure setting, or dependency is required.

## Ads and indexing

All advertisement placeholders, labels, and reserved ad spacing have been removed.
No AdSense JS, Auto Ads, ad refresh, analytics, tracking, or CMP is added. Publisher
verification, `ads.txt`, and the Privacy Policy are unchanged. There are currently
no ad slots in gameplay, public pages, or transition/results screens.

Future advertising strategy, subject to approval and a separate placement review:

- Desktop: right-side display ads alongside substantial publisher content.
- Mobile: appropriate supported placements alongside substantial content, without
  covering controls or creating accidental clicks.
- At most one visible ad at a time.
- No ads between individual questions and no automatic refresh between questions.
- No ads on empty intro, navigation, or round-transition screens.

This documents future intent only; it does not activate advertising or reserve
empty space in the current layout.

Policies considered: [Google publisher-content policy](https://support.google.com/publisherpolicies/answer/11112688)
and [JavaScript SEO guidance](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics).
More useful original content does not guarantee AdSense approval.

Search Console checklist (owner only):

- Confirm the existing domain property for `animoredle.com`; no account or DNS change
  has been made here.
- Submit `https://animoredle.com/sitemap.xml` after deployment.
- Use URL Inspection on the homepage, a guide, and an eligible historical recap.
  Check delivered/rendered content, selected canonical, mobile layout, and HTTP status.
- Useful pages and static assets remain crawlable. API/player/dev routes, duplicate
  static HTML, replay/playtest query variants, and errors carry `X-Robots-Tag: noindex`.
  `robots.txt` also discourages API crawling; it is not an access-control mechanism.
- Review indexing reports and address genuine errors before requesting another
  AdSense review. Do not claim approval or create filler pages.

## Local verification

Unset external database/API configuration for tests. Use Python 3.12 and the existing
uv environment; no dependency installation is needed.

```sh
uv run python -m unittest discover -p 'test_*.py'
node test_challenge_state.js
node test_feedback.js
node --check static/game.js
node --check static/challenge_state.js
node --check static/feedback.js
uv run python -m py_compile app.py database.py public_pages.py feedback.py manage_feedback.py
git diff --check
```

Set `ANIME_DAILY_DEV_MODE=true` **only for isolated tests** to include the two-playtest
API test. Its AI calls are mocked. Newly maintained fixtures require explicit
temporary SQLite paths and never copy production fixture history into a real store.

### Verification record — October 19, 2026

- Baseline: 32 Python tests, with three errors and two failures in stale archive /
  four-round tests, one dev-mode skip, and 26 passes. Existing JavaScript checks passed.
  Fixtures were updated to use isolated records and current publication/canonical
  rules; obsolete promo, cutoff, and shortlist expectations were corrected without
  weakening application validation. Legacy 25-question results remain explicitly tested.
- Final: **46 Python tests passed** with dev mode enabled, including mocked playtest
  curation (no actual AI), existing More Episodes rules/performance tests, official
  completion/progress, standings, and the new public-site/feedback tests. Both
  JavaScript test scripts, all three frontend syntax checks, Python compilation,
  and `git diff --check` passed. The existing Starlette/httpx deprecation warning
  remains; dependency changes were deliberately outside scope.
- In-app Chromium, temporary SQLite only: completed 20/20; refreshed after six
  answers and resumed at More Popular comparison 2 with the carried value; inspected
  20 Breakdown items and 40 MAL links; verified explicit archived-date playback.
  Desktop 1440/1920px, mobile 390px, and narrow 320px content were inspected. New
  pages have no horizontal overflow at 320px. Missing cover fallbacks worked.
  A bug report was submitted on mobile and a general report with the keyboard;
  both displayed server-confirmed private submission references. Console checks
  returned no JavaScript warnings/errors. Temporary viewport overrides were reset.
- SQLite additive migration/reinitialization, preservation, idempotency, privacy,
  origin/size/rate defenses, CLI retrieval, preview/apply retention, archive query
  count, date gates, malformed/legacy records, escaping, and XML were tested.
  PostgreSQL SQL/placeholder adaptation was mocked; Docker was not running and no
  isolated PostgreSQL binaries were then discovered on PATH. That initial limitation
  was subsequently resolved with an explicitly located disposable PostgreSQL cluster
  (see below); the earlier record is not the final verification status.
- No Azure access, production data mutation, external email, MAL/AI requests,
  dependency changes, commits, pushes, or deployment were performed. Real ad serving,
  email delivery, distributed rate limiting, and Search Console/AdSense account
  actions are intentionally not implemented by this task.

### Final pre-deployment correction record

- Historical detail pages retain 404 for missing, invalid, unpublished, legacy,
  or ineligible recaps. Unexpected service/database failures return 503 with a
  retry hint and opaque reference. Logs contain the validated date, exception type,
  SQLSTATE when present, and source frame locations—not exception messages,
  connection strings, SQL parameters, request headers, or player data.
- Recaps and Methodology distinguish current catalog values/winners from the
  original recorded official-answer accuracy. Updating the catalog does not
  recalculate those stored percentages. Gameplay and player storage are unchanged.
- Proxy-aware feedback limiting is opt-in, keeps the original per-address and global
  bounds, and has tests for shared ingress, forged prefixes, untrusted/invalid
  forwarding, and unsafe trust configuration. **Deployment action:** verify the
  actual ACA/Uvicorn trust boundary before configuring proxy addresses. Defaults
  remain conservative and may share the limit across ingress users.
- **Actual engine verification passed on PostgreSQL 12.4**, found outside PATH at
  `C:\Program Files\PostgreSQL\12\bin`. The test creates a new password-protected
  temporary cluster bound to loopback on an unused port, starts/stops only that
  cluster, and deletes its temporary credentials/data. It never uses the host's
  `DATABASE_URL` or an existing PostgreSQL cluster. It checks both report kinds,
  duplicate/conflicting IDs, table/index creation, repeated actual schema SQL,
  retrieval/filtering, administrative CLI, retention preview/apply, and preservation
  of all ten existing structured application tables, including answers/results.
- PostgreSQL 12.4 is an old local test engine, not a deployment recommendation.
  This is not a claim of Azure or production-version verification. Repeat against
  the production major version in an existing isolated staging/local environment
  when available; no paid infrastructure has been created.
- Reproduce the optional engine test by setting `ANIME_DAILY_TEST_POSTGRES_BIN` to
  an installed PostgreSQL `bin` directory, then running
  `uv run python -m unittest test_feedback_postgres -v`. It deliberately skips when
  that setting is absent. The test does not accept an existing database URL.
- Final suite with dev mode and the isolated PostgreSQL test enabled: **51 Python
  tests passed**, both JavaScript test scripts passed, compilation/syntax checks
  passed, and `git diff --check` passed. AI calls remain mocked. No ad placeholders
  were reintroduced. No Azure, real catalog/player data, deployment, commits, or
  pushes were touched. The existing review patch predates these corrections.
