# -*- coding: utf-8 -*-
"""
    Copyright (C) 2017 Sebastian Golasch (plugin.video.netflix)
    Copyright (C) 2018 Caphm (original implementation module)
    Copyright (C) 2019 Stefano Gottardo - @CastagnaIT
    Handle the authentication access

    SPDX-License-Identifier: MIT
    See LICENSES/MIT.md for more information.
"""
from __future__ import absolute_import, division, unicode_literals

import resources.lib.api.website as website
import resources.lib.common as common
import resources.lib.common.cookies as cookies
import resources.lib.kodi.ui as ui
from resources.lib.api.exceptions import (LoginFailedError, LoginValidateError,
                                          MissingCredentialsError, InvalidMembershipStatusError,
                                          InvalidMembershipStatusAnonymous, LoginValidateErrorIncorrectPassword,
                                          NotConnected, NotLoggedInError)
from resources.lib.database.db_utils import TABLE_SESSION
from resources.lib.globals import g
from resources.lib.services.nfsession.session.cookie import SessionCookie
from resources.lib.services.nfsession.session.http_requests import SessionHTTPRequests

try:  # Python 2
    unicode
except NameError:  # Python 3
    unicode = str  # pylint: disable=redefined-builtin


class SessionAccess(SessionCookie, SessionHTTPRequests):
    """Handle the authentication access"""

    def __init__(self):
        super(SessionAccess, self).__init__()
        self.is_prefetch_login = False
        # Share the login function to SessionBase class
        self.external_func_login = self.login

    @common.time_execution(immediate=True)
    def prefetch_login(self):
        """Check if we have stored credentials.
        If so, do the login before the user requests it"""
        from requests import exceptions
        try:
            common.get_credentials()
            if not self.is_logged_in():
                self.login(modal_error_message=False)
            self.is_prefetch_login = True
        except exceptions.RequestException as exc:
            # It was not possible to connect to the web service, no connection, network problem, etc
            import traceback
            common.error('Login prefetch: request exception {}', exc)
            common.debug(g.py2_decode(traceback.format_exc(), 'latin-1'))
        except MissingCredentialsError:
            common.info('Login prefetch: No stored credentials are available')
        except (LoginFailedError, LoginValidateError):
            ui.show_notification(common.get_local_string(30009))
        except (InvalidMembershipStatusError, InvalidMembershipStatusAnonymous):
            ui.show_notification(common.get_local_string(30180), time=10000)

    def assert_logged_in(self):
        """Raise an exception when login cannot be established or maintained"""
        if not common.is_internet_connected():
            raise NotConnected('Internet connection not available')
        if not self.is_logged_in():
            raise NotLoggedInError

    def is_logged_in(self):
        """Check if there are valid login data"""
        valid_login = self._load_cookies() and self._verify_session_cookies() and self._verify_esn_existence()
        return valid_login

    @staticmethod
    def _verify_esn_existence():
        return bool(g.get_esn())

    def get_safe(self, endpoint, **kwargs):
        """
        Before execute a GET request to the designated endpoint,
        check the connection and the validity of the login
        """
        self.assert_logged_in()
        return self.get(endpoint, **kwargs)

    def post_safe(self, endpoint, **kwargs):
        """
        Before execute a POST request to the designated endpoint,
        check the connection and the validity of the login
        """
        self.assert_logged_in()
        return self.post(endpoint, **kwargs)

    @common.time_execution(immediate=True)
    def login(self, modal_error_message=True):
        """Perform account login"""
        try:
            # First we get the authentication url without logging in, required for login API call
            react_context = website.extract_json(self.get('login'), 'reactContext')
            auth_url = website.extract_api_data(react_context)['auth_url']
            common.debug('Logging in...')
            login_response = self.post(
                'login',
                data=_login_payload(common.get_credentials(), auth_url))
            try:
                website.extract_session_data(login_response, validate=True, update_profiles=True)
                common.info('Login successful')
                ui.show_notification(common.get_local_string(30109))
                cookies.save(self.account_hash, self.session.cookies)
                return True
            except (LoginValidateError, LoginValidateErrorIncorrectPassword) as exc:
                self.session.cookies.clear()
                common.purge_credentials()
                if not modal_error_message:
                    raise
                ui.show_ok_dialog(common.get_local_string(30008), unicode(exc))
        except InvalidMembershipStatusError:
            ui.show_error_info(common.get_local_string(30008),
                               common.get_local_string(30180),
                               False, True)
        except Exception:  # pylint: disable=broad-except
            import traceback
            common.error(g.py2_decode(traceback.format_exc(), 'latin-1'))
            self.session.cookies.clear()
            raise
        return False

    @common.time_execution(immediate=True)
    def logout(self):
        """Logout of the current account and reset the session"""
        common.debug('Logging out of current account')

        # Perform the website logout
        self.get('logout')

        g.settings_monitor_suspend(True)

        # Disable and reset auto-update / auto-sync features
        g.ADDON.setSettingInt('lib_auto_upd_mode', 1)
        g.ADDON.setSettingBool('lib_sync_mylist', False)
        g.SHARED_DB.delete_key('sync_mylist_profile_guid')

        # Disable and reset the auto-select profile
        g.LOCAL_DB.set_value('autoselect_profile_guid', '')
        g.ADDON.setSetting('autoselect_profile_name', '')
        g.ADDON.setSettingBool('autoselect_profile_enabled', False)

        # Reset of selected profile guid for library playback
        g.LOCAL_DB.set_value('library_playback_profile_guid', '')
        g.ADDON.setSetting('library_playback_profile', '')

        g.settings_monitor_suspend(False)

        # Delete cookie and credentials
        self.session.cookies.clear()
        cookies.delete(self.account_hash)
        common.purge_credentials()

        # Reset the ESN obtained from website/generated
        g.LOCAL_DB.set_value('esn', '', TABLE_SESSION)

        # Reinitialize the MSL handler (delete msl data file, then reset everything)
        common.send_signal(signal=common.Signals.REINITIALIZE_MSL_HANDLER, data=True)

        g.CACHE.clear(clear_database=True)

        common.info('Logout successful')
        ui.show_notification(common.get_local_string(30113))
        self._init_session()
        common.container_update('path', True)  # Go to a fake page to clear screen
        # Open root page
        common.container_update(g.BASE_URL, True)


def _login_payload(credentials, auth_url):
    return {
        'userLoginId': credentials.get('email'),
        'email': credentials.get('email'),
        'password': credentials.get('password'),
        'rememberMe': 'true',
        'flow': 'websiteSignUp',
        'mode': 'login',
        'action': 'loginAction',
        'withFields': 'rememberMe,nextPage,userLoginId,password,email',
        'authURL': auth_url,
        'nextPage': '',
        'showPassword': ''
    }
