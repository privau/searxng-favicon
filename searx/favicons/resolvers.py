# SPDX-License-Identifier: AGPL-3.0-or-later
"""Implementations of the favicon *resolvers* that are available in the favicon
proxy."""

from __future__ import annotations

__all__ = ['RESOLVERS', 'RESOLVER_MAP']

from typing import Callable
from searx import network

RESOLVERS: list[str]
RESOLVER_MAP: dict[str, Callable]


def allesedv(domain, req_args):
    """Favicon Resolver from allesedv.com / https://favicon.allesedv.com/"""
    data, mime = (None, None)

    # will just return a 200 regardless of the favicon existing or not
    # sometimes will be correct size, sometimes not
    response = network.get(f"https://f1.allesedv.com/32/{domain}", **req_args)
    if response and response.status_code == 200:
        mime = response.headers['Content-Type']
        if mime != 'image/gif':
            data = response.content
    return data, mime


def duckduckgo(domain, req_args):
    """Favicon Resolver from duckduckgo.com / https://blog.jim-nielsen.com/2021/displaying-favicons-for-any-domain/"""
    data, mime = (None, None)

    # will return a 404 if the favicon does not exist and a 200 if it does,
    response = network.get(f"https://icons.duckduckgo.com/ip2/{domain}.ico", **req_args)
    if response and response.status_code == 200:
        # api will respond with a 32x32 png image
        mime = response.headers['Content-Type']
        data = response.content
    return data, mime


def google(domain, req_args):
    """Favicon Resolver from google.com"""
    data, mime = (None, None)

    # URL https://www.google.com/s2/favicons?sz=32&domain={domain}" will be
    # redirected (HTTP 301 Moved Permanently) to t1.gstatic.com/faviconV2:
    response = network.get(
        f"https://t1.gstatic.com/faviconV2?client=SOCIAL&type=FAVICON&fallback_opts=TYPE,SIZE,URL"
        f"&url=https://{domain}&size=32",
        **req_args,
    )
    # will return a 404 if the favicon does not exist and a 200 if it does,
    if response and response.status_code == 200:
        # api will respond with a 32x32 png image
        mime = response.headers['Content-Type']
        data = response.content
    return data, mime


def yandex(domain, req_args):
    """Favicon Resolver from yandex.com"""
    data, mime = (None, None)

    response = network.get(f"https://favicon.yandex.net/favicon/{domain}", **req_args)
    # api will respond with a 16x16 png image, if it doesn't exist, it will be a
    # 1x1 png image (70 bytes)
    if response and response.status_code == 200 and len(response.content) > 70:
        mime = response.headers['Content-Type']
        data = response.content
    return data, mime


RESOLVER_MAP = {
    "allesedv": allesedv,
    "duckduckgo": duckduckgo,
    "google": google,
    "yandex": yandex,
}

RESOLVERS = list(RESOLVER_MAP.keys())
