# SPDX-License-Identifier: AGPL-3.0-or-later
"""Implementations for caching favicons.

:py:obj:`FaviconCacheConfig`:
  Configuration of the favicon cache

:py:obj:`FaviconCache`:
  Abstract base class for the implementation of a favicon cache.

:py:obj:`FaviconCacheSQLite`:
  Favicon cache that manages the favicon BLOBs in a SQLite DB.

:py:obj:`FaviconCacheNull`:
  Fallback solution if the configured cache cannot be used for system reasons.

----

"""

from __future__ import annotations
from typing import Literal

import abc
import hashlib
import pathlib
import sqlite3
import tempfile
import time
from pydantic import BaseModel

from searx import sqlitedb
from searx import logger

CACHE: "FaviconCache"
FALLBACK_ICON = b"FALLBACK_ICON"
logger = logger.getChild('favicons.cache')


def init(cfg: "FaviconCacheConfig"):
    """Initialization of a global ``CACHE``"""

    global CACHE  # pylint: disable=global-statement
    if cfg.db_type == "sqlite":
        if sqlite3.sqlite_version_info <= (3, 35):
            logger.critical(
                "Disable favicon caching completely: SQLite library (%s) is too old! (require >= 3.35)",
                sqlite3.sqlite_version,
            )
            CACHE = FaviconCacheNull(cfg)
        else:
            CACHE = FaviconCacheSQLite(cfg)
    elif cfg.db_type == "mem":
        logger.error("Favicons are cached in memory, don't use this in production!")
        CACHE = FaviconCacheMEM(cfg)
    else:
        raise NotImplementedError(f"favicons db_type '{cfg.db_type}' is unknown")


class FaviconCacheConfig(BaseModel):
    """Configuration of the favicon cache."""

    db_type: Literal["sqlite", "mem"] = "sqlite"
    """Type of the database:

    ``sqlite``:
      :py:obj:`.cache.FaviconCacheSQLite`

    ``mem``:
      :py:obj:`.cache.FaviconCacheMEM` (not recommended)
    """

    db_url: pathlib.Path = pathlib.Path(tempfile.gettempdir()) / "faviconcache.db"
    """URL of the SQLite DB, the path to the database file."""

    HOLD_TIME: int = 60 * 60 * 24 * 30  # 30 days
    """Hold time (default in sec.), after which a BLOB is removed from the cache."""

    LIMIT_TOTAL_BYTES: int = 1024 * 1024 * 1024  # 1 GB
    """Maximum of bytes (default) stored in the cache of all blobs.  Note: The
    limit is only reached at each maintenance interval after which the oldest
    BLOBs are deleted; the limit is exceeded during the maintenance period. If
    the maintenance period is *too long* or maintenance is switched off
    completely, the cache grows uncontrollably."""

    BLOB_MAX_BYTES: int = 1024 * 10  # 10 KB
    """The maximum BLOB size in bytes that a favicon may have so that it can be
    saved in the cache.  If the favicon is larger, it is not saved in the cache
    and must be requested by the client via the proxy."""

    MAINTENANCE_PERIOD: int = 60 * 60
    """Maintenance period in seconds / when :py:obj:`MAINTENANCE_MODE` is set to
    ``auto``."""

    MAINTENANCE_MODE: Literal["auto", "off"] = "auto"
    """Type of maintenance mode

    ``auto``:
      Maintenance is carried out automatically as part of the maintenance
      intervals (:py:obj:`MAINTENANCE_PERIOD`); no external process is required.

    ``off``:
      Maintenance is switched off and must be carried out by an external process
      if required.
    """


class FaviconCache(abc.ABC):
    """Abstract base class for the implementation of a favicon cache."""

    @abc.abstractmethod
    def __init__(self, cfg: FaviconCacheConfig):
        """An instance of the favicon cache is build up from the configuration."""

    @abc.abstractmethod
    def __call__(self, resolver: str, authority: str) -> None | tuple[str | None, bytes | None]:
        """Returns ``None`` or the tuple of ``(data, mime)`` that has been
        registered in the cache.  The ``None`` indicates that there was no entry
        in the cache."""

    @abc.abstractmethod
    def set(self, resolver: str, authority: str, mime: str | None, data: bytes | None):
        """Set data and mime-type in the cache.  If data is None, the
        :py:obj:`FALLBACK_ICON` is registered. in the cache."""


