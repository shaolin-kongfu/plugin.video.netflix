# -*- coding: utf-8 -*-
"""
    Copyright (C) 2017 Sebastian Golasch (plugin.video.netflix)
    Copyright (C) 2018 Caphm (original implementation module)
    Helper functions for inter-process communication via AddonSignals

    SPDX-License-Identifier: MIT
    See LICENSES/MIT.md for more information.
"""
from __future__ import absolute_import, division, unicode_literals
from functools import wraps
import AddonSignals

from resources.lib.globals import g
import resources.lib.api.exceptions as apierrors

from .logging import debug, error, time_execution
from .misc_utils import run_threaded

try:  # Python 2
    unicode
except NameError:  # Python 3
    unicode = str  # pylint: disable=redefined-builtin

IPC_TIMEOUT_SECS = 20


class BackendNotReady(Exception):
    """The background services are not started yet"""


class Signals(object):  # pylint: disable=no-init
    """Signal names for use with AddonSignals"""
    # pylint: disable=too-few-public-methods
    PLAYBACK_INITIATED = 'playback_initiated'
    ESN_CHANGED = 'esn_changed'
    RELEASE_LICENSE = 'release_license'
    LIBRARY_UPDATE_REQUESTED = 'library_update_requested'
    UPNEXT_ADDON_INIT = 'upnext_data'
    QUEUE_VIDEO_EVENT = 'queue_video_event'
    CLEAR_USER_ID_TOKENS = 'clean_user_id_tokens'
    REINITIALIZE_MSL_HANDLER = 'reinitialize_msl_handler'
    SWITCH_EVENTS_HANDLER = 'switch_events_handler'


def register_slot(callback, signal=None, source_id=None):
    """Register a callback with AddonSignals for return calls"""
    name = signal if signal else callback.__name__
    AddonSignals.registerSlot(
        signaler_id=source_id or g.ADDON_ID,
        signal=name,
        callback=callback)
    debug('Registered AddonSignals slot {} to {}'.format(name, callback))


def unregister_slot(callback, signal=None):
    """Remove a registered callback from AddonSignals"""
    name = signal if signal else callback.__name__
    AddonSignals.unRegisterSlot(
        signaler_id=g.ADDON_ID,
        signal=name)
    debug('Unregistered AddonSignals slot {}'.format(name))


def send_signal(signal, data=None, non_blocking=False):
    """Send a signal via AddonSignals"""
    # Using sendSignal of AddonSignals you might think that it is not a blocking call instead is blocking because it
    # uses executeJSONRPC that is a blocking call, so the invoker will remain blocked until the function called by
    # executeJSONRPC has completed his operations, even if it does not return any data.
    # This workaround call sendSignal in a separate thread so immediately releases the invoker.
    # This is to be considered according to the functions to be called,
    # because it could keep the caller blocked for a certain amount of time unnecessarily.
    # To note that several consecutive calls, are made in sequence not at the same time.
    run_threaded(non_blocking, _send_signal, signal, data)


def _send_signal(signal, data):
    AddonSignals.sendSignal(
        source_id=g.ADDON_ID,
        signal=signal,
        data=data)


@time_execution(immediate=False)
def make_call(callname, data=None):
    # Note: IPC over HTTP handle FULL objects serialization, AddonSignals NOT HANDLE the serialization of objects
    if g.IPC_OVER_HTTP:
        return make_http_call(callname, data)
    return make_addonsignals_call(callname, data)


def make_http_call(callname, data):
    """Make an IPC call via HTTP and wait for it to return.
    The contents of data will be expanded to kwargs and passed into the target function."""
    from collections import OrderedDict
    try:  # Python 3
        from urllib.request import build_opener, install_opener, ProxyHandler, HTTPError, URLError, urlopen
    except ImportError:  # Python 2
        from urllib2 import build_opener, install_opener, ProxyHandler, HTTPError, URLError, urlopen
    import json
    debug('Handling HTTP IPC call to {}'.format(callname))
    # Note: On python 3, using 'localhost' slowdown the call (Windows OS is affected) not sure if it is an urllib issue
    url = 'http://127.0.0.1:{}/{}'.format(g.LOCAL_DB.get_value('ns_service_port', 8001), callname)
    install_opener(build_opener(ProxyHandler({})))  # don't use proxy for localhost
    try:
        result = json.loads(
            urlopen(url=url, data=json.dumps(data).encode('utf-8'), timeout=IPC_TIMEOUT_SECS).read(),
            object_pairs_hook=OrderedDict)
    except HTTPError as exc:
        result = json.loads(exc.reason)
    except URLError as exc:
        # On PY2 the exception message have to be decoded with latin-1 for system with symbolic characters
        err_msg = g.py2_decode(str(exc), 'latin-1')
        if '10049' in err_msg:
            err_msg += '\r\nPossible cause is wrong localhost settings in your operative system.'
        error(err_msg)
        raise BackendNotReady(g.py2_encode(err_msg, encoding='latin-1'))
    _raise_for_error(callname, result)
    return result


