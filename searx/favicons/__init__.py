# SPDX-License-Identifier: AGPL-3.0-or-later
"""Implementations for providing the favicons in SearXNG"""

from __future__ import annotations

__all__ = ["init", "favicon_url", "favicon_proxy"]

from .proxy import favicon_url, favicon_proxy


def init():
    # pylint: disable=import-outside-toplevel

    from . import config, cache, proxy

    cfg = config.FaviconConfig.from_toml_file(config.DEFAULT_CFG_TOML, use_cache=True)
    cache.init(cfg.cache)
    proxy.init(cfg.proxy)
    del cache, config, proxy, cfg
