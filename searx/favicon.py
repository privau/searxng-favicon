# SPDX-License-Identifier: AGPL-3.0-or-later
"""This module implements functions needed for the favicon resolver.

"""
# pylint: disable=use-dict-literal

import lxml
from httpx import HTTPError

from searx import settings

from searx.network import get as http_get, post as http_post
from searx.exceptions import SearxEngineResponseException


def update_kwargs(**kwargs):
    if 'timeout' not in kwargs:
        kwargs['timeout'] = settings['outgoing']['request_timeout']
    kwargs['raise_for_httperror'] = True


def get(*args, **kwargs):
    update_kwargs(**kwargs)
    return http_get(*args, **kwargs)


def post(*args, **kwargs):
    update_kwargs(**kwargs)
    return http_post(*args, **kwargs)

def faviconkit(domain):
    """Favicon Resolver from faviconkit.com"""

    url = 'https://causal-coral-swordfish.faviconkit.com/?{domain}/32'

    # will return a 404 if the favicon does not exist and a 200 if it does, 
    resp = get(url.format(domain=domain))

    return resp


backends = {
    'faviconkit': faviconkit,
}


def search_favicon(backend_name, domain):
    backend = backends.get(backend_name)
    if backend is None:
        return []
    try:
        return backend(domain)
    except (HTTPError, SearxEngineResponseException):
        return []