class FaviconCacheNull(FaviconCache):
    """A dummy favicon cache that caches nothing / a fallback solution. The
    NullCache is used when more efficient caches such as the
    :py:obj:`FaviconCacheSQLite` cannot be used because, for example, the SQLite
    library is only available in an old version and does not meet the
    requirements."""

    def __init__(self, cfg: FaviconCacheConfig):
        return None

    def __call__(self, resolver: str, authority: str) -> None | tuple[str | None, bytes | None]:
        return None

    def set(self, resolver: str, authority: str, mime: str | None, data: bytes | None):
        return None


class FaviconCacheSQLite(sqlitedb.SQLiteAppl, FaviconCache):
    """Favicon cache that manages the favicon BLOBs in a SQLite DB.  The DB
    model in the SQLite DB is implemented using the abstract class
    :py:obj:`sqlitedb.SQLiteAppl`.

    The following configurations are required / supported:

    - :py:obj:`FaviconCacheConfig.db_url`
    - :py:obj:`FaviconCacheConfig.HOLD_TIME`
    - :py:obj:`FaviconCacheConfig.LIMIT_TOTAL_BYTES`
    - :py:obj:`FaviconCacheConfig.BLOB_MAX_BYTES`
    - :py:obj:`MAINTENANCE_PERIOD`
    - :py:obj:`MAINTENANCE_MODE`
    """

    DB_SCHEMA = 1

    DDL_BLOBS = """\
CREATE TABLE IF NOT EXISTS blobs (
  sha256     TEXT,
  bytes_c    INTEGER,
  mime       TEXT NOT NULL,
  data       BLOB NOT NULL,
  PRIMARY KEY (sha256))"""

    """Table to store BLOB objects by their sha256 hash values."""

    DDL_BLOB_MAP = """\
CREATE TABLE IF NOT EXISTS blob_map (
    m_time     INTEGER DEFAULT (strftime('%s', 'now')),  -- last modified (unix epoch) time in sec.
    sha256     TEXT,
    resolver   TEXT,
    authority  TEXT,
    PRIMARY KEY (resolver, authority))"""

    """Table to map from (resolver, authority) to sha256 hash values."""

    DDL_CREATE_TABLES = {
        "blobs": DDL_BLOBS,
        "blob_map": DDL_BLOB_MAP,
    }

    SQL_DROP_LEFTOVER_BLOBS = (
        "DELETE FROM blobs WHERE sha256 IN ("
        " SELECT b.sha256"
        "   FROM blobs b"
        "   LEFT JOIN blob_map bm"
        "     ON b.sha256 = bm.sha256"
        "  WHERE bm.sha256 IS NULL)"
    )
    """Delete blobs.sha256 (BLOBs) no longer in blob_map.sha256."""

    SQL_ITER_BLOBS_SHA256_BYTES_C = (
        "SELECT b.sha256, b.bytes_c FROM blobs b"
        "  JOIN blob_map bm "
        "    ON b.sha256 = bm.sha256"
        " ORDER BY bm.m_time ASC"
    )

    SQL_INSERT_BLOBS = (
        "INSERT INTO blobs (sha256, bytes_c, mime, data) VALUES (?, ?, ?, ?)"
        "    ON CONFLICT (sha256) DO NOTHING"
    )  # fmt: skip

    SQL_INSERT_BLOB_MAP = (
        "INSERT INTO blob_map (sha256, resolver, authority) VALUES (?, ?, ?)"
        "    ON CONFLICT DO UPDATE "
        "   SET sha256=excluded.sha256, m_time=strftime('%s', 'now')"
    )

    def __init__(self, cfg: FaviconCacheConfig):
        """An instance of the favicon cache is build up from the configuration."""  #

        if cfg.db_url == ":memory:":
            logger.critical("don't use SQLite DB in :memory: in production!!")
        super().__init__(cfg.db_url)
        self.cfg = cfg

    def __call__(self, resolver: str, authority: str) -> None | tuple[str | None, bytes | None]:

        sql = "SELECT sha256 FROM blob_map WHERE resolver = ? AND authority = ?"
        res = self.DB.execute(sql, (resolver, authority)).fetchone()
        if res is None:
            return None

        data, mime = (None, None)
        sha256 = res[0]
        if sha256 == FALLBACK_ICON:
            return data, mime

        sql = "SELECT data, mime FROM blobs WHERE sha256 = ?"
        res = self.DB.execute(sql, (sha256,)).fetchone()
        if res is not None:
            data, mime = res
        return data, mime

    def set(self, resolver: str, authority: str, mime: str | None, data: bytes | None) -> bool:

        if self.cfg.MAINTENANCE_MODE == "auto" and int(time.time()) > self.next_maintenance_time:
            # Should automatic maintenance be moved to a new thread?
            self.maintenance()

        if data is not None and mime is None:
            logger.error(
                "favicon resolver %s tries to cache mime-type None for authority %s",
                resolver,
                authority,
            )
            return False

        bytes_c = len(data or b"")
        if bytes_c > self.cfg.BLOB_MAX_BYTES:
            logger.info(
                "favicon of resolver: %s / authority: %s to big to cache (bytes: %s) " % (resolver, authority, bytes_c)
            )
            return False

        if data is None:
            sha256 = FALLBACK_ICON
        else:
            sha256 = hashlib.sha256(data).hexdigest()

        with self.connect() as conn:
            if sha256 != FALLBACK_ICON:
                conn.execute(self.SQL_INSERT_BLOBS, (sha256, bytes_c, mime, data))
            conn.execute(self.SQL_INSERT_BLOB_MAP, (sha256, resolver, authority))
        return True

    @property
    def next_maintenance_time(self) -> int:
        """Returns (unix epoch) time of the next maintenance."""

        return self.cfg.MAINTENANCE_PERIOD + self.properties.m_time("LAST_MAINTENANCE")

    def maintenance(self, force=False):

        # Prevent parallel DB maintenance cycles from other DB connections
        # (e.g. in multi thread or process environments).

        if not force and int(time.time()) < self.next_maintenance_time:
            logger.debug("no maintenance required yet, next maintenance interval is in the future")
            return
        self.properties.set("LAST_MAINTENANCE", "")

        # do maintenance tasks

        with self.connect() as conn:

            # drop items not in HOLD time
            res = conn.execute(
                f"DELETE FROM blob_map"
                f" WHERE cast(m_time as integer) < cast(strftime('%s', 'now') as integer) - {self.cfg.HOLD_TIME}"
            )
            logger.debug("dropped %s obsolete blob_map items from db", res.rowcount)
            res = conn.execute(self.SQL_DROP_LEFTOVER_BLOBS)
            logger.debug("dropped %s obsolete BLOBS from db", res.rowcount)

            # drop old items to be in LIMIT_TOTAL_BYTES
            total_bytes = conn.execute("SELECT SUM(bytes_c) FROM blobs").fetchone()[0] or 0
            if total_bytes > self.cfg.LIMIT_TOTAL_BYTES:

                x = total_bytes - self.cfg.LIMIT_TOTAL_BYTES
                c = 0
                sha_list = []
                for row in conn.execute(self.SQL_ITER_BLOBS_SHA256_BYTES_C):
                    sha256, bytes_c = row
                    sha_list.append(sha256)
                    c += bytes_c
                    if c > x:
                        break
                if sha_list:
                    conn.execute("DELETE FROM blobs WHERE sha256 IN ('%s')" % "','".join(sha_list))
                    conn.execute("DELETE FROM blob_map WHERE sha256 IN ('%s')" % "','".join(sha_list))
                    logger.debug("dropped %s blobs with total size of %s bytes", len(sha_list), c)


class FaviconCacheMEM(FaviconCache):
    """Favicon cache in process' memory.  Its just a POC that stores the
    favicons in the memory of the process.

    .. attention::

       Don't use it in production, it will blow up your memory!!

    """

    def __init__(self, cfg):

        self.cfg = cfg
        self._data = {}
        self._sha_mime = {}

    def __call__(self, resolver: str, authority: str) -> None | tuple[str | None, bytes | None]:

        sha, mime = self._sha_mime.get(f"{resolver}:{authority}", (None, None))
        if sha is None:
            return None
        data = self._data.get(sha)
        if data == FALLBACK_ICON:
            data = None
        return data, mime

    def set(self, resolver: str, authority: str, mime: str | None, data: bytes | None):

        if data is None:
            data = FALLBACK_ICON
            mime = None

        elif mime is None:
            logger.error(
                "favicon resolver %s tries to cache mime-type None for authority %s",
                resolver,
                authority,
            )
            return

        digest = hashlib.sha256(data).hexdigest()
        self._data[digest] = data
        self._sha_mime[f"{resolver}:{authority}"] = (digest, mime)