def make_http_call_cache(callname, params, data):
    """Make an IPC call via HTTP and wait for it to return.
    The contents of data will be expanded to kwargs and passed into the target function."""
    try:  # Python 3
        from urllib.request import build_opener, install_opener, ProxyHandler, HTTPError, URLError, Request, urlopen
    except ImportError:  # Python 2
        from urllib2 import build_opener, install_opener, ProxyHandler, HTTPError, URLError, Request, urlopen
    import json
    # debug('Handling HTTP IPC call to {}'.format(callname))
    # Note: On python 3, using 'localhost' slowdown the call (Windows OS is affected) not sure if it is an urllib issue
    url = 'http://127.0.0.1:{}/{}'.format(g.LOCAL_DB.get_value('cache_service_port', 8002), callname)
    install_opener(build_opener(ProxyHandler({})))  # don't use proxy for localhost
    r = Request(url=url, data=data, headers={'Params': json.dumps(params)})
    try:
        result = urlopen(r, timeout=IPC_TIMEOUT_SECS).read()
    except HTTPError as exc:
        try:
            raise apierrors.__dict__[exc.reason]()
        except KeyError:
            raise Exception('The service has returned: {}'.format(exc.reason))
    except URLError as exc:
        # On PY2 the exception message have to be decoded with latin-1 for system with symbolic characters
        err_msg = g.py2_decode(str(exc), 'latin-1')
        if '10049' in err_msg:
            err_msg += '\r\nPossible cause is wrong localhost settings in your operative system.'
        error(err_msg)
        raise BackendNotReady(g.py2_encode(err_msg, encoding='latin-1'))
    return result


def make_addonsignals_call(callname, data):
    """Make an IPC call via AddonSignals and wait for it to return.
    The contents of data will be expanded to kwargs and passed into the target
    function."""
    debug('Handling AddonSignals IPC call to {}'.format(callname))
    result = AddonSignals.makeCall(
        source_id=g.ADDON_ID,
        signal=callname,
        data=data,
        timeout_ms=IPC_TIMEOUT_SECS * 1000)
    _raise_for_error(callname, result)
    if result is None:
        raise Exception('Addon Signals call timeout')
    return result


def _raise_for_error(callname, result):
    if isinstance(result, dict) and 'error' in result:
        try:
            if not result['error'] == 'CacheMiss':
                error('IPC call {callname} returned {error}: {message}'.format(callname=callname, **result))
            raise apierrors.__dict__[result['error']](result['message'])
        except KeyError:
            raise Exception(result['error'])


def ipc_return_call(func):
    """
    Decorator to make a func return callable through IPC
    and handles catching, conversion and forwarding of exceptions
    """
    @wraps(func)
    def make_return_call(instance, data):
        _ipc_return_call_instance(instance, func, data)
    return make_return_call


class EnvelopeIPCReturnCall(object):
    """Makes a function callable through IPC and handles catching, conversion and forwarding of exceptions"""
    # Defines a type of in-memory reference to avoids define functions in the source code just to handle IPC return call
    _func = None

    def __init__(self, func):
        self._func = func

    def call(self, data):
        """Routes the call to the function associated to the class"""
        return _ipc_return_call(self._func, data, self._func.__name__)


def _ipc_return_call_instance(instance, func, data):
    try:
        result = _call_with_instance(instance, func, data)
    except Exception as exc:  # pylint: disable=broad-except
        result = _print_convert_exc(exc)
    return _execute_addonsignals_return_call(result, func.__name__)


def _ipc_return_call(func, data, func_name=None):
    try:
        result = _call(func, data)
    except Exception as exc:  # pylint: disable=broad-except
        result = _print_convert_exc(exc)
    return _execute_addonsignals_return_call(result, func_name)


def _print_convert_exc(exc):
    # The exception is converted into json to be handled by frontend instance
    error('IPC callback raised exception: {exc}', exc=exc)
    import traceback
    error(g.py2_decode(traceback.format_exc(), 'latin-1'))
    return {
        'error': exc.__class__.__name__,
        'message': unicode(exc),
    }


def _execute_addonsignals_return_call(result, func_name):
    """If enabled execute AddonSignals return callback"""
    if g.IPC_OVER_HTTP:
        return result
    # Do not return None or AddonSignals will keep waiting till timeout
    if result is None:
        result = {}
    AddonSignals.returnCall(signal=func_name, source_id=g.ADDON_ID, data=result)
    return result


def _call(func, data):
    if isinstance(data, dict):
        return func(**data)
    if data is not None:
        return func(data)
    return func()


def _call_with_instance(instance, func, data):
    if isinstance(data, dict):
        return func(instance, **data)
    if data is not None:
        return func(instance, data)
    return func(instance)
