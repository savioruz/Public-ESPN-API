"""Celery tasks for ESPN data ingestion.

All tasks are idempotent — safe to retry or run concurrently for
different sport/league combinations.
"""

from __future__ import annotations

import structlog
from celery import shared_task
from django.conf import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# League configuration — the (sport, league) pairs the refresh_all_* tasks fan
# out over. Sourced from settings.INGEST_LEAGUES (env-tunable), trimmed to the
# sports the downstream app uses (soccer incl. fifa.world, football, basketball)
# so each periodic refresh enqueues a handful of tasks, not ~45 — which was
# spiking the shared Postgres into "too many clients already".
# ---------------------------------------------------------------------------

ALL_LEAGUES_CONFIG: list[tuple[str, str]] = [
    (sport, league) for sport, league in settings.INGEST_LEAGUES
]


# ---------------------------------------------------------------------------
# Scoreboard tasks
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def refresh_scoreboard_task(self, sport: str, league: str, date: str | None = None) -> dict:
    """Ingest scoreboard data for a single sport/league."""
    from apps.ingest.services import ScoreboardIngestionService

    try:
        service = ScoreboardIngestionService()
        result = service.ingest_scoreboard(sport, league, date)
        logger.info(
            "scoreboard_task_completed",
            sport=sport,
            league=league,
            date=date,
            created=result.created,
            updated=result.updated,
        )
        return result.to_dict()
    except Exception as exc:
        logger.error("scoreboard_task_failed", sport=sport, league=league, error=str(exc))
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def refresh_all_scoreboards_task(self) -> dict:
    """Re-ingest scoreboards for every configured league, for **today and
    yesterday** (UTC). Yesterday covers ESPN's local/ET date bucketing (a game
    after ~20:00 ET lands on the previous UTC day) and late finishers, so live
    games advance to `final` instead of freezing at their last-seen minute.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    dates = [now.strftime("%Y%m%d"), (now - timedelta(days=1)).strftime("%Y%m%d")]
    total = {"created": 0, "updated": 0, "errors": 0}
    for sport, league in ALL_LEAGUES_CONFIG:
        for date in dates:
            try:
                refresh_scoreboard_task.delay(sport, league, date)
            except Exception as e:
                logger.error(
                    "refresh_all_scoreboards_dispatch_error",
                    sport=sport,
                    league=league,
                    date=date,
                    error=str(e),
                )
                total["errors"] += 1
    return total


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def unstick_scoreboards_task(self, lookback_days: int = 7, max_events: int = 200) -> dict:
    """Re-ingest scoreboards for games that are past kickoff yet still showing
    `scheduled`/`in_progress` — i.e. frozen because their ESPN date bucket falls
    outside the rolling today+yesterday refresh window.

    For each such event we re-ingest its scoreboard for **both** its UTC date and
    the day before (ESPN buckets by local/ET date, so a 02:00 UTC kickoff lives
    under the previous day's scoreboard). `update_or_create` then flips finished
    games to `final`. Targets only the handful of genuinely-stuck events, so the
    fan-out stays small regardless of how far back the freeze goes.
    """
    from datetime import datetime, timedelta, timezone

    from apps.espn.models import Event

    now = datetime.now(timezone.utc)
    floor = now - timedelta(days=lookback_days)
    stuck = (
        Event.objects.filter(
            status__in=[Event.STATUS_SCHEDULED, Event.STATUS_IN_PROGRESS],
            date__lt=now,
            date__gte=floor,
        )
        .select_related("league", "league__sport")
        .order_by("date")[:max_events]
    )

    # Dedupe to a set of (sport, league, date) buckets so multiple stuck games in
    # the same league/day enqueue a single re-ingest.
    buckets: set[tuple[str, str, str]] = set()
    for event in stuck:
        sport = event.league.sport.slug
        league = event.league.slug
        for d in (event.date, event.date - timedelta(days=1)):
            buckets.add((sport, league, d.strftime("%Y%m%d")))

    total = {"stuck_events": len(stuck), "dispatched": 0, "errors": 0}
    for sport, league, date in buckets:
        try:
            refresh_scoreboard_task.delay(sport, league, date)
            total["dispatched"] += 1
        except Exception as e:
            logger.error(
                "unstick_scoreboards_dispatch_error",
                sport=sport,
                league=league,
                date=date,
                error=str(e),
            )
            total["errors"] += 1
    logger.info("unstick_scoreboards_completed", **total)
    return total


# ---------------------------------------------------------------------------
# Team tasks
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def refresh_teams_task(self, sport: str, league: str) -> dict:
    """Ingest team data for a single sport/league."""
    from apps.ingest.services import TeamIngestionService

    try:
        service = TeamIngestionService()
        result = service.ingest_teams(sport, league)
        logger.info("teams_task_completed", sport=sport, league=league, created=result.created)
        return result.to_dict()
    except Exception as exc:
        logger.error("teams_task_failed", sport=sport, league=league, error=str(exc))
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def refresh_all_teams_task(self) -> dict:
    """Ingest teams for every configured league (weekly refresh)."""
    total = {"created": 0, "updated": 0, "errors": 0}
    for sport, league in ALL_LEAGUES_CONFIG:
        try:
            refresh_teams_task.delay(sport, league)
        except Exception as e:
            logger.error("refresh_all_teams_dispatch_error", sport=sport, league=league, error=str(e))
            total["errors"] += 1
    return total


# ---------------------------------------------------------------------------
# News tasks (NEW)
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def refresh_news_task(self, sport: str, league: str, limit: int = 50) -> dict:
    """Ingest news articles for a single sport/league."""
    from apps.ingest.services import NewsIngestionService

    try:
        service = NewsIngestionService()
        result = service.ingest_news(sport, league, limit=limit)
        logger.info(
            "news_task_completed",
            sport=sport,
            league=league,
            created=result.created,
            updated=result.updated,
        )
        return result.to_dict()
    except Exception as exc:
        logger.error("news_task_failed", sport=sport, league=league, error=str(exc))
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def refresh_all_news_task(self) -> dict:
    """Ingest latest news for every configured league (runs every 30 min)."""
    total = {"created": 0, "updated": 0, "errors": 0}
    for sport, league in ALL_LEAGUES_CONFIG:
        try:
            refresh_news_task.delay(sport, league)
        except Exception as e:
            logger.error("refresh_all_news_dispatch_error", sport=sport, league=league, error=str(e))
            total["errors"] += 1
    return total


# ---------------------------------------------------------------------------
# Injury tasks (NEW)
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def refresh_injuries_task(self, sport: str, league: str) -> dict:
    """Refresh injury report for a single sport/league (full snapshot)."""
    from apps.ingest.services import InjuryIngestionService

    try:
        service = InjuryIngestionService()
        result = service.ingest_injuries(sport, league)
        logger.info(
            "injuries_task_completed",
            sport=sport,
            league=league,
            created=result.created,
        )
        return result.to_dict()
    except Exception as exc:
        logger.error("injuries_task_failed", sport=sport, league=league, error=str(exc))
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def refresh_all_injuries_task(self) -> dict:
    """Refresh injury reports for every configured league (runs every 4 hours)."""
    total = {"created": 0, "errors": 0}
    for sport, league in ALL_LEAGUES_CONFIG:
        try:
            refresh_injuries_task.delay(sport, league)
        except Exception as e:
            logger.error("refresh_all_injuries_dispatch_error", sport=sport, league=league, error=str(e))
            total["errors"] += 1
    return total


# ---------------------------------------------------------------------------
# Transaction tasks (NEW)
# ---------------------------------------------------------------------------


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def refresh_transactions_task(self, sport: str, league: str) -> dict:
    """Ingest transactions for a single sport/league."""
    from apps.ingest.services import TransactionIngestionService

    try:
        service = TransactionIngestionService()
        result = service.ingest_transactions(sport, league)
        logger.info(
            "transactions_task_completed",
            sport=sport,
            league=league,
            created=result.created,
            updated=result.updated,
        )
        return result.to_dict()
    except Exception as exc:
        logger.error("transactions_task_failed", sport=sport, league=league, error=str(exc))
        raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def refresh_all_transactions_task(self) -> dict:
    """Refresh transaction feeds for every configured league (runs every 6 hours)."""
    total = {"created": 0, "updated": 0, "errors": 0}
    for sport, league in ALL_LEAGUES_CONFIG:
        try:
            refresh_transactions_task.delay(sport, league)
        except Exception as e:
            logger.error(
                "refresh_all_transactions_dispatch_error",
                sport=sport,
                league=league,
                error=str(e),
            )
            total["errors"] += 1
    return total
