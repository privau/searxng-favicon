# SPDX-License-Identifier: AGPL-3.0-or-later
"""Implementations for a favicon proxy"""

from __future__ import annotations

import base64
import pathlib
import urllib.parse

import flask
from httpx import HTTPError
from pydantic import BaseModel

from searx import get_setting
from searx.webutils import new_hmac, is_hmac_of
from searx.exceptions import SearxEngineResponseException

from .resolvers import RESOLVERS, RESOLVER_MAP
from . import cache

EMPTY_FAVICON_URL = {}

REQ_ARGS = {
    "timeout": get_setting("outgoing.request_timeout"),
    "raise_for_httperror": False,
}

CFG: FaviconProxyConfig = None  # type: ignore


def init(cfg: FaviconProxyConfig):
    global CFG  # pylint: disable=global-statement
    CFG = cfg


class FaviconProxyConfig(BaseModel):
    """Configuration of the favicon proxy."""

    max_age: int = 60 * 60 * 24 * 7  # seven days
    """HTTP header Cache-Control_ ``max-age``

    .. _Cache-Control: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Cache-Control
    """


def favicon_proxy():
    """REST API of SearXNG's favicon proxy service

    ::

        /favicon_proxy?authority=<...>&h=<...>

    ``authority``:
      Domain name :rfc:`3986` / see :py:obj:`favicon_url`

    ``h``:
      HMAC :rfc:`2104`, build up from the :ref:`server.secret_key <settings
      server>` setting.

    """
    authority = flask.request.args.get('authority')

    # malformed request or RFC 3986 authority
    if not authority or "/" in authority:
        return '', 400

    # malformed request / does not have authorisation
    if not is_hmac_of(
        get_setting("server.secret_key"),
        authority.encode(),
        flask.request.args.get('h', ''),
    ):
        return '', 400

    resolver = flask.request.preferences.get_value('favicon_resolver')  # type: ignore

    # if resolver is empty or not valid, just return HTTP 400.
    if not resolver or resolver not in RESOLVERS:
        return "", 400

    data, mime = search_favicon(resolver, authority)

    if data is not None and mime is not None:
        resp = flask.Response(data, mimetype=mime)  # type: ignore
        resp.headers['Cache-Control'] = f"max-age={CFG.max_age}"
        return resp
    # return favicon from /static/themes/simple/img/empty_favicon.svg
    theme = flask.request.preferences.get_value("theme")  # type: ignore

    return flask.send_from_directory(
        pathlib.Path(get_setting("ui.static_path")) / "themes" / theme / "img",  # type: ignore
        "empty_favicon.svg",
        mimetype="image/svg+xml",
    )


def search_favicon(resolver: str, authority: str):
    """Sends the request to the favicon resolver and returns a tuple for the
    favicon.  The tuple consists of ``(data, mime)``, if the resolver has not
    determined a favicon, both values are ``None``.

    ``data``:
      Binary data of the favicon.

    ``mime``:
      Mime type of the favicon.

    """

    data, mime = (None, None)

    func = RESOLVER_MAP.get(resolver)
    if func is None:
        return data, mime

    # to avoid superfluous requests to the resolver, first look in the cache
    data_mime = cache.CACHE(resolver, authority)
    if data_mime is not None:
        return data_mime

    try:
        data, mime = func(authority, REQ_ARGS)
        if data is None or mime is None:
            data, mime = (None, None)

    except (HTTPError, SearxEngineResponseException):
        pass

    cache.CACHE.set(resolver, authority, mime, data)
    return data, mime


def favicon_url(authority: str):
    """Function to generate the image URL used for favicons in SearXNG's result
    lists.  The ``authority`` argument (aka netloc / :rfc:`3986`) is usually a
    (sub-) domain name.  This function is used in the HTML (jinja) templates.

    .. code:: html

       <div class="favicon">
          <img src="{{ favicon_url(result.parsed_url.netloc) }}">
       </div>

    The returned URL is a route to :py:obj:`favicon_proxy` REST API.

    If the favicon is already in the cache, the returned URL is a `data URL`_
    (something like ``data:image/png;base64,...``).  By generating a data url from
    the :py:obj:`.cache.FaviconCache`, additional HTTP roundtripps via the
    :py:obj:`favicon_proxy` are saved.  However, it must also be borne in mind
    that data urls are not cached in the client (web browser).

    .. _data URL: https://developer.mozilla.org/en-US/docs/Web/HTTP/Basics_of_HTTP/Data_URLs

    """

    resolver = flask.request.preferences.get_value('favicon_resolver')  # type: ignore
    # if resolver is empty or not valid, just return nothing.
    if not resolver or resolver not in RESOLVERS:
        return ""

    data_mime = cache.CACHE(resolver, authority)

    if data_mime == (None, None):
        # we have already checked, the resolver does not have a favicon
        return empty_favicon_url()

    if data_mime is not None:
        data, mime = data_mime
        return f"data:{mime};base64,{str(base64.b64encode(data), 'utf-8')}"  # type: ignore

    h = new_hmac(get_setting("server.secret_key"), authority.encode())
    proxy_url = flask.url_for('favicon_proxy')
    query = urllib.parse.urlencode({"authority": authority, "h": h})
    return f"{proxy_url}?{query}"


def empty_favicon_url():
    # return data image URL of favicon from
    # static/themes/simple/img/empty_favicon.svg
    theme = flask.request.preferences.get_value("theme")  # type: ignore
    data_url = EMPTY_FAVICON_URL.get(theme)
    if data_url is not None:
        return data_url
    fname = pathlib.Path(get_setting("ui.static_path")) / "themes" / theme / "img" / "empty_favicon.svg"  # type: ignore
    with open(fname, "r", encoding="utf-8") as f:
        data_url = f.read()
    data_url = urllib.parse.quote(data_url)
    data_url = f"data:image/svg+xml;utf8,{data_url}"
    EMPTY_FAVICON_URL[theme] = data_url
    return data_url
